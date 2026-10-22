"""Elasticsearch backend (BM25 + optional dense vectors).

The index is a derived artefact: it can always be rebuilt from PostgreSQL, which in
turn can be rebuilt from the Markdown files. Document `_id` == section id, so
re-indexing is idempotent.
"""

from __future__ import annotations

import logging
from typing import Any

from elasticsearch import Elasticsearch, NotFoundError, helpers

from app.retrieval.backends.base import RawHit, SearchBackend
from app.retrieval.schemas import SearchFilters

log = logging.getLogger(__name__)

_TEXT = {"type": "text", "analyzer": "english", "fields": {"exact": {"type": "text", "analyzer": "standard"}}}


def _mappings(vector_dims: int | None) -> dict[str, Any]:
    props: dict[str, Any] = {
        "section_id": {"type": "keyword"},
        "document_id": {"type": "keyword"},
        "parent_section_id": {"type": "keyword"},
        "doc_title": _TEXT,
        "doc_type": {"type": "keyword"},
        "doc_kind": {"type": "keyword"},
        "status": {"type": "keyword"},
        "flags": {"type": "keyword"},
        "owner": {"type": "keyword"},
        "people": {"type": "keyword"},
        "doc_date": {"type": "date"},
        "identifiers": {"type": "keyword"},
        "heading": _TEXT,
        "heading_path": {"type": "text", "analyzer": "english"},
        "section_role": {"type": "keyword"},
        "text": _TEXT,
        "source_path": {"type": "keyword"},
        "start_line": {"type": "integer"},
        "end_line": {"type": "integer"},
        "content_hash": {"type": "keyword"},
    }
    if vector_dims:
        props["embedding"] = {"type": "dense_vector", "dims": vector_dims, "index": True, "similarity": "cosine"}
    return {"dynamic": "strict", "properties": props}


def _filters(f: SearchFilters) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if f.doc_types:
        out.append({"terms": {"doc_type": f.doc_types}})
    if f.statuses:
        out.append({"terms": {"status": f.statuses}})
    if f.people:
        out.append({"terms": {"people": f.people}})
    if f.document_ids:
        out.append({"terms": {"document_id": f.document_ids}})
    if f.identifiers:
        out.append({"terms": {"identifiers": f.identifiers}})
    if f.section_roles:
        out.append({"terms": {"section_role": f.section_roles}})
    if f.date_from or f.date_to:
        rng: dict[str, str] = {}
        if f.date_from:
            rng["gte"] = f.date_from.isoformat()
        if f.date_to:
            rng["lte"] = f.date_to.isoformat()
        out.append({"range": {"doc_date": rng}})
    if not f.include_records:
        out.append({"bool": {"must_not": {"term": {"doc_kind": "record"}}}})
    if f.exclude_document_ids:
        out.append({"bool": {"must_not": {"terms": {"document_id": f.exclude_document_ids}}}})
    return out


