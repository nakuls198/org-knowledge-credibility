# Organisational Knowledge Credibility Platform

Assess whether an organisational claim — *"Discounts above 15% require Finance approval"* — is credible, using evidence scattered across Markdown policies, procedures, meeting notes, interviews, communications and CSV records.

**Core principle: search relevance is not credibility.** Retrieval finds candidate evidence; a separate reasoning and credibility engine judges each passage (supports / contradicts / neutral / ambiguous), weighs the source (authority for *this kind* of claim, whether it is still in force, how direct it is), collapses sources that repeat the same origin, and returns a verdict with its uncertainty, its conflicts and the exact passages behind it.

| | |
|---|---|
| Backend | Python 3.11 · FastAPI · Pydantic · SQLAlchemy 2 · Alembic · PostgreSQL 16 · Elasticsearch 8.17 · sentence-transformers · scikit-learn · pytest |
| Frontend | Next.js 15 (App Router) · React 19 · TypeScript · Tailwind CSS 4 · shadcn/ui-style components · TanStack Query · React Flow · Recharts · Zod |
| Optional | NLI cross-encoder (`cross-encoder/nli-deberta-v3-base`), embeddings (`all-MiniLM-L6-v2`), local LLM via Ollama (`gemma4:e4b`) |

## Quick start (Docker, everything)

Requirements: Docker Desktop running (give it at least 8 GB of memory), and the dataset folder.

1. Copy the settings file and point it at the dataset:
   ```bash
   cp .env.example .env
   ```
   In `.env` set `POSTGRES_PASSWORD` to any password and `HOST_DATA_DIR` to the dataset folder, e.g.
   `HOST_DATA_DIR=C:/Users/you/phase2_model_development/phase2_model_development`
   (forward slashes, no quotes). Leave it empty to use the small fictional sample in `data/sample/`.
2. Build and start everything (the first build takes 10–20 minutes):
   ```bash
   docker compose up -d --build
   ```
3. Load the documents into the database and search index (once, about 1–2 minutes; the first run also
   downloads the ML models):
   ```bash
   docker compose exec backend python -m app.cli ingest --full
   ```
4. Open the app: <http://localhost:8502>. API docs: <http://localhost:8001/docs>.

Stop with `docker compose stop`; start again later with `docker compose up -d` (no rebuild or re-ingest needed).
The earlier Next.js frontend is optional: `docker compose --profile nextjs up -d frontend` → <http://localhost:3000>.

## Development setup (native backend/frontend, Docker for infrastructure)

```bash
# 1. infrastructure
docker compose up -d postgres elasticsearch

# 2. backend (Python 3.11)
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu   # optional ML
.venv/Scripts/python -m pip install -e ".[ml,dev]"        # or -r requirements.lock for the exact pinned set
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m app.cli ingest --full             # reads DATA_DIR from ../.env
.venv/Scripts/python -m uvicorn app.main:app --port 8001

# 3. frontend
cd ../frontend
npm ci
echo NEXT_PUBLIC_API_URL=http://127.0.0.1:8001 > .env.local
npm run dev                                               # http://localhost:3000
```

(On macOS/Linux use `.venv/bin/python`.) On Windows prefer `127.0.0.1` over `localhost` in URLs: `localhost` may try IPv6 first and add ~10 s per connection.

### Command line

```bash
python -m app.cli ingest [--full] [--data-dir PATH]   # incremental by default; --full rebuilds the index
python -m app.cli assess "Claim text" [--classifier rules|rules+nli|llm+rules]
python -m app.cli evaluate [--split dev|test|all] [--classifier rules --classifier rules+nli]
```

### Tests

```bash
cd backend
.venv/Scripts/python -m pytest                          # unit + API tests (SQLite + in-memory search, offline)
OKC_TEST_DATABASE_URL=postgresql+psycopg://okc:<pw>@127.0.0.1:5433/okc_test .venv/Scripts/python -m pytest   # + live PG/ES integration
cd ../frontend && npm run typecheck
```

Current result: **65 passed** (including the PostgreSQL + Elasticsearch integration tests). LLM and NLI calls are mocked in unit tests.

## Configuration

Everything dataset- or judgement-specific is in `config/` and is snapshotted into every assessment:

