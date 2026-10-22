"""Reproducible evaluation over evaluation/datasets/claims.csv.

What is measured (and what is not):
* Retrieval  - document-level Recall@K / MRR / nDCG@10 against the labelled supporting +
               contradicting sources of each claim. Labels are document-level, so passage-level
               retrieval quality is NOT measured (no passage labels exist).
* Stance     - for each labelled (claim, document) pair that the investigation retrieved, the
               predicted document stance (any SUPPORTS span -> S, any CONTRADICTS span -> C,
               both -> MIXED, otherwise NONE) vs the expected stance.
* Verdict    - predicted vs expected verdict (accuracy, macro-F1, confusion matrix).
* Calibration- heuristic score vs binary target (expected verdict == SUPPORTED): Brier, log loss,
               ECE, AUC, reliability bins, bootstrap CIs. Small n -> indicative only.
* Stage B    - logistic regression on claim-level evidence features, trained on dev only and
               scored on test; compared with the Stage-A score on the same claims.
* Sensitivity- verdict flip rate / accuracy when weights and thresholds are perturbed.
All labels except the 4 client-sample claims are provisional (author-drafted).
"""

from __future__ import annotations

import copy
import csv
import json
import logging
import math
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings, load_config
from app.evaluation import metrics as M

log = logging.getLogger(__name__)

VERDICTS = ["SUPPORTED", "PARTIALLY_SUPPORTED", "CONTESTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"]
STANCES = ["S", "C", "MIXED", "NONE"]


def load_claims(split: str = "all") -> list[dict[str, Any]]:
    path = get_settings().evaluation_dir / "datasets" / "claims.csv"
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["supporting"] = [x.strip() for x in r["expected_supporting_sources"].split(";") if x.strip()]
        r["contradicting"] = [x.strip() for x in r["expected_contradictory_sources"].split(";") if x.strip()]
    return [r for r in rows if split == "all" or r["split"] == split]


def _doc_ranking(hits) -> list[str]:
    seen: list[str] = []
    for h in hits:
        if h.document_id not in seen:
            seen.append(h.document_id)
    return seen


def retrieval_eval(claims, search, modes: list[str]) -> dict[str, Any]:
    from app.retrieval.schemas import SearchRequest

    out: dict[str, Any] = {}
    for mode in modes:
        if mode != "bm25" and not search.semantic_available():
            out[mode] = {"available": False, "note": "embeddings not enabled / indexed"}
            continue
        per = []
        for c in claims:
            rel = set(c["supporting"]) | set(c["contradicting"])
            if not rel:
                continue
            resp = search.search(SearchRequest(query=c["reference_claim"], size=60, mode=mode, highlight=False))
            ranked = _doc_ranking(resp.hits)
            per.append({"claim_id": c["claim_id"], "split": c["split"],
                        "recall@5": M.recall_at_k(ranked, rel, 5), "recall@10": M.recall_at_k(ranked, rel, 10),
                        "recall@20": M.recall_at_k(ranked, rel, 20), "mrr": M.reciprocal_rank(ranked, rel),
                        "ndcg@10": M.ndcg_at_k(ranked, {d: 1.0 for d in rel}, 10)})
        out[mode] = {"available": True, "n_queries": len(per),
                     **{k: round(M.nanmean([p[k] for p in per]), 4) for k in ("recall@5", "recall@10", "recall@20", "mrr", "ndcg@10")},
                     "per_claim": per}
    return out


def doc_stances(items) -> dict[str, str]:
    by: dict[str, set[str]] = {}
    for it in items:
        by.setdefault(it.ctx.document_id, set()).add(it.judgement.label)
    out = {}
    for d, labels in by.items():
        s, c = "SUPPORTS" in labels, "CONTRADICTS" in labels
        out[d] = "MIXED" if s and c else "S" if s else "C" if c else "NONE"
    return out


def expected_stance(c, doc: str) -> str | None:
    s, k = doc in c["supporting"], doc in c["contradicting"]
    return "MIXED" if s and k else "S" if s else "C" if k else None


