"""Documents, sections, relationships, source files and search."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from okc.api.converters import doc_summary, get_document_or_404, section_counts, section_out
from okc.api.schemas import DocumentDetail, DocumentSummary, Page, RelationshipOut, SectionOut, SourceOut
from okc.common.errors import NotFoundError, ServiceUnavailableError
from okc.db.models import AssessmentEvidence, Claim, Document, Relationship, Section, SourceFile
from okc.db.session import get_db
from okc.search.schemas import SearchFilters, SearchRequest
from okc.search.service import get_search_service

router = APIRouter()
# Relationship types in the order a reader cares about them.
_RELATIONSHIP_ORDER = {"supersedes": 0, "implements": 1, "records_decision": 2, "follows_up": 3, "depends_on": 4, "related_to": 5}


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
    sections = [s for s in d.sections if not s.is_deleted]
    relationship_counts = dict(db.execute(select(Relationship.rel_type, func.count()).where(
        or_(Relationship.source_document_id == d.id, Relationship.target_document_id == d.id))
        .group_by(Relationship.rel_type)).all())
    source = db.get(SourceFile, d.source_file_id)
    return DocumentDetail(**doc_summary(d, len(sections)).model_dump(), doc_type_source=d.doc_type_source,
                          status_source=d.status_source, version_label=d.version_label, date_source=d.date_source,
                          effective_date=d.effective_date, review_date=d.review_date, superseded_date=d.superseded_date,
                          metadata={k: v for k, v in (d.meta or {}).items() if not k.startswith("_")},
                          start_line=d.start_line, end_line=d.end_line, content_hash=d.content_hash, is_deleted=d.is_deleted,
                          source_file_id=d.source_file_id, source_version=source.version if source else 0,
                          sections=[section_out(s) for s in sections], relationship_counts=relationship_counts)


@router.get("/documents/{document_id}/sections", response_model=list[SectionOut], tags=["documents"])
def get_sections(document_id: str, db: Session = Depends(get_db)):
    return [section_out(s) for s in get_document_or_404(db, document_id).sections if not s.is_deleted]


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
    relationships = db.scalars(stmt).all()
    other_ids = {r.target_document_id if r.source_document_id == d.id else r.source_document_id for r in relationships} - {None}
    others = {o.id: o for o in db.scalars(select(Document).where(Document.id.in_(other_ids)))}
    counts = section_counts(db, list(others))
    out = []
    for r in relationships:
        outgoing = r.source_document_id == d.id
        other = others.get(r.target_document_id if outgoing else r.source_document_id)
        out.append(RelationshipOut(
            id=r.id, direction="outgoing" if outgoing else "incoming", rel_type=r.rel_type, origin=r.origin,
            is_inferred=r.is_inferred, confidence=r.confidence, evidence=r.evidence, source_document_id=r.source_document_id,
            target_document_id=r.target_document_id, target_ref=r.target_ref, resolved=r.resolved,
            attributes=r.attributes or {}, other_document=doc_summary(other, counts.get(other.id, 0)) if other else None))
    return sorted(out, key=lambda r: (_RELATIONSHIP_ORDER.get(r.rel_type, 9), r.direction,
                                      r.other_document.id if r.other_document else r.target_ref))


@router.get("/sections/{section_id}", response_model=SectionOut, tags=["documents"])
def get_section(section_id: str, db: Session = Depends(get_db)):
    s = db.scalars(select(Section).where(Section.id == section_id).options(selectinload(Section.document))).first()
    if s is None:
        raise NotFoundError(f"Section '{section_id}' not found")
    return section_out(s)


# ---------------------------------------------------------------- search

def _claims_using(db: Session, section_ids: list[str]) -> dict[str, list[dict]]:
    """For each section: the claims whose latest assessment used it as evidence."""
    if not section_ids:
        return {}
    rows = db.execute(select(AssessmentEvidence.section_id, AssessmentEvidence.label, Claim.id, Claim.text,
                             Claim.latest_verdict, AssessmentEvidence.assessment_id)
                      .join(Claim, Claim.latest_assessment_id == AssessmentEvidence.assessment_id)
                      .where(AssessmentEvidence.section_id.in_(section_ids))).all()
    links: dict[str, list[dict]] = {}
    for sid, label, claim_id, claim_text, verdict, assessment_id in rows:
        found = links.setdefault(sid, [])
        if not any(x["claim_id"] == claim_id for x in found):
            found.append({"claim_id": claim_id, "claim_text": claim_text, "verdict": verdict, "label": label,
                          "assessment_id": assessment_id})
    return links


@router.get("/search", tags=["search"], summary="Search sections (BM25 / semantic / hybrid) with filters and highlights")
def search(
    q: str = Query(..., min_length=1, max_length=500), mode: str = Query("bm25", pattern="^(bm25|semantic|hybrid)$"),
    doc_type: list[str] = Query(default=[]), status: list[str] = Query(default=[]), person: list[str] = Query(default=[]),
    document_id: list[str] = Query(default=[]), identifier: list[str] = Query(default=[]),
    date_from: date | None = None, date_to: date | None = None, include_records: bool = True,
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db),
):
    request = SearchRequest(query=q, mode=mode, size=limit, offset=offset, filters=SearchFilters(
        doc_types=doc_type, statuses=status, people=person, document_ids=document_id, identifiers=identifier,
        date_from=date_from, date_to=date_to, include_records=include_records))
    try:
        response = get_search_service().search(request)
    except Exception as exc:  # noqa: BLE001 - a search backend outage is reported, not hidden
        raise ServiceUnavailableError("Search backend unavailable", {"error": type(exc).__name__}) from exc
    links = _claims_using(db, [h.section_id for h in response.hits])
    payload = response.model_dump(mode="json")
    for hit in payload["hits"]:
        hit["related_claims"] = links.get(hit["section_id"], [])
    return payload


@router.get("/search/facets", tags=["search"], summary="Available filter values")
def facets(db: Session = Depends(get_db)):
    types = db.execute(select(Document.doc_type, func.count()).where(Document.is_deleted.is_(False))
                       .group_by(Document.doc_type)).all()
    statuses = db.execute(select(Document.status, func.count()).where(Document.is_deleted.is_(False))
                          .group_by(Document.status)).all()
    people: dict[str, int] = {}
    for (names,) in db.execute(select(Document.people).where(Document.is_deleted.is_(False), Document.kind != "record")):
        for name in names or []:
            people[name] = people.get(name, 0) + 1
    return {"doc_types": dict(types), "statuses": dict(statuses),
            "people": dict(sorted(people.items(), key=lambda x: -x[1]))}
