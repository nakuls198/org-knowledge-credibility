"""Candidate claim extraction from source documents (separate, testable pipeline).

Proposes claims a user may want to assess. Candidates are NEVER treated as established facts: they
carry their origin (document, section, role) and the reason they were proposed, and become claims
only when a user chooses to assess them.

Sources considered (deterministic rules, configurable section roles):
  * requirement / procedure-step / responsibility sections of policies and procedures
    -> normative claims ("what the rule says")
  * decision sections of meeting notes -> decision claims, flagged when the meeting text marks them
    as proposals, pilots or deferrals
Meeting discussion, interviews and chat messages are deliberately NOT mined: a sentence someone said
is not an organisational fact.
"""

from __future__ import annotations

import re
from typing import Iterable

from pydantic import BaseModel

from okc.db.models import Document

ROLE_KIND = {"requirement": "normative", "procedure_step": "normative", "responsibility": "normative",
             "decision": "decision"}
_MINED_DOC_TYPES = ("policy", "procedure", "meeting_note")
_ENUMERATION = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*")
_TENTATIVE = re.compile(r"\b(pilot|propos|draft|defer|not adopted|no decision|flag(?:ged)? for|park)\w*", re.I)
_HAS_VERB = re.compile(r"\b(require|must|shall|is|are|approv|complet|assign|record|escalat|review|cap|raise|migrat|"
                       r"offer|pause|document|own|accountable|responsible)\w*\b", re.I)
_MIN_CHARS, _MAX_CHARS = 40, 260


class CandidateClaim(BaseModel):
    text: str
    kind: str  # normative | decision
    document_id: str
    document_title: str
    section_id: str
    section_heading: str
    doc_status: str
    reason: str
    caution: str | None = None


def _sentences(text: str) -> list[str]:
    out = []
    for line in text.split("\n"):
        line = _ENUMERATION.sub("", line).strip()
        if not line:
            continue
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line):
            s = s.strip()
            if _MIN_CHARS <= len(s) <= _MAX_CHARS and _HAS_VERB.search(s) and not s.endswith(":"):
                out.append(s if s.endswith((".", "!", "?")) else s + ".")
    return out


def _caution(doc: Document, section_text: str, kind: str) -> str | None:
    caution = None
    if doc.status in ("draft", "superseded"):
        caution = f"Source document is {doc.status}; the statement may not describe current practice."
    elif _TENTATIVE.search(section_text) and kind == "decision":
        caution = "The meeting record contains tentative language (pilot / proposal / deferral)."
    if "potentially_stale" in (doc.flags or []):
        caution = (caution + " " if caution else "") + "Procedure may be out of date."
    return caution


def extract_candidates(docs: Iterable[Document], limit: int = 200) -> list[CandidateClaim]:
    out: list[CandidateClaim] = []
    seen: set[str] = set()
    for d in docs:
        if d.is_deleted or d.kind == "record" or d.doc_type not in _MINED_DOC_TYPES:
            continue
        for s in d.sections:
            kind = ROLE_KIND.get(s.section_role)
            if s.is_deleted or s.parent_section_id or not kind:
                continue
            if d.doc_type == "meeting_note" and kind != "decision":
                continue
            for sentence in _sentences(s.plain_text):
                key = re.sub(r"\W+", " ", sentence.lower()).strip()
                if key in seen:
                    continue
                seen.add(key)
                out.append(CandidateClaim(
                    text=sentence, kind=kind, document_id=d.id, document_title=d.title, section_id=s.id,
                    section_heading=s.heading, doc_status=d.status,
                    reason=f"{s.section_role.replace('_', ' ')} section of a {d.doc_type.replace('_', ' ')}",
                    caution=_caution(d, s.plain_text, kind)))
                if len(out) >= limit:
                    return out
    return out
