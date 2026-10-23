# API (v1)

Interactive OpenAPI documentation: `http://localhost:8001/docs`.
Errors use one shape: `{"error": {"code": "...", "message": "...", "details": ...}}`
(`validation_error` 422, `not_found` 404, `conflict` 409, `service_unavailable` 503).
Lists are paginated: `{"items": [...], "total", "limit", "offset"}`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | database, search index, NLI / embeddings / LLM availability |
| GET | `/api/v1/overview` | corpus statistics computed from the store (includes `source_files`) |
| GET | `/api/v1/config/credibility` | authority weights and temporal factors in force |
| POST | `/api/v1/ingestion/jobs` | start ingestion `{"mode": "incremental" \| "full"}` → 202 |
| GET | `/api/v1/ingestion/jobs`, `/ingestion/jobs/{job_id}` | job status and statistics |
| GET | `/api/v1/ingestion/issues` | issue report (`issue_type`, `severity`, `job_id`) |
| GET | `/api/v1/documents` | filter by `doc_type`, `status`, `kind`, `q`; records excluded unless `include_records=true` |
| GET | `/api/v1/documents/{id}` | metadata (each with its source), sections, relationship counts |
| GET | `/api/v1/documents/{id}/sections`, `/source`, `/relationships` | sections with line spans; original file text; typed edges |
| GET | `/api/v1/sections/{section_id}` | one section |
| GET | `/api/v1/search` | `q`, `mode=bm25\|semantic\|hybrid`, filters, paging; hits carry highlights and an explanation |
| GET | `/api/v1/search/facets` | filter values |
| POST | `/api/v1/answer` | plain answer to any question or claim `{question, classifier_mode?, retrieval_mode?, max_evidence?, fresh?}`: a named record (`DEAL-027`) is checked against the rulebook, "which deals broke the policy?" scans every record, rule questions are answered from the rulebook and the records, anything else is checked as a claim |
| POST | `/api/v1/claims` | create `{text, classifier_mode?, as_of?, assess=true}` → 201, assessment queued |
| GET | `/api/v1/claims/candidates` | candidate claims extracted from requirements and decisions (proposals, not facts) |
| POST | `/api/v1/claims/analyze` | validate and analyse a claim without storing it |
| GET | `/api/v1/claims` | `q`, `verdict`, `needs_review`, `origin` |
| GET / DELETE | `/api/v1/claims/{id}` | claim with analysis and assessment history |
| POST | `/api/v1/claims/{id}/assessments` | re-run `{classifier_mode?, as_of?, max_evidence?, retrieval_mode?}` → 202 |
| GET | `/api/v1/claims/{id}/evidence`, `/graph` | evidence of the latest (or given) assessment; claim → evidence → document graph |
| GET | `/api/v1/assessments/{id}` | full audit record |
| GET | `/api/v1/assessments/{id}/reproduce` | re-score from the recorded evidence and configuration and compare |
| GET | `/api/v1/assessments/{id}/config` | configuration snapshot and versions |
| GET | `/api/v1/evaluation`, `/evaluation/claims` | latest evaluation report; the labelled claim set |

Assessment lifecycle: `queued → running → completed | failed`; poll `GET /claims/{id}` or `GET /assessments/{id}`.
