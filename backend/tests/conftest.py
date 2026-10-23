"""Test configuration: deterministic, offline (SQLite + in-memory search, no ML models, no LLM)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "sample"

os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": "sqlite://",
    "SEARCH_BACKEND": "memory",
    "EMBEDDINGS_ENABLED": "false",
    "NLI_ENABLED": "false",
    "LLM_PROVIDER": "none",
    "DATA_DIR": str(SAMPLE),
    "LOG_LEVEL": "WARNING",
})

from okc.settings import get_settings, load_config  # noqa: E402

get_settings.cache_clear()
load_config.cache_clear()


@pytest.fixture
def ingest_cfg() -> dict:
    return load_config("ingestion")


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A private, modifiable copy of the synthetic sample corpus."""
    dst = tmp_path / "corpus"
    shutil.copytree(SAMPLE, dst)
    return dst


@pytest.fixture
def db_session():
    from sqlalchemy.orm import sessionmaker

    from okc.db.models import Base
    from okc.db.session import make_engine

    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def memory_backend():
    from okc.search.backends.memory import MemoryBackend

    return MemoryBackend()


@pytest.fixture
def ingested(db_session, corpus, ingest_cfg, memory_backend):
    """Corpus ingested into an isolated DB + index. Returns (db, backend, corpus_path, stats)."""
    from okc.corpus.ingest import new_job, run_job

    job = new_job(db_session, corpus, "incremental")
    db_session.commit()
    job = run_job(db_session, job, ingest_cfg, memory_backend)
    assert job.status == "succeeded", job.error
    return db_session, memory_backend, corpus, job.stats


@pytest.fixture(scope="session")
def api_client():
    """FastAPI TestClient over the shared in-memory DB, with the sample corpus ingested once."""
    from fastapi.testclient import TestClient

    from okc.db.models import Base
    from okc.db.session import get_engine
    from okc.main import app

    Base.metadata.create_all(get_engine())
    with TestClient(app) as client:
        r = client.post("/api/v1/ingestion/jobs", json={"mode": "full"})
        assert r.status_code == 202, r.text
        yield client
