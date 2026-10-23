#!/bin/sh
# Create the schema, load the dataset into the database and search index (only what changed), then serve.
set -e
alembic upgrade head
python -m okc.cli ingest
exec uvicorn okc.main:app --host 0.0.0.0 --port 8000
