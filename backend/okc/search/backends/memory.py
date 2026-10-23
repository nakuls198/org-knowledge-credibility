"""In-process BM25 backend.

Used by unit tests, offline runs and the lite install (no Elasticsearch needed). It mirrors the
Elasticsearch behaviour closely enough (field boosts, filters, highlighting, identifier boosts) for
a corpus of this size, and it can be rebuilt from the database at any time.
"""

from __future__ import annotations

import html
import math
import re
from collections import Counter
from datetime import date
from typing import Any

import numpy as np

from okc.common.text import STOPWORDS, stem, tokenize
from okc.search.backends.base import RawHit, SearchBackend
from okc.search.schemas import SearchFilters

_FIELD_BOOSTS = {"doc_title": 2.0, "heading": 1.5, "heading_path": 1.0, "text": 1.0}
K1, B = 1.2, 0.75
_TIE_BREAKER = 0.3  # best_fields + tie_breaker, like Elasticsearch multi_match
_IDENTIFIER_BOOST = 3.0


def _analyze(text: str) -> list[str]:
    return [stem(t) for t in tokenize(text) if t not in STOPWORDS]


def _passes(src: dict[str, Any], f: SearchFilters) -> bool:
    if f.doc_types and src.get("doc_type") not in f.doc_types:
        return False
    if f.statuses and src.get("status") not in f.statuses:
        return False
    if f.people and not set(f.people) & set(src.get("people") or []):
        return False
    if f.document_ids and src.get("document_id") not in f.document_ids:
        return False
    if f.exclude_document_ids and src.get("document_id") in f.exclude_document_ids:
        return False
    if f.identifiers and not set(f.identifiers) & set(src.get("identifiers") or []):
        return False
    if f.section_roles and src.get("section_role") not in f.section_roles:
        return False
    if not f.include_records and src.get("doc_kind") == "record":
        return False
    if f.date_from or f.date_to:
        d = src.get("doc_date")
        if not d:
            return False
        d = date.fromisoformat(d) if isinstance(d, str) else d
        if (f.date_from and d < f.date_from) or (f.date_to and d > f.date_to):
            return False
    return True


def _highlight(text: str, terms: set[str], max_fragments: int = 2, fragment: int = 220) -> list[str]:
    """HTML-escaped fragments with the matching words wrapped in <mark>."""
    if not terms:
        return []
    spans = [(m.start(), m.end()) for m in re.finditer(r"[A-Za-z0-9]+", text) if stem(m.group().lower()) in terms]
    if not spans:
        return []
    fragments, used_until = [], -1
    for s, _ in spans:
        if s <= used_until or len(fragments) >= max_fragments:
            continue
        a = max(0, s - fragment // 3)
        b = min(len(text), a + fragment)
        pieces, pos = [], a
        for ss, ee in spans:
            if ss >= a and ee <= b:
                pieces.append(html.escape(text[pos:ss]))
                pieces.append("<mark>" + html.escape(text[ss:ee]) + "</mark>")
                pos = ee
        pieces.append(html.escape(text[pos:b]))
        fragments.append("".join(pieces).strip())
        used_until = b
    return fragments


class MemoryBackend(SearchBackend):
    name = "memory"

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self._tf: dict[str, dict[str, Counter]] = {}
        self._len: dict[str, dict[str, int]] = {}
        self._vectors: dict[str, np.ndarray] = {}
        self._exists = False

    # ------------------------------------------------------------ lifecycle
    def ensure_index(self, recreate: bool = False, vector_dims: int | None = None) -> None:
        if recreate:
            self.docs.clear()
            self._tf.clear()
            self._len.clear()
            self._vectors.clear()
        self._exists = True

    def index_exists(self) -> bool:
        return self._exists

    @property
    def supports_vectors(self) -> bool:
        return bool(self._vectors)

    def upsert(self, docs: list[dict[str, Any]]) -> int:
        for d in docs:
            sid = d["section_id"]
            src = {k: v for k, v in d.items() if k != "embedding"}
            self.docs[sid] = src
            self._tf[sid] = {f: Counter(_analyze(" ".join(src[f]) if isinstance(src.get(f), list) else str(src.get(f) or "")))
                             for f in _FIELD_BOOSTS}
            self._len[sid] = {f: sum(c.values()) for f, c in self._tf[sid].items()}
            if d.get("embedding") is not None:
                v = np.asarray(d["embedding"], dtype=np.float32)
                self._vectors[sid] = v / (np.linalg.norm(v) or 1.0)
        self._exists = True
        return len(docs)

    def delete(self, ids: list[str]) -> int:
        removed = 0
        for i in ids:
            if self.docs.pop(i, None) is not None:
                removed += 1
            self._tf.pop(i, None)
            self._len.pop(i, None)
            self._vectors.pop(i, None)
        return removed

    def count(self) -> int:
        return len(self.docs)

    # ------------------------------------------------------------ search
    def lexical(self, query, filters, size, offset=0, highlight=True, boost_identifiers=None):
        q_terms = _analyze(query)
        n_docs = max(1, len(self.docs))
        avg_len = {f: (sum(lens[f] for lens in self._len.values()) / n_docs) or 1.0 for f in _FIELD_BOOSTS}
        doc_freq: Counter = Counter()
        for tfs in self._tf.values():
            for f, c in tfs.items():
                for t in set(q_terms) & set(c):
                    doc_freq[(f, t)] += 1

        scored = []
        for sid, src in self.docs.items():
            if not _passes(src, filters):
                continue
            total, best_field, matched = 0.0, 0.0, []
            for f, boost in _FIELD_BOOSTS.items():
                counts, length, field_score = self._tf[sid][f], self._len[sid][f], 0.0
                for t in q_terms:
                    tf = counts.get(t, 0)
                    if not tf:
                        continue
                    idf = math.log(1 + (n_docs - doc_freq[(f, t)] + 0.5) / (doc_freq[(f, t)] + 0.5))
                    field_score += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / avg_len[f]))
                best_field = max(best_field, field_score * boost)
                total += field_score * boost
            score = best_field + _TIE_BREAKER * (total - best_field)
            if score > 0:
                matched.append("bm25_text")
            for ident in boost_identifiers or []:
                if ident in (src.get("identifiers") or []):
                    score += _IDENTIFIER_BOOST
                    matched.append(f"id:{ident}")
                if src.get("document_id") == ident:
                    score += _IDENTIFIER_BOOST
                    matched.append(f"doc:{ident}")
            if score > 0:
                scored.append((score, sid, matched))

        scored.sort(key=lambda x: (-x[0], x[1]))
        terms = set(q_terms)
        hits = []
        for score, sid, matched in scored[offset : offset + size]:
            src = self.docs[sid]
            hits.append(RawHit(id=sid, score=score, source=src, matched=matched,
                               highlights=_highlight(src.get("text", ""), terms) if highlight else []))
        return len(scored), hits

    def vector(self, vector, filters, size):
        if not self._vectors:
            return []
        q = np.asarray(vector, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        scored = [(float(v @ q), sid) for sid, v in self._vectors.items() if _passes(self.docs[sid], filters)]
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [RawHit(id=sid, score=(1 + s) / 2, source=self.docs[sid], highlights=[], matched=["semantic"])
                for s, sid in scored[:size]]

    def get(self, ids):
        return [self.docs[i] for i in ids if i in self.docs]

    def health(self) -> dict[str, Any]:
        return {"ok": True, "status": "in-memory", "index": "memory", "index_exists": self._exists,
                "documents": len(self.docs), "vectors": bool(self._vectors)}
