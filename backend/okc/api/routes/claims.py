"""Claims, assessments, evidence and the evidence graph."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from okc.api.converters import (
    assessment_summary,
    claim_out,
    doc_summary,
    get_assessment_or_404,
    get_claim_or_404,
    section_counts,
)
from okc.api.graph import build_graph
from okc.api.schemas import (
    AssessmentCreate,
    AssessmentDetail,
    ClaimCreate,
    ClaimDetail,
    ClaimOut,
    EvidenceOut,
    GraphOut,
    Page,
)
from okc.assessment.investigate import get_gazetteer
from okc.assessment.store import create_assessment, reproduce, run_assessment
from okc.claims.analysis import ClaimValidationError, analyze
from okc.claims.candidates import extract_candidates
from okc.common.errors import AppError, NotFoundError
from okc.common.text import strip_markdown
from okc.db.models import Assessment, Claim, Document, Section, SourceFile
from okc.db.session import get_db, get_sessionmaker

router = APIRouter()
_STUCK_AFTER = timedelta(minutes=30)
_SPAN_SEARCH_CHARS = 30


def _run_assessment_task(assessment_id: str, options: dict) -> None:
    with get_sessionmaker()() as db:
        run_assessment(db, assessment_id, **options)


def _queue(db: Session, claim: Claim, body: AssessmentCreate, background: BackgroundTasks) -> Assessment:
    a = create_assessment(db, claim, body.as_of)
    db.commit()
    background.add_task(_run_assessment_task, a.id, {"classifier_mode": body.classifier_mode,
                                                     "max_evidence": body.max_evidence, "retrieval_mode": body.retrieval_mode})
    return a


def _claim_detail(c: Claim) -> ClaimDetail:
    summaries = [assessment_summary(a) for a in c.assessments]
    latest = next((s for s in summaries if s.id == c.latest_assessment_id), summaries[0] if summaries else None)
    return ClaimDetail(**claim_out(c).model_dump(), analysis=c.analysis or {}, assessments=summaries, latest_assessment=latest)


# ---------------------------------------------------------------- claims

@router.post("/claims", status_code=status.HTTP_201_CREATED, response_model=ClaimDetail, tags=["claims"],
             summary="Create a claim (and queue its first assessment unless assess=false)")
def create_claim(body: ClaimCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        analysis = analyze(body.text, get_gazetteer(db))
    except ClaimValidationError as exc:
        raise AppError(str(exc)) from exc
    claim = db.scalars(select(Claim).where(Claim.normalized_text == analysis.normalized)).first()
    if claim is None:
        claim = Claim(id=str(uuid.uuid4()), text=body.text, normalized_text=analysis.normalized,
                      analysis=analysis.model_dump(mode="json"), origin="user", tags=body.tags)
        db.add(claim)
        db.flush()
    if body.assess:
        _queue(db, claim, AssessmentCreate(classifier_mode=body.classifier_mode, as_of=body.as_of), background)
    db.commit()
    db.refresh(claim)
    return _claim_detail(claim)


@router.get("/claims", response_model=Page[ClaimOut], tags=["claims"])
def list_claims(q: str | None = Query(None, max_length=200), verdict: list[str] = Query(default=[]),
                needs_review: bool | None = None, origin: str | None = None,
                limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    stmt = select(Claim)
    if q:
        stmt = stmt.where(func.lower(Claim.text).like(f"%{q.lower()}%"))
    if verdict:
        conditions = [Claim.latest_verdict.in_([v for v in verdict if v != "UNASSESSED"])]
        if "UNASSESSED" in verdict:
            conditions.append(Claim.latest_verdict.is_(None))
        stmt = stmt.where(or_(*conditions))
    if needs_review is not None:
        stmt = stmt.where(Claim.needs_review.is_(needs_review))
    if origin:
        stmt = stmt.where(Claim.origin == origin)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Claim.updated_at.desc()).limit(limit).offset(offset)).all()
    return Page(items=[claim_out(c) for c in rows], total=total, limit=limit, offset=offset)


@router.get("/claims/candidates", tags=["claims"],
            summary="Candidate claims proposed from policy requirements and meeting decisions (not facts)")
def claim_candidates(document_id: str | None = None, kind: str | None = None, q: str | None = None,
                     limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    stmt = select(Document).where(Document.is_deleted.is_(False)).options(selectinload(Document.sections))
    if document_id:
        stmt = stmt.where(Document.id == document_id)
    candidates = extract_candidates(db.scalars(stmt).all(), limit=500)
    if kind:
        candidates = [c for c in candidates if c.kind == kind]
    if q:
        candidates = [c for c in candidates if q.lower() in c.text.lower()]
    return {"items": [c.model_dump() for c in candidates[:limit]], "total": len(candidates),
            "note": "Candidates are proposals extracted by rules; they are not established facts until assessed."}


@router.post("/claims/analyze", tags=["claims"], summary="Validate and analyse a claim without storing it")
def analyze_claim(body: ClaimCreate, db: Session = Depends(get_db)):
    try:
        return analyze(body.text, get_gazetteer(db)).model_dump()
    except ClaimValidationError as exc:
        raise AppError(str(exc)) from exc


@router.get("/claims/{claim_id}", response_model=ClaimDetail, tags=["claims"])
def get_claim(claim_id: str, db: Session = Depends(get_db)):
    return _claim_detail(get_claim_or_404(db, claim_id))


@router.delete("/claims/{claim_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["claims"])
def delete_claim(claim_id: str, db: Session = Depends(get_db)):
    db.delete(get_claim_or_404(db, claim_id))
    db.commit()


@router.post("/claims/{claim_id}/assessments", status_code=status.HTTP_202_ACCEPTED, tags=["claims"],
             summary="(Re-)run an assessment; poll GET /assessments/{id} until status is completed")
def rerun_assessment(claim_id: str, body: AssessmentCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    claim = get_claim_or_404(db, claim_id)
    running = db.scalars(select(Assessment).where(Assessment.claim_id == claim.id,
                                                  Assessment.status.in_(["queued", "running"]))).first()
    if running:
        started = running.created_at if running.created_at.tzinfo else running.created_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - started < _STUCK_AFTER:
            return assessment_summary(running)
        running.status, running.error = "failed", "Timed out: no result after 30 minutes."
        running.completed_at = datetime.now(timezone.utc)
        db.commit()
    return assessment_summary(_queue(db, claim, body, background))


# ---------------------------------------------------------------- assessments

def _span_line(sf: SourceFile | None, sec: Section | None, span: str) -> int | None:
    """Best-effort line number of the evidence span inside the original file."""
    if sf is None or sec is None or not span.strip():
        return None
    needle = re.sub(r"\s+", " ", span.strip())[:_SPAN_SEARCH_CHARS].lower()
    lines = sf.raw_content.split("\n")
    for i in range(max(0, sec.start_line - 1), min(len(lines), sec.end_line)):
        if needle and needle in re.sub(r"\s+", " ", strip_markdown(lines[i])).lower():
            return i + 1
    return sec.start_line


def _assessment_detail(db: Session, a: Assessment) -> AssessmentDetail:
    claim = db.get(Claim, a.claim_id)
    evidence = list(a.evidence)
    doc_ids = list({e.document_id for e in evidence})
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.id.in_(doc_ids)))}
    sections = {s.id: s for s in db.scalars(select(Section).where(Section.id.in_([e.section_id for e in evidence])))}
    files = {sf.id: sf for sf in db.scalars(select(SourceFile).where(SourceFile.id.in_({d.source_file_id for d in docs.values()})))}
    counts = section_counts(db, doc_ids)
    rows = []
    for e in evidence:
        d, s = docs.get(e.document_id), sections.get(e.section_id)
        cx = (e.signals or {}).get("_context", {})
        rows.append(EvidenceOut(
            id=e.id, rank=e.rank, evidence_ref=f"E{e.rank}", section_id=e.section_id, document_id=e.document_id,
            document=doc_summary(d, counts.get(d.id, 0)) if d else None,
            heading=s.heading if s else cx.get("heading", ""), section_role=s.section_role if s else cx.get("section_role", ""),
            source_path=d.rel_path if d else cx.get("source_path", ""), start_line=s.start_line if s else cx.get("start_line", 0),
            end_line=s.end_line if s else cx.get("end_line", 0),
            span_line=_span_line(files.get(d.source_file_id) if d else None, s, e.span_text),
            span_start=e.span_start, span_end=e.span_end, span_text=e.span_text, section_text=e.section_text,
            section_changed_since=bool(s and s.content_hash != e.section_content_hash) or s is None,
            label=e.label, scope=e.scope, strength=e.strength, rationale=e.rationale,
            signals={k: v for k, v in (e.signals or {}).items() if k != "_context"}, features=e.features or {},
            contribution=e.contribution, cluster_id=e.cluster_id, retrieval=e.retrieval or {}, duplicates=e.duplicates or []))
    return AssessmentDetail(**assessment_summary(a).model_dump(), claim_text=claim.text if claim else "", result=a.result or {},
                            explanation=a.explanation or {},
                            retrieval={k: v for k, v in (a.retrieval or {}).items() if k not in ("documents_snapshot", "edges_snapshot")},
                            config_hash=a.config_hash, versions=a.versions or {}, evidence=rows)


@router.get("/assessments/{assessment_id}", response_model=AssessmentDetail, tags=["assessments"])
def get_assessment(assessment_id: str, db: Session = Depends(get_db)):
    return _assessment_detail(db, get_assessment_or_404(db, assessment_id))


@router.get("/assessments/{assessment_id}/reproduce", tags=["assessments"],
            summary="Re-score from the recorded evidence + configuration and compare")
def reproduce_assessment(assessment_id: str, db: Session = Depends(get_db)):
    get_assessment_or_404(db, assessment_id)
    return reproduce(db, assessment_id)


@router.get("/assessments/{assessment_id}/config", tags=["assessments"], summary="Configuration snapshot used by an assessment")
def assessment_config(assessment_id: str, db: Session = Depends(get_db)):
    a = get_assessment_or_404(db, assessment_id)
    return {"config_hash": a.config_hash, "versions": a.versions, "config": a.config_snapshot}


# ---------------------------------------------------------------- evidence and graph

def _latest_completed(db: Session, claim: Claim, assessment_id: str | None) -> Assessment | None:
    if assessment_id:
        a = db.get(Assessment, assessment_id)
        if a is None or a.claim_id != claim.id:
            raise NotFoundError("Assessment not found for this claim")
        return a
    return db.scalars(select(Assessment).where(Assessment.claim_id == claim.id, Assessment.status == "completed")
                      .order_by(Assessment.created_at.desc())).first()


@router.get("/claims/{claim_id}/evidence", tags=["claims"], summary="Evidence of the latest (or given) assessment")
def claim_evidence(claim_id: str, assessment_id: str | None = None, label: list[str] = Query(default=[]),
                   db: Session = Depends(get_db)):
    a = _latest_completed(db, get_claim_or_404(db, claim_id), assessment_id)
    if a is None:
        return {"assessment_id": None, "items": []}
    items = [e for e in _assessment_detail(db, a).evidence if not label or e.label in label]
    return {"assessment_id": a.id, "items": items}


@router.get("/claims/{claim_id}/graph", response_model=GraphOut, tags=["claims"],
            summary="Evidence graph: claim -> evidence -> document -> related documents")
def claim_graph(claim_id: str, assessment_id: str | None = None, expand: list[str] = Query(default=[]),
                rel_type: list[str] = Query(default=[]), doc_type: list[str] = Query(default=[]),
                label: list[str] = Query(default=[]), include_neutral: bool = False, db: Session = Depends(get_db)):
    claim = get_claim_or_404(db, claim_id)
    return build_graph(db, claim, _latest_completed(db, claim, assessment_id), expand, rel_type, doc_type, label, include_neutral)
