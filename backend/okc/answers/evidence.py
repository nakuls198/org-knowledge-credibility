"""Evidence blocks shown with every plain answer: the record, the rule, audit entries, mentions."""

from __future__ import annotations

import re
import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from okc.answers.compliance import fix_text
from okc.assessment.investigate import corpus_version
from okc.db.models import Document, Section
from okc.settings import load_config

ANSWER_VERSION = "answers-1.0"

_HEADER = re.compile(r"^\s*(from|to|cc|subject|timestamp|participants|thread relationship|date)\s*:", re.I)
_TITLE_LINE = re.compile(r"^[A-Z]+-\d+\s+[—-]")
_RECORD_ID = re.compile(r"[A-Z]{2,6}-\d{2,4}")
_HIDDEN_SECTION_NAMES = ("header", "record")


def trim(text: str | None, n: int = 260) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def around(text: str, needle: str, n: int = 360) -> str:
    """What a message actually says about needle: the first non-header line mentioning it, plus the reply."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if needle in ln and not _HEADER.match(ln):
            return trim(" ".join(lines[i:i + 2]), n)
    body = [ln for ln in lines if not _HEADER.match(ln)]
    return trim(" ".join(body) or text, n)


def message_body(text: str) -> str:
    """A message without its header lines and title."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    body = [ln for ln in lines if not _HEADER.match(ln) and not _TITLE_LINE.match(ln)]
    return " ".join(body) or text


def type_label(doc_type: str | None, status: str | None = None) -> str:
    names = load_config("compliance").get("type_names", {})
    base = names.get(doc_type or "", (doc_type or "File").replace("_", " ").capitalize())
    return f"{base} ({status.upper()})" if status in ("superseded", "draft") else base


def evidence_item(doc: Document, section_id: str | None, quote: str, role: str, note: str = "") -> dict[str, Any]:
    return {"doc_id": doc.id, "section_id": section_id, "title": doc.title, "type": type_label(doc.doc_type, doc.status),
            "date": doc.doc_date.isoformat() if doc.doc_date else None, "status": doc.status, "path": doc.rel_path,
            "quote": quote, "role": role, "note": note}


def base_answer(question: str, kind: str, outcome: str, headline: str, answer: str, **kw: Any) -> dict[str, Any]:
    return {"question": question, "kind": kind, "outcome": outcome, "headline": headline, "answer": answer,
            "reasons": kw.get("reasons", []), "caveats": kw.get("caveats", []), "evidence": kw.get("evidence", []),
            "facts": kw.get("facts", []), "checked_as": kw.get("checked_as"), "note": kw.get("note"),
            "claim_id": kw.get("claim_id"), "assessment_id": kw.get("assessment_id"), "items": kw.get("items", []),
            "version": ANSWER_VERSION}


# ---------------------------------------------------------------- how each record type reads as one line

def _crm_line(m: dict[str, str]) -> str:
    parts = [f"{m.get('discount_pct') or '?'}% discount", m.get("stage", ""),
             f"closed {m['close_date']}" if m.get("close_date") else "",
             f"approval: {m.get('approval_record') or 'none'}" + (f" ({m['approval_role']})" if m.get("approval_role") else "")]
    line = f"{m.get('customer_name', '')}: " + ", ".join(p for p in parts if p)
    return line + (f". Note: {m['notes']}" if m.get("notes") else "")


def _task_line(m: dict[str, str]) -> str:
    return f"{m.get('project', '')}. {m.get('comments', '')}".strip()


def _audit_line(m: dict[str, str]) -> str:
    return (f"{m.get('timestamp', '')} · {m.get('actor_name', '')} ({m.get('actor_role', '')}): {m.get('action', '')}"
            f" {m.get('object', '')} ({m.get('previous_state', '')} → {m.get('new_state', '')})"
            + (f". {m['notes']}" if m.get("notes") else ""))


_RECORD_LINES = {"crm_record": _crm_line, "task_record": _task_line, "audit_event": _audit_line}


def record_line(doc: Document) -> str:
    meta = {k: fix_text(str(v)) for k, v in (doc.meta or {}).items()}
    formatter = _RECORD_LINES.get(doc.doc_type)
    if formatter:
        return formatter(meta)
    return trim(" · ".join(f"{k}: {v}" for k, v in meta.items() if v), 300)


# ---------------------------------------------------------------- database lookups

_audit_lock = threading.Lock()
_audit_cache: dict[str, Any] = {}


def audit_by_record(db: Session) -> dict[str, list[dict[str, Any]]]:
    """Audit-log events grouped by the record they concern (cached per corpus version)."""
    key = corpus_version(db)
    with _audit_lock:
        if _audit_cache.get("key") != key:
            events: dict[str, list[dict[str, Any]]] = {}
            audit_type = load_config("compliance")["audit_record_type"]
            for d in db.scalars(select(Document).where(Document.doc_type == audit_type, Document.is_deleted.is_(False))):
                meta = d.meta or {}
                for rid in {meta.get("related_record") or "", *_RECORD_ID.findall(meta.get("object") or "")} - {""}:
                    events.setdefault(rid, []).append({**meta, "_doc_id": d.id})
            _audit_cache.update(key=key, audit=events)
        return _audit_cache["audit"]


def mentions(db: Session, rid: str, exclude: set[str], limit: int = 4) -> list[tuple[Document, Section]]:
    """Sections of non-record documents (messages, meetings, interviews) that mention a record id."""
    rows = db.execute(select(Document, Section).join(Section, Section.document_id == Document.id)
                      .where(Section.plain_text.contains(rid), Section.is_deleted.is_(False),
                             Document.is_deleted.is_(False), Document.kind != "record",
                             Document.kind != "container")).all()
    seen, out = set(exclude), []
    for d, s in sorted(rows, key=lambda r: (r[1].parent_section_id is None, len(r[1].plain_text)), reverse=True):
        if d.id not in seen:
            seen.add(d.id)
            out.append((d, s))
    return out[:limit]


def rule_evidence(db: Session, rule: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rule:
        return None
    section = db.get(Section, rule["section"])
    doc = db.get(Document, rule["policy"])
    if not doc:
        return None
    return evidence_item(doc, section.id if section else None,
                         trim(section.plain_text if section else rule["rule_text"], 420), "rule", "The rule")


def first_section(db: Session, doc_id: str) -> Section | None:
    return db.scalars(select(Section).where(Section.document_id == doc_id)).first()


# ---------------------------------------------------------------- section names

def section_name(heading: str | None, doc_id: str, title: str | None = None) -> str | None:
    """A section heading worth showing (not the document's own title / header)."""
    h = (heading or "").strip()
    if not h or h == (title or "").strip() or h.startswith(doc_id) or h.lower() in _HIDDEN_SECTION_NAMES:
        return None
    return h


def fill_sections(db: Session, evidence: list[dict[str, Any]]) -> None:
    """Add the section heading to each evidence block (records and audit entries have none)."""
    for ev in evidence:
        if "section" in ev or not ev.get("section_id"):
            ev.setdefault("section", None)
            continue
        s = db.get(Section, ev["section_id"])
        ev["section"] = (section_name(s.heading if s else None, ev["doc_id"], ev.get("title"))
                         if ev.get("role") not in ("record", "audit", "practice") else None)
