"""Search request / response contracts shared by all backends."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

SearchMode = Literal["bm25", "semantic", "hybrid"]


class SearchFilters(BaseModel):
    doc_types: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    section_roles: list[str] = Field(default_factory=list)
    exclude_document_ids: list[str] = Field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    include_records: bool = True


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    mode: SearchMode = "bm25"
    size: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1000)
    highlight: bool = True


class SearchHit(BaseModel):
    section_id: str
    document_id: str
    doc_title: str
    doc_type: str
    status: str
    heading: str
    heading_path: list[str]
    section_role: str
    text: str
    source_path: str
    start_line: int
    end_line: int
    doc_date: date | None = None
    people: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    score: float
    rank: int
    highlights: list[str] = Field(default_factory=list)  # HTML-escaped text with <mark> tags only
    matched: list[str] = Field(default_factory=list)  # which query clauses / channels matched
    channel_ranks: dict[str, int] = Field(default_factory=dict)  # e.g. {"bm25": 1, "semantic": 4}
    explanation: str = ""


class SearchResponse(BaseModel):
    query: str
    mode: SearchMode
    backend: str
    total: int
    took_ms: float
    hits: list[SearchHit]
    notes: list[str] = Field(default_factory=list)
