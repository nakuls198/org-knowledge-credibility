"""Two extra analyses on the labelled claims.

* Stage B     logistic regression on claim-level evidence features, trained on dev only and scored on
              test; compared with the Stage-A score on the same claims.
* Sensitivity verdict flip rate / accuracy when weights and thresholds are perturbed.
"""

from __future__ import annotations

import copy
from typing import Any

from okc.credibility.engine import CredibilityEngine
from okc.evaluation import metrics as M


def stage_b(dev_rows: list[dict[str, Any]], test_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Logistic regression on claim-level evidence features (train dev -> score test)."""
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import LeaveOneOut, cross_val_predict
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return {"available": False}
    if not dev_rows or not test_rows:
        return {"available": False, "note": "needs both dev and test rows"}

    def target(rows):
        return [1 if r["claim"]["expected_verdict"] == "SUPPORTED" else 0 for r in rows]

    features = list(dev_rows[0]["features"].keys())
    x_dev = np.array([[r["features"][f] for f in features] for r in dev_rows])
    x_test = np.array([[r["features"][f] for f in features] for r in test_rows])
    y_dev, y_test = np.array(target(dev_rows)), target(test_rows)
    if len(set(y_dev)) < 2:
        return {"available": False, "note": "dev split has a single class"}

    def new_model():
        return make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000))

    model = new_model().fit(x_dev, y_dev)
    p_test = model.predict_proba(x_test)[:, 1].tolist()
    baseline_test = [r["outcome"].result.score for r in test_rows]
    x_all, y_all = np.vstack([x_dev, x_test]), np.concatenate([y_dev, np.array(y_test)])
    loo = cross_val_predict(new_model(), x_all, y_all, cv=LeaveOneOut(), method="predict_proba")[:, 1].tolist()
    baseline_all = [r["outcome"].result.score for r in dev_rows + test_rows]
    coefficients = dict(zip(features, [round(float(c), 4) for c in model[-1].coef_[0]]))
    return {
        "available": True, "features": features, "train": "dev", "n_train": len(y_dev), "n_test": len(y_test),
        "coefficients_standardised": coefficients,
        "test": {"logreg": {"brier": round(M.brier(y_test, p_test), 4), "log_loss": round(M.log_loss(y_test, p_test), 4),
                            "auc": round(M.auc(y_test, p_test), 4)},
                 "baseline_score": {"brier": round(M.brier(y_test, baseline_test), 4),
                                    "log_loss": round(M.log_loss(y_test, baseline_test), 4),
                                    "auc": round(M.auc(y_test, baseline_test), 4)}},
        "leave_one_out_all": {"logreg": {"brier": round(M.brier(list(y_all), loo), 4), "auc": round(M.auc(list(y_all), loo), 4)},
                              "baseline_score": {"brier": round(M.brier(list(y_all), baseline_all), 4),
                                                 "auc": round(M.auc(list(y_all), baseline_all), 4)}},
        "note": "Tiny training set; treat as a feasibility comparison, not a validated model. A Bayesian (PyMC) "
                "model was not fitted: with ~30 labelled training claims it could not be validated meaningfully.",
    }


def _scale(table: dict[str, float], keys: tuple[str, ...], factor: float) -> None:
    for k in keys:
        table[k] *= factor


def _scenarios(factors=(0.75, 1.25)):
    """(name, mutation of a credibility config) pairs: perturbations of the assumptions."""
    for f in factors:
        yield f"authority_formal_x{f}", lambda c, f=f: [_scale(t, ("formal",), f) for t in c["authority"].values()]
        yield f"authority_practice_x{f}", lambda c, f=f: [_scale(t, ("practice",), f) for t in c["authority"].values()]
        yield f"authority_testimony_belief_x{f}", lambda c, f=f: [_scale(t, ("testimony", "belief"), f) for t in c["authority"].values()]
    yield "equal_authority_all_scopes", lambda c: [t.update({k: 0.7 for k in t}) for t in c["authority"].values()]
    yield "no_temporal_discounting", lambda c: c["temporal"].update({k: 1.0 for k in c["temporal"] if k != "after_as_of"})
    yield "dependent_discount_0 (dependents ignored)", lambda c: c["dependence"].update({"dependent_discount": 0.0})
    yield "dependent_discount_1 (no independence model)", lambda c: c["dependence"].update({"dependent_discount": 1.0})
    yield "priors_0.25", lambda c: c["aggregation"].update({"prior_support": 0.25, "prior_contradict": 0.25})
    yield "priors_1.0", lambda c: c["aggregation"].update({"prior_support": 1.0, "prior_contradict": 1.0})
    for shift in (0.05, -0.05):
        yield f"thresholds_{shift:+.2f}", lambda c, s=shift: c["verdict"].update(
            {"supported": c["verdict"]["supported"] + s, "contradicted": c["verdict"]["contradicted"] + s})


def sensitivity(rows: list[dict[str, Any]], base_cfg: dict[str, Any]) -> dict[str, Any]:
    """Re-scores the recorded evidence judgements under perturbed assumptions (retrieval/classification fixed)."""

    def rescore(cfg: dict[str, Any]) -> list[str]:
        engine = CredibilityEngine(cfg)
        return [engine.assess(r["outcome"].analysis, [copy.copy(it) for it in r["outcome"].items],
                              r["outcome"].docs, r["outcome"].edges).verdict for r in rows]

    expected = [r["claim"]["expected_verdict"] for r in rows]
    base = rescore(base_cfg)
    scenarios: dict[str, Any] = {}
    for name, mutate in _scenarios():
        cfg = copy.deepcopy(base_cfg)
        mutate(cfg)
        verdicts = rescore(cfg)
        scenarios[name] = {"flip_rate": round(sum(a != b for a, b in zip(base, verdicts)) / len(verdicts), 4),
                           "accuracy": round(M.accuracy(expected, verdicts), 4)}
    return {"base_accuracy": round(M.accuracy(expected, base), 4), "scenarios": scenarios,
            "note": "Re-scores the recorded evidence judgements under perturbed assumptions (retrieval/classification fixed)."}
