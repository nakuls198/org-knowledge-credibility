"""Claims, assessments, evidence and the evidence graph."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import assessment_summary, claim_out, doc_summary, get_claim_or_404, section_counts
from app.api.schemas import (
    AssessmentCreate,
    AssessmentDetail,
    ClaimCreate,
    ClaimDetail,
    ClaimOut,
    EvidenceOut,
    GraphEdge,
    GraphNode,
    GraphOut,
    Page,
)
from app.claims.analysis import ClaimValidationError, analyze
from app.core.errors import AppError, NotFoundError
from app.core.text import strip_markdown
from app.database.models import Assessment, Claim, Document, Relationship, Section, SourceFile
from app.database.session import get_db, get_sessionmaker

router = APIRouter()


def _run_assessment_task(assessment_id: str, options: dict) -> None:
    from app.assessments.service import run_assessment

    with get_sessionmaker()() as db:
        run_assessment(db, assessment_id, **options)


def _queue(db: Session, claim: Claim, body: AssessmentCreate, background: BackgroundTasks) -> Assessment:
    from app.assessments.service import create_assessment

    a = create_assessment(db, claim, body.as_of)
    db.commit()
    background.add_task(_run_assessment_task, a.id, {"classifier_mode": body.classifier_mode,
                                                     "max_evidence": body.max_evidence, "retrieval_mode": body.retrieval_mode})
    return a


@router.post("/claims", status_code=status.HTTP_201_CREATED, response_model=ClaimDetail, tags=["claims"],
             summary="Create a claim (and queue its first assessment unless assess=false)")
def create_claim(body: ClaimCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    from app.assessments.service import get_gazetteer

    try:
        analysis = analyze(body.text, get_gazetteer(db))
    except ClaimValidationError as exc:
        raise AppError(str(exc)) from exc
    existing = db.scalars(select(Claim).where(Claim.normalized_text == analysis.normalized)).first()
    if existing:
        claim = existing
    else:
        claim = Claim(id=str(uuid.uuid4()), text=body.text, normalized_text=analysis.normalized,
                      analysis=analysis.model_dump(mode="json"), origin="user", tags=body.tags)
        db.add(claim)
        db.flush()
    if body.assess:
        _queue(db, claim, AssessmentCreate(classifier_mode=body.classifier_mode, as_of=body.as_of), background)
    db.commit()
    db.refresh(claim)
    return _claim_detail(claim)


def _claim_detail(c: Claim) -> ClaimDetail:
    summaries = [assessment_summary(a) for a in c.assessments]
    latest = next((s for s in summaries if s.id == c.latest_assessment_id), summaries[0] if summaries else None)
    return ClaimDetail(**claim_out(c).model_dump(), analysis=c.analysis or {}, assessments=summaries, latest_assessment=latest)


@router.get("/claims", response_model=Page[ClaimOut], tags=["claims"])
def list_claims(q: str | None = Query(None, max_length=200), verdict: list[str] = Query(default=[]),
                needs_review: bool | None = None, origin: str | None = None,
                limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    stmt = select(Claim)
    if q:
        stmt = stmt.where(func.lower(Claim.text).like(f"%{q.lower()}%"))
    if verdict:
        conds = [Claim.latest_verdict.in_([v for v in verdict if v != "UNASSESSED"])]
        if "UNASSESSED" in verdict:
            conds.append(Claim.latest_verdict.is_(None))
        stmt = stmt.where(or_(*conds))
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
    from sqlalchemy.orm import selectinload

    from app.claims.extraction import extract_candidates

    stmt = select(Document).where(Document.is_deleted.is_(False)).options(selectinload(Document.sections))
    if document_id:
        stmt = stmt.where(Document.id == document_id)
    cands = extract_candidates(db.scalars(stmt).all(), limit=500)
    if kind:
        cands = [c for c in cands if c.kind == kind]
    if q:
        cands = [c for c in cands if q.lower() in c.text.lower()]
    return {"items": [c.model_dump() for c in cands[:limit]], "total": len(cands),
            "note": "Candidates are proposals extracted by rules; they are not established facts until assessed."}


@router.get("/claims/{claim_id}", response_model=ClaimDetail, tags=["claims"])
def get_claim(claim_id: str, db: Session = Depends(get_db)):
    return _claim_detail(get_claim_or_404(db, claim_id))


@router.delete("/claims/{claim_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["claims"])
def delete_claim(claim_id: str, db: Session = Depends(get_db)):
    db.delete(get_claim_or_404(db, claim_id))
    db.commit()


@router.post("/claims/{claim_id}/assessments", status_code=status.HTTP_202_ACCEPTED, tags=["claims"],
             summary="(Re-)run an assessment; poll GET /assessments/{id} until status is completed")
def create_assessment(claim_id: str, body: AssessmentCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    claim = get_claim_or_404(db, claim_id)
    running = db.scalars(select(Assessment).where(Assessment.claim_id == claim.id,
                                                  Assessment.status.in_(["queued", "running"]))).first()
    if running:
        from datetime import datetime, timedelta, timezone

        started = running.created_at if running.created_at.tzinfo else running.created_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - started < timedelta(minutes=30):
            return assessment_summary(running)
        running.status, running.error = "failed", "Timed out: no result after 30 minutes."
        running.completed_at = datetime.now(timezone.utc)
        db.commit()
    return assessment_summary(_queue(db, claim, body, background))


def _span_line(sf: SourceFile | None, sec: Section | None, span: str) -> int | None:
    """Best-effort line number of the evidence span inside the original file."""
    if sf is None or sec is None or not span.strip():
        return None
    needle = re.sub(r"\s+", " ", span.strip())[:50].lower()
    lines = sf.raw_content.split("\n")
    for i in range(max(0, sec.start_line - 1), min(len(lines), sec.end_line)):
        if needle[:30] and needle[:30] in re.sub(r"\s+", " ", strip_markdown(lines[i])).lower():
            return i + 1
    return sec.start_line


def _assessment_detail(db: Session, a: Assessment) -> AssessmentDetail:
    claim = db.get(Claim, a.claim_id)
    ev = list(a.evidence)
    doc_ids = list({e.document_id for e in ev})
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.id.in_(doc_ids)))}
    secs = {s.id: s for s in db.scalars(select(Section).where(Section.id.in_([e.section_id for e in ev])))}
    sfs = {sf.id: sf for sf in db.scalars(select(SourceFile).where(SourceFile.id.in_({d.source_file_id for d in docs.values()})))}
    counts = section_counts(db, doc_ids)
    out = []
    for e in ev:
        d, s = docs.get(e.document_id), secs.get(e.section_id)
        cx = (e.signals or {}).get("_context", {})
        out.append(EvidenceOut(
            id=e.id, rank=e.rank, evidence_ref=f"E{e.rank}", section_id=e.section_id, document_id=e.document_id,
            document=doc_summary(d, counts.get(d.id, 0)) if d else None,
            heading=s.heading if s else cx.get("heading", ""), section_role=s.section_role if s else cx.get("section_role", ""),
            source_path=d.rel_path if d else cx.get("source_path", ""), start_line=s.start_line if s else cx.get("start_line", 0),
            end_line=s.end_line if s else cx.get("end_line", 0),
            span_line=_span_line(sfs.get(d.source_file_id) if d else None, s, e.span_text),
            span_start=e.span_start, span_end=e.span_end, span_text=e.span_text, section_text=e.section_text,
            section_changed_since=bool(s and s.content_hash != e.section_content_hash) or s is None,
            label=e.label, scope=e.scope, strength=e.strength, rationale=e.rationale,
            signals={k: v for k, v in (e.signals or {}).items() if k != "_context"}, features=e.features or {},
            contribution=e.contribution, cluster_id=e.cluster_id, retrieval=e.retrieval or {}, duplicates=e.duplicates or []))
    base = assessment_summary(a).model_dump()
    return AssessmentDetail(**base, claim_text=claim.text if claim else "", result=a.result or {},
                            explanation=a.explanation or {},
                            retrieval={k: v for k, v in (a.retrieval or {}).items() if k not in ("documents_snapshot", "edges_snapshot")},
                            config_hash=a.config_hash, versions=a.versions or {}, evidence=out)


@router.get("/assessments/{assessment_id}", response_model=AssessmentDetail, tags=["assessments"])
def get_assessment(assessment_id: str, db: Session = Depends(get_db)):
    a = db.get(Assessment, assessment_id)
    if a is None:
        raise NotFoundError(f"Assessment '{assessment_id}' not found")
    return _assessment_detail(db, a)


@router.get("/assessments/{assessment_id}/reproduce", tags=["assessments"],
            summary="Re-score from the recorded evidence + configuration snapshot and compare")
def reproduce_assessment(assessment_id: str, db: Session = Depends(get_db)):
    from app.assessments.service import reproduce

    if db.get(Assessment, assessment_id) is None:
        raise NotFoundError(f"Assessment '{assessment_id}' not found")
    return reproduce(db, assessment_id)


@router.get("/assessments/{assessment_id}/config", tags=["assessments"], summary="Configuration snapshot used by an assessment")
def assessment_config(assessment_id: str, db: Session = Depends(get_db)):
    a = db.get(Assessment, assessment_id)
    if a is None:
        raise NotFoundError(f"Assessment '{assessment_id}' not found")
    return {"config_hash": a.config_hash, "versions": a.versions, "config": a.config_snapshot}


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
    claim = get_claim_or_404(db, claim_id)
    a = _latest_completed(db, claim, assessment_id)
    if a is None:
        return {"assessment_id": None, "items": []}
    detail = _assessment_detail(db, a)
    items = [e for e in detail.evidence if not label or e.label in label]
    return {"assessment_id": a.id, "items": items}


@router.get("/claims/{claim_id}/graph", response_model=GraphOut, tags=["claims"],
            summary="Evidence graph: claim -> evidence -> document -> related documents")
def claim_graph(claim_id: str, assessment_id: str | None = None, expand: list[str] = Query(default=[]),
                rel_type: list[str] = Query(default=[]), doc_type: list[str] = Query(default=[]),
                label: list[str] = Query(default=[]), include_neutral: bool = False, db: Session = Depends(get_db)):
    claim = get_claim_or_404(db, claim_id)
    a = _latest_completed(db, claim, assessment_id)
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}
    nodes["claim"] = GraphNode(id="claim", type="claim", label=claim.text[:120],
                               data={"claim_id": claim.id, "verdict": claim.latest_verdict, "score": claim.latest_score,
                                     "needs_review": claim.needs_review, "text": claim.text})
    if a is None:
        return GraphOut(claim_id=claim.id, assessment_id=None, nodes=list(nodes.values()), edges=[], filters={})
    ev = [e for e in a.evidence if (include_neutral or e.label != "NEUTRAL") and (not label or e.label in label)]
    doc_ids = {e.document_id for e in ev}
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.id.in_(doc_ids)))}
    if doc_type:
        ev = [e for e in ev if docs.get(e.document_id) and docs[e.document_id].doc_type in doc_type]
        doc_ids = {e.document_id for e in ev}
    for e in ev:
        nid = f"ev:{e.rank}"
        nodes[nid] = GraphNode(id=nid, type="evidence", label=f"E{e.rank}: {e.span_text[:80]}",
                               data={"rank": e.rank, "label": e.label, "scope": e.scope, "strength": e.strength,
                                     "weight": (e.features or {}).get("weight"), "contribution": e.contribution,
                                     "cluster_id": e.cluster_id, "section_id": e.section_id, "document_id": e.document_id,
                                     "span": e.span_text[:400], "rationale": e.rationale})
        edges[f"claim->{nid}"] = GraphEdge(id=f"claim->{nid}", source=nid, target="claim", type=e.label.lower(),
                                           label=e.label.lower(), data={"weight": (e.features or {}).get("weight"),
                                                                        "contribution": e.contribution})
        did = f"doc:{e.document_id}"
        edges[f"{nid}->{did}"] = GraphEdge(id=f"{nid}->{did}", source=nid, target=did, type="part_of", label="in",
                                           data={"section_id": e.section_id})
    counts = section_counts(db, list(docs))
    for d_id in doc_ids:
        d = docs.get(d_id)
        if d:
            nodes[f"doc:{d_id}"] = GraphNode(id=f"doc:{d_id}", type="document", label=f"{d.id}", data={**doc_summary(d, counts.get(d_id, 0)).model_dump(mode="json")})
    # relationships of evidence documents (+ expanded documents)
    frontier = set(doc_ids) | set(expand)
    stmt = select(Relationship).where(Relationship.target_document_id.is_not(None),
                                      or_(Relationship.source_document_id.in_(frontier), Relationship.target_document_id.in_(frontier)),
                                      Relationship.rel_type != "external_reference")
    if rel_type:
        stmt = stmt.where(Relationship.rel_type.in_(rel_type))
    rels = db.scalars(stmt).all()
    other = {r.source_document_id for r in rels} | {r.target_document_id for r in rels}
    odocs = {d.id: d for d in db.scalars(select(Document).where(Document.id.in_(other - set(docs))))}
    ocounts = section_counts(db, list(odocs))
    for r in rels:
        for end in (r.source_document_id, r.target_document_id):
            if f"doc:{end}" not in nodes:
                d = odocs.get(end) or docs.get(end)
                if d is None or (doc_type and d.doc_type not in doc_type):
                    continue
                nodes[f"doc:{end}"] = GraphNode(id=f"doc:{end}", type="related_document", label=end,
                                                data={**doc_summary(d, ocounts.get(end, counts.get(end, 0))).model_dump(mode="json"),
                                                      "expanded": end in expand})
        s, t = f"doc:{r.source_document_id}", f"doc:{r.target_document_id}"
        if s in nodes and t in nodes:
            edges[r.id] = GraphEdge(id=r.id, source=s, target=t, type=r.rel_type, label=r.rel_type.replace("_", " "),
                                    data={"origin": r.origin, "inferred": r.is_inferred, "confidence": r.confidence,
                                          "evidence": r.evidence[:300], "attributes": r.attributes or {}})
    return GraphOut(claim_id=claim.id, assessment_id=a.id, nodes=list(nodes.values()), edges=list(edges.values()),
                    filters={"rel_types": sorted({e.type for e in edges.values()}),
                             "doc_types": sorted({n.data.get("doc_type") for n in nodes.values() if n.data.get("doc_type")})})


@router.post("/claims/analyze", tags=["claims"], summary="Validate and analyse a claim without storing it")
def analyze_claim(body: ClaimCreate, db: Session = Depends(get_db)):
    from app.assessments.service import get_gazetteer

    try:
        return analyze(body.text, get_gazetteer(db)).model_dump()
    except ClaimValidationError as exc:
        raise AppError(str(exc)) from exc

