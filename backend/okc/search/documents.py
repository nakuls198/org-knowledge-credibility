"""What gets indexed: one search document per leaf section."""

from __future__ import annotations

from typing import Any

from okc.db.models import Document, Section


def indexable_sections(doc: Document) -> list[Section]:
    """Leaf sections only: a chunked parent is represented by its chunks."""
    sections = [s for s in doc.sections if not s.is_deleted]
    parents = {s.parent_section_id for s in sections if s.parent_section_id}
    return [s for s in sections if s.id not in parents]


def embedding_text(doc: Document, sec: Section) -> str:
    return f"{doc.title}. {sec.heading}. {sec.plain_text}"[:2000]


def section_search_doc(doc: Document, sec: Section, embedding: list[float] | None = None) -> dict[str, Any]:
    out = {
        "section_id": sec.id, "document_id": doc.id, "parent_section_id": sec.parent_section_id,
        "doc_title": doc.title, "doc_type": doc.doc_type, "doc_kind": doc.kind, "status": doc.status,
        "flags": list(doc.flags or []), "owner": doc.owner, "people": list(doc.people or []),
        "doc_date": doc.doc_date.isoformat() if doc.doc_date else None,
        "identifiers": sorted(set(([doc.identifier] if doc.identifier else []) + list(sec.identifiers or []))),
        "heading": sec.heading, "heading_path": list(sec.heading_path or []), "section_role": sec.section_role,
        "text": sec.plain_text, "source_path": doc.rel_path, "start_line": sec.start_line, "end_line": sec.end_line,
        "content_hash": sec.content_hash,
    }
    if embedding is not None:
        out["embedding"] = embedding
    return out
