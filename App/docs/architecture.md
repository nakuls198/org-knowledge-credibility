# Architecture and data model

## System overview

```
                ┌──────────────────────── Next.js (frontend/) ────────────────────────┐
                │ Overview · Claims · Assessment · Evidence graph · Search · Documents │
                │ Evaluation · Ingestion          (TanStack Query → REST /api/v1)       │
                └───────────────────────────────┬──────────────────────────────────────┘
                                                │ HTTP/JSON
┌───────────────────────────────── FastAPI (backend/app) ──────────────────────────────────┐
│ api/v1  ─►  assessments.service ─► investigation ─► reasoning ─► credibility ─► explanations│
│                 │                    │  (retrieval.service)                                 │
│ ingestion.pipeline ─► markdown/csv parsers ─► relationships.resolver                        │
│ evaluation.runner (metrics, calibration, Stage B, sensitivity)                              │
└──────────┬────────────────────────────────┬───────────────────────────────┬───────────────┘
           │ SQLAlchemy                      │ elasticsearch-py              │ optional
     PostgreSQL (canonical)          Elasticsearch (derived index)     NLI / embeddings / Ollama
```

A modular monolith: one deployable API, one UI, two data services. Long-running work (ingestion,
assessment) runs as FastAPI background tasks with job/status records; there is no queue because the
corpus is small and one worker is enough — Redis/Celery can be added if jobs grow.

**PostgreSQL is the source of truth.** Elasticsearch is rebuilt from it at any time
(`ingest --full`, or automatically when the index is missing). Section ids are the Elasticsearch
`_id`, so indexing is idempotent.

## Pipeline modules

