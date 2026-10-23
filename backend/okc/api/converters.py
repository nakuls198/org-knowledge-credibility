"""Row -> API model converters and 404 helpers shared by the route modules."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from okc.api.schemas import AssessmentSummary, ClaimOut, DocumentSummary, SectionOut
from okc.common.errors import NotFoundError
from okc.db.models import Assessment, Claim, Document, Section


def doc_summary(d: Document, section_count: int | None = None) -> DocumentSummary:
    return DocumentSummary(
        id=d.id, title=d.title, doc_type=d.doc_type, kind=d.kind, status=d.status, identifier=d.identifier,
        owner=d.owner, people=list(d.people or []), doc_date=d.doc_date, rel_path=d.rel_path, flags=list(d.flags or []),
        section_count=section_count if section_count is not None else len([s for s in d.sections if not s.is_deleted]),
    )


def section_out(s: Section) -> SectionOut:
    return SectionOut(id=s.id, document_id=s.document_id, parent_section_id=s.parent_section_id, ordinal=s.ordinal,
                      heading=s.heading, heading_path=list(s.heading_path or []), level=s.level, section_role=s.section_role,
                      text=s.text, plain_text=s.plain_text, start_line=s.start_line, end_line=s.end_line,
                      content_hash=s.content_hash, identifiers=list(s.identifiers or []))


def claim_out(c: Claim) -> ClaimOut:
    return ClaimOut(id=c.id, text=c.text, normalized_text=c.normalized_text, origin=c.origin, tags=list(c.tags or []),
                    latest_assessment_id=c.latest_assessment_id, latest_verdict=c.latest_verdict,
                    latest_score=c.latest_score, needs_review=c.needs_review, created_at=c.created_at, updated_at=c.updated_at)


def assessment_summary(a: Assessment) -> AssessmentSummary:
    return AssessmentSummary(id=a.id, claim_id=a.claim_id, status=a.status, verdict=a.verdict, score=a.score,
                             needs_review=a.needs_review, as_of=a.as_of, created_at=a.created_at,
                             completed_at=a.completed_at, error=a.error,
                             classifier=(a.versions or {}).get("classifier", {}).get("mode") if a.versions else None)


def section_counts(db: Session, doc_ids: list[str]) -> dict[str, int]:
    if not doc_ids:
        return {}
    rows = db.execute(select(Section.document_id, func.count(Section.id))
                      .where(Section.document_id.in_(doc_ids), Section.is_deleted.is_(False))
                      .group_by(Section.document_id)).all()
    return dict(rows)


def get_document_or_404(db: Session, document_id: str) -> Document:
    d = db.get(Document, document_id)
    if d is None:
        raise NotFoundError(f"Document '{document_id}' not found")
    return d


def get_claim_or_404(db: Session, claim_id: str) -> Claim:
    c = db.get(Claim, claim_id)
    if c is None:
        raise NotFoundError(f"Claim '{claim_id}' not found")
    return c


def get_assessment_or_404(db: Session, assessment_id: str) -> Assessment:
    a = db.get(Assessment, assessment_id)
    if a is None:
        raise NotFoundError(f"Assessment '{assessment_id}' not found")
    return a
