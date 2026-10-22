"""In-process BM25 backend.

Used by unit tests and when Elasticsearch is unavailable. It mirrors the ES
behaviour closely enough for tests (field boosts, filters, highlighting) but is
not a substitute for the production index.
"""

from __future__ import annotations

import html
import math
import re
from collections import Counter
from datetime import date
from typing import Any

import numpy as np

from app.core.text import STOPWORDS, stem, tokenize
from app.retrieval.backends.base import RawHit, SearchBackend
from app.retrieval.schemas import SearchFilters

_FIELDS = {"doc_title": 2.0, "heading": 1.5, "heading_path": 1.0, "text": 1.0}
K1, B = 1.2, 0.75


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
        if f.date_from and d < f.date_from:
            return False
        if f.date_to and d > f.date_to:
            return False
    return True


def _highlight(text: str, terms: set[str], max_frags: int = 2, frag: int = 220) -> list[str]:
    if not terms:
        return []
    spans = [(m.start(), m.end()) for m in re.finditer(r"[A-Za-z0-9]+", text) if stem(m.group().lower()) in terms]
    if not spans:
        return []
    frags, used_until = [], -1
    for s, _ in spans:
        if s <= used_until or len(frags) >= max_frags:
            continue
        a = max(0, s - frag // 3)
        b = min(len(text), a + frag)
        piece, out, pos = text[a:b], [], a
        for ss, ee in spans:
            if ss >= a and ee <= b:
                out.append(html.escape(text[pos:ss]))
                out.append("<mark>" + html.escape(text[ss:ee]) + "</mark>")
                pos = ee
        out.append(html.escape(text[pos:b]))
        frags.append("".join(out).strip())
        used_until = b
        del piece
    return frags


class MemoryBackend(SearchBackend):
    name = "memory"

    def __init__(self):
        self.docs: dict[str, dict[str, Any]] = {}
        self._tf: dict[str, dict[str, Counter]] = {}
        self._len: dict[str, dict[str, int]] = {}
        self._vectors: dict[str, np.ndarray] = {}
        self._exists = False

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

    def upsert(self, docs):
        for d in docs:
            sid = d["section_id"]
            vec = d.get("embedding")
            src = {k: v for k, v in d.items() if k != "embedding"}
            self.docs[sid] = src
            self._tf[sid] = {f: Counter(_analyze(" ".join(src[f]) if isinstance(src.get(f), list) else str(src.get(f) or "")))
                             for f in _FIELDS}
            self._len[sid] = {f: sum(c.values()) for f, c in self._tf[sid].items()}
            if vec is not None:
                v = np.asarray(vec, dtype=np.float32)
                self._vectors[sid] = v / (np.linalg.norm(v) or 1.0)
        self._exists = True
        return len(docs)

    def delete(self, ids):
        n = 0
        for i in ids:
            if self.docs.pop(i, None) is not None:
                n += 1
            self._tf.pop(i, None)
            self._len.pop(i, None)
            self._vectors.pop(i, None)
        return n

    def count(self) -> int:
        return len(self.docs)

    def lexical(self, query, filters, size, offset=0, highlight=True, boost_identifiers=None):
        q_terms = _analyze(query)
        N = max(1, len(self.docs))
        avg = {f: (sum(l[f] for l in self._len.values()) / N) or 1.0 for f in _FIELDS}
        df: dict[tuple[str, str], int] = Counter()
        for sid, tfs in self._tf.items():
            for f, c in tfs.items():
                for t in set(q_terms) & set(c):
                    df[(f, t)] += 1
        scored = []
        for sid, src in self.docs.items():
            if not _passes(src, filters):
                continue
            total, matched = 0.0, []
            best_field = 0.0
            for f, boost in _FIELDS.items():
                c, ln, fs = self._tf[sid][f], self._len[sid][f], 0.0
                for t in q_terms:
                    tf = c.get(t, 0)
                    if not tf:
                        continue
                    idf = math.log(1 + (N - df[(f, t)] + 0.5) / (df[(f, t)] + 0.5))
                    fs += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * ln / avg[f]))
                best_field = max(best_field, fs * boost)
                total += fs * boost
            score = best_field + 0.3 * (total - best_field)  # best_fields + tie_breaker, like ES
            if score > 0:
                matched.append("bm25_text")
            for ident in boost_identifiers or []:
                if ident in (src.get("identifiers") or []):
                    score += 3.0
                    matched.append(f"id:{ident}")
                if src.get("document_id") == ident:
                    score += 3.0
                    matched.append(f"doc:{ident}")
            if score > 0:
                scored.append((score, sid, matched))
        scored.sort(key=lambda x: (-x[0], x[1]))
        terms = set(q_terms)
        out = []
        for score, sid, matched in scored[offset : offset + size]:
            src = self.docs[sid]
            out.append(RawHit(id=sid, score=score, source=src,
                              highlights=_highlight(src.get("text", ""), terms) if highlight else [], matched=matched))
        return len(scored), out

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