| Module | Responsibility |
|---|---|
| `ingestion/markdown_parser.py` | Front matter + `**Key:** value` metadata, markdown-it heading detection (ignores code fences), heading-path sections with line/char offsets and hashes, controlled chunking of long sections, thread splitting |
| `ingestion/csv_parser.py` | One record document per row with exact line span; incomplete-record and near-duplicate entity checks |
| `ingestion/pipeline.py` | Discovery, SHA-256 diff (new / modified / unchanged / deleted / re-parsed), persistence, version history, incremental indexing, parser signature |
| `relationships/resolver.py` | Typed edges with origin + confidence + evidence; inference rules; cycle (Tarjan SCC), duplicate, identity, staleness, review-overdue and metadata-gap checks |
| `retrieval/` | Backend interface (Elasticsearch / in-memory BM25), embeddings, hybrid search via Reciprocal Rank Fusion, hit explanations |
| `claims/extraction.py` | Separate candidate-claim pipeline: proposes claims only from requirement / procedure / decision sections, keeps provenance, flags draft / superseded / stale / tentative sources; never mines interviews or chats as facts |
| `claims/` | Validation, normalisation, claim type (normative / practice / factual), quantifier, negation, thresholds, actors (gazetteer from roster tables) |
| `assessments/investigation.py` | Claim, counter-evidence, change ("was X replaced?"), identifier and linked-document channels; evidence-unit selection; duplicate collapse |
| `reasoning/` | Rules classifier (named signals, including `replaced_by`: a tool / system the claim names was migrated away from, archived or retired), NLI combination policy, Ollama LLM classifier with schema validation |
| `credibility/engine.py` | Features, dependence clusters, aggregation, verdict, temporal + governance analysis, caveats, next steps |
| `explanations/builder.py` | Template explanation from recorded outputs; optional validated LLM paraphrase |
| `evaluation/` | Metrics and the reproducible evaluation runner |
| `answers/` | Plain answers (`POST /answer`): question understanding (record IDs, scans, yes/no and who/what questions, meaning-based match to the files' own statement), record compliance checks against `config/compliance.yaml` (POL-01/02 discount approval, POL-06 renewal cap, POL-07 expenses; versioned by date, audit-log timing), rule and practice questions, and a direct answer with 3–5 pieces of evidence for every claim |

## Credibility model (Stage A baseline)

For each evidence unit *i* (all factors in [0, 1], configured in `config/credibility.yaml`):

```
weight_i = authority(claim_type, scope_i) × doc_type_modifier
         × temporal_i × directness_i × provenance_i × strength_i
```

* **scope** — formal, decision, proposal, practice, testimony, belief, history — from parsed metadata
  (status, section role, document type) and passage cues, never from a filename alone.
* **temporal** — superseded documents (for current claims), stale procedures, overdue reviews, history
  sections, undated sources and evidence after the as-of date are discounted.
* **dependence** — units in the same document, linked by `follows_up` / `depends_on` /
  `records_decision` / `implements`, near-duplicate in wording or meaning, or repeated testimony of
  one person form a cluster. A cluster contributes its strongest unit fully and the rest at 25%.

```
S, C  = cluster-aggregated support / contradiction mass
score = (S + a) / (S + C + a + b)          a = b = 0.25
interval = central 90% of Beta(S + a, C + b)   (evidence-weight uncertainty, heuristic)
```

Verdict thresholds, the partial-support rule, the minimum-coverage rule and review triggers are in
the same file. **The score is a heuristic summary, not a calibrated probability**; evaluation reports
how it tracks labels.

## Data model (PostgreSQL)

| Table | Key columns | Notes |
|---|---|---|
| `source_files` | `id`, `rel_path` (unique), `content_hash`, `raw_content`, `version`, `parser_signature`, `is_deleted` | one row per file |
| `source_file_versions` | `source_file_id`, `version`, `content_hash`, `raw_content`, `change_type` | every distinct version (new / modified / deleted) |
| `documents` | `id` (shared identifier or path slug), `kind` (file / thread / record / container), `doc_type` + `doc_type_source`, `status` + `status_source`, `owner`, `people`, `doc_date` + `date_source`, `effective/review/superseded_date`, `metadata` (JSONB), `flags`, line span, `content_hash` | soft-deleted, never hard-deleted |
| `sections` | `id` (`<doc>:<heading-slug>`), `document_id`, `parent_section_id`, `heading_path`, `section_role`, `text`, `plain_text`, line + char span, `content_hash`, `identifiers` | chunks reference their parent |
| `relationships` | `source_document_id`, `target_document_id`, `target_ref`, `rel_type`, `origin`, `is_inferred`, `confidence`, `evidence`, `attributes` | recomputed corpus-wide each ingestion |
| `ingestion_jobs` / `ingestion_issues` | status, mode, stats / severity, type, message, details | issue report per job |
| `claims` | `text`, `normalized_text`, `analysis`, `origin`, `latest_*`, `needs_review` | |
| `assessments` | `verdict`, `score`, `result`, `explanation`, `retrieval` (queries, limits, document + edge snapshots), `config_snapshot`, `config_hash`, `versions`, `as_of` | immutable audit record |
| `assessment_evidence` | `section_id`, `document_id`, `section_content_hash`, `section_text` snapshot, span offsets, `label`, `scope`, `strength`, `rationale`, `signals`, `features`, `contribution`, `cluster_id`, `retrieval` | one row per judged unit |
| `evaluation_runs` | dataset, config, metrics | |

Identifiers are deterministic (`sf_<sha1(path)>`, document = shared id, section = `<doc>:<slug>[-n]`,
relationship = hash of source/target/type), so ingestion is repeatable and assessments keep pointing
at the same sections.

## Elasticsearch index `okc-sections`

One document per leaf section: ids, document fields (title, type, kind, status, flags, owner, people,
date, identifiers), `heading`, `heading_path`, `section_role`, `text` (English analyzer + `exact`
sub-field), source path and line span, content hash, optional 384-d `embedding` (cosine HNSW).
Lexical queries use `multi_match` (title^2, heading^1.5, text) plus identifier boosts with named
clauses, so each hit explains why it matched. Hybrid search fuses BM25 and kNN rankings with RRF
(k = 60) in the service layer.

## Security

* Retrieved Markdown is untrusted. It reaches an LLM only inside delimited `<passage>` blocks with a
  system prompt that forbids following embedded instructions; the model has no tools; its output is
  schema-validated, quotes must be verbatim, and narratives citing unknown evidence are discarded.
  The synthetic corpus contains a prompt-injection string used in tests.
* Highlights are HTML-escaped by the API and rendered as text nodes in the UI (no `innerHTML`).
* Secrets live only in `.env` (git-ignored); logs pass through a redaction filter.
* Services bind to `127.0.0.1`; Elasticsearch security is disabled for local development only.
* No authentication (single-user local deployment by design) — add an auth proxy before any shared deployment.