def stance_match(exp: str, pred: str) -> bool:
    return exp == pred or (exp == "MIXED" and pred in ("S", "C")) or (pred == "MIXED" and exp in ("S", "C"))


def claim_features(result, items) -> dict[str, float]:
    sup = [it for it in items if it.judgement.label == "SUPPORTS"]
    con = [it for it in items if it.judgement.label == "CONTRADICTS"]
    return {
        "support_mass": result.support_mass, "contradict_mass": result.contradict_mass,
        "independent_support": result.independent_support, "independent_contradiction": result.independent_contradiction,
        "coverage": result.coverage, "conflict": result.conflict,
        "max_auth_support": max((it.features["authority"] * it.features["temporal"] for it in sup), default=0.0),
        "max_auth_contra": max((it.features["authority"] * it.features["temporal"] for it in con), default=0.0),
        "n_ambiguous": result.ambiguous_count,
    }


def _run_claims(db, claims, classifier_mode: str, search) -> list[dict[str, Any]]:
    from app.assessments.service import get_gazetteer, investigate
    from app.reasoning.classifier import EvidenceClassifier
    from app.reasoning.llm import get_llm_classifier
    from app.reasoning.nli import get_nli
    from app.reasoning.rules import RulesClassifier

    cfg_rsn = {**load_config("reasoning"), "_synonyms": load_config("retrieval").get("synonyms", {})}
    clf = EvidenceClassifier(RulesClassifier(cfg_rsn, get_gazetteer(db)), cfg_rsn, nli=get_nli(),
                             llm=get_llm_classifier(), mode=classifier_mode)
    rows = []
    for c in claims:
        t0 = time.perf_counter()
        out = investigate(db, c["reference_claim"], search=search, narrative=False, classifier=clf)
        st = doc_stances(out.items)
        rows.append({"claim": c, "outcome": out, "doc_stance": st, "seconds": round(time.perf_counter() - t0, 2),
                     "effective_mode": clf.mode, "notes": clf.notes})
    return rows


def _stance_metrics(rows) -> dict[str, Any]:
    pairs = []
    expected_total = 0
    for r in rows:
        c = r["claim"]
        for d in set(c["supporting"]) | set(c["contradicting"]):
            expected_total += 1
            exp = expected_stance(c, d)
            pred = r["doc_stance"].get(d)
            pairs.append({"claim_id": c["claim_id"], "document_id": d, "expected": exp,
                          "predicted": pred if pred is not None else "NOT_RETRIEVED"})
    retrieved = [p for p in pairs if p["predicted"] != "NOT_RETRIEVED"]
    yt = [p["expected"] for p in retrieved]
    yp = [p["predicted"] for p in retrieved]
    return {
        "labelled_pairs": expected_total,
        "evidence_recall": round(len(retrieved) / expected_total, 4) if expected_total else None,
        "n_scored": len(retrieved),
        "accuracy_lenient": round(sum(stance_match(t, p) for t, p in zip(yt, yp)) / len(yt), 4) if yt else None,
        "accuracy_strict": round(M.accuracy(yt, yp), 4) if yt else None,
        "per_class": M.per_class(yt, yp, ["S", "C"]),
        "macro_f1_SC": round(M.macro_f1(yt, yp, ["S", "C"]), 4) if yt else None,
        "confusion": {"labels_true": ["S", "C", "MIXED"], "labels_pred": STANCES,
                      "matrix": [[sum(1 for t, p in zip(yt, yp) if t == lt and p == lp) for lp in STANCES]
                                 for lt in ["S", "C", "MIXED"]]},
        "pairs": pairs,
    }


