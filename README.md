# Northfield AI: Organisational Knowledge Credibility Platform

Companies write down how they work in policies, process documents, meeting notes, chat threads and system records, and these sources often disagree. **Northfield AI checks whether a statement about the organisation is actually true**, shows the evidence for and against it, and says how much each source should be trusted.

> *"Head of Finance and People Ops must approve new-business discounts of 15 percent or more in the CRM before Closed Won."*
> → The app finds the policy that requires it, the CRM and audit records that show whether it happened, and any interview or chat that describes a workaround. It returns a verdict with the quotes behind it.

**Core principle: search relevance is not credibility.** Finding passages that mention a topic is easy. Deciding which of them to believe is the hard part. This project separates the two: retrieval gathers candidate evidence, then a reasoning and credibility engine judges each passage (supports / contradicts / not relevant), weighs the source (an approved policy counts more than a Slack message; a superseded or draft document counts less), collapses copies that repeat the same origin, and returns a verdict with its uncertainty, its conflicts and the exact quotes behind it.

## What you can do with it

| Tab | What it does |
|---|---|
| **Check a claim** | Type a statement, or pick an example. You get a verdict, the reasons, the supporting and contradicting passages with exact quotes, and caveats and next steps. |
| **Ask** | Ask in plain language: *"Who approves big discounts?"*, *"Is DEAL-027 a violation of policy?"*, *"Which deals broke the discount policy?"* The app answers from the policy rulebook and the records, citing the files. |
| **Evidence** | Browse, preview and download all 45 source files, and see how the app's answers compare with 52 claims the team labelled by hand. |
| **How it works** | The eight steps behind every answer, what each verdict means, and how much each kind of source counts. |

## Run it

You need **Docker Desktop** (give it at least **8 GB of memory**) and about **8 GB of free disk**. Nothing else: no Python install, no accounts, no API keys, no internet access after the first build.

```bash
git clone <this repository>
cd <this repository>
docker compose up -d --build
```

The first build takes **10 to 20 minutes** (it installs the dependencies and downloads both ML models into the image). The first start then takes a minute or two while the dataset is loaded and indexed. After that, startup takes seconds.

| | |
|---|---|
| App | <http://localhost:8501> |
| API docs | <http://localhost:8001/docs> |

Follow the start-up with `docker compose logs -f backend`; the app is ready when it prints `Application startup complete`.

```bash
docker compose stop        # pause (data is kept)
docker compose up -d       # resume
docker compose down -v     # remove everything, including the loaded data
```

### If something goes wrong

* **The build is killed or Elasticsearch keeps restarting:** Docker has too little memory. Raise it in Docker Desktop → Settings → Resources to at least 8 GB.
* **The app says the backend is not reachable:** the backend is still loading the dataset. Wait for `Application startup complete` in the backend log.
* **A port is taken:** change the left-hand port numbers in `docker-compose.yml` (`8001` and `8501`).

## The dataset

`data/northfield/` holds the dataset the platform is built around: a **synthetic** company, *Northfield Software Ltd* (29 employees, 1 August 2025 to 31 July 2026), with deliberately realistic inconsistencies. It contains no real people or customers.

| Folder | Contents |
|---|---|
| `00_overview` | company overview, org chart and roster, role descriptions, decision-rights table, superseded versions |
| `02_policies` | approved policies (discounts, renewals, expenses, escalation, onboarding, …) |
| `03_process_docs` | how each process is meant to run |
| `04_interviews` | ten employee interviews: what people say actually happens |
| `05_meetings` | ten meeting records and decisions |
| `06_communications` | email and Slack threads |
| `07_crm` | 75 CRM deal records |
| `08_project_tasks`, `09_audit_log` | task records and a 200-event system audit log |

Some traps are deliberate: a policy that was superseded, a procedure that was never updated, a meeting summary that contradicts the minutes, a workaround repeated in several threads. To use the platform on a different organisation, replace the contents of `data/northfield/` with your own Markdown and CSV files and adjust `config/` (see below).

## How an answer is produced

