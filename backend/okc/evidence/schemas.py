"""Evidence contracts shared by gathering, classification and scoring."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Label = Literal["SUPPORTS", "CONTRADICTS", "NEUTRAL", "AMBIGUOUS"]
Scope = Literal["formal", "decision", "proposal", "discussion", "practice", "testimony", "belief", "history"]


class EvidenceContext(BaseModel):
    """Everything the classifier may look at for one evidence unit (all provenance kept)."""

    section_id: str
    document_id: str
    doc_title: str
    doc_type: str
    doc_kind: str
    status: str
    flags: list[str] = Field(default_factory=list)
    doc_date: str | None = None
    doc_people: list[str] = Field(default_factory=list)
    heading: str
    section_role: str
    source_path: str
    start_line: int
    end_line: int
    section_text: str
    span_start: int
    span_end: int
    relevance: float = 0.0
    section_relevance: float = 0.0
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def span(self) -> str:
        return self.section_text[self.span_start : self.span_end]


class Judgement(BaseModel):
    label: Label
    scope: Scope
    strength: float = Field(ge=0, le=1)
    directness: float = Field(ge=0, le=1)
    modality: str
    conditions: list[str] = Field(default_factory=list)
    rationale: str
    signals: dict[str, Any] = Field(default_factory=dict)
    classifier: str
