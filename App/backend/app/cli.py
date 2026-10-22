"""Command-line entry points.

  python -m app.cli init-db                 # create tables (dev) - production uses `alembic upgrade head`
  python -m app.cli ingest [--full] [--data-dir PATH]
  python -m app.cli reindex                 # rebuild the search index from PostgreSQL
  python -m app.cli assess "claim text"     # one-off assessment, printed as JSON
  python -m app.cli evaluate [--split dev|test|all] [--classifier rules|rules+nli|llm]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.config import get_settings, load_config
from app.core.logging import configure_logging


def _cmd_init_db(_args) -> None:
    from app.database.models import Base
    from app.database.session import get_engine

    Base.metadata.create_all(get_engine())
    print("tables created")


def _cmd_ingest(args) -> None:
    from app.database.session import get_sessionmaker
    from app.ingestion.pipeline import new_job, run_job
    from app.retrieval.embeddings import get_embedder
    from app.retrieval.service import get_backend

    s = get_settings()
    data_dir = Path(args.data_dir) if args.data_dir else s.data_dir
    with get_sessionmaker()() as db:
        job = new_job(db, data_dir, "full" if args.full else "incremental")
        db.commit()
        job = run_job(db, job, load_config("ingestion"), get_backend(), get_embedder())
        print(json.dumps({"job_id": job.id, "status": job.status, "error": job.error, "stats": job.stats}, indent=2, default=str))
        if job.status != "succeeded":
            sys.exit(1)


def _cmd_reindex(_args) -> None:
    _cmd_ingest(argparse.Namespace(full=True, data_dir=None))


def _cmd_assess(args) -> None:
    from app.assessments.service import assess_text

    print(json.dumps(assess_text(args.claim, classifier=args.classifier), indent=2, default=str))


def _cmd_evaluate(args) -> None:
    from app.evaluation.runner import run_evaluation

    report = run_evaluation(split=args.split, classifiers=args.classifier, save=not args.no_save)
    print(json.dumps(report["summary"], indent=2, default=str))


def main(argv: list[str] | None = None) -> None:
    configure_logging(get_settings().log_level)
    p = argparse.ArgumentParser(prog="okc")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db").set_defaults(fn=_cmd_init_db)
    ing = sub.add_parser("ingest")
    ing.add_argument("--full", action="store_true", help="re-parse everything and rebuild the search index")
    ing.add_argument("--data-dir")
    ing.set_defaults(fn=_cmd_ingest)
    sub.add_parser("reindex").set_defaults(fn=_cmd_reindex)
    a = sub.add_parser("assess")
    a.add_argument("claim")
    a.add_argument("--classifier", default=None)
    a.set_defaults(fn=_cmd_assess)
    e = sub.add_parser("evaluate")
    e.add_argument("--split", default="all", choices=["dev", "test", "all"])
    e.add_argument("--classifier", action="append", help="classifier mode(s) to compare; default from config")
    e.add_argument("--no-save", action="store_true")
    e.set_defaults(fn=_cmd_evaluate)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
