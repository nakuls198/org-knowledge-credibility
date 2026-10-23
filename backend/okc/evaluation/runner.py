"""Reproducible evaluation over `evaluation/datasets/claims.csv`.

Reports retrieval, stance, verdict and calibration (see `okc.evaluation.measures`), plus the Stage B
comparison and a sensitivity analysis (`okc.evaluation.analyses`). All labels except the four
client-sample claims are provisional (author-drafted).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from okc.assessment.investigate import build_classifier, get_gazetteer, investigate
from okc.db.models import EvaluationRun
from okc.db.session import get_sessionmaker
from okc.evaluation import analyses
from okc.evaluation import measures as ms
from okc.evaluation import metrics as M
from okc.search.service import SearchService, get_search_service
from okc.settings import get_settings, load_config

log = logging.getLogger(__name__)
_SPLITS = ("dev", "test")


def _run_claims(db, claims: list[dict[str, Any]], classifier_mode: str, search: SearchService) -> list[dict[str, Any]]:
    classifier = build_classifier(get_gazetteer(db), classifier_mode)
    rows = []
    for c in claims:
        t0 = time.perf_counter()
        outcome = investigate(db, c["reference_claim"], search=search, narrative=False, classifier=classifier)
        rows.append({"claim": c, "outcome": outcome, "doc_stance": ms.document_stances(outcome.items),
                     "seconds": round(time.perf_counter() - t0, 2), "effective_mode": classifier.mode,
                     "notes": classifier.notes})
    return rows


def _by_split(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {"all": rows, **{s: [r for r in rows if r["claim"]["split"] == s] for s in _SPLITS}}


def _classifier_report(mode: str, rows: list[dict[str, Any]], started: float) -> dict[str, Any]:
    for r in rows:
        r["features"] = ms.claim_features(r["outcome"].result, r["outcome"].items)
    splits = {name: part for name, part in _by_split(rows).items() if part}
    in_pipeline = []
    for r in rows:
        relevant = set(r["claim"]["supporting"]) | set(r["claim"]["contradicting"])
        if relevant:
            ranked = list(dict.fromkeys(it.ctx.document_id for it in r["outcome"].items))
            in_pipeline.append({"recall@all_evidence": M.recall_at_k(ranked, relevant, len(ranked) or 1),
                                "mrr": M.reciprocal_rank(ranked, relevant)})
    return {
        "effective_mode": rows[0]["effective_mode"] if rows else mode, "notes": rows[0]["notes"] if rows else [],
        "seconds_total": round(time.perf_counter() - started, 1),
        "investigation_evidence_recall": round(M.nanmean([p["recall@all_evidence"] for p in in_pipeline]), 4),
        "stance": {s: ms.stance_metrics(v) for s, v in splits.items()},
        "verdict": {s: ms.verdict_metrics(v) for s, v in splits.items()},
        "calibration": {s: ms.calibration(v) for s, v in splits.items()},
        "stage_b": analyses.stage_b(_by_split(rows)["dev"], _by_split(rows)["test"]),
        "sensitivity": analyses.sensitivity(rows, load_config("credibility")),
        "claims": [{"claim_id": r["claim"]["claim_id"], "split": r["claim"]["split"],
                    "claim_type": r["outcome"].analysis.claim_type, "expected": r["claim"]["expected_verdict"],
                    "predicted": r["outcome"].result.verdict, "score": r["outcome"].result.score,
                    "interval": r["outcome"].result.interval, "needs_review": r["outcome"].result.needs_review,
                    "features": r["features"], "doc_stance": r["doc_stance"], "seconds": r["seconds"]} for r in rows],
    }


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"retrieval": {m: {k: v for k, v in d.items() if k != "per_claim"}
                                             for m, d in report["retrieval"].items()}}
    for mode, b in report["classifiers"].items():
        summary[mode] = {"effective_mode": b["effective_mode"],
                         "verdict_accuracy": {s: b["verdict"][s]["accuracy"] for s in b["verdict"]},
                         "verdict_macro_f1": {s: b["verdict"][s]["macro_f1"] for s in b["verdict"]},
                         "stance_accuracy_lenient": {s: b["stance"][s]["accuracy_lenient"] for s in b["stance"]},
                         "evidence_recall": {s: b["stance"][s]["evidence_recall"] for s in b["stance"]},
                         "brier": {s: b["calibration"][s].get("brier") for s in b["calibration"]},
                         "seconds": b["seconds_total"]}
    return summary


def run_evaluation(split: str = "all", classifiers: list[str] | None = None, save: bool = True,
                   retrieval_modes: list[str] | None = None) -> dict[str, Any]:
    settings = get_settings()
    claims = ms.load_claims("all")
    classifiers = classifiers or [load_config("reasoning").get("classifier", {}).get("mode", "rules")]
    search = get_search_service()
    selected = [c for c in claims if split == "all" or c["split"] == split]
    report: dict[str, Any] = {
        "id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {"path": "evaluation/datasets/claims.csv", "n_claims": len(claims),
                    "by_split": M.label_counts([c["split"] for c in claims]),
                    "label_sources": M.label_counts([c["label_source"] for c in claims]),
                    "verdict_distribution": M.label_counts([c["expected_verdict"] for c in claims])},
        "settings": {"search_backend": search.backend.name, "embeddings": search.semantic_available(),
                     "nli_model": settings.nli_model if settings.nli_enabled else None,
                     "llm_model": settings.llm_model if settings.llm_provider != "none" else None},
        "split_evaluated": split, "classifiers": {},
        "retrieval": ms.retrieval_eval(selected, search, retrieval_modes or ["bm25", "semantic", "hybrid"]),
    }
    with get_sessionmaker()() as db:
        for mode in classifiers:
            started = time.perf_counter()
            report["classifiers"][mode] = _classifier_report(mode, _run_claims(db, selected, mode, search), started)
    report["summary"] = _summary(report)
    if save:
        save_report(report)
    return report


def save_report(report: dict[str, Any]) -> Path:
    out_dir = get_settings().evaluation_dir / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"eval_{stamp}.json"
    text = json.dumps(report, indent=2, default=str)
    path.write_text(text, encoding="utf-8")
    (out_dir / "latest.json").write_text(text, encoding="utf-8")
    try:
        with get_sessionmaker()() as db:
            db.add(EvaluationRun(id=report["id"], dataset=report["dataset"],
                                 config={"split": report["split_evaluated"], "classifiers": list(report["classifiers"]),
                                         "settings": report["settings"]},
                                 metrics=json.loads(json.dumps(report["summary"], default=str))))
            db.commit()
    except Exception as exc:  # noqa: BLE001 - the JSON file is the primary artefact
        log.warning("Could not record evaluation run in the database: %s", exc)
    return path
