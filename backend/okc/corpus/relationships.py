"""Relationship resolution: typed edges between documents.

Every edge records how it was found:
  explicit_link     - a Markdown link to another file
  metadata          - a header field such as `Supersedes:` / `Related policy:` / thread relationship
  reference_section - an ID listed in a "References" section
  structured_field  - a CSV reference column (e.g. related_decision_or_document)
  id_mention        - an identifier mentioned in running text (type `mentions`, weaker)
  filename_mention  - a file name mentioned in running text
  inferred          - produced by a documented rule; always carries evidence + confidence < 1

Deliberately NOT done: inferring `supersedes` from a newer date alone.
Corpus-level quality checks live in `okc.corpus.checks`.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from okc.common.text import short_hash
from okc.corpus.checks import run_corpus_checks
from okc.corpus.identifiers import IdentifierScheme
from okc.corpus.parsed import ParseIssue
from okc.db.models import Document

_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")
_FILENAME = re.compile(r"\b([\w\-]+\.(?:md|csv))\b")
_NEGATION = re.compile(r"\b(no one|nobody|not|never|no |rather than|without|hasn't|has not|wasn't)\b", re.I)
_SUPERSEDED_BY_TEXT = re.compile(r"superseded by\s+(?:the\s+)?(?P<target>.{1,160}?)(?=\.\s|\.$|\n|$)", re.I)
_INFORMAL_LINK = re.compile(r"^\s*(none|n/a|no\b|not\b)", re.I)
_EXTERNAL_HREF = re.compile(r"^[a-z]+://|^mailto:", re.I)
# When several declarations describe the same edge, the strongest origin represents it.
_ORIGIN_RANK = {"metadata": 6, "explicit_link": 5, "structured_field": 4, "reference_section": 4, "inferred": 3,
                "filename_mention": 2, "id_mention": 1}


@dataclass
class Edge:
    source: str
    target: str | None
    target_ref: str
    rel_type: str
    origin: str
    confidence: float = 1.0
    evidence: str = ""
    section_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return "rel_" + short_hash(f"{self.source}|{self.target or self.target_ref}|{self.rel_type}", 20)


@dataclass
class ResolutionResult:
    edges: list[Edge]
    issues: list[ParseIssue]
    doc_flags: dict[str, list[str]]
    reference_date: date | None


def sentence_around(text: str, needle: str, width: int = 220) -> str:
    i = text.find(needle)
    if i < 0:
        return text[:width]
    start = max(text.rfind("\n", 0, i), text.rfind(". ", 0, i))
    ends = [x for x in (text.find("\n", i), text.find(". ", i)) if x >= 0]
    end = min(ends) if ends else len(text)
    return text[start + 1 : end + 1].strip()[:width]


class _Corpus:
    """Lookup tables over the active documents."""

    def __init__(self, docs: list[Document]):
        self.by_id = {d.id: d for d in docs}
        self.by_ident: dict[str, list[Document]] = defaultdict(list)
        self.by_path: dict[str, list[Document]] = defaultdict(list)
        for d in docs:
            if d.identifier:
                self.by_ident[d.identifier].append(d)
            self.by_path[d.rel_path].append(d)
        self.by_basename: dict[str, list[str]] = defaultdict(list)
        for p in self.by_path:
            self.by_basename[posixpath.basename(p)].append(p)
        self.known_prefixes = {i.split("-")[0] for i in self.by_ident}

    def file_doc(self, path: str) -> Document | None:
        candidates = self.by_path.get(path, [])
        for kind in ("file", "container"):
            for c in candidates:
                if c.kind == kind:
                    return c
        return candidates[0] if len(candidates) == 1 else None

    def resolve_ident(self, ident: str) -> str | None:
        hits = self.by_ident.get(ident) or []
        return hits[0].id if hits else (ident if ident in self.by_id else None)


class RelationshipResolver:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.ids = IdentifierScheme(cfg)

    def resolve(self, docs: Iterable[Document]) -> ResolutionResult:
        docs = [d for d in docs if not d.is_deleted]
        corpus = _Corpus(docs)
        edges: list[Edge] = []
        issues: list[ParseIssue] = []

        for d in docs:
            header_sid = next((s.id for s in d.sections if s.section_role in ("header", "communication", "record")), None)
            self._metadata_edges(d, header_sid, corpus, edges, issues)
            self._thread_edges(d, header_sid, corpus, edges)
            self._csv_reference_edges(d, header_sid, corpus, edges, issues)
            self._section_edges(d, corpus, edges, issues)

        edges.extend(self._infer_records_decision(docs, corpus.by_ident))
        edges = self._dedupe(edges)

        checked = run_corpus_checks(docs, corpus.by_id, edges, self.cfg)
        issues.extend(checked.issues)
        unique: dict[tuple, ParseIssue] = {}
        for i in issues:
            unique.setdefault((i.issue_type, i.document_id, i.message), i)
        return ResolutionResult(edges=edges, issues=list(unique.values()), doc_flags=checked.doc_flags,
                                reference_date=checked.reference_date)

    # ------------------------------------------------------------ edge sources
    def _id_edges(self, src: Document, text: str, rel_type: str, origin: str, section_id: str | None,
                  corpus: _Corpus, edges: list[Edge], issues: list[ParseIssue],
                  reverse: bool = False, confidence: float = 1.0) -> None:
        for ident in self.ids.find_all(text):
            if ident == src.identifier or ident == src.id:
                continue
            target = corpus.resolve_ident(ident)
            evidence = sentence_around(text, ident)
            if target is None:
                prefix = ident.split("-")[0]
                if prefix in self.ids.prefixes or prefix in corpus.known_prefixes:
                    issues.append(ParseIssue("warning", "broken_reference",
                                             f"{src.id} references {ident}, which does not exist in the corpus",
                                             rel_path=src.rel_path, document_id=src.id, section_id=section_id,
                                             details={"reference": ident, "context": evidence, "origin": origin}))
                    edges.append(Edge(src.id, None, ident, rel_type, origin, confidence, evidence, section_id,
                                      {"unresolved": True}))
                else:
                    edges.append(Edge(src.id, None, ident, "external_reference", origin, confidence, evidence,
                                      section_id, {"unresolved": True, "external_entity": True}))
                continue
            if reverse:
                edges.append(Edge(target, src.id, src.id, rel_type, origin, confidence, evidence, section_id,
                                  {"declared_in": src.id}))
            else:
                edges.append(Edge(src.id, target, ident, rel_type, origin, confidence, evidence, section_id))

    def _metadata_edges(self, d, header_sid, corpus, edges, issues) -> None:
        meta = d.meta or {}
        for key, spec in self.cfg.get("metadata_relationships", {}).items():
            if not meta.get(key):
                continue
            value = str(meta[key])
            rel_type, confidence = spec["type"], 1.0
            if _INFORMAL_LINK.match(value) and rel_type not in ("related_to", "references"):
                # "None formal; operates alongside POL-03" is an informal association, not `implements`.
                rel_type, confidence = "related_to", 0.7
            self._id_edges(d, value, rel_type, "metadata", header_sid, corpus, edges, issues,
                           reverse=spec.get("direction") == "reverse", confidence=confidence)

    def _thread_edges(self, d, header_sid, corpus, edges) -> None:
        relationship = (d.meta or {}).get("thread_relationship")
        if not relationship:
            return
        relationship = str(relationship)
        for pattern in self.cfg.get("thread_relationship_patterns", []):
            for m in re.finditer(pattern["pattern"], relationship, re.I):
                target = corpus.resolve_ident(m.group("id"))
                edges.append(Edge(d.id, target, m.group("id"), pattern["type"], "metadata", 1.0, relationship,
                                  header_sid, {} if target else {"unresolved": True}))

    def _csv_reference_edges(self, d, header_sid, corpus, edges, issues) -> None:
        meta = d.meta or {}
        for column in meta.get("_csv_reference_columns", []) or []:
            if meta.get(column):
                self._id_edges(d, str(meta[column]), "references", "structured_field", header_sid, corpus, edges, issues)

    def _section_edges(self, d, corpus, edges, issues) -> None:
        for s in d.sections:
            if s.is_deleted or s.parent_section_id:
                continue
            for m in _LINK.finditer(s.text):
                self._link_edge(d, s, m, corpus, edges, issues)
            if s.section_role == "references":
                self._id_edges(d, s.plain_text, "references", "reference_section", s.id, corpus, edges, issues)
            elif d.kind != "record":
                self._id_edges(d, s.plain_text, "mentions", "id_mention", s.id, corpus, edges, issues, confidence=0.9)
            for fm in _FILENAME.finditer(s.plain_text):
                for path in corpus.by_basename.get(fm.group(1), []):
                    target = corpus.file_doc(path)
                    if target and target.id != d.id:
                        edges.append(Edge(d.id, target.id, fm.group(1), "references", "filename_mention", 0.9,
                                          sentence_around(s.plain_text, fm.group(1)), s.id))
            if d.status == "superseded":
                self._superseded_by_text_edges(d, s, corpus, edges)

    @staticmethod
    def _link_edge(d, s, m, corpus, edges, issues) -> None:
        href = m.group(2)
        if _EXTERNAL_HREF.match(href) or href.startswith("#"):
            return
        target_path = posixpath.normpath(posixpath.join(posixpath.dirname(d.rel_path), href.split("#")[0]))
        target = corpus.file_doc(target_path)
        evidence = sentence_around(s.plain_text, m.group(1))
        if target is None:
            issues.append(ParseIssue("warning", "broken_link", f"Link to '{href}' does not resolve",
                                     rel_path=d.rel_path, document_id=d.id, section_id=s.id,
                                     details={"href": href, "resolved_path": target_path}))
            edges.append(Edge(d.id, None, href, "references", "explicit_link", 1.0, evidence, s.id, {"unresolved": True}))
        elif target.id != d.id:
            edges.append(Edge(d.id, target.id, href, "references", "explicit_link", 1.0, evidence, s.id))

    def _superseded_by_text_edges(self, d, s, corpus, edges) -> None:
        """'Superseded by X' stated in the text of a superseded document."""
        for m in _SUPERSEDED_BY_TEXT.finditer(s.plain_text):
            text = m.group("target")
            targets = [corpus.resolve_ident(i) for i in self.ids.find_all(text)]
            for filename in _FILENAME.findall(text):
                for path in corpus.by_basename.get(filename, []):
                    doc = corpus.file_doc(path)
                    if doc:
                        targets.append(doc.id)
            for t in {t for t in targets if t and t != d.id}:
                edges.append(Edge(t, d.id, d.id, "supersedes", "inferred", 0.85, m.group(0)[:220], s.id,
                                  {"rule": "superseded-by statement in superseded document"}))

    # ------------------------------------------------------------ inference
    def _infer_records_decision(self, docs: list[Document], by_ident) -> list[Edge]:
        """A meeting 'records' the decision about a policy / procedure it names in a decision section."""
        inference = self.cfg.get("inference", {})
        roles = set(inference.get("records_decision_section_roles", ["decision", "action"]))
        out: list[Edge] = []
        governed = {d.id: d for d in docs if d.doc_type in ("policy", "procedure")}
        for meeting in (d for d in docs if d.doc_type == "meeting_note"):
            for s in meeting.sections:
                if s.section_role not in roles or s.parent_section_id:
                    continue
                for line in s.plain_text.split("\n"):
                    for ident in self.ids.find_all(line):
                        target = (by_ident.get(ident) or [None])[0]
                        if target is None or target.id not in governed:
                            continue
                        if _NEGATION.search(line):
                            continue  # "No one was assigned to update PROC-03" is not a recorded decision
                        out.append(Edge(meeting.id, target.id, ident, "records_decision", "inferred",
                                        float(inference.get("records_decision_confidence", 0.6)), line.strip()[:220], s.id,
                                        {"rule": f"identifier mentioned in meeting '{s.heading}' section"}))
            if meeting.doc_date:  # a governed document cites the meeting's date next to the word "meeting"
                iso = meeting.doc_date.isoformat()
                for g in governed.values():
                    for s in g.sections:
                        if iso in s.plain_text:
                            line = sentence_around(s.plain_text, iso)
                            if "meeting" in line.lower():
                                out.append(Edge(meeting.id, g.id, iso, "records_decision", "inferred",
                                                float(inference.get("records_decision_date_match_confidence", 0.8)),
                                                line, s.id, {"rule": "document cites a meeting on the meeting's date"}))
        return out

    @staticmethod
    def _dedupe(edges: list[Edge]) -> list[Edge]:
        """One edge per (source, target, type). Every independent declaration is kept as evidence."""
        groups: dict[str, list[Edge]] = defaultdict(list)
        for e in edges:
            groups[e.id].append(e)
        kept: dict[str, Edge] = {}
        for gid, group in groups.items():
            group.sort(key=lambda e: (-_ORIGIN_RANK.get(e.origin, 0), -e.confidence))
            best = group[0]
            if len(group) > 1:
                best.attributes = {**best.attributes,
                                   "origins": sorted({g.origin for g in group}),
                                   "declarations": [{"origin": g.origin, "evidence": g.evidence, "section_id": g.section_id,
                                                     "confidence": g.confidence} for g in group][:5]}
                best.confidence = max(g.confidence for g in group)
            kept[gid] = best
        # A `mentions` edge adds nothing when a stronger typed edge exists for the same pair.
        typed_pairs = {(e.source, e.target) for e in kept.values() if e.rel_type != "mentions" and e.target}
        return [e for e in kept.values() if not (e.rel_type == "mentions" and (e.source, e.target) in typed_pairs)]
