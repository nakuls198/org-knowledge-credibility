"""Evidence gathering for a claim.

Channels (each candidate remembers which channels found it and at what rank):
  claim       BM25 (or hybrid) search with the claim text + synonym expansion
  counter     a deliberately contrary query (claim key terms + exception/workaround wording) so that
              disconfirming passages are retrieved, not only confirming ones
  change      "was the tool / system the claim names replaced or retired?"
  identifier  sections mentioning / belonging to identifiers named in the claim
  linked      sections of documents related (supersedes, implements, records_decision ...) to the top
              retrieved documents
  semantic    vector search (only when embeddings are enabled and mode = hybrid)
Then: evidence units are selected inside each section, exact / near-duplicate units are collapsed
(duplicates are recorded on the kept unit), and the top-N units are returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from okc.claims.analysis import ClaimAnalysis, expand_query
from okc.common.text import jaccard, shingles
from okc.db.models import Document, Relationship, Section
from okc.evidence.schemas import EvidenceContext
from okc.evidence.spans import best_units, term_weights, unit_relevance
from okc.search.schemas import SearchFilters, SearchRequest
from okc.search.service import SearchService

_PROPER_NAME = re.compile(r"(?<!^)\b([A-Z][a-zA-Z0-9]{2,})\b")
_DEFAULT_CHANGE_TERMS = "migrate migrated moved replaced archived retired switched"
_TOP_DOCUMENTS_FOR_LINKS = 8
_MAX_UNITS_PER_DOCUMENT = 3
_WHOLE_RECORD_CHARS = 1200
_RELEVANCE_WEIGHT, _CHANNEL_WEIGHT = 0.75, 0.25  # unit relevance vs retrieval-channel rank


@dataclass
class Candidate:
    section_id: str
    channels: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)

    def best_rank(self) -> int:
        return min(self.channels.values()) if self.channels else 999


@dataclass
class _Unit:
    relevance: float
    section: Section
    start: int
    end: int
    matched: list[str]
    duplicates: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.section.plain_text[self.start : self.end]


@dataclass
class Gathered:
    contexts: list[EvidenceContext]
    retrieval: list[dict[str, Any]]  # per context: channels, ranks, reasons, duplicates
    log: dict[str, Any]


def _normalise(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


class Investigator:
    def __init__(self, db: Session, search: SearchService, cfg: dict[str, Any], non_tool_names: set[str] | None = None):
        self.db = db
        self.search = search
        self.cfg = cfg.get("claim_retrieval", {})
        self.synonyms = cfg.get("synonyms", {})
        self.non_tool_names = non_tool_names or set()

    # ------------------------------------------------------------------ entry
    def gather(self, analysis: ClaimAnalysis, mode: str | None = None, max_evidence: int | None = None) -> Gathered:
        cfg = self.cfg
        mode = mode or cfg.get("mode", "bm25")
        max_evidence = max_evidence or int(cfg.get("max_evidence", 24))
        log: dict[str, Any] = {"mode": mode, "queries": {}, "limits": {k: cfg.get(k) for k in (
            "lexical_k", "counter_k", "identifier_k", "max_evidence", "spans_per_section", "min_relevance")}}

        pool = self._search_channels(analysis, mode, log)
        rows = {s.id: s for s in self.db.scalars(select(Section).where(Section.id.in_(list(pool))))}
        linked = self._linked_documents(pool, rows, log)
        if linked:
            self._add_linked_sections(analysis, pool, rows, linked)
        sections, docs = self._active_sections(pool, rows)
        log["candidate_sections"] = len(sections)

        weights = term_weights(analysis, [s.plain_text for s in sections])
        units, section_relevance = self._select_units(analysis, sections, docs, weights, log)
        kept = self._collapse_and_limit(units, pool, docs, max_evidence, log)
        contexts, retrieval = self._build_contexts(kept, pool, docs, section_relevance)
        return Gathered(contexts=contexts, retrieval=retrieval, log=log)

    # --------------------------------------------------------------- searching
    def _run(self, pool: dict[str, Candidate], channel: str, query: str, size: int, filters: SearchFilters | None = None,
             mode: str = "bm25", reason: str = "") -> int:
        if not query.strip():
            return 0
        resp = self.search.search(SearchRequest(query=query[:1000], size=size, mode=mode, highlight=False,
                                                filters=filters or SearchFilters()))
        for h in resp.hits:
            c = pool.setdefault(h.section_id, Candidate(h.section_id))
            c.channels.setdefault(channel, h.rank)
            c.scores.setdefault(channel, h.score)
            c.reasons.append(reason or f"{channel} query, rank {h.rank} ({h.explanation})")
        return len(resp.hits)

    def _search_channels(self, analysis: ClaimAnalysis, mode: str, log: dict[str, Any]) -> dict[str, Candidate]:
        cfg = self.cfg
        pool: dict[str, Candidate] = {}

        query = expand_query(analysis, self.synonyms)
        log["queries"]["claim"] = query
        log["claim_hits"] = self._run(pool, "claim", query, int(cfg.get("lexical_k", 30)), mode=mode)

        key_terms = " ".join(analysis.key_phrases[:6] + [a.mention for a in analysis.actors][:3])
        counter = key_terms + " " + " ".join(cfg.get("counter_evidence_terms", []))
        log["queries"]["counter"] = counter
        log["counter_hits"] = self._run(pool, "counter", counter, int(cfg.get("counter_k", 20)),
                                        reason="counter-evidence query (exceptions / workarounds / contrary practice)")

        # change query: was a tool / system the claim names migrated away from or retired?
        actor_words = {w for a in analysis.actors for w in a.mention.split()}
        names = [m.group(1) for m in _PROPER_NAME.finditer(analysis.normalized)
                 if m.group(1) not in actor_words and m.group(1) not in self.non_tool_names]
        for name in dict.fromkeys(names):
            change = f"{name} {cfg.get('change_terms', _DEFAULT_CHANGE_TERMS)}"
            log["queries"][f"change:{name}"] = change
            self._run(pool, f"change:{name}", change, int(cfg.get("change_k", 10)),
                      reason=f"change query: was {name} replaced or retired?")

        for ident in analysis.identifiers:
            log["queries"][f"identifier:{ident}"] = ident
            self._run(pool, f"identifier:{ident}", f"{ident} {analysis.normalized}", int(cfg.get("identifier_k", 10)),
                      SearchFilters(identifiers=[ident]), reason=f"mentions {ident}")
            self._run(pool, f"identifier:{ident}", analysis.normalized, int(cfg.get("identifier_k", 10)),
                      SearchFilters(document_ids=[ident]), reason=f"section of {ident}")
        return pool

    # ------------------------------------------------------- linked documents
    def _linked_documents(self, pool: dict[str, Candidate], rows: dict[str, Section], log: dict[str, Any]) -> dict[str, str]:
        """Documents related to the top retrieved ones: {document id: why it was pulled in}."""
        cfg = self.cfg
        top_docs: list[str] = []
        for c in sorted(pool.values(), key=lambda c: (c.channels.get("claim", 999), c.best_rank())):
            s = rows.get(c.section_id)
            if s and s.document_id not in top_docs:
                top_docs.append(s.document_id)
            if len(top_docs) >= _TOP_DOCUMENTS_FOR_LINKS:
                break
        relationships = self.db.scalars(select(Relationship).where(
            Relationship.rel_type.in_(cfg.get("linked_relationship_types", [])),
            Relationship.target_document_id.is_not(None),
            or_(Relationship.source_document_id.in_(top_docs), Relationship.target_document_id.in_(top_docs)))).all()
        linked: dict[str, str] = {}
        for r in relationships:
            other = r.target_document_id if r.source_document_id in top_docs else r.source_document_id
            if other and other not in top_docs and other not in linked:
                anchor = r.source_document_id if other == r.target_document_id else r.target_document_id
                linked[other] = (f"{anchor} {r.rel_type} "
                                 f"{r.target_document_id if anchor == r.source_document_id else r.source_document_id}")
            if len(linked) >= int(cfg.get("max_linked_documents", 12)):
                break
        log["linked_documents"] = linked
        return linked

    def _add_linked_sections(self, analysis: ClaimAnalysis, pool: dict[str, Candidate], rows: dict[str, Section],
                             linked: dict[str, str]) -> None:
        """Pull the most relevant sections of each linked document into the pool (the `linked` channel)."""
        weights = term_weights(analysis, [s.plain_text for s in rows.values()])
        candidates = self.db.scalars(select(Section).where(Section.document_id.in_(list(linked)),
                                                           Section.is_deleted.is_(False))).all()
        per_document: dict[str, list[tuple[float, Section]]] = {}
        for s in candidates:
            relevance, _ = unit_relevance(s.plain_text, analysis, weights, self.synonyms)
            per_document.setdefault(s.document_id, []).append((relevance, s))
        for doc_id, scored in per_document.items():
            scored.sort(key=lambda x: -x[0])
            for rank, (_, s) in enumerate(scored[: int(self.cfg.get("linked_per_document", 2))], start=1):
                c = pool.setdefault(s.id, Candidate(s.id))
                c.channels.setdefault("linked", rank)
                c.reasons.append(f"linked document: {linked[doc_id]}")
                rows[s.id] = s

    def _active_sections(self, pool: dict[str, Candidate], rows: dict[str, Section]) -> tuple[list[Section], dict[str, Document]]:
        """Pool sections that still exist (not soft-deleted), in pool order, with their documents."""
        missing = [sid for sid in pool if sid not in rows]
        if missing:
            for s in self.db.scalars(select(Section).where(Section.id.in_(missing))):
                rows[s.id] = s
        doc_ids = {s.document_id for s in rows.values()}
        docs = {d.id: d for d in self.db.scalars(select(Document).where(Document.id.in_(doc_ids)))}
        sections = [rows[sid] for sid in pool if sid in rows and not rows[sid].is_deleted
                    and not docs[rows[sid].document_id].is_deleted]
        return sections, docs

    # ------------------------------------------------------------ evidence units
    def _select_units(self, analysis: ClaimAnalysis, sections: list[Section], docs: dict[str, Document],
                      weights: dict[str, float], log: dict[str, Any]) -> tuple[list[_Unit], dict[str, float]]:
        cfg = self.cfg
        min_relevance = float(cfg.get("min_relevance", 0.12))
        per_section = int(cfg.get("spans_per_section", 2))
        units: list[_Unit] = []
        section_relevance: dict[str, float] = {}
        dropped = 0
        for s in sections:
            section_relevance[s.id] = unit_relevance(s.plain_text, analysis, weights, self.synonyms)[0]
            whole = docs[s.document_id].kind in ("record", "thread") and len(s.plain_text) <= _WHOLE_RECORD_CHARS
            k = per_section + 1 if s.section_role == "header" else per_section  # metadata lines are often decisive
            for start, end, relevance, matched in best_units(s.plain_text, analysis, weights, self.synonyms, k, is_record=whole):
                if relevance < min_relevance:
                    dropped += 1
                    continue
                units.append(_Unit(relevance, s, start, end, matched))
        log["units_below_relevance"] = dropped
        return units, section_relevance

    def _collapse_and_limit(self, units: list[_Unit], pool: dict[str, Candidate], docs: dict[str, Document],
                            max_evidence: int, log: dict[str, Any]) -> list[_Unit]:
        """Rank units, collapse copies into the strongest one, and apply the per-document / record / total limits."""
        cfg = self.cfg

        def rank_key(u: _Unit) -> float:
            channel_bonus = 1.0 / (1 + pool[u.section.id].best_rank() / 5)
            return -(_RELEVANCE_WEIGHT * u.relevance + _CHANNEL_WEIGHT * channel_bonus)

        units.sort(key=rank_key)
        collapse = float(cfg.get("near_duplicate_jaccard", 0.85))
        max_records = int(cfg.get("max_records", 8))
        kept: list[_Unit] = []
        kept_shingles: list[set] = []
        per_document: dict[str, int] = {}
        duplicates = records = 0
        for u in units:
            norm, sh = _normalise(u.text), shingles(u.text)
            twin = next((i for i, k in enumerate(kept)
                         if norm == _normalise(k.text) or (len(sh) > 4 and jaccard(sh, kept_shingles[i]) >= collapse)), None)
            if twin is not None:
                kept[twin].duplicates.append(f"{u.section.id}[{u.start}:{u.end}]")
                duplicates += 1
                continue
            doc_id = u.section.document_id
            if per_document.get(doc_id, 0) >= _MAX_UNITS_PER_DOCUMENT:
                continue
            if docs[doc_id].kind == "record":
                if records >= max_records:
                    continue
                records += 1
            if len(kept) >= max_evidence:
                break
            kept.append(u)
            kept_shingles.append(sh)
            per_document[doc_id] = per_document.get(doc_id, 0) + 1
        log["duplicates_collapsed"] = duplicates
        log["evidence_units"] = len(kept)
        return kept

    def _build_contexts(self, kept: list[_Unit], pool: dict[str, Candidate], docs: dict[str, Document],
                        section_relevance: dict[str, float]) -> tuple[list[EvidenceContext], list[dict[str, Any]]]:
        contexts, retrieval = [], []
        for u in kept:
            s, d, c = u.section, docs[u.section.document_id], pool[u.section.id]
            contexts.append(EvidenceContext(
                section_id=s.id, document_id=d.id, doc_title=d.title, doc_type=d.doc_type, doc_kind=d.kind,
                status=d.status, flags=list(d.flags or []), doc_date=d.doc_date.isoformat() if d.doc_date else None,
                doc_people=list(d.people or []), heading=s.heading, section_role=s.section_role, source_path=d.rel_path,
                start_line=s.start_line, end_line=s.end_line, section_text=s.plain_text, span_start=u.start, span_end=u.end,
                relevance=round(u.relevance, 4), section_relevance=round(section_relevance.get(s.id, u.relevance), 4),
                meta={k: v for k, v in (d.meta or {}).items() if not k.startswith("_")},
            ))
            retrieval.append({"channels": c.channels, "scores": {k: round(v, 4) for k, v in c.scores.items()},
                              "reasons": list(dict.fromkeys(c.reasons))[:4], "matched_terms": u.matched,
                              "relevance": round(u.relevance, 4), "duplicates": u.duplicates,
                              "content_hash": s.content_hash})
        return contexts, retrieval