class ElasticsearchBackend(SearchBackend):
    name = "elasticsearch"

    def __init__(self, url: str, index: str, timeout_s: float = 20.0):
        self.index = index
        self.es = Elasticsearch(url, request_timeout=timeout_s, retry_on_timeout=True, max_retries=3)
        self._has_vectors: bool | None = None

    # -------------------------------------------------------------- lifecycle
    def ensure_index(self, recreate: bool = False, vector_dims: int | None = None) -> None:
        exists = self.es.indices.exists(index=self.index)
        if exists and recreate:
            self.es.indices.delete(index=self.index)
            exists = False
        if not exists:
            self.es.indices.create(
                index=self.index,
                settings={"number_of_shards": 1, "number_of_replicas": 0},
                mappings=_mappings(vector_dims),
            )
        self._has_vectors = None

    def index_exists(self) -> bool:
        return bool(self.es.indices.exists(index=self.index))

    @property
    def supports_vectors(self) -> bool:
        if self._has_vectors is None:
            try:
                m = self.es.indices.get_mapping(index=self.index)
                props = next(iter(m.values()))["mappings"]["properties"]
                self._has_vectors = "embedding" in props
            except Exception:  # noqa: BLE001
                self._has_vectors = False
        return self._has_vectors

    def upsert(self, docs: list[dict[str, Any]]) -> int:
        if not docs:
            return 0
        if not self.supports_vectors:
            docs = [{k: v for k, v in d.items() if k != "embedding"} for d in docs]
        actions = ({"_op_type": "index", "_index": self.index, "_id": d["section_id"], "_source": d} for d in docs)
        ok, errors = helpers.bulk(self.es, actions, raise_on_error=False, refresh="wait_for")
        if errors:
            log.warning("Elasticsearch bulk index reported %d errors; first: %s", len(errors), errors[0])
        return ok

    def delete(self, ids: list[str]) -> int:
        if not ids:
            return 0
        actions = ({"_op_type": "delete", "_index": self.index, "_id": i} for i in ids)
        ok, _ = helpers.bulk(self.es, actions, raise_on_error=False, refresh="wait_for")
        return ok

    def count(self) -> int:
        try:
            return int(self.es.count(index=self.index)["count"])
        except NotFoundError:
            return 0

    # ----------------------------------------------------------------- search
    def lexical(self, query, filters, size, offset=0, highlight=True, boost_identifiers=None):
        should: list[dict[str, Any]] = [
            {"multi_match": {"query": query, "fields": ["doc_title^2", "heading^1.5", "heading_path", "text", "text.exact^0.5"],
                             "type": "best_fields", "tie_breaker": 0.3, "_name": "bm25_text"}},
        ]
        for ident in boost_identifiers or []:
            should.append({"term": {"identifiers": {"value": ident, "boost": 3.0, "_name": f"id:{ident}"}}})
            should.append({"term": {"document_id": {"value": ident, "boost": 3.0, "_name": f"doc:{ident}"}}})
        body: dict[str, Any] = {
            "query": {"bool": {"should": should, "minimum_should_match": 1, "filter": _filters(filters)}},
            "from": offset, "size": size, "track_total_hits": True,
            "_source": {"excludes": ["embedding"]},
        }
        if highlight:
            body["highlight"] = {"encoder": "html", "pre_tags": ["<mark>"], "post_tags": ["</mark>"],
                                 "fields": {"text": {"fragment_size": 220, "number_of_fragments": 2},
                                            "heading": {"number_of_fragments": 0}}}
        res = self.es.search(index=self.index, **body)
        hits = []
        for h in res["hits"]["hits"]:
            hl = h.get("highlight", {})
            hits.append(RawHit(id=h["_id"], score=float(h["_score"] or 0.0), source=h["_source"],
                               highlights=hl.get("text", []) or hl.get("heading", []),
                               matched=h.get("matched_queries", [])))
        return int(res["hits"]["total"]["value"]), hits

    def vector(self, vector, filters, size):
        if not self.supports_vectors:
            return []
        res = self.es.search(index=self.index, knn={"field": "embedding", "query_vector": vector, "k": size,
                                                    "num_candidates": max(50, size * 5), "filter": _filters(filters)},
                             size=size, _source={"excludes": ["embedding"]})
        return [RawHit(id=h["_id"], score=float(h["_score"]), source=h["_source"], highlights=[], matched=["semantic"])
                for h in res["hits"]["hits"]]

    def get(self, ids):
        if not ids:
            return []
        res = self.es.mget(index=self.index, ids=ids, _source_excludes=["embedding"])
        return [d["_source"] for d in res["docs"] if d.get("found")]

    def health(self) -> dict[str, Any]:
        try:
            info = self.es.info()
            h = self.es.cluster.health()
            return {"ok": h["status"] in ("green", "yellow"), "status": h["status"], "version": info["version"]["number"],
                    "index": self.index, "index_exists": self.index_exists(), "documents": self.count(),
                    "vectors": self.supports_vectors if self.index_exists() else False}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": type(exc).__name__, "index": self.index}
