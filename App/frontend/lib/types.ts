// Mirrors backend/app/api/schemas.py (OpenAPI: /docs).

export type Verdict = "SUPPORTED" | "PARTIALLY_SUPPORTED" | "CONTESTED" | "CONTRADICTED" | "INSUFFICIENT_EVIDENCE";
export type Label = "SUPPORTS" | "CONTRADICTS" | "NEUTRAL" | "AMBIGUOUS";
export type ClassifierMode = "rules" | "rules+nli" | "llm" | "llm+rules";
export type SearchMode = "bm25" | "semantic" | "hybrid";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface DocumentSummary {
  id: string;
  title: string;
  doc_type: string;
  kind: string;
  status: string;
  identifier: string | null;
  owner: string | null;
  people: string[];
  doc_date: string | null;
  rel_path: string;
  flags: string[];
  section_count: number;
}

export interface Section {
  id: string;
  document_id: string;
  parent_section_id: string | null;
  ordinal: number;
  heading: string;
  heading_path: string[];
  level: number;
  section_role: string;
  text: string;
  plain_text: string;
  start_line: number;
  end_line: number;
  content_hash: string;
  identifiers: string[];
}

export interface DocumentDetail extends DocumentSummary {
  doc_type_source: string;
  status_source: string;
  version_label: string | null;
  date_source: string | null;
  effective_date: string | null;
  review_date: string | null;
  superseded_date: string | null;
  metadata: Record<string, unknown>;
  start_line: number;
  end_line: number;
  content_hash: string;
  is_deleted: boolean;
  source_version: number;
  sections: Section[];
  relationship_counts: Record<string, number>;
}

export interface Relationship {
  id: string;
  direction: "outgoing" | "incoming";
  rel_type: string;
  origin: string;
  is_inferred: boolean;
  confidence: number;
  evidence: string;
  source_document_id: string;
  target_document_id: string | null;
  target_ref: string;
  resolved: boolean;
  other_document: DocumentSummary | null;
  attributes: Record<string, unknown>;
}

export interface SourceFile {
  document_id: string;
  rel_path: string;
  file_kind: string;
  encoding: string;
  content_hash: string;
  version: number;
  start_line: number;
  end_line: number;
  content: string;
}

export interface SearchHit {
  section_id: string;
  document_id: string;
  doc_title: string;
  doc_type: string;
  status: string;
  heading: string;
  heading_path: string[];
  section_role: string;
  text: string;
  source_path: string;
  start_line: number;
  end_line: number;
  doc_date: string | null;
  people: string[];
  flags: string[];
  identifiers: string[];
  score: number;
  rank: number;
  highlights: string[];
  matched: string[];
  channel_ranks: Record<string, number>;
  explanation: string;
  related_claims: { claim_id: string; claim_text: string; verdict: Verdict | null; label: Label; assessment_id: string }[];
}

export interface SearchResponse {
  query: string;
  mode: SearchMode;
  backend: string;
  total: number;
  took_ms: number;
  hits: SearchHit[];
  notes: string[];
}

export interface AssessmentSummary {
  id: string;
  claim_id: string;
  status: "queued" | "running" | "completed" | "failed";
  verdict: Verdict | null;
  score: number | null;
  needs_review: boolean;
  as_of: string | null;
  created_at: string;
  completed_at: string | null;
  error: string | null;
  classifier: string | null;
}

export interface Claim {
  id: string;
  text: string;
  normalized_text: string;
  origin: string;
  tags: string[];
  latest_assessment_id: string | null;
  latest_verdict: Verdict | null;
  latest_score: number | null;
  needs_review: boolean;
  created_at: string;
  updated_at: string;
}

export interface ClaimDetail extends Claim {
  analysis: ClaimAnalysis;
  assessments: AssessmentSummary[];
  latest_assessment: AssessmentSummary | null;
}

export interface ClaimAnalysis {
  normalized?: string;
  claim_type?: "normative" | "practice" | "factual";
  claim_type_reason?: string;
  quantifier?: string;
  negated?: boolean;
  actors?: { mention: string; kind: string; person?: string | null; role?: string | null; function?: string | null }[];
  numbers?: { value: number; unit: string; comparator: string; raw: string }[];
  identifiers?: string[];
  warnings?: string[];
  conditions?: string[];
}

export interface Evidence {
  id: number;
  rank: number;
  evidence_ref: string;
  section_id: string;
  document_id: string;
  document: DocumentSummary | null;
  heading: string;
  section_role: string;
  source_path: string;
  start_line: number;
  end_line: number;
  span_line: number | null;
  span_start: number;
  span_end: number;
  span_text: string;
  section_text: string;
  section_changed_since: boolean;
  label: Label;
  scope: string;
  strength: number;
  rationale: string;
  signals: Record<string, unknown>;
  features: Record<string, number>;
  contribution: number;
  cluster_id: string | null;
  retrieval: { channels?: Record<string, number>; reasons?: string[]; relevance?: number; matched_terms?: string[] };
  duplicates: string[];
}

export interface Governance {
  supporting: string[];
  contradicting: string[];
  resolution: string;
  explanation: string;
  governing_document: string | null;
}

export interface Cluster {
  id: string;
  label: Label;
  members: number[];
  documents: string[];
  reasons: string[];
  mass: number;
}

export interface CredibilityResult {
  method: string;
  assessment_kind: string;
  score: number;
  verdict: Verdict;
  needs_review: boolean;
  review_reasons: string[];
  interval: [number, number];
  uncertainty_std: number;
  support_mass: number;
  contradict_mass: number;
  ambiguous_count: number;
  conflict: number;
  coverage: number;
  coverage_detail: { covered: string[]; missing: string[] };
  independent_support: number;
  independent_contradiction: number;
  clusters: Cluster[];
  practice_adherence: number | null;
  temporal_notes: string[];
  governance: Governance[];
  caveats: string[];
  missing_information: string[];
  next_steps: string[];
  as_of: string | null;
}

export interface EvidenceRef {
  evidence: string;
  document_id: string;
  section_id: string;
  scope: string;
  weight: number;
  why: string;
}

export interface Explanation {
  generated_by: string;
  summary: string;
  verdict: Verdict;
  claim_type: string;
  claim_type_reason: string;
  reasons: string[];
  key_supporting: EvidenceRef[];
  key_contradicting: EvidenceRef[];
  ambiguous: EvidenceRef[];
  temporal: string[];
  governance: Governance[];
  caveats: string[];
  uncertainty: string;
  missing_information: string[];
  next_steps: string[];
  limitations: string[];
  llm_narrative?: { text: string | null; model: string; validated: boolean; rejected_reason?: string };
}

export interface AssessmentDetail extends AssessmentSummary {
  claim_text: string;
  result: CredibilityResult;
  explanation: Explanation;
  retrieval: Record<string, unknown> & { queries?: Record<string, string> };
  config_hash: string | null;
  versions: Record<string, unknown>;
  evidence: Evidence[];
}

export interface GraphNode {
  id: string;
  type: "claim" | "evidence" | "document" | "related_document";
  label: string;
  data: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  label: string;
  data: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
}

export interface GraphResponse {
  claim_id: string;
  assessment_id: string | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  filters: { rel_types?: string[]; doc_types?: string[] };
}

export interface IngestionJob {
  id: string;
  status: string;
  mode: string;
  data_dir: string;
  stats: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  issue_counts: Record<string, number>;
}

export interface Issue {
  id: number;
  severity: "info" | "warning" | "error";
  issue_type: string;
  document_id: string | null;
  section_id: string | null;
  rel_path: string | null;
  message: string;
  details: Record<string, unknown>;
}
