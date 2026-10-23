"""Command-line entry points.

  python -m okc.cli init-db                 # create tables (dev); production uses `alembic upgrade head`
  python -m okc.cli ingest [--full] [--data-dir PATH]
  python -m okc.cli reindex                 # rebuild the search index from the database
  python -m okc.cli assess "claim text"     # one-off assessment, printed as JSON
  python -m okc.cli evaluate [--split dev|test|all] [--classifier rules|rules+nli|llm]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from okc.assessment.store import assess_text
from okc.common.logging_setup import configure_logging
from okc.corpus.ingest import new_job, run_job
from okc.db.models import Base
from okc.db.session import get_engine, get_sessionmaker
from okc.evaluation.runner import run_evaluation
from okc.search.embeddings import get_embedder
from okc.search.service import get_backend
from okc.settings import get_settings, load_config


def _cmd_init_db(_args) -> None:
    Base.metadata.create_all(get_engine())
    print("tables created")


def _cmd_ingest(args) -> None:
    data_dir = Path(args.data_dir) if args.data_dir else get_settings().data_dir
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
    print(json.dumps(assess_text(args.claim, classifier=args.classifier), indent=2, default=str))


def _cmd_evaluate(args) -> None:
    report = run_evaluation(split=args.split, classifiers=args.classifier, save=not args.no_save)
    print(json.dumps(report["summary"], indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="okc")
    commands = parser.add_subparsers(dest="cmd", required=True)
    commands.add_parser("init-db").set_defaults(fn=_cmd_init_db)

    ingest = commands.add_parser("ingest")
    ingest.add_argument("--full", action="store_true", help="re-parse everything and rebuild the search index")
    ingest.add_argument("--data-dir")
    ingest.set_defaults(fn=_cmd_ingest)

    commands.add_parser("reindex").set_defaults(fn=_cmd_reindex)

    assess = commands.add_parser("assess")
    assess.add_argument("claim")
    assess.add_argument("--classifier", default=None)
    assess.set_defaults(fn=_cmd_assess)

    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--split", default="all", choices=["dev", "test", "all"])
    evaluate.add_argument("--classifier", action="append", help="classifier mode(s) to compare; default from config")
    evaluate.add_argument("--no-save", action="store_true")
    evaluate.set_defaults(fn=_cmd_evaluate)
    return parser


def main(argv: list[str] | None = None) -> None:
    configure_logging(get_settings().log_level)
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