def _verdict_metrics(rows) -> dict[str, Any]:
    yt = [r["claim"]["expected_verdict"] for r in rows]
    yp = [r["outcome"].result.verdict for r in rows]
    n = len(rows)
    acc_ci = M.bootstrap_ci(lambda idx: sum(yt[i] == yp[i] for i in idx) / len(idx), n) if n else (None, None)
    # collapse: credible (SUPPORTED/PARTIAL) vs not
    coarse = lambda v: "SUPPORTED" if v in ("SUPPORTED", "PARTIALLY_SUPPORTED") else v
    return {
        "n": n, "accuracy": round(M.accuracy(yt, yp), 4) if n else None,
        "accuracy_ci95": [round(x, 3) for x in acc_ci] if n else None,
        "accuracy_partial_as_supported": round(M.accuracy([coarse(t) for t in yt], [coarse(p) for p in yp]), 4) if n else None,
        "macro_f1": round(M.macro_f1(yt, yp, VERDICTS), 4) if n else None,
        "per_class": M.per_class(yt, yp, VERDICTS),
        "confusion": {"labels": VERDICTS, "matrix": M.confusion(yt, yp, VERDICTS)},
        "predicted_distribution": M.label_counts(yp), "expected_distribution": M.label_counts(yt),
    }


def _calibration(rows) -> dict[str, Any]:
    y = [1 if r["claim"]["expected_verdict"] == "SUPPORTED" else 0 for r in rows]
    p = [r["outcome"].result.score for r in rows]
    if not rows or len(set(y)) < 2:
        return {"available": False, "note": "needs both outcome classes"}
    bins, ece = M.reliability(y, p, bins=5)
    base = sum(y) / len(y)
    n = len(y)
    return {
        "available": True, "n": n, "target": "expected_verdict == SUPPORTED", "positives": sum(y),
        "brier": round(M.brier(y, p), 4),
        "brier_ci95": [round(x, 4) for x in M.bootstrap_ci(lambda idx: M.brier([y[i] for i in idx], [p[i] for i in idx]), n)],
        "brier_constant_base_rate": round(M.brier(y, [base] * n), 4),
        "log_loss": round(M.log_loss(y, p), 4), "ece_5bin": round(ece, 4), "auc": round(M.auc(y, p), 4),
        "reliability": bins,
        "note": "Indicative only: small n and provisional labels; the score is a heuristic, not a calibrated probability.",
    }


def _stage_b(dev_rows, test_rows) -> dict[str, Any]:
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
    feats = list(dev_rows[0]["features"].keys())
    Xd = np.array([[r["features"][f] for f in feats] for r in dev_rows])
    yd = np.array([1 if r["claim"]["expected_verdict"] == "SUPPORTED" else 0 for r in dev_rows])
    Xt = np.array([[r["features"][f] for f in feats] for r in test_rows])
    yt = [1 if r["claim"]["expected_verdict"] == "SUPPORTED" else 0 for r in test_rows]
    if len(set(yd)) < 2:
        return {"available": False, "note": "dev split has a single class"}
    model = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000))
    model.fit(Xd, yd)
    pt = model.predict_proba(Xt)[:, 1].tolist()
    base = [r["outcome"].result.score for r in test_rows]
    Xall = np.vstack([Xd, Xt])
    yall = np.concatenate([yd, np.array(yt)])
    loo = cross_val_predict(make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000)), Xall, yall,
                            cv=LeaveOneOut(), method="predict_proba")[:, 1].tolist()
    base_all = [r["outcome"].result.score for r in dev_rows + test_rows]
    coefs = dict(zip(feats, [round(float(c), 4) for c in model[-1].coef_[0]]))
    return {
        "available": True, "features": feats, "train": "dev", "test": "test", "n_train": len(yd), "n_test": len(yt),
        "coefficients_standardised": coefs,
        "test": {"logreg": {"brier": round(M.brier(yt, pt), 4), "log_loss": round(M.log_loss(yt, pt), 4),
                            "auc": round(M.auc(yt, pt), 4)},
                 "baseline_score": {"brier": round(M.brier(yt, base), 4), "log_loss": round(M.log_loss(yt, base), 4),
                                    "auc": round(M.auc(yt, base), 4)}},
        "leave_one_out_all": {"logreg": {"brier": round(M.brier(list(yall), loo), 4), "auc": round(M.auc(list(yall), loo), 4)},
                              "baseline_score": {"brier": round(M.brier(list(yall), base_all), 4),
                                                 "auc": round(M.auc(list(yall), base_all), 4)}},
        "note": "Tiny training set; treat as a feasibility comparison, not a validated model. A Bayesian (PyMC) "
                "model was not fitted: with ~30 labelled training claims it could not be validated meaningfully.",
    }


