"""What is measured on the labelled claim set (and what is not).

* Retrieval   document-level Recall@K / MRR / nDCG@10 against the labelled supporting + contradicting
              sources of each claim. Labels are document-level, so passage-level retrieval quality is
              NOT measured (no passage labels exist).
* Stance      for each labelled (claim, document) pair that the investigation retrieved, the predicted
              document stance (any SUPPORTS span -> S, any CONTRADICTS span -> C, both -> MIXED,
              otherwise NONE) vs the expected stance.
* Verdict     predicted vs expected verdict (accuracy, macro-F1, confusion matrix).
* Calibration heuristic score vs binary target (expected verdict == SUPPORTED): Brier, log loss, ECE,
              AUC, reliability bins, bootstrap CIs. Small n -> indicative only.
"""

from __future__ import annotations

import csv
from typing import Any

from okc.evaluation import metrics as M
from okc.search.schemas import SearchRequest
from okc.search.service import SearchService
from okc.settings import get_settings

VERDICTS = ["SUPPORTED", "PARTIALLY_SUPPORTED", "CONTESTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"]
STANCES = ["S", "C", "MIXED", "NONE"]
DATASET = ("datasets", "claims.csv")


def load_claims(split: str = "all") -> list[dict[str, Any]]:
    path = get_settings().evaluation_dir.joinpath(*DATASET)
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["supporting"] = [x.strip() for x in r["expected_supporting_sources"].split(";") if x.strip()]
        r["contradicting"] = [x.strip() for x in r["expected_contradictory_sources"].split(";") if x.strip()]
    return [r for r in rows if split == "all" or r["split"] == split]


def _document_ranking(hits) -> list[str]:
    return list(dict.fromkeys(h.document_id for h in hits))