| File | Controls |
|---|---|
| `config/ingestion.yaml` | include/exclude globs, identifier prefixes, document-type and status rules, metadata aliases, relationship rules, section roles, chunk size, inference rules |
| `config/retrieval.yaml` | ranking mode, RRF constant, claim-retrieval channels and limits, counter-evidence terms, synonyms |
| `config/reasoning.yaml` | classifier mode, cue lexicons, thresholds, NLI and LLM settings |
| `config/credibility.yaml` | authority matrix (claim type × evidence scope), temporal factors, dependence model, priors, verdict thresholds |
| `config/entities.yaml` | role / function aliases (people and roles are read from roster tables in the corpus) |

Environment variables are listed in `.env.example` (no secrets are required for the local baseline).

### Optional components

| Feature | Needs | Default |
|---|---|---|
| NLI evidence signal | `NLI_ENABLED=true` (model downloads on first use, ~0.7 GB) | on in `.env`, off in `.env.example` |
| Semantic / hybrid search | `EMBEDDINGS_ENABLED=true`, then `ingest --full` | on in `.env` |
| Local LLM classifier ("deep review") and narrative | Ollama running + `ollama pull gemma4:e4b`, `LLM_PROVIDER=ollama` | off; ~40 s per passage on CPU |

No external API credentials are needed. Nothing is sent outside the machine.

## How it works

See [docs/architecture.md](docs/architecture.md) for the full design and data model, [docs/api.md](docs/api.md) for the API, [docs/evaluation.md](docs/evaluation.md) for method and results and [docs/limitations.md](docs/limitations.md) for assumptions and limits.

1. **Ingest** — Markdown is split by headings (never mid-clause), with line spans, content hashes and stable ids (`POL-01:formal-requirements`). Metadata comes from YAML front matter or `- **Key:** value` header lines; every inferred value records its source. The communications log is split into threads; CSV rows become record documents. Incremental: unchanged files are skipped, deletions propagate, every file version is kept.
2. **Relate** — explicit links, metadata fields (*Supersedes*, *Related policy*), ID mentions, CSV reference columns and documented inference rules produce typed edges (`supersedes`, `implements`, `records_decision`, `follows_up`, `depends_on`, …) with origin and confidence. A newer date alone never implies supersession. Broken references, cycles, duplicates, ambiguous identities and stale procedures are reported.
3. **Retrieve** — claim query (+ synonyms), a deliberately contrary *counter-evidence* query, identifier lookup and relationship expansion; units (sentence / list item / chat turn / record) are scored for relevance; copies are collapsed.
4. **Reason** — transparent rules (thresholds, who approves, negation, workaround language, records) plus an NLI cross-encoder; every label keeps its signals. Optional local LLM with schema-validated output.
5. **Weigh** — authority depends on the claim type (a policy is the best evidence of a *rule*, a record of a *practice*), temporal applicability (superseded, stale, draft, as-of date), directness, provenance and strength. Dependent sources form one lineage.
6. **Decide** — heuristic score with an evidence-weight interval, verdict (supported / partially supported / contested / contradicted / insufficient evidence), governance analysis (which document governs, or human review), caveats, missing information and next steps.
7. **Suggest** — a separate extraction pipeline proposes candidate claims from policy requirements and meeting decisions (flagged when the source is a draft, superseded, stale or tentative). Candidates are never treated as facts; a user chooses which to assess.
8. **Audit** — every assessment stores evidence snapshots, labels, signals, features, configuration and versions; `/assessments/{id}/reproduce` re-scores it.

## Repository layout

```
backend/app/        api/ core/ database/ ingestion/ relationships/ retrieval/ claims/ reasoning/ credibility/ explanations/ assessments/ evaluation/
backend/tests/      pytest suite
backend/alembic/    migrations
frontend/           Next.js app (app/, components/, lib/)
config/             all tunable rules and weights
data/sample/        synthetic fictional corpus (default DATA_DIR)
evaluation/         datasets/ (labelled claims), results/, compare/ (cross-version comparison)
docs/               architecture, API, evaluation, limitations
```

The confidential Northfield dataset is never copied into the repository; it is read from `DATA_DIR` / mounted read-only.