def _sensitivity(rows, base_cfg) -> dict[str, Any]:
    from app.credibility.engine import CredibilityEngine

    def rescore(cfg) -> list[str]:
        eng = CredibilityEngine(cfg)
        out = []
        for r in rows:
            o = r["outcome"]
            items = [copy.copy(it) for it in o.items]
            res = eng.assess(o.analysis, items, o.docs, o.edges)
            out.append(res.verdict)
        return out

    yt = [r["claim"]["expected_verdict"] for r in rows]
    base = rescore(base_cfg)
    scenarios: dict[str, Any] = {}

    def scen(name, mutate):
        cfg = copy.deepcopy(base_cfg)
        mutate(cfg)
        v = rescore(cfg)
        scenarios[name] = {"flip_rate": round(sum(a != b for a, b in zip(base, v)) / len(v), 4),
                           "accuracy": round(M.accuracy(yt, v), 4)}

    for f in (0.75, 1.25):
        scen(f"authority_formal_x{f}", lambda c, f=f: [t.__setitem__("formal", t["formal"] * f) for t in c["authority"].values()])
        scen(f"authority_practice_x{f}", lambda c, f=f: [t.__setitem__("practice", t["practice"] * f) for t in c["authority"].values()])
        scen(f"authority_testimony_belief_x{f}", lambda c, f=f: [(t.__setitem__("testimony", t["testimony"] * f),
                                                                  t.__setitem__("belief", t["belief"] * f)) for t in c["authority"].values()])
    scen("equal_authority_all_scopes", lambda c: [t.update({k: 0.7 for k in t}) for t in c["authority"].values()])
    scen("no_temporal_discounting", lambda c: c["temporal"].update({k: 1.0 for k in c["temporal"] if k != "after_as_of"}))
    scen("dependent_discount_0 (dependents ignored)", lambda c: c["dependence"].update({"dependent_discount": 0.0}))
    scen("dependent_discount_1 (no independence model)", lambda c: c["dependence"].update({"dependent_discount": 1.0}))
    scen("priors_0.25", lambda c: c["aggregation"].update({"prior_support": 0.25, "prior_contradict": 0.25}))
    scen("priors_1.0", lambda c: c["aggregation"].update({"prior_support": 1.0, "prior_contradict": 1.0}))
    scen("thresholds_+0.05", lambda c: c["verdict"].update({"supported": c["verdict"]["supported"] + 0.05,
                                                            "contradicted": c["verdict"]["contradicted"] + 0.05}))
    scen("thresholds_-0.05", lambda c: c["verdict"].update({"supported": c["verdict"]["supported"] - 0.05,
                                                            "contradicted": c["verdict"]["contradicted"] - 0.05}))
    return {"base_accuracy": round(M.accuracy(yt, base), 4), "scenarios": scenarios,
            "note": "Re-scores the recorded evidence judgements under perturbed assumptions (retrieval/classification fixed)."}


