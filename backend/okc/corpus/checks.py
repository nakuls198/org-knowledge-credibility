"""Corpus-level quality checks run after relationships are resolved.

Cycles in supersession / dependency chains, staleness, review dates, duplicate content, identity
clashes and missing metadata. Each check reports `ParseIssue`s; some also flag documents.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from okc.common.text import jaccard, shingles
from okc.corpus.parsed import ParseIssue
from okc.db.models import Document

if TYPE_CHECKING:  # Edge lives next to the resolver; imported for typing only to avoid a cycle
    from okc.corpus.relationships import Edge

_TRIVIAL_TEXT = re.compile(r"^(none|not documented|n/?a|none documented|none specified)[^a-z]*", re.I)
_STRICT_CYCLE_TYPES = {"supersedes", "depends_on", "follows_up"}
_LOOSE_CYCLE_TYPES = {"references", "related_to", "implements"}
_NEAR_DUPLICATE_CEILING = 0.999  # at or above this the text is identical, which the exact check reports


@dataclass
class CheckResult:
    issues: list[ParseIssue] = field(default_factory=list)
    doc_flags: dict[str, list[str]] = field(default_factory=dict)
    reference_date: date | None = None


def run_corpus_checks(docs: list[Document], by_id: dict[str, Document], edges: list["Edge"],
                      cfg: dict[str, Any]) -> CheckResult:
    issues: list[ParseIssue] = []
    flags: dict[str, list[str]] = defaultdict(list)
    _check_cycles(edges, issues)
    ref_date = reference_date(docs)
    _temporal_flags(docs, by_id, edges, issues, flags, ref_date, cfg)
    _duplicates(docs, issues, cfg)
    _identity(docs, issues)
    _metadata_gaps(docs, issues, cfg)
    return CheckResult(issues=issues, doc_flags=dict(flags), reference_date=ref_date)


def reference_date(docs: list[Document]) -> date | None:
    """Latest dated record in the corpus: used as 'now' for staleness checks (no hard-coded date)."""
    dates = [d.doc_date for d in docs if d.doc_date]
    return max(dates) if dates else None


# ---------------------------------------------------------------- cycles

def _cycles(edges: list["Edge"], types: set[str]) -> list[list[str]]:
    """Strongly connected components with more than one document (Tarjan)."""
    graph: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        if e.rel_type in types and e.target and e.source != e.target:
            graph[e.source].add(e.target)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = [0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, ()):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            component = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            if len(component) > 1:
                components.append(sorted(component))

    for v in list(graph):
        if v not in index:
            visit(v)
    return components


def _check_cycles(edges: list["Edge"], issues: list[ParseIssue]) -> None:
    for comp in _cycles(edges, _STRICT_CYCLE_TYPES):
        issues.append(ParseIssue("error", "circular_dependency",
                                 f"Circular supersession/dependency chain: {' -> '.join(comp)}",
                                 details={"documents": comp}))
    for comp in _cycles(edges, _LOOSE_CYCLE_TYPES):
        issues.append(ParseIssue("info", "circular_reference",
                                 f"Documents reference each other in a cycle: {', '.join(comp)}",
                                 details={"documents": comp}))


# ---------------------------------------------------------------- time

def _temporal_flags(docs, by_id, edges, issues, flags, ref_date, cfg) -> None:
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
    if not cfg.get("inference", {}).get("stale_implementation_check", True):
        return
    # A procedure last updated before the policy it implements took effect may be out of date.
    for e in edges:
        if e.rel_type != "implements" or not e.target:
            continue
        procedure, policy = by_id.get(e.source), by_id.get(e.target)
        if not procedure or not policy:
            continue
        policy_effective = policy.effective_date or policy.doc_date
        procedure_updated = procedure.doc_date
        if policy_effective and procedure_updated and procedure_updated < policy_effective:
            flags[procedure.id].append("potentially_stale")
            msg = (f"{procedure.id} (last updated {procedure_updated}) implements {policy.id}, which took effect "
                   f"{policy_effective}; the procedure may not reflect the current policy")
            issues.append(ParseIssue("warning", "stale_implementation", msg, rel_path=procedure.rel_path,
                                     document_id=procedure.id,
                                     details={"policy": policy.id, "policy_effective": str(policy_effective),
                                              "procedure_updated": str(procedure_updated)}))
            e.attributes["stale"] = True
            e.attributes["stale_reason"] = msg


# ---------------------------------------------------------------- duplicates and identity

def _duplicates(docs: list[Document], issues: list[ParseIssue], cfg: dict[str, Any]) -> None:
    min_chars = int(cfg.get("near_duplicate_min_chars", 120))
    threshold = float(cfg.get("near_duplicate_jaccard", 0.6))
    sections = [(d, s) for d in docs for s in d.sections
                if not s.is_deleted and not s.parent_section_id and s.section_role not in ("header", "record")
                and len(s.plain_text) >= 20 and not _TRIVIAL_TEXT.match(s.plain_text.strip())]

    by_text: dict[str, list[str]] = defaultdict(list)
    for _, s in sections:
        by_text[re.sub(r"\W+", " ", s.plain_text.lower()).strip()].append(s.id)
    for ids in by_text.values():
        if len(ids) > 1 and len({i.split(":")[0] for i in ids}) > 1:
            issues.append(ParseIssue("info", "duplicate_content", f"Identical text in {len(ids)} sections",
                                     section_id=ids[0], details={"sections": ids}))

    long_sections = [(d, s, shingles(s.plain_text)) for d, s in sections if len(s.plain_text) >= min_chars]
    for i in range(len(long_sections)):
        for j in range(i + 1, len(long_sections)):
            d1, s1, sh1 = long_sections[i]
            d2, s2, sh2 = long_sections[j]
            if d1.id == d2.id:
                continue
            similarity = jaccard(sh1, sh2)
            if threshold <= similarity < _NEAR_DUPLICATE_CEILING:
                issues.append(ParseIssue("info", "near_duplicate_content",
                                         f"{s1.id} and {s2.id} are near-duplicates (Jaccard {similarity:.2f})",
                                         section_id=s1.id,
                                         details={"sections": [s1.id, s2.id], "jaccard": round(similarity, 3)}))


def _identity(docs: list[Document], issues: list[ParseIssue]) -> None:
    by_ident: dict[str, list[Document]] = defaultdict(list)
    for d in docs:
        if d.identifier:
            by_ident[d.identifier].append(d)
    for ident, group in by_ident.items():
        if len(group) > 1:
            issues.append(ParseIssue("warning", "ambiguous_identity",
                                     f"Identifier {ident} is claimed by {len(group)} documents",
                                     document_id=group[0].id,
                                     details={"documents": [x.id for x in group], "paths": [x.rel_path for x in group]}))
    by_title: dict[str, list[Document]] = defaultdict(list)
    for d in docs:
        if d.kind == "file":
            by_title[re.sub(r"\W+", " ", re.sub(r"^[A-Z]+-\d+\s*\W*", "", d.title)).strip().lower()].append(d)
    for title, group in by_title.items():
        if len(group) > 1 and title:
            issues.append(ParseIssue("info", "similar_titles",
                                     f"{len(group)} documents share the title '{title}' - check which one governs",
                                     details={"documents": [x.id for x in group]}))


# ---------------------------------------------------------------- metadata

def _metadata_gaps(docs: list[Document], issues: list[ParseIssue], cfg: dict[str, Any]) -> None:
    """Fields each document type is expected to carry (`expected_metadata` in the ingestion config)."""
    expected = cfg.get("expected_metadata", {})
    undated_exempt = set(cfg.get("undated_exempt_doc_types", []))
    for d in docs:
        rule = expected.get(d.doc_type, {})
        missing = [f for f in rule.get("fields", []) if not (d.meta or {}).get(f)]
        if missing:
            issues.append(ParseIssue(rule.get("severity", "info"), "missing_metadata",
                                     f"{d.id} ({d.doc_type}) has no {', '.join(missing)}",
                                     rel_path=d.rel_path, document_id=d.id, details={"missing": missing}))
        if d.doc_date is None and d.kind != "container" and d.doc_type not in undated_exempt:
            issues.append(ParseIssue("info", "undated_document", f"{d.id} has no usable date",
                                     rel_path=d.rel_path, document_id=d.id))
