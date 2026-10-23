"""Health, overview dashboard, ingestion and evaluation endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from okc.api.schemas import IngestionJobCreate, IngestionJobOut, IssueOut, Page
from okc.common.errors import ConflictError, NotFoundError
from okc.corpus.ingest import new_job, run_job
from okc.db.models import (
    AssessmentEvidence,
    Claim,
    Document,
    EvaluationRun,
    IngestionIssue,
    IngestionJob,
    Relationship,
    Section,
    SourceFile,
)
from okc.db.session import get_db, get_sessionmaker
from okc.evaluation.measures import load_claims
from okc.evidence.llm import get_llm_classifier
from okc.evidence.nli import get_nli
from okc.search.embeddings import get_embedder
from okc.search.service import get_backend
from okc.settings import get_settings, load_config

router = APIRouter()


# ---------------------------------------------------------------- health

@router.get("/health", tags=["system"], summary="Service health (database, search index, optional models)")
def health(db: Session = Depends(get_db)):
    s = get_settings()
    try:
        db.execute(text("SELECT 1"))
        database = {"ok": True}
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        database = {"ok": False, "error": type(exc).__name__}
    search = get_backend().health()
    nli, llm = get_nli(), get_llm_classifier()
    components = {
        "database": database,
        "search": search,
        "embeddings": {"enabled": s.embeddings_enabled, "model": s.embedding_model if s.embeddings_enabled else None},
        "nli": {"enabled": s.nli_enabled, "model": s.nli_model if s.nli_enabled else None,
                "loaded": bool(nli and nli.loaded), "error": nli.error if nli else None},
        "llm": {"provider": s.llm_provider, "model": s.llm_model if s.llm_provider != "none" else None,
                "available": bool(llm and llm.available), "error": llm.error if llm else None},
    }
    return {"status": "ok" if database["ok"] and search.get("ok") else "degraded", "components": components}


# ---------------------------------------------------------------- configuration

@router.get("/config/credibility", tags=["system"],
            summary="The credibility assumptions in force (authority by scope, temporal factors, thresholds)")
def credibility_config():
    """Public tunables only (config/credibility.yaml); the UI explains how sources are weighed from this."""
    return load_config("credibility")


# ---------------------------------------------------------------- overview

def _corpus_stats(db: Session) -> dict[str, Any]:
    s = get_settings()
    active = Document.is_deleted.is_(False)
    by_type = db.execute(select(Document.doc_type, Document.kind, func.count()).where(active)
                         .group_by(Document.doc_type, Document.kind)).all()
    statuses = db.execute(select(Document.status, func.count()).where(active).group_by(Document.status)).all()
    n_sections = db.scalar(select(func.count()).select_from(Section).where(Section.is_deleted.is_(False))) or 0
    source_files = db.scalar(select(func.count()).select_from(SourceFile).where(SourceFile.is_deleted.is_(False))) or 0
    return {"data_dir_name": Path(s.data_dir).name, "source_files": source_files,
            "documents_by_type": [{"doc_type": t, "kind": k, "count": c} for t, k, c in by_type],
            "documents_total": sum(c for *_, c in by_type), "sections_total": n_sections, "status_counts": dict(statuses)}


def _claim_stats(db: Session) -> dict[str, Any]:
    verdicts = db.execute(select(Claim.latest_verdict, func.count()).group_by(Claim.latest_verdict)).all()
    needs_review = db.scalar(select(func.count()).select_from(Claim).where(Claim.needs_review.is_(True))) or 0
    latest_ids = [r[0] for r in db.execute(select(Claim.latest_assessment_id)
                                           .where(Claim.latest_assessment_id.is_not(None))).all()]
    contradictions = 0
    if latest_ids:
        contradictions = db.scalar(select(func.count()).select_from(AssessmentEvidence).where(
            AssessmentEvidence.assessment_id.in_(latest_ids), AssessmentEvidence.label == "CONTRADICTS")) or 0
    return {"total": db.scalar(select(func.count()).select_from(Claim)) or 0,
            "by_verdict": {(v or "UNASSESSED"): c for v, c in verdicts}, "needs_review": needs_review,
            "contradicting_evidence_items": contradictions}


def _ingestion_stats(db: Session) -> dict[str, Any]:
    job = db.scalars(select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(1)).first()
    issues = []
    if job:
        issues = db.execute(select(IngestionIssue.severity, IngestionIssue.issue_type, func.count())
                            .where(IngestionIssue.job_id == job.id)
                            .group_by(IngestionIssue.severity, IngestionIssue.issue_type)).all()
    return {"latest_job": _job_out(job, db).model_dump() if job else None,
            "issues": [{"severity": sv, "issue_type": it, "count": c} for sv, it, c in issues]}


def _latest_evaluation() -> dict[str, Any] | None:
    path = get_settings().evaluation_dir / "results" / "latest.json"
    if not path.exists():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    return {"id": report.get("id"), "created_at": report.get("created_at"), "summary": report.get("summary"),
            "dataset": report.get("dataset"), "source": "computed by the evaluation pipeline (evaluation/results/latest.json)"}


@router.get("/overview", tags=["system"], summary="Dashboard statistics computed from the live store")
def overview(db: Session = Depends(get_db)):
    relationship_types = db.execute(select(Relationship.rel_type, Relationship.is_inferred, func.count())
                                    .group_by(Relationship.rel_type, Relationship.is_inferred)).all()
    return {"corpus": _corpus_stats(db), "claims": _claim_stats(db),
            "relationships": [{"rel_type": t, "inferred": i, "count": c} for t, i, c in relationship_types],
            "ingestion": _ingestion_stats(db), "evaluation": _latest_evaluation()}


# ---------------------------------------------------------------- ingestion

def _job_out(job: IngestionJob, db: Session) -> IngestionJobOut:
    counts = dict(db.execute(select(IngestionIssue.severity, func.count()).where(IngestionIssue.job_id == job.id)
                             .group_by(IngestionIssue.severity)).all())
    return IngestionJobOut(id=job.id, status=job.status, mode=job.mode, data_dir=Path(job.data_dir).name, stats=job.stats or {},
                           error=job.error, created_at=job.created_at, started_at=job.started_at,
                           finished_at=job.finished_at, issue_counts=counts)


def _run_ingestion(job_id: str) -> None:
    with get_sessionmaker()() as db:
        run_job(db, db.get(IngestionJob, job_id), load_config("ingestion"), get_backend(), get_embedder())


@router.post("/ingestion/jobs", status_code=status.HTTP_202_ACCEPTED, response_model=IngestionJobOut, tags=["ingestion"],
             summary="Start an ingestion job (incremental by default; `full` re-parses and rebuilds the index)")
def create_ingestion_job(body: IngestionJobCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    running = db.scalars(select(IngestionJob).where(IngestionJob.status.in_(["queued", "running"]))).first()
    if running:
        raise ConflictError("An ingestion job is already running", {"job_id": running.id})
    job = new_job(db, get_settings().data_dir, body.mode)
    db.commit()
    background.add_task(_run_ingestion, job.id)
    return _job_out(job, db)


@router.get("/ingestion/jobs", response_model=Page[IngestionJobOut], tags=["ingestion"])
def list_ingestion_jobs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    total = db.scalar(select(func.count()).select_from(IngestionJob)) or 0
    jobs = db.scalars(select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(limit).offset(offset)).all()
    return Page(items=[_job_out(j, db) for j in jobs], total=total, limit=limit, offset=offset)


@router.get("/ingestion/jobs/{job_id}", response_model=IngestionJobOut, tags=["ingestion"])
def get_ingestion_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise NotFoundError(f"Ingestion job '{job_id}' not found")
    return _job_out(job, db)


@router.get("/ingestion/issues", response_model=Page[IssueOut], tags=["ingestion"],
            summary="Ingestion issue report (latest job unless job_id is given)")
def list_issues(job_id: str | None = None, issue_type: str | None = None, severity: str | None = None,
                limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    if job_id is None:
        job = db.scalars(select(IngestionJob).where(IngestionJob.status == "succeeded")
                         .order_by(IngestionJob.created_at.desc()).limit(1)).first()
        if job is None:
            return Page(items=[], total=0, limit=limit, offset=offset)
        job_id = job.id
    query = select(IngestionIssue).where(IngestionIssue.job_id == job_id)
    if issue_type:
        query = query.where(IngestionIssue.issue_type == issue_type)
    if severity:
        query = query.where(IngestionIssue.severity == severity)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(IngestionIssue.severity.desc(), IngestionIssue.issue_type, IngestionIssue.id)
                      .limit(limit).offset(offset)).all()
    return Page(items=[IssueOut(id=r.id, severity=r.severity, issue_type=r.issue_type, document_id=r.document_id,
                                section_id=r.section_id, rel_path=r.rel_path, message=r.message, details=r.details or {})
                       for r in rows], total=total, limit=limit, offset=offset)


# ---------------------------------------------------------------- evaluation

@router.get("/evaluation", tags=["evaluation"], summary="Latest evaluation report and run history")
def evaluation(db: Session = Depends(get_db)):
    path = get_settings().evaluation_dir / "results" / "latest.json"
    runs = db.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(20)).all()
    report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if report:
        for block in report.get("classifiers", {}).values():
            for split in block.get("stance", {}).values():
                split.pop("pairs", None)  # keep the payload small; full pairs stay in the JSON file
    return {"available": report is not None,
            "note": None if report else "No evaluation has been run yet. Run `python -m okc.cli evaluate`.",
            "report": report,
            "runs": [{"id": r.id, "created_at": r.created_at, "config": r.config, "metrics": r.metrics} for r in runs]}


@router.get("/evaluation/claims", tags=["evaluation"], summary="The labelled evaluation claim set")
def evaluation_claims():
    return {"items": load_claims("all")}
