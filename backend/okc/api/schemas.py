"""API request/response models (documented in the OpenAPI schema)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")
ClassifierMode = Literal["rules", "rules+nli", "llm", "llm+rules"]
RetrievalMode = Literal["bm25", "semantic", "hybrid"]


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ----------------------------------------------------------------- ingestion
class IngestionJobCreate(BaseModel):
    mode: Literal["incremental", "full"] = "incremental"


class IngestionJobOut(BaseModel):
    id: str
    status: str
    mode: str
    data_dir: str
    stats: dict[str, Any]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    issue_counts: dict[str, int] = Field(default_factory=dict)


class IssueOut(BaseModel):
    id: int
    severity: str
    issue_type: str
    document_id: str | None
    section_id: str | None
    rel_path: str | None
    message: str
    details: dict[str, Any]


# ----------------------------------------------------------------- documents
class DocumentSummary(BaseModel):
    id: str
    title: str
    doc_type: str
    kind: str
    status: str
    identifier: str | None
    owner: str | None
    people: list[str]
    doc_date: date | None
    rel_path: str
    flags: list[str]
    section_count: int = 0


class SectionOut(BaseModel):
    id: str
    document_id: str
    parent_section_id: str | None
    ordinal: int
    heading: str
    heading_path: list[str]
    level: int
    section_role: str
    text: str
    plain_text: str
    start_line: int
    end_line: int
    content_hash: str
    identifiers: list[str]


class DocumentDetail(DocumentSummary):
    doc_type_source: str
    status_source: str
    version_label: str | None
    date_source: str | None
    effective_date: date | None
    review_date: date | None
    superseded_date: date | None
    metadata: dict[str, Any]
    start_line: int
    end_line: int
    content_hash: str
    is_deleted: bool
    source_file_id: str
    source_version: int
    sections: list[SectionOut]
    relationship_counts: dict[str, int]


class RelationshipOut(BaseModel):
    id: str
    direction: Literal["outgoing", "incoming"]
    rel_type: str
    origin: str
    is_inferred: bool
    confidence: float
    evidence: str
    source_document_id: str
    target_document_id: str | None
    target_ref: str
    resolved: bool
    other_document: DocumentSummary | None = None
    attributes: dict[str, Any]


class SourceOut(BaseModel):
    document_id: str
    rel_path: str
    file_kind: str
    encoding: str
    content_hash: str
    version: int
    start_line: int
    end_line: int
    content: str


# -------------------------------------------------------------------- claims
class ClaimCreate(BaseModel):
    text: str = Field(min_length=10, max_length=600, description="A natural-language organisational claim")
    tags: list[str] = Field(default_factory=list, max_length=10)
    assess: bool = True
    classifier_mode: ClassifierMode | None = None
    as_of: date | None = None

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v.split()) < 3:
            raise ValueError("a claim needs at least three words")
        return v


class AssessmentCreate(BaseModel):
    classifier_mode: ClassifierMode | None = None
    as_of: date | None = None
    max_evidence: int | None = Field(default=None, ge=4, le=60)
    retrieval_mode: RetrievalMode | None = None


class AssessmentSummary(BaseModel):
    id: str
    claim_id: str
    status: str
    verdict: str | None
    score: float | None
    needs_review: bool
    as_of: date | None
    created_at: datetime
    completed_at: datetime | None
    error: str | None
    classifier: str | None = None


class ClaimOut(BaseModel):
    id: str
    text: str
    normalized_text: str
    origin: str
    tags: list[str]
    latest_assessment_id: str | None
    latest_verdict: str | None
    latest_score: float | None
    needs_review: bool
    created_at: datetime
    updated_at: datetime


class ClaimDetail(ClaimOut):
    analysis: dict[str, Any]
    assessments: list[AssessmentSummary]
    latest_assessment: AssessmentSummary | None = None


class EvidenceOut(BaseModel):
    id: int
    rank: int
    evidence_ref: str
    section_id: str
    document_id: str
    document: DocumentSummary | None = None
    heading: str = ""
    section_role: str = ""
    source_path: str = ""
    start_line: int = 0
    end_line: int = 0
    span_line: int | None = None
    span_start: int
    span_end: int
    span_text: str
    section_text: str
    section_changed_since: bool = False
    label: str
    scope: str
    strength: float
    rationale: str
    signals: dict[str, Any]
    features: dict[str, Any]
    contribution: float
    cluster_id: str | None
    retrieval: dict[str, Any]
    duplicates: list[Any]


class AssessmentDetail(AssessmentSummary):
    claim_text: str
    result: dict[str, Any]
    explanation: dict[str, Any]
    retrieval: dict[str, Any]
    config_hash: str | None
    versions: dict[str, Any]
    evidence: list[EvidenceOut]


# --------------------------------------------------------------------- graph
class GraphNode(BaseModel):
    id: str
    type: Literal["claim", "evidence", "document", "related_document"]
    label: str
    data: dict[str, Any]


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str
    label: str
    data: dict[str, Any]


class GraphOut(BaseModel):
    claim_id: str
    assessment_id: str | None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    filters: dict[str, Any]


# ------------------------------------------------------------------- answers
class AnswerRequest(BaseModel):
    question: str = Field(min_length=3, max_length=600, description="Any question or statement about the organisation")
    classifier_mode: ClassifierMode | None = None
    retrieval_mode: RetrievalMode | None = None
    max_evidence: int | None = Field(default=None, ge=4, le=60)
    fresh: bool = Field(default=False, description="Re-run the check even if an up-to-date result exists")