def retrieval_eval(claims: list[dict[str, Any]], search: SearchService, modes: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for mode in modes:
        if mode != "bm25" and not search.semantic_available():
            out[mode] = {"available": False, "note": "embeddings not enabled / indexed"}
            continue
        per_claim = []
        for c in claims:
            relevant = set(c["supporting"]) | set(c["contradicting"])
            if not relevant:
                continue
            ranked = _document_ranking(search.search(
                SearchRequest(query=c["reference_claim"], size=60, mode=mode, highlight=False)).hits)
            per_claim.append({"claim_id": c["claim_id"], "split": c["split"],
                              "recall@5": M.recall_at_k(ranked, relevant, 5), "recall@10": M.recall_at_k(ranked, relevant, 10),
                              "recall@20": M.recall_at_k(ranked, relevant, 20), "mrr": M.reciprocal_rank(ranked, relevant),
                              "ndcg@10": M.ndcg_at_k(ranked, {d: 1.0 for d in relevant}, 10)})
        out[mode] = {"available": True, "n_queries": len(per_claim),
                     **{k: round(M.nanmean([p[k] for p in per_claim]), 4) for k in ("recall@5", "recall@10", "recall@20", "mrr", "ndcg@10")},
                     "per_claim": per_claim}
    return out


def document_stances(items) -> dict[str, str]:
    by_document: dict[str, set[str]] = {}
    for it in items:
        by_document.setdefault(it.ctx.document_id, set()).add(it.judgement.label)
    out = {}
    for doc, labels in by_document.items():
        s, c = "SUPPORTS" in labels, "CONTRADICTS" in labels
        out[doc] = "MIXED" if s and c else "S" if s else "C" if c else "NONE"
    return out


def expected_stance(c: dict[str, Any], doc: str) -> str | None:
    s, k = doc in c["supporting"], doc in c["contradicting"]
    return "MIXED" if s and k else "S" if s else "C" if k else None


def stance_match(expected: str, predicted: str) -> bool:
    return expected == predicted or (expected == "MIXED" and predicted in ("S", "C")) or (predicted == "MIXED" and expected in ("S", "C"))


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


def stance_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = []
    expected_total = 0
    for r in rows:
        c = r["claim"]
        for d in set(c["supporting"]) | set(c["contradicting"]):
            expected_total += 1
            predicted = r["doc_stance"].get(d)
            pairs.append({"claim_id": c["claim_id"], "document_id": d, "expected": expected_stance(c, d),
                          "predicted": predicted if predicted is not None else "NOT_RETRIEVED"})
    retrieved = [p for p in pairs if p["predicted"] != "NOT_RETRIEVED"]
    y_true, y_pred = [p["expected"] for p in retrieved], [p["predicted"] for p in retrieved]
    return {
        "labelled_pairs": expected_total,
        "evidence_recall": round(len(retrieved) / expected_total, 4) if expected_total else None,
        "n_scored": len(retrieved),
        "accuracy_lenient": round(sum(stance_match(t, p) for t, p in zip(y_true, y_pred)) / len(y_true), 4) if y_true else None,
        "accuracy_strict": round(M.accuracy(y_true, y_pred), 4) if y_true else None,
        "per_class": M.per_class(y_true, y_pred, ["S", "C"]),
        "macro_f1_SC": round(M.macro_f1(y_true, y_pred, ["S", "C"]), 4) if y_true else None,
        "confusion": {"labels_true": ["S", "C", "MIXED"], "labels_pred": STANCES,
                      "matrix": [[sum(1 for t, p in zip(y_true, y_pred) if t == lt and p == lp) for lp in STANCES]
                                 for lt in ["S", "C", "MIXED"]]},
        "pairs": pairs,
    }


def _coarse(verdict: str) -> str:
    """Credible = SUPPORTED or PARTIALLY_SUPPORTED."""
    return "SUPPORTED" if verdict in ("SUPPORTED", "PARTIALLY_SUPPORTED") else verdict


def verdict_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    y_true = [r["claim"]["expected_verdict"] for r in rows]
    y_pred = [r["outcome"].result.verdict for r in rows]
    n = len(rows)
    ci = M.bootstrap_ci(lambda idx: sum(y_true[i] == y_pred[i] for i in idx) / len(idx), n) if n else (None, None)
    return {
        "n": n, "accuracy": round(M.accuracy(y_true, y_pred), 4) if n else None,
        "accuracy_ci95": [round(x, 3) for x in ci] if n else None,
        "accuracy_partial_as_supported": round(M.accuracy([_coarse(t) for t in y_true], [_coarse(p) for p in y_pred]), 4) if n else None,
        "macro_f1": round(M.macro_f1(y_true, y_pred, VERDICTS), 4) if n else None,
        "per_class": M.per_class(y_true, y_pred, VERDICTS),
        "confusion": {"labels": VERDICTS, "matrix": M.confusion(y_true, y_pred, VERDICTS)},
        "predicted_distribution": M.label_counts(y_pred), "expected_distribution": M.label_counts(y_true),
    }


def calibration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    y = [1 if r["claim"]["expected_verdict"] == "SUPPORTED" else 0 for r in rows]
    p = [r["outcome"].result.score for r in rows]
    if not rows or len(set(y)) < 2:
        return {"available": False, "note": "needs both outcome classes"}
    bins, ece = M.reliability(y, p, bins=5)
    base_rate, n = sum(y) / len(y), len(y)
    return {
        "available": True, "n": n, "target": "expected_verdict == SUPPORTED", "positives": sum(y),
        "brier": round(M.brier(y, p), 4),
        "brier_ci95": [round(x, 4) for x in M.bootstrap_ci(lambda idx: M.brier([y[i] for i in idx], [p[i] for i in idx]), n)],
        "brier_constant_base_rate": round(M.brier(y, [base_rate] * n), 4),
        "log_loss": round(M.log_loss(y, p), 4), "ece_5bin": round(ece, 4), "auc": round(M.auc(y, p), 4),
        "reliability": bins,
        "note": "Indicative only: small n and provisional labels; the score is a heuristic, not a calibrated probability.",
    }