1. **Ingest.** Markdown is split by headings (never mid-clause) into sections with line spans, content hashes and stable ids such as `POL-01:formal-requirements`. Metadata comes from the file headers; every inferred value records its source. CSV rows become record documents. Re-ingestion is incremental.
2. **Relate.** Explicit links, metadata fields (*Supersedes*, *Related policy*), ID mentions and documented rules produce typed edges (`supersedes`, `implements`, `records_decision`, …). A newer date alone never implies supersession. Broken references, cycles, duplicates and stale procedures are reported.
3. **Retrieve.** Keyword search (BM25) and meaning search (MiniLM embeddings) run together and are merged by reciprocal rank fusion. A deliberately contrary query hunts for counter-evidence, and identifiers such as `DEAL-014` are looked up exactly.
4. **Judge each passage.** Transparent rules (thresholds, who approves, negation, workaround language, records) are combined with an NLI model (`cross-encoder/nli-deberta-v3-base`). Every label keeps the signals that produced it.
5. **Weigh.** Authority depends on the kind of claim (a policy is the best evidence of a *rule*, a record is the best evidence of a *practice*), on whether the source is still in force, how direct it is, and how strong the statement is. Sources that repeat each other form one cluster and share one vote.
6. **Decide.** A fixed formula turns the weighted evidence into a score and an uncertainty interval, then a verdict: Supported, Likely, Conflicted, Unlikely, Unsupported or Not enough evidence. No model sets the number. Governance analysis says which document governs a conflict, or that a human should decide.
7. **Explain and audit.** A template writes the explanation from the evidence. Every assessment stores its evidence snapshots, labels, signals, configuration and versions, and can be re-scored exactly (`/assessments/{id}/reproduce`).

The score is a heuristic summary, not a calibrated probability. See [docs/limitations.md](docs/limitations.md).

## Configuration

Everything judgement- or dataset-specific lives in `config/` and is snapshotted into every assessment.

| File | Controls |
|---|---|
| `ingestion.yaml` | which files are read, identifier prefixes, document-type and status rules, metadata aliases, relationship rules, chunking |
| `retrieval.yaml` | ranking, fusion constant, retrieval channels and limits, counter-evidence terms |
| `reasoning.yaml` | cue lexicons, thresholds, NLI settings |
| `credibility.yaml` | authority matrix (claim type × source scope), temporal discounts, dependence model, priors, verdict thresholds |
| `entities.yaml` | role and function aliases (people and roles are read from the roster tables) |
| `compliance.yaml` | the record rulebook: discount approvals, renewal cap, expenses |
| `answers.yaml` | record-id patterns and rule-question topics for plain answers |

## Project layout

```
data/northfield/     the dataset (Markdown + CSV)
config/              all tunable rules and weights
backend/okc/         the engine and API (Python)
  corpus/ search/ claims/ evidence/ credibility/ assessment/ answers/ evaluation/ api/
backend/tests/       test suite (with a tiny fictional corpus in fixtures/)
backend/migrations/  database schema (Alembic)
ui/                  the Streamlit app
evaluation/          52 labelled claims and saved evaluation results
docs/                architecture, API, evaluation, limitations
docker-compose.yml   the whole stack
```

Technology: Python 3.11, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, Elasticsearch 8.17, sentence-transformers (MiniLM embeddings and DeBERTa NLI, CPU), scikit-learn, Streamlit, Plotly.

## Documentation

* [docs/architecture.md](docs/architecture.md): design, credibility model and data model
* [docs/api.md](docs/api.md): the REST API
* [docs/evaluation.md](docs/evaluation.md): how it is measured
* [docs/limitations.md](docs/limitations.md): assumptions and known limits

## Development

The backend tests run offline against a tiny fictional corpus (`backend/tests/fixtures/sample`), with SQLite, in-memory search and fake models, so they need no Docker:

```bash
cd backend
python -m venv .venv                                    # Python 3.11 or 3.12; macOS/Linux: .venv/bin/python
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```

To work on the engine against the real stack, rebuild after a change with `docker compose up -d --build backend`. Command-line tools run inside the container:

```bash
docker compose exec backend python -m okc.cli assess "Claim text"
docker compose exec backend python -m okc.cli evaluate --split all --classifier rules --classifier rules+nli
docker compose exec backend python -m okc.cli ingest --full     # rebuild the index from scratch
```
