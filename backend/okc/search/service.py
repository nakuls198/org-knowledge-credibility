"""Search service: lexical (BM25), semantic and hybrid (Reciprocal Rank Fusion) retrieval.

Every hit keeps document id, section id, file path and line span, plus a short explanation of *why*
it was retrieved (which clauses / channels matched).
"""

from __future__ import annotations

import logging
import time
from datetime import date
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from okc.common.text import find_identifiers
from okc.db.models import Document
from okc.db.session import get_sessionmaker
from okc.search.backends.base import RawHit, SearchBackend
from okc.search.documents import embedding_text, indexable_sections, section_search_doc
from okc.search.embeddings import Embedder, get_embedder, usable_embedder
from okc.search.schemas import SearchHit, SearchRequest, SearchResponse
from okc.settings import get_settings, load_config

log = logging.getLogger(__name__)


def rrf_fuse(rankings: dict[str, list[str]], k: int = 60) -> list[tuple[str, float, dict[str, int]]]:
    """Reciprocal Rank Fusion: score(d) = sum_c 1 / (k + rank_c(d))."""
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for channel, ids in rankings.items():
        for r, i in enumerate(ids, start=1):
            scores[i] = scores.get(i, 0.0) + 1.0 / (k + r)
            ranks.setdefault(i, {})[channel] = r
    fused = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    return [(i, s, ranks[i]) for i, s in fused]


def _explain(matched: list[str], channel_ranks: dict[str, int]) -> str:
    parts = []
    if "bm25_text" in matched or "bm25" in channel_ranks:
        r = channel_ranks.get("bm25")
        parts.append("keyword match (BM25)" + (f", rank {r}" if r else ""))
    for m in matched:
        if m.startswith("id:"):
            parts.append(f"mentions identifier {m[3:]}")
        elif m.startswith("doc:"):
            parts.append(f"belongs to document {m[4:]}")
    if "semantic" in channel_ranks:
        parts.append(f"semantic similarity, rank {channel_ranks['semantic']}")
    return "; ".join(parts) or "retrieved"


def to_hit(raw: RawHit, rank: int, channel_ranks: dict[str, int] | None = None, score: float | None = None) -> SearchHit:
    src = raw["source"]
    d = src.get("doc_date")
    channel_ranks = channel_ranks or {}
    return SearchHit(
        section_id=src["section_id"], document_id=src["document_id"], doc_title=src.get("doc_title", ""),
        doc_type=src.get("doc_type", ""), status=src.get("status", ""), heading=src.get("heading", ""),
        heading_path=src.get("heading_path") or [], section_role=src.get("section_role", ""), text=src.get("text", ""),
        source_path=src.get("source_path", ""), start_line=int(src.get("start_line", 0)), end_line=int(src.get("end_line", 0)),
        doc_date=date.fromisoformat(d) if isinstance(d, str) else d, people=src.get("people") or [],
        flags=src.get("flags") or [], identifiers=src.get("identifiers") or [],
        score=round(float(score if score is not None else raw["score"]), 6), rank=rank,
        highlights=raw.get("highlights") or [], matched=raw.get("matched") or [],
        channel_ranks=channel_ranks, explanation=_explain(raw.get("matched") or [], channel_ranks),
    )


class SearchService:
    def __init__(self, backend: SearchBackend, embedder: Embedder | None = None, cfg: dict[str, Any] | None = None):
        self.backend = backend
        self.embedder = embedder
        self.cfg = cfg if cfg is not None else load_config("retrieval")

    def semantic_available(self) -> bool:
        return bool(self.embedder and self.embedder.available and self.backend.supports_vectors)

    def search(self, req: SearchRequest) -> SearchResponse:
        t0 = time.perf_counter()
        notes: list[str] = []
        mode = req.mode
        if mode in ("semantic", "hybrid") and not self.semantic_available():
            notes.append(f"{mode} retrieval requested but embeddings are not available; fell back to BM25")
            mode = "bm25"
        identifiers = sorted(set(find_identifiers(req.query)))
        if mode == "bm25":
            total, hits = self._bm25(req, identifiers)
        else:
            total, hits = self._semantic_or_hybrid(req, mode, identifiers)
        return SearchResponse(query=req.query, mode=mode, backend=self.backend.name, total=total,
                              took_ms=round((time.perf_counter() - t0) * 1000, 1), hits=hits, notes=notes)

    def _bm25(self, req: SearchRequest, identifiers: list[str]) -> tuple[int, list[SearchHit]]:
        total, raws = self.backend.lexical(req.query, req.filters, req.size, req.offset, req.highlight, identifiers)
        return total, [to_hit(r, req.offset + i + 1, {"bm25": req.offset + i + 1}) for i, r in enumerate(raws)]

    def _semantic_or_hybrid(self, req: SearchRequest, mode: str, identifiers: list[str]) -> tuple[int, list[SearchHit]]:
        hybrid_cfg = self.cfg.get("hybrid", {})
        depth = req.offset + req.size
        candidates = int(hybrid_cfg.get("candidate_depth", 50))
        vector = self.embedder.encode([req.query])[0]  # type: ignore[union-attr]
        semantic = self.backend.vector(vector, req.filters, max(candidates, depth))
        if mode == "semantic":
            window = semantic[req.offset:depth]
            return len(semantic), [to_hit(r, i + 1, {"semantic": i + 1}) for i, r in enumerate(window, start=req.offset)]

        _, lexical = self.backend.lexical(req.query, req.filters, max(candidates, depth), 0, req.highlight, identifiers)
        by_id = {r["id"]: r for r in semantic}
        by_id.update({r["id"]: r for r in lexical})  # prefer the lexical copy (it has highlights)
        fused = rrf_fuse({"bm25": [r["id"] for r in lexical], "semantic": [r["id"] for r in semantic]},
                         k=int(hybrid_cfg.get("rrf_k", 60)))
        hits = []
        for rank, (sid, score, ranks) in enumerate(fused[req.offset:depth], start=req.offset + 1):
            raw = RawHit(by_id[sid])
            raw["matched"] = sorted(set(raw.get("matched") or []) | set(ranks))
            hits.append(to_hit(raw, rank, ranks, score))
        return len(fused), hits


@lru_cache
def get_backend() -> SearchBackend:
    s = get_settings()
    if s.search_backend == "memory":
        from okc.search.backends.memory import MemoryBackend

        return MemoryBackend()
    from okc.search.backends.elastic import ElasticsearchBackend

    return ElasticsearchBackend(s.elasticsearch_url, s.elasticsearch_index, s.elasticsearch_timeout_s)


def get_search_service() -> SearchService:
    backend = get_backend()
    if backend.name == "memory" and backend.count() == 0:
        hydrate_from_store(backend)
    return SearchService(backend, get_embedder())


def hydrate_from_store(backend: SearchBackend) -> int:
    """(Re)build an index from the database: the store is canonical, the index is derived."""
    embedder = usable_embedder()
    with get_sessionmaker()() as db:
        docs = db.scalars(select(Document).where(Document.is_deleted.is_(False))
                          .options(selectinload(Document.sections))).all()
        pairs = [(d, s) for d in docs for s in indexable_sections(d)]
        backend.ensure_index(recreate=True, vector_dims=embedder.dims if embedder else None)
        vectors = (embedder.encode([embedding_text(d, s) for d, s in pairs])
                   if embedder and pairs else [None] * len(pairs))
        return backend.upsert([section_search_doc(d, s, v) for (d, s), v in zip(pairs, vectors)])
