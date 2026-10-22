"use client";

import Link from "next/link";
import { Suspense, use, useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Card, CardBody, CardHeader, ErrorState, Skeleton } from "@/components/ui/primitives";
import { fmtDate, titleCase } from "@/lib/utils";

function DocumentView({ id }: { id: string }) {
  const params = useSearchParams();
  const sectionId = params.get("section");
  const line = Number(params.get("line")) || null;
  const doc = useQuery({ queryKey: ["document", id], queryFn: () => api.document(id) });
  const src = useQuery({ queryKey: ["source", id], queryFn: () => api.source(id) });
  const rels = useQuery({ queryKey: ["rels", id, "all"], queryFn: () => api.relationships(id) });
  const targetRef = useRef<HTMLDivElement>(null);
  const section = doc.data?.sections.find((s) => s.id === sectionId);
  const focusLine = line ?? section?.start_line ?? null;

  useEffect(() => {
    targetRef.current?.scrollIntoView({ block: "center" });
  }, [src.data, focusLine]);

  const lines = useMemo(() => src.data?.content.split("\n") || [], [src.data]);
  if (doc.isError) return <ErrorState error={doc.error} />;
  if (!doc.data) return <Skeleton className="h-96" />;
  const d = doc.data;
  const meta: [string, React.ReactNode][] = [
    ["Type", <>{titleCase(d.doc_type)} <span className="text-muted-foreground">({d.doc_type_source})</span></>],
    ["Status", <>{d.status} <span className="text-muted-foreground">({d.status_source})</span></>],
    ["Owner", d.owner || "—"],
    ["People", d.people.join(", ") || "—"],
    ["Version", d.version_label || "—"],
    ["Date", <>{fmtDate(d.doc_date)} {d.date_source && <span className="text-muted-foreground">({d.date_source})</span>}</>],
    ["Effective", fmtDate(d.effective_date)],
    ["Review", fmtDate(d.review_date)],
    ["Source", <span className="break-all">{d.rel_path} · lines {d.start_line}–{d.end_line} · v{d.source_version}</span>],
  ];

  return (
    <div className="">
      <Link href="/documents" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden="true" /> Documents
      </Link>
      <div className="hero"><div className="kicker mt-5">{d.id} · {d.doc_type.replace(/_/g, " ")}</div><h1 className="!mt-1.5 !text-[2.4rem] max-sm:!text-[1.8rem]">{d.title}</h1></div>
      <div className="mt-1 mb-4 flex flex-wrap gap-1.5">
        <Badge tone="outline">{d.id}</Badge>
        {d.flags.map((f) => <Badge key={f} tone="ambiguous">{f.replace(/_/g, " ")}</Badge>)}
        {d.is_deleted && <Badge tone="contradicts">deleted from corpus</Badge>}
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Card className="min-w-0">
          <CardHeader title="The original file" description={section ? `Highlighted: ${section.heading}${line ? ` (passage at line ${line})` : ""}` : "Lines belonging to this document are shown in full contrast"} />
          <CardBody className="p-0">
            {!src.data ? <Skeleton className="m-4 h-64" /> : (
              <div className="max-h-[72vh] overflow-auto border border-line bg-card py-2 font-mono text-[0.78rem] leading-5 text-ink">
                {lines.map((text, i) => {
                  const n = i + 1;
                  const inDoc = n >= d.start_line && n <= d.end_line;
                  const inSection = section && n >= section.start_line && n <= section.end_line;
                  const isTarget = n === focusLine;
                  return (
                    <div key={n} ref={isTarget ? targetRef : undefined} className={`flex ${isTarget ? "bg-hl" : inSection ? "bg-hl-faint" : ""} ${inDoc ? "" : "opacity-40"}`}>
                      <span className="w-12 shrink-0 select-none pr-3 text-right text-muted-foreground">{n}</span>
                      <span className="whitespace-pre-wrap break-words pr-4">{text || " "}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </CardBody>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="About this document" description="Each value notes where it was read from" />
            <CardBody>
              <dl className="grid grid-cols-[80px_1fr] gap-x-2 gap-y-1.5 text-xs">
                {meta.map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted-foreground">{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Sections" description={`${d.sections.length} section(s)`} />
            <ul className="max-h-64 overflow-auto text-xs">
              {d.sections.map((s) => (
                <li key={s.id}>
                  <Link href={`/documents/${encodeURIComponent(d.id)}?section=${encodeURIComponent(s.id)}`} className={`block border-b border-border px-4 py-1.5 hover:bg-muted ${s.id === sectionId ? "bg-accent" : ""}`}>
                    {s.heading} <span className="text-muted-foreground">· {s.section_role} · L{s.start_line}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
          <Card>
            <CardHeader title="Linked documents" description="Links, references and relations worked out from the text (inferred ones are marked)" />
            <CardBody className="text-xs">
              {rels.data?.length ? (
                <ul className="space-y-2">
                  {rels.data.map((r) => (
                    <li key={r.id}>
                      <div>
                        <span className="text-muted-foreground">{r.direction === "outgoing" ? "→" : "←"}</span>{" "}
                        <b className="font-medium">{r.rel_type.replace(/_/g, " ")}</b>{" "}
                        {r.other_document ? <Link className="text-primary hover:underline" href={`/documents/${encodeURIComponent(r.other_document.id)}`}>{r.other_document.id}</Link> : <span className="text-contradicts">{r.target_ref} (unresolved)</span>}
                        <Badge tone={r.is_inferred ? "ambiguous" : "outline"} className="ml-1">{r.origin}{r.is_inferred ? ` · ${r.confidence}` : ""}</Badge>
                      </div>
                      {r.evidence && <div className="mt-0.5 line-clamp-2 text-muted-foreground">“{r.evidence}”</div>}
                    </li>
                  ))}
                </ul>
              ) : <p className="text-muted-foreground">{rels.isLoading ? "Loading…" : "No relationships."}</p>}
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}

export default function DocumentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <Suspense fallback={<Skeleton className="h-96" />}>
      <DocumentView id={decodeURIComponent(id)} />
    </Suspense>
  );
}
