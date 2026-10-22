import type {
  AssessmentDetail,
  AssessmentSummary,
  Claim,
  ClaimDetail,
  ClassifierMode,
  DocumentDetail,
  DocumentSummary,
  GraphResponse,
  IngestionJob,
  Issue,
  Page,
  Relationship,
  SearchMode,
  SearchResponse,
  Section,
  SourceFile,
} from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | string[] | undefined | null>;

function qs(params?: Query): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, x));
    else sp.append(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function request<T>(path: string, init?: RequestInit & { query?: Query }): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/v1${path}${qs(init?.query)}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "network_error", `Cannot reach the API at ${API_URL}. Is the backend running?`);
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const err = body?.error;
    const msg =
      err?.code === "validation_error" && Array.isArray(err.details)
        ? err.details.map((d: { msg: string }) => d.msg).join("; ")
        : err?.message || `Request failed (${res.status})`;
    throw new ApiError(res.status, err?.code || "http_error", msg, err?.details);
  }
  return body as T;
}

export const api = {
  health: () => request<{ status: string; components: Record<string, any> }>("/health"), // eslint-disable-line @typescript-eslint/no-explicit-any
  overview: () => request<Record<string, any>>("/overview"), // eslint-disable-line @typescript-eslint/no-explicit-any

  documents: (q: Query) => request<Page<DocumentSummary>>("/documents", { query: q }),
  document: (id: string) => request<DocumentDetail>(`/documents/${encodeURIComponent(id)}`),
  sections: (id: string) => request<Section[]>(`/documents/${encodeURIComponent(id)}/sections`),
  source: (id: string) => request<SourceFile>(`/documents/${encodeURIComponent(id)}/source`),
  relationships: (id: string, q?: Query) => request<Relationship[]>(`/documents/${encodeURIComponent(id)}/relationships`, { query: q }),

  search: (q: Query & { q: string; mode?: SearchMode }) => request<SearchResponse>("/search", { query: q }),
  facets: () => request<{ doc_types: Record<string, number>; statuses: Record<string, number>; people: Record<string, number> }>("/search/facets"),

  claims: (q: Query) => request<Page<Claim>>("/claims", { query: q }),
  candidates: (q: Query) =>
    request<{ items: { text: string; kind: string; document_id: string; section_id: string; section_heading: string; reason: string; caution: string | null }[]; total: number; note: string }>("/claims/candidates", { query: q }),
  claim: (id: string) => request<ClaimDetail>(`/claims/${encodeURIComponent(id)}`),
  createClaim: (body: { text: string; classifier_mode?: ClassifierMode; as_of?: string; tags?: string[] }) =>
    request<ClaimDetail>("/claims", { method: "POST", body: JSON.stringify(body) }),
  deleteClaim: (id: string) => request<void>(`/claims/${encodeURIComponent(id)}`, { method: "DELETE" }),
  reassess: (id: string, body: { classifier_mode?: ClassifierMode; as_of?: string; retrieval_mode?: SearchMode }) =>
    request<AssessmentSummary>(`/claims/${encodeURIComponent(id)}/assessments`, { method: "POST", body: JSON.stringify(body) }),
  assessment: (id: string) => request<AssessmentDetail>(`/assessments/${encodeURIComponent(id)}`),
  reproduce: (id: string) => request<Record<string, any>>(`/assessments/${encodeURIComponent(id)}/reproduce`), // eslint-disable-line @typescript-eslint/no-explicit-any
  graph: (id: string, q?: Query) => request<GraphResponse>(`/claims/${encodeURIComponent(id)}/graph`, { query: q }),

  ingestionJobs: () => request<Page<IngestionJob>>("/ingestion/jobs"),
  startIngestion: (mode: "incremental" | "full") =>
    request<IngestionJob>("/ingestion/jobs", { method: "POST", body: JSON.stringify({ mode }) }),
  issues: (q: Query) => request<Page<Issue>>("/ingestion/issues", { query: q }),

  evaluation: () => request<{ available: boolean; note: string | null; report: any; runs: any[] }>("/evaluation"), // eslint-disable-line @typescript-eslint/no-explicit-any
  evaluationClaims: () => request<{ items: Record<string, any>[] }>("/evaluation/claims"), // eslint-disable-line @typescript-eslint/no-explicit-any
};
