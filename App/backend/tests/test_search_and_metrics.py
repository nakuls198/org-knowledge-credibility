"""Search service (memory backend), RRF fusion, evaluation metrics."""

import math

import pytest

from app.evaluation import metrics as M
from app.retrieval.schemas import SearchFilters, SearchRequest
from app.retrieval.service import SearchService, rrf_fuse


@pytest.fixture
def search(ingested):
    db, backend, *_ = ingested
    return SearchService(backend, embedder=None, cfg={"hybrid": {"rrf_k": 60}})


def test_bm25_ranks_relevant_sections_with_traceability(search):
    r = search.search(SearchRequest(query="manager approval for all leave requests", size=5))
    assert r.backend == "memory" and r.hits
    top = r.hits[0]
    assert top.document_id in {"POL-10", "MTG-10", "COMMS-20", "PROC-10"}
    assert top.source_path.endswith(".md") and top.start_line >= 1 and top.section_id.startswith(top.document_id)
    assert top.explanation.startswith("keyword match")


def test_filters_and_identifier_boost(search):
    r = search.search(SearchRequest(query="leave approval", filters=SearchFilters(doc_types=["meeting_note"])))
    assert r.hits and {h.doc_type for h in r.hits} == {"meeting_note"}
    r2 = search.search(SearchRequest(query="POL-10 leave", size=3))
    assert any(m.startswith(("id:POL-10", "doc:POL-10")) for m in r2.hits[0].matched)
    r3 = search.search(SearchRequest(query="leave", filters=SearchFilters(statuses=["superseded"])))
    assert r3.hits and all(h.status == "superseded" for h in r3.hits)
    r4 = search.search(SearchRequest(query="leave", filters=SearchFilters(include_records=False)))
    assert all(not h.document_id.startswith("TASK-") for h in r4.hits)


def test_highlights_are_html_escaped(search):
    r = search.search(SearchRequest(query="leave sign-off", size=10))
    for h in r.hits:
        for frag in h.highlights:
            assert "<script" not in frag
            assert frag.replace("<mark>", "").replace("</mark>", "").count("<") == 0


def test_semantic_request_falls_back_without_embeddings(search):
    r = search.search(SearchRequest(query="leave", mode="hybrid"))
    assert r.mode == "bm25" and r.notes


def test_rrf_fusion():
    fused = rrf_fuse({"bm25": ["a", "b", "c"], "semantic": ["b", "c", "d"]}, k=60)
    assert [f[0] for f in fused][:2] == ["b", "c"]
    assert fused[0][2] == {"bm25": 2, "semantic": 1}


def test_ranking_metrics():
    assert M.recall_at_k(["a", "x", "b"], {"a", "b"}, 2) == 0.5
    assert M.reciprocal_rank(["x", "a"], {"a"}) == 0.5
    assert M.ndcg_at_k(["a", "b"], {"a": 1, "b": 1}, 10) == pytest.approx(1.0)
    assert M.ndcg_at_k(["x", "a"], {"a": 1}, 10) == pytest.approx(1 / math.log2(3))
    assert math.isnan(M.recall_at_k(["a"], set(), 5))


def test_probabilistic_metrics():
    y, p = [1, 0, 1, 0], [0.9, 0.1, 0.8, 0.3]
    assert M.brier(y, p) == pytest.approx((0.01 + 0.01 + 0.04 + 0.09) / 4)
    assert M.auc(y, p) == 1.0
    bins, ece = M.reliability(y, p, bins=5)
    assert sum(b["n"] for b in bins) == 4 and 0 <= ece <= 1
    lo, hi = M.bootstrap_ci(lambda idx: sum(y[i] for i in idx) / len(idx), len(y), reps=200)
    assert 0 <= lo <= hi <= 1


def test_classification_metrics():
    yt, yp = ["S", "S", "C", "C"], ["S", "C", "C", "C"]
    pc = M.per_class(yt, yp, ["S", "C"])
    assert pc["S"]["precision"] == 1.0 and pc["S"]["recall"] == 0.5
    assert M.confusion(yt, yp, ["S", "C"]) == [[1, 1], [0, 2]]
    assert M.accuracy(yt, yp) == 0.75