def run_evaluation(split: str = "all", classifiers: list[str] | None = None, save: bool = True,
                   retrieval_modes: list[str] | None = None) -> dict[str, Any]:
    from app.database.session import get_sessionmaker
    from app.retrieval.service import get_search_service

    settings = get_settings()
    claims = load_claims("all")
    classifiers = classifiers or [load_config("reasoning").get("classifier", {}).get("mode", "rules")]
    search = get_search_service()
    report: dict[str, Any] = {
        "id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {"path": "evaluation/datasets/claims.csv", "n_claims": len(claims),
                    "by_split": M.label_counts([c["split"] for c in claims]),
                    "label_sources": M.label_counts([c["label_source"] for c in claims]),
                    "verdict_distribution": M.label_counts([c["expected_verdict"] for c in claims])},
        "settings": {"search_backend": search.backend.name, "embeddings": search.semantic_available(),
                     "nli_model": settings.nli_model if settings.nli_enabled else None,
                     "llm_model": settings.llm_model if settings.llm_provider != "none" else None},
        "split_evaluated": split, "classifiers": {}, "retrieval": {},
    }
    selected = [c for c in claims if split == "all" or c["split"] == split]
    report["retrieval"] = retrieval_eval(selected, search, retrieval_modes or ["bm25", "semantic", "hybrid"])
    with get_sessionmaker()() as db:
        for mode in classifiers:
            t0 = time.perf_counter()
            rows = _run_claims(db, selected, mode, search)
            for r in rows:
                r["features"] = claim_features(r["outcome"].result, r["outcome"].items)
            by_split = {s: [r for r in rows if r["claim"]["split"] == s] for s in ("dev", "test")}
            pipeline_ret = []
            for r in rows:
                rel = set(r["claim"]["supporting"]) | set(r["claim"]["contradicting"])
                if rel:
                    ranked = list(dict.fromkeys(it.ctx.document_id for it in r["outcome"].items))
                    pipeline_ret.append({"recall@all_evidence": M.recall_at_k(ranked, rel, len(ranked) or 1),
                                         "mrr": M.reciprocal_rank(ranked, rel)})
            block = {
                "effective_mode": rows[0]["effective_mode"] if rows else mode, "notes": rows[0]["notes"] if rows else [],
                "seconds_total": round(time.perf_counter() - t0, 1),
                "investigation_evidence_recall": round(M.nanmean([p["recall@all_evidence"] for p in pipeline_ret]), 4),
                "stance": {s: _stance_metrics(v) for s, v in {"all": rows, **by_split}.items() if v},
                "verdict": {s: _verdict_metrics(v) for s, v in {"all": rows, **by_split}.items() if v},
                "calibration": {s: _calibration(v) for s, v in {"all": rows, **by_split}.items() if v},
                "stage_b": _stage_b(by_split["dev"], by_split["test"]),
                "sensitivity": _sensitivity(rows, load_config("credibility")),
                "claims": [{"claim_id": r["claim"]["claim_id"], "split": r["claim"]["split"],
                            "claim_type": r["outcome"].analysis.claim_type,
                            "expected": r["claim"]["expected_verdict"], "predicted": r["outcome"].result.verdict,
                            "score": r["outcome"].result.score, "interval": r["outcome"].result.interval,
                            "needs_review": r["outcome"].result.needs_review, "features": r["features"],
                            "doc_stance": r["doc_stance"], "seconds": r["seconds"]} for r in rows],
            }
            report["classifiers"][mode] = block
    report["summary"] = {
        "retrieval": {m: {k: v for k, v in d.items() if k != "per_claim"} for m, d in report["retrieval"].items()},
        **{f"{m}": {"effective_mode": b["effective_mode"],
                    "verdict_accuracy": {s: b["verdict"][s]["accuracy"] for s in b["verdict"]},
                    "verdict_macro_f1": {s: b["verdict"][s]["macro_f1"] for s in b["verdict"]},
                    "stance_accuracy_lenient": {s: b["stance"][s]["accuracy_lenient"] for s in b["stance"]},
                    "evidence_recall": {s: b["stance"][s]["evidence_recall"] for s in b["stance"]},
                    "brier": {s: b["calibration"][s].get("brier") for s in b["calibration"]},
                    "seconds": b["seconds_total"]}
           for m, b in report["classifiers"].items()},
    }
    if save:
        save_report(report)
    return report


def save_report(report: dict[str, Any]) -> Path:
    outdir = get_settings().evaluation_dir / "results"
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = outdir / f"eval_{stamp}.json"
    text = json.dumps(report, indent=2, default=str)
    path.write_text(text, encoding="utf-8")
    (outdir / "latest.json").write_text(text, encoding="utf-8")
    try:
        from app.database.models import EvaluationRun
        from app.database.session import get_sessionmaker

        with get_sessionmaker()() as db:
            db.add(EvaluationRun(id=report["id"], dataset=report["dataset"],
                                 config={"split": report["split_evaluated"], "classifiers": list(report["classifiers"]),
                                         "settings": report["settings"]},
                                 metrics=json.loads(json.dumps(report["summary"], default=str))))
            db.commit()
    except Exception as exc:  # noqa: BLE001 - the JSON file is the primary artefact
        log.warning("Could not record evaluation run in the database: %s", exc)
    return path
