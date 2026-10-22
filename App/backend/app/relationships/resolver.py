"""Relationship resolution and corpus-level quality checks.

Every edge records how it was found:
  explicit_link     - a Markdown link to another file
  metadata          - a header field such as `Supersedes:` / `Related policy:` / thread relationship
  reference_section - an ID listed in a "References" section
  structured_field  - a CSV reference column (e.g. related_decision_or_document)
  id_mention        - an identifier mentioned in running text (type `mentions`, weaker)
  filename_mention  - a file name mentioned in running text
  inferred          - produced by a documented rule; always carries evidence + confidence < 1

Deliberately NOT done: inferring `supersedes` from a newer date alone.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from app.core.text import jaccard, shingles, short_hash
from app.database.models import Document
from app.ingestion.markdown_parser import IdentifierScheme
from app.ingestion.parsed import ParseIssue

_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")
_FILENAME = re.compile(r"\b([\w\-]+\.(?:md|csv))\b")
_NEGATION = re.compile(r"\b(no one|nobody|not|never|no |rather than|without|hasn't|has not|wasn't)\b", re.I)
_SUPERSEDED_BY_TEXT = re.compile(r"superseded by\s+(?:the\s+)?(?P<target>.{1,160}?)(?=\.\s|\.$|\n|$)", re.I)
_INFORMAL_LINK = re.compile(r"^\s*(none|n/a|no\b|not\b)", re.I)
_ORIGIN_RANK = {"metadata": 6, "explicit_link": 5, "structured_field": 4, "reference_section": 4, "inferred": 3,
                "filename_mention": 2, "id_mention": 1}
_TRIVIAL_TEXT = re.compile(r"^(none|not documented|n/?a|none documented|none specified)[^a-z]*", re.I)


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


def _sentence_around(text: str, needle: str, width: int = 220) -> str:
    i = text.find(needle)
    if i < 0:
        return text[:width]
    s = max(text.rfind("\n", 0, i), text.rfind(". ", 0, i))
    e_candidates = [x for x in (text.find("\n", i), text.find(". ", i)) if x >= 0]
    e = min(e_candidates) if e_candidates else len(text)
    return text[s + 1 : e + 1].strip()[:width]


class RelationshipResolver:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.ids = IdentifierScheme(cfg)

    def resolve(self, docs: Iterable[Document]) -> ResolutionResult:
        docs = [d for d in docs if not d.is_deleted]
        by_id = {d.id: d for d in docs}
        by_ident: dict[str, list[Document]] = defaultdict(list)
        for d in docs:
            if d.identifier:
                by_ident[d.identifier].append(d)
        by_path: dict[str, list[Document]] = defaultdict(list)
        for d in docs:
            by_path[d.rel_path].append(d)
        by_basename: dict[str, list[str]] = defaultdict(list)
        for p in by_path:
            by_basename[posixpath.basename(p)].append(p)

        edges: list[Edge] = []
        issues: list[ParseIssue] = []
        flags: dict[str, list[str]] = defaultdict(list)
        known_doc_prefixes = {i.split("-")[0] for i in by_ident}

        def file_doc(path: str) -> Document | None:
            cands = by_path.get(path, [])
            for kind in ("file", "container"):
                for c in cands:
                    if c.kind == kind:
                        return c
            return cands[0] if len(cands) == 1 else None

        def resolve_ident(ident: str) -> str | None:
            hits = by_ident.get(ident) or []
            return hits[0].id if hits else (ident if ident in by_id else None)

        def add_id_edges(src: Document, text: str, rel_type: str, origin: str, section_id: str | None,
                         reverse: bool = False, confidence: float = 1.0):
            for ident in self.ids.find_all(text):
                if ident == src.identifier or ident == src.id:
                    continue
                tgt = resolve_ident(ident)
                ev = _sentence_around(text, ident)
                if tgt is None:
                    prefix = ident.split("-")[0]
                    if prefix in self.ids.prefixes or prefix in known_doc_prefixes:
                        issues.append(ParseIssue("warning", "broken_reference",
                                                 f"{src.id} references {ident}, which does not exist in the corpus",
                                                 rel_path=src.rel_path, document_id=src.id, section_id=section_id,
                                                 details={"reference": ident, "context": ev, "origin": origin}))
                        edges.append(Edge(src.id, None, ident, rel_type, origin, confidence, ev, section_id,
                                          {"unresolved": True}))
                    else:
                        edges.append(Edge(src.id, None, ident, "external_reference", origin, confidence, ev,
                                          section_id, {"unresolved": True, "external_entity": True}))
                    continue
                if reverse:
                    edges.append(Edge(tgt, src.id, src.id, rel_type, origin, confidence, ev, section_id,
                                      {"declared_in": src.id}))
                else:
                    edges.append(Edge(src.id, tgt, ident, rel_type, origin, confidence, ev, section_id))

        meta_rel = self.cfg.get("metadata_relationships", {})
        thread_pats = [(re.compile(p["pattern"], re.I), p["type"]) for p in self.cfg.get("thread_relationship_patterns", [])]

        for d in docs:
            meta = d.meta or {}
            header_sid = next((s.id for s in d.sections if s.section_role in ("header", "communication", "record")), None)
            # 1. metadata-declared relationships
            for key, spec in meta_rel.items():
                if meta.get(key):
                    value = str(meta[key])
                    rel_type, conf = spec["type"], 1.0
                    if _INFORMAL_LINK.match(value) and rel_type not in ("related_to", "references"):
                        # "None formal; operates alongside POL-03" is an informal association, not `implements`
                        rel_type, conf = "related_to", 0.7
                    add_id_edges(d, value, rel_type, "metadata", header_sid,
                                 reverse=spec.get("direction") == "reverse", confidence=conf)
            # 2. thread relationship phrases (communications)
            if meta.get("thread_relationship"):
                tr = str(meta["thread_relationship"])
                for pat, typ in thread_pats:
                    for m in pat.finditer(tr):
                        tgt = resolve_ident(m.group("id"))
                        edges.append(Edge(d.id, tgt, m.group("id"), typ, "metadata", 1.0, tr, header_sid,
                                          {} if tgt else {"unresolved": True}))
            # 3. CSV reference columns
            for col in meta.get("_csv_reference_columns", []) or []:
                if meta.get(col):
                    add_id_edges(d, str(meta[col]), "references", "structured_field", header_sid)
            # 4. section-level: links, reference sections, mentions, filenames
            for s in d.sections:
                if s.is_deleted or s.parent_section_id:
                    continue
                for m in _LINK.finditer(s.text):
                    href = m.group(2)
                    if re.match(r"^[a-z]+://|^mailto:", href, re.I) or href.startswith("#"):
                        continue
                    target_path = posixpath.normpath(posixpath.join(posixpath.dirname(d.rel_path), href.split("#")[0]))
                    tdoc = file_doc(target_path)
                    ev = _sentence_around(s.plain_text, m.group(1))
                    if tdoc is None:
                        issues.append(ParseIssue("warning", "broken_link", f"Link to '{href}' does not resolve",
                                                 rel_path=d.rel_path, document_id=d.id, section_id=s.id,
                                                 details={"href": href, "resolved_path": target_path}))
                        edges.append(Edge(d.id, None, href, "references", "explicit_link", 1.0, ev, s.id, {"unresolved": True}))
                    elif tdoc.id != d.id:
                        edges.append(Edge(d.id, tdoc.id, href, "references", "explicit_link", 1.0, ev, s.id))
                if s.section_role == "references":
                    add_id_edges(d, s.plain_text, "references", "reference_section", s.id)
                elif d.kind != "record":
                    add_id_edges(d, s.plain_text, "mentions", "id_mention", s.id, confidence=0.9)
                for fm in _FILENAME.finditer(s.plain_text):
                    for p in by_basename.get(fm.group(1), []):
                        tdoc = file_doc(p)
                        if tdoc and tdoc.id != d.id:
                            edges.append(Edge(d.id, tdoc.id, fm.group(1), "references", "filename_mention", 0.9,
                                              _sentence_around(s.plain_text, fm.group(1)), s.id))
                # "Superseded by X" stated in text of a superseded document
                if d.status == "superseded":
                    for m in _SUPERSEDED_BY_TEXT.finditer(s.plain_text):
                        tgt_txt = m.group("target")
                        targets = [resolve_ident(i) for i in self.ids.find_all(tgt_txt)]
                        targets += [file_doc(p).id for fn in _FILENAME.findall(tgt_txt) for p in by_basename.get(fn, [])
                                    if file_doc(p)]
                        for t in {t for t in targets if t and t != d.id}:
                            edges.append(Edge(t, d.id, d.id, "supersedes", "inferred", 0.85, m.group(0)[:220], s.id,
                                              {"rule": "superseded-by statement in superseded document"}))

        edges.extend(self._infer_records_decision(docs, by_ident))
        edges = self._dedupe(edges)
        self._check_cycles(edges, issues)
        ref_date = self._reference_date(docs)
        self._temporal_flags(docs, by_id, edges, issues, flags, ref_date)
        self._duplicates(docs, issues)
        self._identity(docs, issues)
        self._metadata_gaps(docs, issues)
        unique: dict[tuple, ParseIssue] = {}
        for i in issues:
            unique.setdefault((i.issue_type, i.document_id, i.message), i)
        return ResolutionResult(edges=edges, issues=list(unique.values()), doc_flags=dict(flags), reference_date=ref_date)

    # --------------------------------------------------------------- inference
    def _infer_records_decision(self, docs: list[Document], by_ident) -> list[Edge]:
        inf = self.cfg.get("inference", {})
        roles = set(inf.get("records_decision_section_roles", ["decision", "action"]))
        out: list[Edge] = []
        meetings = [d for d in docs if d.doc_type == "meeting_note"]
        governed = {d.id: d for d in docs if d.doc_type in ("policy", "procedure")}
        for mtg in meetings:
            for s in mtg.sections:
                if s.section_role not in roles or s.parent_section_id:
                    continue
                for line in s.plain_text.split("\n"):
                    for ident in self.ids.find_all(line):
                        tgt = (by_ident.get(ident) or [None])[0]
                        if tgt is None or tgt.id not in governed:
                            continue
                        if _NEGATION.search(line):
                            continue  # "No one was assigned to update PROC-03" is not a recorded decision
                        out.append(Edge(mtg.id, tgt.id, ident, "records_decision", "inferred",
                                        float(inf.get("records_decision_confidence", 0.6)), line.strip()[:220], s.id,
                                        {"rule": f"identifier mentioned in meeting '{s.heading}' section"}))
            # date match: governed doc cites the meeting date next to the word "meeting"
            if mtg.doc_date:
                iso = mtg.doc_date.isoformat()
                for g in governed.values():
                    for s in g.sections:
                        if iso in s.plain_text:
                            line = _sentence_around(s.plain_text, iso)
                            if "meeting" in line.lower():
                                out.append(Edge(mtg.id, g.id, iso, "records_decision", "inferred",
                                                float(inf.get("records_decision_date_match_confidence", 0.8)),
                                                line, s.id, {"rule": "document cites a meeting on the meeting's date"}))
        return out

    @staticmethod
    def _dedupe(edges: list[Edge]) -> list[Edge]:
        """One edge per (source, target, type). Every independent declaration is kept as evidence."""
        groups: dict[str, list[Edge]] = defaultdict(list)
        for e in edges:
            groups[e.id].append(e)
        seen: dict[str, Edge] = {}
        for gid, group in groups.items():
            group.sort(key=lambda e: (-_ORIGIN_RANK.get(e.origin, 0), -e.confidence))
            best = group[0]
            if len(group) > 1:
                best.attributes = {**best.attributes,
                                   "origins": sorted({g.origin for g in group}),
                                   "declarations": [{"origin": g.origin, "evidence": g.evidence, "section_id": g.section_id,
                                                     "confidence": g.confidence} for g in group][:5]}
                best.confidence = max(g.confidence for g in group)
            seen[gid] = best
        # A `mentions` edge adds nothing when a stronger typed edge exists for the same pair.
        typed_pairs = {(e.source, e.target) for e in seen.values() if e.rel_type != "mentions" and e.target}
        return [e for e in seen.values() if not (e.rel_type == "mentions" and (e.source, e.target) in typed_pairs)]

    # ------------------------------------------------------------------ checks
    @staticmethod
    def _check_cycles(edges: list[Edge], issues: list[ParseIssue]) -> None:
        def cycles_for(types: set[str]) -> list[list[str]]:
            g: dict[str, set[str]] = defaultdict(set)
            for e in edges:
                if e.rel_type in types and e.target and e.source != e.target:
                    g[e.source].add(e.target)
            # Tarjan SCC
            index, low, on, stack, out = {}, {}, set(), [], []
            counter = [0]

            def strong(v):
                index[v] = low[v] = counter[0]
                counter[0] += 1
                stack.append(v)
                on.add(v)
                for w in g.get(v, ()):
                    if w not in index:
                        strong(w)
                        low[v] = min(low[v], low[w])
                    elif w in on:
                        low[v] = min(low[v], index[w])
                if low[v] == index[v]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    if len(comp) > 1:
                        out.append(sorted(comp))

            for v in list(g):
                if v not in index:
                    strong(v)
            return out

        for comp in cycles_for({"supersedes", "depends_on", "follows_up"}):
            issues.append(ParseIssue("error", "circular_dependency",
                                     f"Circular supersession/dependency chain: {' -> '.join(comp)}",
                                     details={"documents": comp}))
        for comp in cycles_for({"references", "related_to", "implements"}):
            issues.append(ParseIssue("info", "circular_reference",
                                     f"Documents reference each other in a cycle: {', '.join(comp)}",
                                     details={"documents": comp}))

    @staticmethod
    def _reference_date(docs: list[Document]) -> date | None:
        """Latest dated record in the corpus - used as 'now' for staleness checks (no hard-coded date)."""
        dates = [d.doc_date for d in docs if d.doc_date]
        return max(dates) if dates else None

    def _temporal_flags(self, docs, by_id, edges, issues, flags, ref_date) -> None:
        for d in docs:
            if d.review_date and ref_date and d.review_date < ref_date and d.status != "superseded":
                flags[d.id].append("review_overdue")
                issues.append(ParseIssue("info", "review_overdue",
                                         f"{d.id} review date {d.review_date} is before the corpus reference date {ref_date}",
                                         rel_path=d.rel_path, document_id=d.id))
            if d.status == "superseded":
                flags[d.id].append("superseded")
            if d.status == "draft":
                flags[d.id].append("draft_not_in_force")
        if not self.cfg.get("inference", {}).get("stale_implementation_check", True):
            return
        # Effective dates of each governing document (incl. via supersedes chains)
        for e in edges:
            if e.rel_type != "implements" or not e.target:
                continue
            proc, pol = by_id.get(e.source), by_id.get(e.target)
            if not proc or not pol:
                continue
            pol_eff = pol.effective_date or pol.doc_date
            proc_upd = proc.doc_date
            if pol_eff and proc_upd and proc_upd < pol_eff:
                flags[proc.id].append("potentially_stale")
                msg = (f"{proc.id} (last updated {proc_upd}) implements {pol.id}, which took effect {pol_eff}; "
                       f"the procedure may not reflect the current policy")
                issues.append(ParseIssue("warning", "stale_implementation", msg, rel_path=proc.rel_path,
                                         document_id=proc.id, details={"policy": pol.id, "policy_effective": str(pol_eff),
                                                                       "procedure_updated": str(proc_upd)}))
                e.attributes["stale"] = True
                e.attributes["stale_reason"] = msg

    def _duplicates(self, docs: list[Document], issues: list[ParseIssue]) -> None:
        min_chars = int(self.cfg.get("near_duplicate_min_chars", 120))
        thr = float(self.cfg.get("near_duplicate_jaccard", 0.6))
        secs = [(d, s) for d in docs for s in d.sections
                if not s.is_deleted and not s.parent_section_id and s.section_role not in ("header", "record")
                and len(s.plain_text) >= 20 and not _TRIVIAL_TEXT.match(s.plain_text.strip())]
        by_hash: dict[str, list[str]] = defaultdict(list)
        for d, s in secs:
            by_hash[re.sub(r"\W+", " ", s.plain_text.lower()).strip()].append(s.id)
        for ids in by_hash.values():
            if len(ids) > 1 and len({i.split(":")[0] for i in ids}) > 1:
                issues.append(ParseIssue("info", "duplicate_content", f"Identical text in {len(ids)} sections",
                                         section_id=ids[0], details={"sections": ids}))
        long_secs = [(d, s, shingles(s.plain_text)) for d, s in secs if len(s.plain_text) >= min_chars]
        for i in range(len(long_secs)):
            for j in range(i + 1, len(long_secs)):
                d1, s1, sh1 = long_secs[i]
                d2, s2, sh2 = long_secs[j]
                if d1.id == d2.id:
                    continue
                sim = jaccard(sh1, sh2)
                if thr <= sim < 0.999:
                    issues.append(ParseIssue("info", "near_duplicate_content",
                                             f"{s1.id} and {s2.id} are near-duplicates (Jaccard {sim:.2f})",
                                             section_id=s1.id, details={"sections": [s1.id, s2.id], "jaccard": round(sim, 3)}))

    @staticmethod
    def _identity(docs: list[Document], issues: list[ParseIssue]) -> None:
        by_ident: dict[str, list[Document]] = defaultdict(list)
        for d in docs:
            if d.identifier:
                by_ident[d.identifier].append(d)
        for ident, ds in by_ident.items():
            if len(ds) > 1:
                issues.append(ParseIssue("warning", "ambiguous_identity",
                                         f"Identifier {ident} is claimed by {len(ds)} documents",
                                         document_id=ds[0].id, details={"documents": [x.id for x in ds],
                                                                        "paths": [x.rel_path for x in ds]}))
        by_title: dict[str, list[Document]] = defaultdict(list)
        for d in docs:
            if d.kind == "file":
                by_title[re.sub(r"\W+", " ", re.sub(r"^[A-Z]+-\d+\s*\W*", "", d.title)).strip().lower()].append(d)
        for title, ds in by_title.items():
            if len(ds) > 1 and title:
                issues.append(ParseIssue("info", "similar_titles",
                                         f"{len(ds)} documents share the title '{title}' - check which one governs",
                                         details={"documents": [x.id for x in ds]}))

    @staticmethod
    def _metadata_gaps(docs: list[Document], issues: list[ParseIssue]) -> None:
        expect = {
            "policy": ["owner", "version", "approval_status", "effective_date"],
            "procedure": ["owner", "last_updated"],
            "meeting_note": ["date", "participants"],
            "interview": ["interview_date"],
            "communication": ["timestamp"],
        }
        for d in docs:
            missing = [f for f in expect.get(d.doc_type, []) if not (d.meta or {}).get(f)]
            if missing:
                issues.append(ParseIssue("info" if d.doc_type != "policy" else "warning", "missing_metadata",
                                         f"{d.id} ({d.doc_type}) has no {', '.join(missing)}",
                                         rel_path=d.rel_path, document_id=d.id, details={"missing": missing}))
            if d.doc_date is None and d.kind != "container" and d.doc_type not in ("org_reference",):
                issues.append(ParseIssue("info", "undated_document", f"{d.id} has no usable date",
                                         rel_path=d.rel_path, document_id=d.id))
