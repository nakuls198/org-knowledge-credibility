"""Evidence gathering for a claim.

Channels (each candidate remembers which channels found it and at what rank):
  claim       BM25 (or hybrid) search with the claim text + synonym expansion
  counter     a deliberately contrary query (claim key terms + exception/workaround
              wording) so that disconfirming passages are retrieved, not only confirming ones
  identifier  sections mentioning / belonging to identifiers named in the claim
  linked      sections of documents related (supersedes, implements, records_decision ...)
              to the top retrieved documents
  semantic    vector search (only when embeddings are enabled and mode = hybrid)
Then: evidence units are selected inside each section, exact / near-duplicate units are
collapsed (duplicates are recorded on the kept unit), and the top-N units are returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.claims.analysis import ClaimAnalysis, expand_query
from app.core.text import jaccard, shingles
from app.database.models import Document, Relationship, Section
from app.reasoning.schemas import EvidenceContext
from app.reasoning.spans import best_units, term_weights, unit_relevance
from app.retrieval.schemas import SearchFilters, SearchRequest
from app.retrieval.service import SearchService


# Capitalised words that are not names of tools / systems (sentence words, months, generic roles).
_COMMON_CAPS = {"The", "This", "That", "Head", "Finance", "Sales", "People", "Ops", "Support", "Lead", "Customer", "Success",
                "Delivery", "Engineering", "Product", "Manager", "Account", "Executive", "Northfield", "Closed", "Won", "Lost",
                "Sev", "Policy", "Process", "Slack", "Email", "January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"}


@dataclass
class Candidate:
    section_id: str
    channels: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)

    def best_rank(self) -> int:
        return min(self.channels.values()) if self.channels else 999


@dataclass
class Gathered:
    contexts: list[EvidenceContext]
    retrieval: list[dict[str, Any]]  # per context: channels, ranks, reasons, duplicates
    log: dict[str, Any]


class Investigator:
    def __init__(self, db: Session, search: SearchService, cfg: dict[str, Any]):
        self.db = db
        self.search = search
        self.cfg = cfg.get("claim_retrieval", {})
        self.synonyms = cfg.get("synonyms", {})

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

    def gather(self, analysis: ClaimAnalysis, mode: str | None = None, max_evidence: int | None = None) -> Gathered:
        cfg = self.cfg
        mode = mode or cfg.get("mode", "bm25")
        max_evidence = max_evidence or int(cfg.get("max_evidence", 24))
        pool: dict[str, Candidate] = {}
        log: dict[str, Any] = {"mode": mode, "queries": {}, "limits": {k: cfg.get(k) for k in
                               ("lexical_k", "counter_k", "identifier_k", "max_evidence", "spans_per_section", "min_relevance")}}

        q_main = expand_query(analysis, self.synonyms)
        log["queries"]["claim"] = q_main
        n = self._run(pool, "claim", q_main, int(cfg.get("lexical_k", 30)), mode=mode)
        log["claim_hits"] = n

        key_terms = " ".join(analysis.key_phrases[:6] + [a.mention for a in analysis.actors][:3])
        q_counter = key_terms + " " + " ".join(cfg.get("counter_evidence_terms", []))
        log["queries"]["counter"] = q_counter
        log["counter_hits"] = self._run(pool, "counter", q_counter, int(cfg.get("counter_k", 20)),
                                        reason="counter-evidence query (exceptions / workarounds / contrary practice)")

        # ---- change query: was a tool / system the claim names migrated away from or retired?
        actor_words = {w for a in analysis.actors for w in a.mention.split()}
        names = [m.group(1) for m in re.finditer(r"(?<!^)\b([A-Z][a-zA-Z0-9]{2,})\b", analysis.normalized)
                 if m.group(1) not in actor_words and m.group(1) not in _COMMON_CAPS]
        for name in dict.fromkeys(names):
            q_change = f"{name} {cfg.get('change_terms', 'migrate migrated moved replaced archived retired switched')}"
            log["queries"][f"change:{name}"] = q_change
            self._run(pool, f"change:{name}", q_change, int(cfg.get("change_k", 10)),
                      reason=f"change query: was {name} replaced or retired?")

        for ident in analysis.identifiers:
            log["queries"][f"identifier:{ident}"] = ident
            self._run(pool, f"identifier:{ident}", f"{ident} {analysis.normalized}", int(cfg.get("identifier_k", 10)),
                      SearchFilters(identifiers=[ident]), reason=f"mentions {ident}")
            self._run(pool, f"identifier:{ident}", analysis.normalized, int(cfg.get("identifier_k", 10)),
                      SearchFilters(document_ids=[ident]), reason=f"section of {ident}")

        # ---- linked documents (relationship expansion)
        sec_rows = {s.id: s for s in self.db.scalars(select(Section).where(Section.id.in_(list(pool))))}
        top_docs: list[str] = []
        for c in sorted(pool.values(), key=lambda c: (c.channels.get("claim", 999), c.best_rank())):
            s = sec_rows.get(c.section_id)
            if s and s.document_id not in top_docs:
                top_docs.append(s.document_id)
            if len(top_docs) >= 8:
                break
        types = cfg.get("linked_relationship_types", [])
        rels = self.db.scalars(select(Relationship).where(
            Relationship.rel_type.in_(types), Relationship.target_document_id.is_not(None),
            or_(Relationship.source_document_id.in_(top_docs), Relationship.target_document_id.in_(top_docs)))).all()
        linked: dict[str, str] = {}
        for r in rels:
            other = r.target_document_id if r.source_document_id in top_docs else r.source_document_id
            if other and other not in top_docs and other not in linked:
                anchor = r.source_document_id if other == r.target_document_id else r.target_document_id
                linked[other] = f"{anchor} {r.rel_type} {r.target_document_id if anchor == r.source_document_id else r.source_document_id}"
            if len(linked) >= int(cfg.get("max_linked_documents", 12)):
                break
        log["linked_documents"] = linked
        if linked:
            weights_tmp = term_weights(analysis, [s.plain_text for s in sec_rows.values()])
            lsecs = self.db.scalars(select(Section).where(Section.document_id.in_(list(linked)),
                                                          Section.is_deleted.is_(False))).all()
            per_doc: dict[str, list[tuple[float, Section]]] = {}
            for s in lsecs:
                rel, _ = unit_relevance(s.plain_text, analysis, weights_tmp, self.synonyms)
                per_doc.setdefault(s.document_id, []).append((rel, s))
            for doc_id, lst in per_doc.items():
                lst.sort(key=lambda x: -x[0])
                for rank, (rel, s) in enumerate(lst[: int(cfg.get("linked_per_document", 2))], start=1):
                    c = pool.setdefault(s.id, Candidate(s.id))
                    c.channels.setdefault("linked", rank)
                    c.reasons.append(f"linked document: {linked[doc_id]}")
                    sec_rows[s.id] = s

        # ---- load everything we need from the canonical store
        missing = [sid for sid in pool if sid not in sec_rows]
        if missing:
            for s in self.db.scalars(select(Section).where(Section.id.in_(missing))):
                sec_rows[s.id] = s
        doc_ids = {s.document_id for s in sec_rows.values()}
        docs = {d.id: d for d in self.db.scalars(select(Document).where(Document.id.in_(doc_ids)))}
        # chunked parents: use chunks (they are what the index holds); skip deleted
        sections = [sec_rows[sid] for sid in pool if sid in sec_rows and not sec_rows[sid].is_deleted
                    and not docs[sec_rows[sid].document_id].is_deleted]
        log["candidate_sections"] = len(sections)

        # ---- evidence units
        weights = term_weights(analysis, [s.plain_text for s in sections])
        min_rel = float(cfg.get("min_relevance", 0.12))
        k = int(cfg.get("spans_per_section", 2))
        units: list[tuple[float, Section, int, int, list[str]]] = []
        sec_rel: dict[str, float] = {}
        dropped = 0
        for s in sections:
            sec_rel[s.id] = unit_relevance(s.plain_text, analysis, weights, self.synonyms)[0]
            doc = docs[s.document_id]
            whole = doc.kind in ("record", "thread") and len(s.plain_text) <= 1200
            kk = k + 1 if s.section_role == "header" else k  # metadata lines (status, dates) are often decisive
            for us, ue, rel, matched in best_units(s.plain_text, analysis, weights, self.synonyms, kk, is_record=whole):
                if rel < min_rel:
                    dropped += 1
                    continue
                units.append((rel, s, us, ue, matched))
        log["units_below_relevance"] = dropped

        def rank_key(u):
            rel, s, *_ = u
            c = pool[s.id]
            channel_bonus = 1.0 / (1 + c.best_rank() / 5)
            return -(0.75 * rel + 0.25 * channel_bonus)

        units.sort(key=rank_key)
        kept: list[tuple[float, Section, int, int, list[str], list[str]]] = []
        per_doc_count: dict[str, int] = {}
        collapse = float(cfg.get("near_duplicate_jaccard", 0.85))
        kept_sh: list[set] = []
        n_dups = 0
        n_records = 0
        max_records = int(cfg.get("max_records", 8))
        for rel, s, us, ue, matched in units:
            text = s.plain_text[us:ue]
            norm = re.sub(r"\W+", " ", text.lower()).strip()
            sh = shingles(text)
            dup_of = None
            for idx, (k_rel, ks, kus, kue, _, _) in enumerate(kept):
                ktext = ks.plain_text[kus:kue]
                if norm == re.sub(r"\W+", " ", ktext.lower()).strip() or (len(sh) > 4 and jaccard(sh, kept_sh[idx]) >= collapse):
                    dup_of = idx
                    break
            if dup_of is not None:
                kept[dup_of][5].append(f"{s.id}[{us}:{ue}]")
                n_dups += 1
                continue
            if per_doc_count.get(s.document_id, 0) >= 3:
                continue
            if docs[s.document_id].kind == "record":
                if n_records >= max_records:
                    continue
                n_records += 1
            if len(kept) >= max_evidence:
                break
            kept.append((rel, s, us, ue, matched, []))
            kept_sh.append(sh)
            per_doc_count[s.document_id] = per_doc_count.get(s.document_id, 0) + 1
        log["duplicates_collapsed"] = n_dups
        log["evidence_units"] = len(kept)

        contexts, retrieval = [], []
        for rel, s, us, ue, matched, dups in kept:
            d = docs[s.document_id]
            c = pool[s.id]
            contexts.append(EvidenceContext(
                section_id=s.id, document_id=d.id, doc_title=d.title, doc_type=d.doc_type, doc_kind=d.kind,
                status=d.status, flags=list(d.flags or []), doc_date=d.doc_date.isoformat() if d.doc_date else None,
                doc_people=list(d.people or []), heading=s.heading, section_role=s.section_role, source_path=d.rel_path,
                start_line=s.start_line, end_line=s.end_line, section_text=s.plain_text, span_start=us, span_end=ue,
                relevance=round(rel, 4), section_relevance=round(sec_rel.get(s.id, rel), 4), meta={k: v for k, v in (d.meta or {}).items() if not k.startswith("_")},
            ))
            retrieval.append({"channels": c.channels, "scores": {k: round(v, 4) for k, v in c.scores.items()},
                              "reasons": list(dict.fromkeys(c.reasons))[:4], "matched_terms": matched,
                              "relevance": round(rel, 4), "duplicates": dups, "content_hash": s.content_hash})
        return Gathered(contexts=contexts, retrieval=retrieval, log=log)
