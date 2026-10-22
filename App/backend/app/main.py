"""FastAPI application entry point.  OpenAPI docs: /docs (Swagger) and /redoc."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import answers, claims, documents, system
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging

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


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="Organisational Knowledge Credibility API", version="1.0.0", description=DESCRIPTION,
                  openapi_tags=[
                      {"name": "system", "description": "Health and dashboard statistics"},
                      {"name": "ingestion", "description": "Markdown/CSV ingestion jobs and issue reports"},
                      {"name": "documents", "description": "Documents, sections, sources and relationships"},
                      {"name": "search", "description": "BM25 / semantic / hybrid search"},
                      {"name": "claims", "description": "Claims, assessments, evidence and evidence graph"},
                      {"name": "assessments", "description": "Assessment audit records and reproduction"},
                      {"name": "evaluation", "description": "Evaluation reports computed from labelled claims"},
                  ])
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])
    install_error_handlers(app)
    for r in (system.router, documents.router, claims.router, answers.router):
        app.include_router(r, prefix=API_PREFIX)

    @app.on_event("startup")
    def _warm_up() -> None:
        """Load optional models and build the in-memory index in the background so the first
        assessment is not slowed by one-off initialisation."""
        import threading

        def work():
            try:
                from app.reasoning.nli import get_nli
                from app.retrieval.service import get_search_service

                nli = get_nli()
                if nli is not None:
                    nli.available  # noqa: B018 - triggers lazy load
                get_search_service()
            except Exception:  # noqa: BLE001 - warm-up is best effort
                pass

        threading.Thread(target=work, daemon=True).start()

    @app.on_event("startup")
    def _mark_interrupted() -> None:
        """Jobs run in-process; anything still queued/running at startup was cut off by a restart."""
        from datetime import datetime, timezone

        from sqlalchemy import update

        from app.database.models import Assessment, IngestionJob
        from app.database.session import session_scope

        try:
            with session_scope() as db:
                now = datetime.now(timezone.utc)
                msg = "Interrupted: the service restarted while this was running. Run it again."
                db.execute(update(Assessment).where(Assessment.status.in_(["queued", "running"]))
                           .values(status="failed", error=msg, completed_at=now))
                db.execute(update(IngestionJob).where(IngestionJob.status.in_(["queued", "running"]))
                           .values(status="failed", error=msg, finished_at=now))
        except Exception:  # noqa: BLE001 - e.g. tables not created yet
            pass

    @app.get("/", include_in_schema=False)
    def root():
        return {"name": "okc-api", "docs": "/docs", "api": API_PREFIX}

    return app


app = create_app()
