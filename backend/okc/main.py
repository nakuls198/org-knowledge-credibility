"""FastAPI application entry point.  OpenAPI docs: /docs (Swagger) and /redoc."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update

from okc.api.routes import answers, claims, documents, system
from okc.common.errors import install_error_handlers
from okc.common.logging_setup import configure_logging
from okc.db.models import Assessment, IngestionJob
from okc.db.session import session_scope
from okc.evidence.nli import get_nli
from okc.search.service import get_search_service
from okc.settings import get_settings

log = logging.getLogger(__name__)
API_PREFIX = "/api/v1"

DESCRIPTION = """
Assess the credibility of organisational claims against evidence distributed across
Markdown documents (policies, procedures, meeting notes, interviews, communications)
and structured records.

* **Search relevance is not credibility**: retrieval finds candidate evidence; a separate
  reasoning + credibility engine judges it (authority, recency, independence, conflicts).
* Every assessment stores its evidence spans, labels, features, configuration snapshot and
  versions, and can be re-scored via `/assessments/{id}/reproduce`.
* Credibility scores are **heuristic** summaries, not calibrated probabilities.
"""

OPENAPI_TAGS = [
    {"name": "system", "description": "Health and dashboard statistics"},
    {"name": "ingestion", "description": "Markdown/CSV ingestion jobs and issue reports"},
    {"name": "documents", "description": "Documents, sections, sources and relationships"},
    {"name": "search", "description": "BM25 / semantic / hybrid search"},
    {"name": "claims", "description": "Claims, assessments, evidence and evidence graph"},
    {"name": "assessments", "description": "Assessment audit records and reproduction"},
    {"name": "answers", "description": "Plain answers to questions about the organisation"},
    {"name": "evaluation", "description": "Evaluation reports computed from labelled claims"},
]


def _mark_interrupted_jobs() -> None:
    """Jobs run in-process; anything still queued/running at startup was cut off by a restart."""
    message = "Interrupted: the service restarted while this was running. Run it again."
    now = datetime.now(timezone.utc)
    try:
        with session_scope() as db:
            db.execute(update(Assessment).where(Assessment.status.in_(["queued", "running"]))
                       .values(status="failed", error=message, completed_at=now))
            db.execute(update(IngestionJob).where(IngestionJob.status.in_(["queued", "running"]))
                       .values(status="failed", error=message, finished_at=now))
    except Exception as exc:  # noqa: BLE001 - e.g. tables not created yet
        log.info("Skipped marking interrupted jobs: %s", type(exc).__name__)


def _warm_up() -> None:
    """Load optional models and build the in-memory index so the first assessment is not slowed by it."""
    try:
        nli = get_nli()
        if nli is not None:
            nli.available  # noqa: B018 - triggers the lazy load
        get_search_service()
    except Exception as exc:  # noqa: BLE001 - warm-up is best effort
        log.info("Warm-up skipped: %s", type(exc).__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _mark_interrupted_jobs()
    threading.Thread(target=_warm_up, daemon=True).start()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="Organisational Knowledge Credibility API", version="1.0.0", description=DESCRIPTION,
                  openapi_tags=OPENAPI_TAGS, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])
    install_error_handlers(app)
    for router in (system.router, documents.router, claims.router, answers.router):
        app.include_router(router, prefix=API_PREFIX)

    @app.get("/", include_in_schema=False)
    def root():
        return {"name": "okc-api", "docs": "/docs", "api": API_PREFIX}

    return app


app = create_app()
