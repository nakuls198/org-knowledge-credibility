"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { api } from "@/lib/api";
import type { SearchHit, SearchMode } from "@/lib/types";
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, ErrorState, Input, NativeSelect, PageHeader, Skeleton } from "@/components/ui/primitives";
import { Highlight, VerdictBadge } from "@/components/claims/badges";
import { fmtDate, titleCase } from "@/lib/utils";

const querySchema = z.string().trim().min(2, "Type at least two characters.").max(500);

function Preview({ hit }: { hit: SearchHit }) {
  const rels = useQuery({ queryKey: ["rels", hit.document_id], queryFn: () => api.relationships(hit.document_id, { include_mentions: false }) });
  return (
    <Card className="sticky top-4 bg-paper">
      <CardHeader
        title={`${hit.document_id} · ${hit.heading}`}
        description={`${titleCase(hit.doc_type)} · ${hit.status}${hit.doc_date ? ` · ${fmtDate(hit.doc_date)}` : ""}`}
        action={
          <Button asChild size="sm">
            <Link href={`/documents/${encodeURIComponent(hit.document_id)}?section=${encodeURIComponent(hit.section_id)}`}>Open</Link>
          </Button>
        }
      />
      <CardBody className="space-y-3 text-sm">
        <p className="text-xs text-muted-foreground">{hit.source_path} · lines {hit.start_line}–{hit.end_line}</p>
        <p className="quote max-h-80 overflow-auto whitespace-pre-wrap !text-[1rem]">{hit.text}</p>
        <div>
          <h3 className="mb-1 text-xs font-semibold">Related documents</h3>
          {rels.data?.length ? (
            <ul className="space-y-1 text-xs">
              {rels.data.slice(0, 10).map((r) => (
                <li key={r.id}>
                  <span className="text-muted-foreground">{r.direction === "outgoing" ? "→" : "←"} {r.rel_type.replace(/_/g, " ")}{r.is_inferred && " (inferred)"} </span>
                  {r.other_document ? (
                    <Link className="text-primary hover:underline" href={`/documents/${encodeURIComponent(r.other_document.id)}`}>
                      {r.other_document.id}
                    </Link>
                  ) : (
                    <span className="text-contradicts">{r.target_ref} (unresolved)</span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">{rels.isLoading ? "Loading…" : "No typed relationships."}</p>
          )}
        </div>
        {hit.related_claims.length > 0 && (
          <div>
            <h3 className="mb-1 text-xs font-semibold">Used as evidence for</h3>
            <ul className="space-y-1.5">
              {hit.related_claims.map((c) => (
                <li key={c.claim_id} className="text-xs">
                  <Link href={`/claims/${c.claim_id}`} className="hover:underline">{c.claim_text}</Link>{" "}
                  <VerdictBadge verdict={c.verdict} />
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

export default function SearchPage() {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<SearchMode>("bm25");
  const [docType, setDocType] = useState("");
  const [status, setStatus] = useState("");
  const [records, setRecords] = useState(true);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<SearchHit | null>(null);
  const facets = useQuery({ queryKey: ["facets"], queryFn: api.facets });
  const res = useQuery({
    queryKey: ["search", query, mode, docType, status, records, dateFrom, dateTo, offset],
    queryFn: () =>
      api.search({ q: query, mode, doc_type: docType ? [docType] : undefined, status: status ? [status] : undefined, include_records: records,
        date_from: dateFrom || undefined, date_to: dateTo || undefined, limit: 20, offset }),
    enabled: !!query,
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const p = querySchema.safeParse(input);
    if (!p.success) return setError(p.error.issues[0].message);
    setError(null);
    setOffset(0);
    setSelected(null);
    setQuery(p.data);
  }

  return (
    <div className="">
      <PageHeader title={<>Search <span className="hl">the record</span></>} description="Find passages across every policy, meeting, interview, message and system record. A match is not proof: check a claim to weigh the evidence." />
      <form onSubmit={submit} className="mb-4 space-y-2" noValidate>
        <div className="flex gap-2">
          <Input value={input} onChange={(e) => setInput(e.target.value)} placeholder="discount approval threshold" aria-label="Search query" aria-invalid={!!error} />
          <NativeSelect value={mode} onChange={(e) => setMode(e.target.value as SearchMode)} aria-label="Ranking mode">
            <option value="bm25">Keyword (BM25)</option>
            <option value="semantic">Semantic</option>
            <option value="hybrid">Hybrid (RRF)</option>
          </NativeSelect>
          <Button type="submit" variant="default">Search</Button>
        </div>
        {error && <p className="text-sm text-contradicts">{error}</p>}
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <NativeSelect value={docType} onChange={(e) => setDocType(e.target.value)} aria-label="Document type">
            <option value="">All types</option>
            {Object.keys(facets.data?.doc_types || {}).map((t) => (
              <option key={t} value={t}>{titleCase(t)}</option>
            ))}
          </NativeSelect>
          <NativeSelect value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="">Any status</option>
            {Object.keys(facets.data?.statuses || {}).map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </NativeSelect>
          <label className="text-xs text-muted-foreground">From <Input type="date" className="inline-block w-36" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} /></label>
          <label className="text-xs text-muted-foreground">To <Input type="date" className="inline-block w-36" value={dateTo} onChange={(e) => setDateTo(e.target.value)} /></label>
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={records} onChange={(e) => setRecords(e.target.checked)} /> Include CSV records</label>
        </div>
      </form>

      {!query ? (
        <EmptyState title="Search the organisation's knowledge" body="Try a topic such as 'renewal discount cap', a person, or an identifier such as POL-01." />
      ) : res.isError ? (
        <ErrorState error={res.error} onRetry={() => res.refetch()} />
      ) : !res.data ? (
        <Skeleton className="h-64" />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
          <div>
            <p className="mb-2 text-xs text-muted-foreground">
              {res.data.total} result(s) · {res.data.mode} on {res.data.backend} · {res.data.took_ms} ms
              {res.data.notes.map((n) => <span key={n} className="ml-2 bg-hl-soft px-1 text-ink">{n}</span>)}
            </p>
            {res.data.hits.length === 0 ? (
              <EmptyState title="No matches" body="Try fewer or different words, or remove filters." />
            ) : (
              <ol className="space-y-2">
                {res.data.hits.map((h) => (
                  <li key={h.section_id}>
                    <button onClick={() => setSelected(h)} className={`w-full border-b border-line py-3 text-left hover:bg-hl-faint ${selected?.section_id === h.section_id ? "bg-hl-faint shadow-[inset_3px_0_0_var(--ink)] pl-3" : ""}`}>
                      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <span className="font-mono">#{h.rank}</span>
                        <span className="font-semibold text-foreground">{h.document_id}</span>
                        <span>{h.heading}</span>
                        <Badge tone="outline">{titleCase(h.doc_type)}</Badge>
                        {h.status !== "unspecified" && <Badge tone={h.status === "superseded" || h.status === "draft" ? "ambiguous" : "outline"}>{h.status}</Badge>}
                        {h.related_claims.length > 0 && <Badge tone="accent">evidence for {h.related_claims.length} claim(s)</Badge>}
                      </div>
                      <div className="passage mt-1.5 font-serif text-[1.02rem] leading-relaxed text-ink">
                        {h.highlights.length ? h.highlights.map((f, i) => <span key={i}>{i > 0 && " … "}<Highlight fragment={f} /></span>) : h.text.slice(0, 240)}
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">{h.explanation} · {h.source_path}:{h.start_line}</div>
                    </button>
                  </li>
                ))}
              </ol>
            )}
            <div className="mt-3 flex gap-2">
              <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous</Button>
              <Button size="sm" disabled={offset + 20 >= res.data.total} onClick={() => setOffset(offset + 20)}>Next</Button>
            </div>
          </div>
          <div>{selected ? <Preview hit={selected} /> : <EmptyState title="Select a result" body="Preview the section, its related documents and the claims it was used for." />}</div>
        </div>
      )}
    </div>
  );
}
