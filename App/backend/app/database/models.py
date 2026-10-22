"""Canonical relational store (PostgreSQL). Elasticsearch is a derived, rebuildable index.

Identifier scheme (deterministic, human-readable):
  * source file   : "sf_" + sha1(relative path)[:16]
  * document      : shared identifier when present (POL-01, COMMS-03, DEAL-014),
                    else a slug of the file path (e.g. "00-overview/company-overview")
  * section       : "<document_id>::<heading-path-slug>[#n]" ; chunks append "~k"
  * relationship  : "rel_" + sha1(source|target|type|origin|evidence)[:20]
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SourceFile(Base):
    __tablename__ = "source_files"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    rel_path: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    file_kind: Mapped[str] = mapped_column(String(16))  # markdown | csv
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    encoding: Mapped[str] = mapped_column(String(32), default="utf-8")
    raw_content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    parser_signature: Mapped[str] = mapped_column(String(32), default="")  # parser code + ingestion config
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    versions: Mapped[list[SourceFileVersion]] = relationship(back_populates="source_file", cascade="all, delete-orphan")


class SourceFileVersion(Base):
    """Every distinct content version ever ingested - supports temporal / audit questions."""

    __tablename__ = "source_file_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_file_id: Mapped[str] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    raw_content: Mapped[str] = mapped_column(Text)
    change_type: Mapped[str] = mapped_column(String(16))  # new | modified | deleted
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source_file: Mapped[SourceFile] = relationship(back_populates="versions")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    source_file_id: Mapped[str] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"), index=True)
    rel_path: Mapped[str] = mapped_column(String(1024), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # file | thread | record | container
    identifier: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512))
    doc_type: Mapped[str] = mapped_column(String(64), index=True)
    doc_type_source: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="unspecified", index=True)
    status_source: Mapped[str] = mapped_column(String(256), default="")
    owner: Mapped[str | None] = mapped_column(String(255))
    people: Mapped[list] = mapped_column(JSONType, default=list)  # authors / participants / actors
    version_label: Mapped[str | None] = mapped_column(String(64))
    doc_date: Mapped[date | None] = mapped_column(Date, index=True)
    date_source: Mapped[str | None] = mapped_column(String(64))
    effective_date: Mapped[date | None] = mapped_column(Date)
    review_date: Mapped[date | None] = mapped_column(Date)
    superseded_date: Mapped[date | None] = mapped_column(Date)
    meta: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)
    flags: Mapped[list] = mapped_column(JSONType, default=list)  # e.g. review_overdue, potentially_stale
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sections: Mapped[list[Section]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Section.ordinal"
    )


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    parent_section_id: Mapped[str | None] = mapped_column(String(512))
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(String(512))
    heading_path: Mapped[list] = mapped_column(JSONType, default=list)
    level: Mapped[int] = mapped_column(Integer, default=0)
    section_role: Mapped[str] = mapped_column(String(32), default="content")
    text: Mapped[str] = mapped_column(Text)  # original Markdown text of the section body
    plain_text: Mapped[str] = mapped_column(Text)  # markup-stripped text used for search / reasoning
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    identifiers: Mapped[list] = mapped_column(JSONType, default=list)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    document: Mapped[Document] = relationship(back_populates="sections")


class Relationship(Base):
    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    target_document_id: Mapped[str | None] = mapped_column(String(255), index=True)
    target_ref: Mapped[str] = mapped_column(String(512))  # raw reference text (id, path, ...)
    rel_type: Mapped[str] = mapped_column(String(32), index=True)
    origin: Mapped[str] = mapped_column(String(32))  # explicit_link | metadata | id_mention | structured_field | inferred
    is_inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    evidence: Mapped[str] = mapped_column(Text, default="")
    source_section_id: Mapped[str | None] = mapped_column(String(512))
    resolved: Mapped[bool] = mapped_column(Boolean, default=True)
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict)

    __table_args__ = (Index("ix_rel_target_type", "target_document_id", "rel_type"),)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|succeeded|failed
    mode: Mapped[str] = mapped_column(String(16), default="incremental")  # incremental | full
    data_dir: Mapped[str] = mapped_column(String(1024))
    stats: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionIssue(Base):
    __tablename__ = "ingestion_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), index=True)
    severity: Mapped[str] = mapped_column(String(16))  # info | warning | error
    issue_type: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str | None] = mapped_column(String(255))
    section_id: Mapped[str | None] = mapped_column(String(512))
    rel_path: Mapped[str | None] = mapped_column(String(1024))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text, index=False)
    analysis: Mapped[dict] = mapped_column(JSONType, default=dict)
    origin: Mapped[str] = mapped_column(String(32), default="user")  # user | evaluation | extracted
    external_ref: Mapped[str | None] = mapped_column(String(64), index=True)  # e.g. evaluation claim id
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    latest_assessment_id: Mapped[str | None] = mapped_column(String(40))
    latest_verdict: Mapped[str | None] = mapped_column(String(32), index=True)
    latest_score: Mapped[float | None] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    assessments: Mapped[list[Assessment]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", order_by="Assessment.created_at.desc()"
    )


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|completed|failed
    verdict: Mapped[str | None] = mapped_column(String(32))
    score: Mapped[float | None] = mapped_column(Float)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    as_of: Mapped[date | None] = mapped_column(Date)
    result: Mapped[dict] = mapped_column(JSONType, default=dict)  # metrics, features, issues, next steps
    explanation: Mapped[dict] = mapped_column(JSONType, default=dict)
    retrieval: Mapped[dict] = mapped_column(JSONType, default=dict)  # queries, channels, limits
    config_snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)
    config_hash: Mapped[str | None] = mapped_column(String(32))
    versions: Mapped[dict] = mapped_column(JSONType, default=dict)  # engine / classifier / model / prompt
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    claim: Mapped[Claim] = relationship(back_populates="assessments")
    evidence: Mapped[list[AssessmentEvidence]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", order_by="AssessmentEvidence.rank"
    )


class AssessmentEvidence(Base):
    """One evidence span as it was seen and judged during an assessment (immutable snapshot)."""

    __tablename__ = "assessment_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    section_id: Mapped[str] = mapped_column(String(512), index=True)
    document_id: Mapped[str] = mapped_column(String(255), index=True)
    section_content_hash: Mapped[str] = mapped_column(String(64))
    section_text: Mapped[str] = mapped_column(Text)  # snapshot of the section's plain text
    span_start: Mapped[int] = mapped_column(Integer)  # offsets into section_text
    span_end: Mapped[int] = mapped_column(Integer)
    span_text: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(16), index=True)  # SUPPORTS|CONTRADICTS|NEUTRAL|AMBIGUOUS
    scope: Mapped[str] = mapped_column(String(16))  # formal | practice | belief | decision | proposal | context
    strength: Mapped[float] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(Text)
    signals: Mapped[dict] = mapped_column(JSONType, default=dict)  # classifier intermediate outputs
    features: Mapped[dict] = mapped_column(JSONType, default=dict)  # credibility features
    contribution: Mapped[float] = mapped_column(Float, default=0.0)
    cluster_id: Mapped[str | None] = mapped_column(String(64))
    retrieval: Mapped[dict] = mapped_column(JSONType, default=dict)  # channels, ranks, scores, reasons
    duplicates: Mapped[list] = mapped_column(JSONType, default=list)

    assessment: Mapped[Assessment] = relationship(back_populates="evidence")


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="completed")
    dataset: Mapped[dict] = mapped_column(JSONType, default=dict)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
