"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Badge, Card, CardBody, EmptyState, ErrorState, Input, NativeSelect, PageHeader, Skeleton } from "@/components/ui/primitives";
import { fmtDate, titleCase } from "@/lib/utils";

export default function DocumentsPage() {
  return (
    <Suspense fallback={<Skeleton className="mt-8 h-96" />}>
      <DocumentsView />
    </Suspense>
  );
}

function DocumentsView() {
  const params = useSearchParams();
  const [q, setQ] = useState("");
  const [type, setType] = useState(params.get("type") || "");
  const [records, setRecords] = useState(false);
  const facets = useQuery({ queryKey: ["facets"], queryFn: api.facets });
  const docs = useQuery({
    queryKey: ["documents", q, type, records],
    queryFn: () => api.documents({ q: q || undefined, doc_type: type ? [type] : undefined, include_records: records, limit: 500 }),
  });
  return (
    <div className="">
      <PageHeader title={<>What is <span className="hl">in the record</span>?</>} description="Every document read from the source files, with its type, status, date and warnings. Open one to read the original." />
      <div className="mb-3 flex flex-wrap gap-2">
        <Input className="max-w-xs" placeholder="Filter by id, title or path" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter documents" />
        <NativeSelect value={type} onChange={(e) => setType(e.target.value)} aria-label="Document type">
          <option value="">All types</option>
          {Object.keys(facets.data?.doc_types || {}).map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
        </NativeSelect>
        <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={records} onChange={(e) => setRecords(e.target.checked)} /> Include CSV records</label>
      </div>
      {docs.isError ? <ErrorState error={docs.error} /> : !docs.data ? <Skeleton className="h-96" /> : docs.data.items.length === 0 ? (
        <EmptyState title="No documents" body="Run an ingestion job to load the corpus." />
      ) : (
        <Card>
          <CardBody className="overflow-x-auto p-0">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr className="border-b border-border">
                  {["ID", "Title", "Type", "Status", "Date", "Sections", "Flags"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {docs.data.items.map((d) => (
                  <tr key={d.id} className="border-b border-line last:border-0 hover:bg-hl-faint">
                    <td className="px-3 py-2 font-mono text-xs"><Link className="text-primary hover:underline" href={`/documents/${encodeURIComponent(d.id)}`}>{d.id}</Link></td>
                    <td className="px-3 py-2 font-serif text-[1.02rem] text-ink">{d.title}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{titleCase(d.doc_type)}</td>
                    <td className="px-3 py-2"><Badge tone={d.status === "superseded" || d.status === "draft" ? "ambiguous" : "outline"}>{d.status}</Badge></td>
                    <td className="px-3 py-2 whitespace-nowrap text-muted-foreground">{fmtDate(d.doc_date)}</td>
                    <td className="px-3 py-2 tabular-nums">{d.section_count}</td>
                    <td className="px-3 py-2">{d.flags.map((f) => <Badge key={f} tone="ambiguous" className="mr-1">{f.replace(/_/g, " ")}</Badge>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
