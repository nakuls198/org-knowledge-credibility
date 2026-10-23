# Architecture and data model

```
          Streamlit UI (ui/)  ── HTTP/JSON ──►  FastAPI (backend/okc)
                                                  │
   api ─► answers / assessment ─► evidence (gather → classify) ─► credibility ─► explanation
                  │                     │
                  │                  search (service ─► memory | elastic backends)
   corpus (parse → relationships → checks → ingest) ─► db (SQLAlchemy)
   evaluation (metrics, calibration, sensitivity)
           │                  │                         │
      PostgreSQL         Elasticsearch            optional NLI / embeddings / Ollama
```

A modular monolith: one API, one UI, two data services. Ingestion and assessment run as background
tasks with job/status records; there is no queue because the corpus is small.
**PostgreSQL is the source of truth.** The search index is derived and can be rebuilt at any time
(`ingest --full`, `reindex`). SQLite plus the in-memory backend gives a dependency-free mode for
tests and small runs.

## Packages (`backend/okc/`)

| Package | Responsibility |
|---|---|
| `settings.py`, `common/` | environment settings and YAML config loading with a config hash; errors, logging, identifier and sentence helpers |
| `db/` | SQLAlchemy models and sessions |
| `corpus/` | Markdown and CSV parsing, metadata extraction, relationship resolution (typed edges with origin and confidence), consistency checks (cycles, duplicates, stale procedures, review dates), and the incremental ingest pipeline (SHA-256 diff, version history) |
| `search/` | documents to index, embeddings, BM25 / dense / hybrid (RRF) service, Elasticsearch and in-memory backends |
| `claims/` | claim analysis (type, quantifier, negation, thresholds, actors from a roster gazetteer) and candidate-claim extraction |
| `evidence/` | investigation (claim, counter-evidence, change, identifier and linked-document channels), span selection, named signals, rules classifier, NLI, optional Ollama LLM with schema validation, and the combination policy |
| `credibility/` | features, dependence clusters, aggregation, verdict, temporal and governance analysis, caveats and next steps, template explanation |
| `assessment/` | `investigate` and `run_assessment` / `reproduce` / `assess_text`: the audited path from a claim to a stored assessment |
| `answers/` | plain answers: question understanding, record compliance against `config/compliance.yaml`, rule Q&A, claim answers |
| `evaluation/` | metrics, measures, analyses and the reproducible runner |
| `api/` | schemas, converters, graph builder, routes; `okc/main.py` builds the app and `cli.py` is the command line |

## Credibility model

For each evidence unit *i* (all factors in [0, 1], configured in `config/credibility.yaml`):

```
weight_i = authority(claim_type, scope_i) × doc_type_modifier
         × temporal_i × directness_i × provenance_i × strength_i
```

* **scope**: formal, decision, proposal, practice, testimony, belief, history. Taken from parsed metadata and passage cues, never from a file name.
* **temporal**: superseded documents (for current claims), stale procedures, overdue reviews, history sections, undated sources and evidence after the as-of date are discounted.
* **dependence**: units in the same document, linked by `follows_up` / `depends_on` / `records_decision` / `implements`, near-duplicate in wording or meaning, or repeated testimony of one person form a cluster. A cluster contributes its strongest unit fully and the rest at a reduced weight.

```
S, C  = cluster-aggregated support / contradiction mass
score = (S + a) / (S + C + a + b)
interval = central 90% of Beta(S + a, C + b)
```

Verdict thresholds, the partial-support rule, the minimum-coverage rule and review triggers are in the same file. The score is a heuristic summary, not a calibrated probability.

## Data model

| Table | Notes |
|---|---|
| `source_files`, `source_file_versions` | one row per file; every distinct version is kept |
| `documents` | shared identifier or path slug as id; kind (file / thread / record / container); type, status, dates and the source of each value; soft-deleted |
| `sections` | `<doc>:<heading-slug>` ids, heading path, role, text, line and character spans, content hash |
| `relationships` | source, target, type, origin, inferred flag, confidence, evidence; recomputed corpus-wide each ingestion |
| `ingestion_jobs`, `ingestion_issues` | status, mode, statistics; issue report per job |
| `claims`, `assessments`, `assessment_evidence` | the immutable audit record: verdict, score, retrieval queries, configuration snapshot and hash, versions, and one row per judged unit with text snapshot, label, signals, features and contribution |
| `evaluation_runs` | dataset, configuration, metrics |

Identifiers are deterministic, so ingestion is repeatable and assessments keep pointing at the same sections.

## Security

* Retrieved Markdown is untrusted. It reaches an LLM only inside delimited passage blocks with a system prompt that forbids following embedded instructions; the model has no tools, its output is schema-validated and quotes must be verbatim. The sample corpus contains a prompt-injection string used in tests.
* Highlights are HTML-escaped by the API.
* Secrets live only in `.env` (git-ignored); logs pass through a redaction filter.
* Services bind to `127.0.0.1`; Elasticsearch security is disabled for local development only.
* No authentication (single-user local deployment by design). Add an auth proxy before any shared deployment.
