# API (v1)

Interactive OpenAPI documentation: `http://localhost:8001/docs` (Swagger) and `/redoc`.
All errors use one shape: `{"error": {"code": "...", "message": "...", "details": ...}}`
(`validation_error` 422, `not_found` 404, `conflict` 409, `service_unavailable` 503).
Lists are paginated: `{"items": [...], "total", "limit", "offset"}`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | database, search index, NLI / embeddings / LLM availability |
| GET | `/api/v1/overview` | dashboard statistics computed from the store |
| POST | `/api/v1/ingestion/jobs` | start ingestion `{"mode": "incremental" \| "full"}` → 202 |
| GET | `/api/v1/ingestion/jobs`, `/api/v1/ingestion/jobs/{job_id}` | job status and statistics |
| GET | `/api/v1/ingestion/issues` | issue report (`issue_type`, `severity`, `job_id`) |
| GET | `/api/v1/documents` | filter by `doc_type`, `status`, `kind`, `q`; records excluded unless `include_records=true` |
| GET | `/api/v1/documents/{id}` | metadata (each with its source), sections, relationship counts |
| GET | `/api/v1/documents/{id}/sections` | sections with line spans and hashes |
| GET | `/api/v1/documents/{id}/source` | original file content + this document's line range |
| GET | `/api/v1/documents/{id}/relationships` | incoming / outgoing typed edges with origin, confidence, evidence |
| GET | `/api/v1/sections/{section_id}` | one section |
| GET | `/api/v1/search` | `q`, `mode=bm25\|semantic\|hybrid`, `doc_type`, `status`, `person`, `identifier`, `date_from`, `date_to`, `include_records`, `limit`, `offset`; hits carry highlights, explanation, channel ranks and linked claims |
| GET | `/api/v1/search/facets` | filter values |
| POST | `/api/v1/answer` | **plain answer** to any question or claim `{question, classifier_mode?, retrieval_mode?, max_evidence?, fresh?}`: a named record (`DEAL-027`) is checked against the policy rulebook; "which deals broke the policy?" scans every record; rule questions (who may approve, caps, thresholds) are answered from the rulebook plus what the records show; anything else is checked as a claim. Returns `outcome`, `headline`, `answer`, `reasons`, `caveats`, `evidence` (with source and role) and, for claims, the `assessment_id` |
| POST | `/api/v1/claims` | create `{text, classifier_mode?, as_of?, assess=true}` → 201, assessment queued |
| GET | `/api/v1/claims/candidates` | candidate claims extracted from policy requirements and meeting decisions (`kind`, `q`, `document_id`); proposals, not facts |
| POST | `/api/v1/claims/analyze` | validate and analyse a claim without storing it |
| GET | `/api/v1/claims` | `q`, `verdict`, `needs_review`, `origin` |
| GET / DELETE | `/api/v1/claims/{id}` | claim with analysis and assessment history |
| POST | `/api/v1/claims/{id}/assessments` | re-run `{classifier_mode?, as_of?, max_evidence?, retrieval_mode?}` → 202 |
| GET | `/api/v1/claims/{id}/evidence` | evidence of the latest (or `assessment_id`) assessment, filter by `label` |
| GET | `/api/v1/claims/{id}/graph` | nodes/edges: claim → evidence → document → related documents; `expand`, `rel_type`, `doc_type`, `label`, `include_neutral` |
| GET | `/api/v1/assessments/{id}` | full audit record: result, explanation, evidence with spans/features/signals/source line |
| GET | `/api/v1/assessments/{id}/reproduce` | re-score from the recorded evidence and configuration and compare |
| GET | `/api/v1/assessments/{id}/config` | configuration snapshot and versions |
| GET | `/api/v1/evaluation` | latest evaluation report and run history |
| GET | `/api/v1/evaluation/claims` | the labelled evaluation claim set |

Assessment lifecycle: `queued → running → completed | failed`; poll `GET /claims/{id}` or
`GET /assessments/{id}`.
