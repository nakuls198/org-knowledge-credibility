"""Data carried through credibility scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from okc.evidence.schemas import EvidenceContext, Judgement

# SUPPORTED | PARTIALLY_SUPPORTED | CONTESTED | CONTRADICTED | INSUFFICIENT_EVIDENCE
Verdict = str


@dataclass
class EvidenceItem:
    ctx: EvidenceContext
    judgement: Judgement
    retrieval: dict[str, Any] = field(default_factory=dict)
    features: dict[str, float] = field(default_factory=dict)
    weight: float = 0.0
    contribution: float = 0.0
    cluster_id: str | None = None


@dataclass
class DocInfo:
    """The document facts scoring needs (a snapshot, so a stored assessment can be re-scored later)."""

    id: str
    title: str
    doc_type: str
    status: str
    owner: str | None
    flags: list[str]
    doc_date: date | None
    effective_date: date | None
    superseded_date: date | None


@dataclass
class RelationEdge:
    """A typed relationship between two evidence documents."""

    source: str
    target: str
    rel_type: str
    origin: str
    evidence: str
    attributes: dict[str, Any] = field(default_factory=dict)


class Cluster(BaseModel):
    id: str
    label: str
    members: list[int]  # evidence indexes
    documents: list[str]
    reasons: list[str]
    mass: float
    strongest: int


class GovernanceConflict(BaseModel):
    supporting: list[str]
    contradicting: list[str]
    resolution: str  # resolved_by_supersession | resolved_by_staleness | resolved_by_proposal_status | unresolved
    explanation: str
    governing_document: str | None = None


class CredibilityResult(BaseModel):
    method: str
    assessment_kind: str = "heuristic"  # never presented as a calibrated probability
    score: float
    verdict: str
    needs_review: bool
    review_reasons: list[str]
    interval: tuple[float, float]
    uncertainty_std: float
    support_mass: float
    contradict_mass: float
    ambiguous_count: int
    conflict: float
    coverage: float
    coverage_detail: dict[str, list[str]]
    independent_support: int
    independent_contradiction: int
    clusters: list[Cluster]
    practice_adherence: float | None = None
    temporal_notes: list[str] = Field(default_factory=list)
    governance: list[GovernanceConflict] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    as_of: str | None = None
