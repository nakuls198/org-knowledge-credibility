"""Intermediate (pre-persistence) structures produced by the parsers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class ParsedLink:
    text: str
    href: str
    section_id: str
    line: int


@dataclass
class ParsedSection:
    id: str
    heading: str
    heading_path: list[str]
    level: int
    ordinal: int
    text: str
    plain_text: str
    start_line: int  # 1-based, inclusive, relative to the source file
    end_line: int
    start_char: int  # offsets into the (newline-normalised) source file
    end_char: int
    section_role: str = "content"
    parent_section_id: str | None = None
    identifiers: list[str] = field(default_factory=list)
    content_hash: str = ""


@dataclass
class ParsedDocument:
    id: str
    rel_path: str
    kind: str  # file | thread | record | container
    title: str
    identifier: str | None
    doc_type: str
    doc_type_source: str
    status: str
    status_source: str
    owner: str | None
    people: list[str]
    version_label: str | None
    doc_date: date | None
    date_source: str | None
    effective_date: date | None
    review_date: date | None
    superseded_date: date | None
    meta: dict[str, Any]
    start_line: int
    end_line: int
    content_hash: str
    sections: list[ParsedSection] = field(default_factory=list)
    links: list[ParsedLink] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


@dataclass
class ParseIssue:
    severity: str
    issue_type: str
    message: str
    rel_path: str | None = None
    document_id: str | None = None
    section_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedFile:
    rel_path: str
    file_kind: str
    content: str
    content_hash: str
    encoding: str
    documents: list[ParsedDocument] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
