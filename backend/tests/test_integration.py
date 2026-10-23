"""Integration tests against live PostgreSQL + Elasticsearch (skipped when unavailable).

They use separate throw-away names (a temporary schema-free database file is not
possible for PostgreSQL, so a dedicated test index and fresh tables in a dedicated
database are used): OKC_TEST_DATABASE_URL / OKC_TEST_ELASTICSEARCH_URL.
"""

import os
import uuid

import pytest

PG = os.environ.get("OKC_TEST_DATABASE_URL")
ES = os.environ.get("OKC_TEST_ELASTICSEARCH_URL", "http://127.0.0.1:9200")

pytestmark = pytest.mark.integration


def _es_up() -> bool:
    try:
        import httpx

        return httpx.get(ES, timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture
def es_backend():
    if not _es_up():
        pytest.skip("Elasticsearch not reachable")
    from okc.search.backends.elastic import ElasticsearchBackend

    b = ElasticsearchBackend(ES, f"okc-test-{uuid.uuid4().hex[:8]}")
    yield b
    b.es.indices.delete(index=b.index, ignore_unavailable=True)


@pytest.fixture
def pg_session():
    if not PG:
        pytest.skip("OKC_TEST_DATABASE_URL not set")
    from sqlalchemy.orm import sessionmaker

    from okc.db.models import Base
    from okc.db.session import make_engine

    engine = make_engine(PG)
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not reachable: {type(exc).__name__}")
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_elasticsearch_index_search_and_delete(db_session, corpus, ingest_cfg, es_backend):
    from okc.corpus.ingest import new_job, run_job
    from okc.search.schemas import SearchFilters, SearchRequest
    from okc.search.service import SearchService

    job = run_job(db_session, new_job(db_session, corpus, "incremental"), ingest_cfg, es_backend)
    assert job.status == "succeeded" and es_backend.count() == job.stats["sections_indexed"]
    svc = SearchService(es_backend, None, {"hybrid": {}})
    r = svc.search(SearchRequest(query="manager approval leave", size=5, filters=SearchFilters(doc_types=["policy"])))
    assert r.backend == "elasticsearch" and r.hits and all(h.doc_type == "policy" for h in r.hits)
    assert any("<mark>" in f for h in r.hits for f in h.highlights)
    # incremental delete propagates to the index
    (corpus / "04_interviews" / "INT-20_jamie_lee.md").unlink()
    run_job(db_session, new_job(db_session, corpus, "incremental"), ingest_cfg, es_backend)
    assert not es_backend.get(["INT-20:discussion-notes"])
    # idempotent re-index: same ids, same count
    n = es_backend.count()
    run_job(db_session, new_job(db_session, corpus, "full"), ingest_cfg, es_backend)
    assert es_backend.count() == n


def test_postgres_persistence_roundtrip(pg_session, corpus, ingest_cfg, memory_backend):
    from sqlalchemy import select

    from okc.db.models import Document, Relationship
    from okc.corpus.ingest import new_job, run_job

    job = run_job(pg_session, new_job(pg_session, corpus, "incremental"), ingest_cfg, memory_backend)
    assert job.status == "succeeded"
    pol = pg_session.get(Document, "POL-10")
    assert pol.meta["supersedes"].startswith("POL-09")  # JSONB round-trip
    assert pg_session.scalars(select(Relationship).where(Relationship.rel_type == "supersedes")).first() is not None
