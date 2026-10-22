"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, ErrorState, NativeSelect, PageHeader, Skeleton } from "@/components/ui/primitives";
import { fmtDate, titleCase } from "@/lib/utils";

function IngestionView() {
  const params = useSearchParams();
  const qc = useQueryClient();
  const [type, setType] = useState(params.get("type") || "");
  const [severity, setSeverity] = useState("");
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: api.ingestionJobs,
    refetchInterval: (q) => (q.state.data?.items.some((j) => ["queued", "running"].includes(j.status)) ? 2000 : false),
  });
  const issues = useQuery({ queryKey: ["issues", type, severity], queryFn: () => api.issues({ issue_type: type || undefined, severity: severity || undefined, limit: 500 }) });
  const start = useMutation({
    mutationFn: api.startIngestion,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
  const running = jobs.data?.items.some((j) => ["queued", "running"].includes(j.status));
  const latest = jobs.data?.items[0];
  const types = Array.from(new Set((issues.data?.items || []).map((i) => i.issue_type)));

  return (
    <div className="space-y-4">
      <PageHeader
        title={<>Is the record <span className="hl">in good shape</span>?</>}
        description="Reading the files again only processes what changed: new and edited files are re-read, deleted ones are removed, the rest are skipped."
        actions={
          <div className="flex gap-2">
            <Button onClick={() => start.mutate("incremental")} disabled={running || start.isPending}>Read changed files</Button>
            <Button variant="default" onClick={() => start.mutate("full")} disabled={running || start.isPending}>Rebuild everything</Button>
          </div>
        }
      />
      {start.isError && <ErrorState error={start.error} />}
      <Card>
        <CardHeader title="Jobs" description={running ? "A job is running…" : latest ? `Latest: ${latest.status} · ${fmtDate(latest.finished_at)}` : undefined} />
        <CardBody className="overflow-x-auto p-0">
          {!jobs.data ? <Skeleton className="m-4 h-24" /> : jobs.data.items.length === 0 ? <div className="p-4"><EmptyState title="No ingestion yet" body="Run a full rebuild to load the corpus." /></div> : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr className="border-b border-border">{["Started", "Mode", "Status", "New", "Modified", "Unchanged", "Deleted", "Indexed", "Issues"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
              </thead>
              <tbody>
                {jobs.data.items.map((j) => (
                  <tr key={j.id} className="border-b border-border last:border-0">
                    <td className="px-3 py-2 whitespace-nowrap">{fmtDate(j.created_at)}</td>
                    <td className="px-3 py-2">{j.mode}</td>
                    <td className="px-3 py-2"><Badge tone={j.status === "succeeded" ? "supports" : j.status === "failed" ? "contradicts" : "accent"}>{j.status}</Badge>{j.error && <div className="text-xs text-contradicts">{j.error}</div>}</td>
                    {["files_new", "files_modified", "files_unchanged", "files_deleted", "sections_indexed", "issues"].map((k) => <td key={k} className="px-3 py-2 tabular-nums">{j.stats?.[k] ?? "—"}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Issue report" description="Broken references, circular links, duplicates, ambiguous identities, stale procedures and missing metadata from the latest successful job" />
        <CardBody className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <NativeSelect value={type} onChange={(e) => setType(e.target.value)} aria-label="Issue type">
              <option value="">All issue types</option>
              {Array.from(new Set([type, ...types].filter(Boolean))).map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
            </NativeSelect>
            <NativeSelect value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity">
              <option value="">Any severity</option><option value="error">error</option><option value="warning">warning</option><option value="info">info</option>
            </NativeSelect>
          </div>
          {issues.isError ? <ErrorState error={issues.error} /> : !issues.data ? <Skeleton className="h-40" /> : issues.data.items.length === 0 ? <EmptyState title="No issues" /> : (
            <ul className="divide-y divide-border text-sm">
              {issues.data.items.map((i) => (
                <li key={i.id} className="flex flex-wrap items-start gap-2 py-2">
                  <Badge tone={i.severity === "info" ? "outline" : i.severity === "error" ? "contradicts" : "ambiguous"}>{i.severity}</Badge>
                  <span className="text-xs text-muted-foreground">{titleCase(i.issue_type)}</span>
                  <span className="flex-1">{i.message}</span>
                  {i.document_id && <Link className="text-xs text-primary hover:underline" href={`/documents/${encodeURIComponent(i.document_id)}`}>{i.document_id}</Link>}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

export default function IngestionPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96" />}>
      <IngestionView />
    </Suspense>
  );
}
