"""Documents, sections, relationships, source files and search."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import doc_summary, get_document_or_404, section_counts
from app.api.schemas import DocumentDetail, DocumentSummary, Page, RelationshipOut, SectionOut, SourceOut
from app.core.errors import ServiceUnavailableError
from app.database.models import AssessmentEvidence, Claim, Document, Relationship, Section, SourceFile
from app.database.session import get_db
from app.retrieval.schemas import SearchFilters, SearchRequest

router = APIRouter()


def _section_out(s: Section) -> SectionOut:
    return SectionOut(id=s.id, document_id=s.document_id, parent_section_id=s.parent_section_id, ordinal=s.ordinal,
                      heading=s.heading, heading_path=list(s.heading_path or []), level=s.level, section_role=s.section_role,
                      text=s.text, plain_text=s.plain_text, start_line=s.start_line, end_line=s.end_line,
                      content_hash=s.content_hash, identifiers=list(s.identifiers or []))


@router.get("/documents", response_model=Page[DocumentSummary], tags=["documents"])
def list_documents(
    doc_type: list[str] = Query(default=[]), status: list[str] = Query(default=[]),
    kind: list[str] = Query(default=[], description="file | thread | record | container"),
    q: str | None = Query(None, max_length=200, description="filter on id / title / path"),
    include_records: bool = Query(False, description="CSV records are excluded unless requested"),
    include_deleted: bool = False,
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db),
):
    stmt = select(Document)
    if not include_deleted:
        stmt = stmt.where(Document.is_deleted.is_(False))
    if doc_type:
        stmt = stmt.where(Document.doc_type.in_(doc_type))
    if status:
        stmt = stmt.where(Document.status.in_(status))
    if kind:
        stmt = stmt.where(Document.kind.in_(kind))
    elif not include_records:
        stmt = stmt.where(Document.kind != "record")
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Document.id).like(like), func.lower(Document.title).like(like),
                              func.lower(Document.rel_path).like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    docs = db.scalars(stmt.order_by(Document.doc_type, Document.id).limit(limit).offset(offset)).all()
    counts = section_counts(db, [d.id for d in docs])
    return Page(items=[doc_summary(d, counts.get(d.id, 0)) for d in docs], total=total, limit=limit, offset=offset)


@router.get("/documents/{document_id}", response_model=DocumentDetail, tags=["documents"])
def get_document(document_id: str, db: Session = Depends(get_db)):
    d = get_document_or_404(db, document_id)
    secs = [s for s in d.sections if not s.is_deleted]
    rc = dict(db.execute(select(Relationship.rel_type, func.count()).where(
        or_(Relationship.source_document_id == d.id, Relationship.target_document_id == d.id))
        .group_by(Relationship.rel_type)).all())
    sf = db.get(SourceFile, d.source_file_id)
    base = doc_summary(d, len(secs)).model_dump()
    return DocumentDetail(**base, doc_type_source=d.doc_type_source, status_source=d.status_source,
                          version_label=d.version_label, date_source=d.date_source, effective_date=d.effective_date,
                          review_date=d.review_date, superseded_date=d.superseded_date,
                          metadata={k: v for k, v in (d.meta or {}).items() if not k.startswith("_")},
                          start_line=d.start_line, end_line=d.end_line, content_hash=d.content_hash, is_deleted=d.is_deleted,
                          source_file_id=d.source_file_id, source_version=sf.version if sf else 0,
                          sections=[_section_out(s) for s in secs], relationship_counts=rc)


@router.get("/documents/{document_id}/sections", response_model=list[SectionOut], tags=["documents"])
def get_sections(document_id: str, db: Session = Depends(get_db)):
    d = get_document_or_404(db, document_id)
    return [_section_out(s) for s in d.sections if not s.is_deleted]


@router.get("/documents/{document_id}/source", response_model=SourceOut, tags=["documents"],
            summary="Original source file content with this document's line range (for locating passages)")
def get_source(document_id: str, db: Session = Depends(get_db)):
    d = get_document_or_404(db, document_id)
    sf = db.get(SourceFile, d.source_file_id)
    return SourceOut(document_id=d.id, rel_path=sf.rel_path, file_kind=sf.file_kind, encoding=sf.encoding,
                     content_hash=sf.content_hash, version=sf.version, start_line=d.start_line, end_line=d.end_line,
                     content=sf.raw_content)


@router.get("/documents/{document_id}/relationships", response_model=list[RelationshipOut], tags=["documents"])
def get_relationships(document_id: str, rel_type: list[str] = Query(default=[]), include_mentions: bool = True,
                      db: Session = Depends(get_db)):
    d = get_document_or_404(db, document_id)
    stmt = select(Relationship).where(or_(Relationship.source_document_id == d.id, Relationship.target_document_id == d.id))
    if rel_type:
        stmt = stmt.where(Relationship.rel_type.in_(rel_type))
    if not include_mentions:
        stmt = stmt.where(Relationship.rel_type != "mentions")
    rels = db.scalars(stmt).all()
    other_ids = {r.target_document_id if r.source_document_id == d.id else r.source_document_id for r in rels} - {None}
    others = {o.id: o for o in db.scalars(select(Document).where(Document.id.in_(other_ids)))}
    counts = section_counts(db, list(others))
    out = []
    for r in rels:
        outgoing = r.source_document_id == d.id
        oid = r.target_document_id if outgoing else r.source_document_id
        o = others.get(oid) if oid else None
        out.append(RelationshipOut(id=r.id, direction="outgoing" if outgoing else "incoming", rel_type=r.rel_type,
                                   origin=r.origin, is_inferred=r.is_inferred, confidence=r.confidence, evidence=r.evidence,
                                   source_document_id=r.source_document_id, target_document_id=r.target_document_id,
                                   target_ref=r.target_ref, resolved=r.resolved, attributes=r.attributes or {},
                                   other_document=doc_summary(o, counts.get(o.id, 0)) if o else None))
    order = {"supersedes": 0, "implements": 1, "records_decision": 2, "follows_up": 3, "depends_on": 4, "related_to": 5}
    return sorted(out, key=lambda r: (order.get(r.rel_type, 9), r.direction, r.other_document.id if r.other_document else r.target_ref))


@router.get("/search", tags=["search"], summary="Search sections (BM25 / semantic / hybrid) with filters and highlights")
def search(
    q: str = Query(..., min_length=1, max_length=500), mode: str = Query("bm25", pattern="^(bm25|semantic|hybrid)$"),
    doc_type: list[str] = Query(default=[]), status: list[str] = Query(default=[]), person: list[str] = Query(default=[]),
    document_id: list[str] = Query(default=[]), identifier: list[str] = Query(default=[]),
    date_from: date | None = None, date_to: date | None = None, include_records: bool = True,
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db),
):
    from app.retrieval.service import get_search_service

    req = SearchRequest(query=q, mode=mode, size=limit, offset=offset, filters=SearchFilters(
        doc_types=doc_type, statuses=status, people=person, document_ids=document_id, identifiers=identifier,
        date_from=date_from, date_to=date_to, include_records=include_records))
    try:
        resp = get_search_service().search(req)
    except Exception as exc:  # noqa: BLE001 - search backend outage is reported, not hidden
        raise ServiceUnavailableError("Search backend unavailable", {"error": type(exc).__name__}) from exc
    # link hits to claims whose latest assessment used them as evidence
    sec_ids = [h.section_id for h in resp.hits]
    links: dict[str, list[dict]] = {}
    if sec_ids:
        rows = db.execute(select(AssessmentEvidence.section_id, AssessmentEvidence.label, Claim.id, Claim.text,
                                 Claim.latest_verdict, AssessmentEvidence.assessment_id)
                          .join(Claim, Claim.latest_assessment_id == AssessmentEvidence.assessment_id)
                          .where(AssessmentEvidence.section_id.in_(sec_ids))).all()
        for sid, label, cid, ctext, verdict, aid in rows:
            lst = links.setdefault(sid, [])
            if not any(x["claim_id"] == cid for x in lst):
                lst.append({"claim_id": cid, "claim_text": ctext, "verdict": verdict, "label": label, "assessment_id": aid})
    payload = resp.model_dump(mode="json")
    for h in payload["hits"]:
        h["related_claims"] = links.get(h["section_id"], [])
    return payload


@router.get("/search/facets", tags=["search"], summary="Available filter values")
def facets(db: Session = Depends(get_db)):
    types = db.execute(select(Document.doc_type, func.count()).where(Document.is_deleted.is_(False))
                       .group_by(Document.doc_type)).all()
    statuses = db.execute(select(Document.status, func.count()).where(Document.is_deleted.is_(False))
                          .group_by(Document.status)).all()
    people: dict[str, int] = {}
    for (ppl,) in db.execute(select(Document.people).where(Document.is_deleted.is_(False), Document.kind != "record")):
        for p in ppl or []:
            people[p] = people.get(p, 0) + 1
    return {"doc_types": dict(types), "statuses": dict(statuses),
            "people": dict(sorted(people.items(), key=lambda x: -x[1]))}


@router.get("/sections/{section_id}", response_model=SectionOut, tags=["documents"])
def get_section(section_id: str, db: Session = Depends(get_db)):
    from app.core.errors import NotFoundError

    s = db.scalars(select(Section).where(Section.id == section_id).options(selectinload(Section.document))).first()
    if s is None:
        raise NotFoundError(f"Section '{section_id}' not found")
    return _section_out(s)
