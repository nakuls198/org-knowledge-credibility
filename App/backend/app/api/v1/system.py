"""Health, overview dashboard and ingestion endpoints."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.schemas import IngestionJobCreate, IngestionJobOut, IssueOut, Page
from app.core.config import REPO_ROOT, get_settings, load_config
from app.core.errors import ConflictError, NotFoundError
from app.database.models import (
    Assessment,
    AssessmentEvidence,
    Claim,
    Document,
    IngestionIssue,
    IngestionJob,
    Relationship,
    Section,
)
from app.database.session import get_db, get_sessionmaker

router = APIRouter()


@router.get("/health", tags=["system"], summary="Service health (database, search index, optional models)")
def health(db: Session = Depends(get_db)):
    from app.reasoning.llm import get_llm_classifier
    from app.reasoning.nli import get_nli
    from app.retrieval.service import get_backend

    s = get_settings()
    try:
        db.execute(text("SELECT 1"))
        db_ok = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        db_ok = {"ok": False, "error": type(exc).__name__}
    search = get_backend().health()
    nli, llm = get_nli(), get_llm_classifier()
    components = {
        "database": db_ok,
        "search": search,
        "embeddings": {"enabled": s.embeddings_enabled, "model": s.embedding_model if s.embeddings_enabled else None},
        "nli": {"enabled": s.nli_enabled, "model": s.nli_model if s.nli_enabled else None,
                "loaded": bool(nli and nli._model is not None), "error": nli.error if nli else None},
        "llm": {"provider": s.llm_provider, "model": s.llm_model if s.llm_provider != "none" else None,
                "available": bool(llm and llm.available) if llm else False, "error": llm.error if llm else None},
    }
    overall = "ok" if db_ok["ok"] and search.get("ok") else "degraded"
    return {"status": overall, "components": components}


@router.get("/overview", tags=["system"], summary="Dashboard statistics computed from the live store")
def overview(db: Session = Depends(get_db)):
    s = get_settings()
    docs = db.execute(select(Document.doc_type, Document.kind, func.count()).where(Document.is_deleted.is_(False))
                      .group_by(Document.doc_type, Document.kind)).all()
    statuses = db.execute(select(Document.status, func.count()).where(Document.is_deleted.is_(False))
                          .group_by(Document.status)).all()
    n_sections = db.scalar(select(func.count()).select_from(Section).where(Section.is_deleted.is_(False))) or 0
    claims_total = db.scalar(select(func.count()).select_from(Claim)) or 0
    verdicts = db.execute(select(Claim.latest_verdict, func.count()).group_by(Claim.latest_verdict)).all()
    review = db.scalar(select(func.count()).select_from(Claim).where(Claim.needs_review.is_(True))) or 0
    latest_ids = [r[0] for r in db.execute(select(Claim.latest_assessment_id).where(Claim.latest_assessment_id.is_not(None))).all()]
    contradictions = 0
    if latest_ids:
        contradictions = db.scalar(select(func.count()).select_from(AssessmentEvidence).where(
            AssessmentEvidence.assessment_id.in_(latest_ids), AssessmentEvidence.label == "CONTRADICTS")) or 0
    rel_types = db.execute(select(Relationship.rel_type, Relationship.is_inferred, func.count())
                           .group_by(Relationship.rel_type, Relationship.is_inferred)).all()
    job = db.scalars(select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(1)).first()
    issues = []
    if job:
        issues = db.execute(select(IngestionIssue.severity, IngestionIssue.issue_type, func.count())
                            .where(IngestionIssue.job_id == job.id)
                            .group_by(IngestionIssue.severity, IngestionIssue.issue_type)).all()
    eval_path = s.evaluation_dir / "results" / "latest.json"
    evaluation = None
    if eval_path.exists():
        rep = json.loads(eval_path.read_text(encoding="utf-8"))
        evaluation = {"id": rep.get("id"), "created_at": rep.get("created_at"), "summary": rep.get("summary"),
                      "dataset": rep.get("dataset"), "source": "computed by the evaluation pipeline (evaluation/results/latest.json)"}
    sample_dir = (REPO_ROOT / "data" / "sample").resolve()
    is_sample = Path(s.data_dir).resolve() == sample_dir
    return {
        "corpus": {"data_dir_name": Path(s.data_dir).name, "is_synthetic_sample": is_sample,
                   "documents_by_type": [{"doc_type": t, "kind": k, "count": c} for t, k, c in docs],
                   "documents_total": sum(c for *_x, c in docs), "sections_total": n_sections,
                   "status_counts": dict(statuses)},
        "claims": {"total": claims_total, "by_verdict": {(v or "UNASSESSED"): c for v, c in verdicts},
                   "needs_review": review, "contradicting_evidence_items": contradictions},
        "relationships": [{"rel_type": t, "inferred": i, "count": c} for t, i, c in rel_types],
        "ingestion": {"latest_job": _job_out(job, db).model_dump() if job else None,
                      "issues": [{"severity": sv, "issue_type": it, "count": c} for sv, it, c in issues]},
        "evaluation": evaluation,
    }


def _job_out(job: IngestionJob, db: Session) -> IngestionJobOut:
    counts = dict(db.execute(select(IngestionIssue.severity, func.count()).where(IngestionIssue.job_id == job.id)
                             .group_by(IngestionIssue.severity)).all())
    return IngestionJobOut(id=job.id, status=job.status, mode=job.mode, data_dir=Path(job.data_dir).name, stats=job.stats or {},
                           error=job.error, created_at=job.created_at, started_at=job.started_at,
                           finished_at=job.finished_at, issue_counts=counts)


def _run_ingestion(job_id: str) -> None:
    from app.ingestion.pipeline import run_job
    from app.retrieval.embeddings import get_embedder
    from app.retrieval.service import get_backend

    with get_sessionmaker()() as db:
        job = db.get(IngestionJob, job_id)
        run_job(db, job, load_config("ingestion"), get_backend(), get_embedder())


@router.post("/ingestion/jobs", status_code=status.HTTP_202_ACCEPTED, response_model=IngestionJobOut, tags=["ingestion"],
             summary="Start an ingestion job (incremental by default; `full` re-parses and rebuilds the index)")
def create_ingestion_job(body: IngestionJobCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    from app.ingestion.pipeline import new_job

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
    q = select(IngestionIssue).where(IngestionIssue.job_id == job_id)
    if issue_type:
        q = q.where(IngestionIssue.issue_type == issue_type)
    if severity:
        q = q.where(IngestionIssue.severity == severity)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(IngestionIssue.severity.desc(), IngestionIssue.issue_type, IngestionIssue.id)
                      .limit(limit).offset(offset)).all()
    return Page(items=[IssueOut(id=r.id, severity=r.severity, issue_type=r.issue_type, document_id=r.document_id,
                                section_id=r.section_id, rel_path=r.rel_path, message=r.message, details=r.details or {})
                       for r in rows], total=total, limit=limit, offset=offset)


@router.get("/evaluation", tags=["evaluation"], summary="Latest evaluation report and run history")
def evaluation(db: Session = Depends(get_db)):
    from app.database.models import EvaluationRun

    s = get_settings()
    path = s.evaluation_dir / "results" / "latest.json"
    runs = db.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(20)).all()
    report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if report:
        for block in report.get("classifiers", {}).values():
            for k in ("stance",):
                for split in block.get(k, {}).values():
                    split.pop("pairs", None)  # keep the payload small; full pairs stay in the JSON file
    return {"available": report is not None,
            "note": None if report else "No evaluation has been run yet. Run `python -m app.cli evaluate`.",
            "report": report,
            "runs": [{"id": r.id, "created_at": r.created_at, "config": r.config, "metrics": r.metrics} for r in runs]}


@router.get("/evaluation/claims", tags=["evaluation"], summary="The labelled evaluation claim set")
def evaluation_claims():
    from app.evaluation.runner import load_claims

    return {"items": load_claims("all")}

