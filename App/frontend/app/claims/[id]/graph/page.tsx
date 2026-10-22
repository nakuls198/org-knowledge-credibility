"use client";

import Link from "next/link";
import { Suspense, use, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, Plus } from "lucide-react";
import { api } from "@/lib/api";
import type { Label } from "@/lib/types";
import { Badge, Button, Card, CardBody, ErrorState, Skeleton } from "@/components/ui/primitives";
import { EvidenceGraph } from "@/components/graph/evidence-graph";
import { LabelBadge, VerdictBadge } from "@/components/claims/badges";
import { num, titleCase } from "@/lib/utils";

const LABELS: Label[] = ["SUPPORTS", "CONTRADICTS", "AMBIGUOUS", "NEUTRAL"];
const REL_TYPES = ["supersedes", "implements", "records_decision", "follows_up", "depends_on", "related_to", "references", "mentions"];

function GraphView({ id }: { id: string }) {
  const search = useSearchParams();
  const assessmentId = search.get("assessment") || undefined;
  const [labels, setLabels] = useState<Label[]>(["SUPPORTS", "CONTRADICTS", "AMBIGUOUS"]);
  const [relTypes, setRelTypes] = useState<string[]>(REL_TYPES.filter((r) => r !== "mentions" && r !== "references"));
  const [docTypes, setDocTypes] = useState<string[]>([]);
  const [expand, setExpand] = useState<string[]>([]);
  const [sel, setSel] = useState<Record<string, any> | null>(null); // eslint-disable-line @typescript-eslint/no-explicit-any

  const claim = useQuery({ queryKey: ["claim", id], queryFn: () => api.claim(id) });
  const graph = useQuery({
    queryKey: ["graph", id, assessmentId, labels, relTypes, docTypes, expand],
    queryFn: () =>
      api.graph(id, {
        assessment_id: assessmentId,
        label: labels,
        include_neutral: labels.includes("NEUTRAL"),
        rel_type: relTypes,
        doc_type: docTypes,
        expand,
      }),
    placeholderData: (prev) => prev,
  });
  const allDocTypes = graph.data?.filters.doc_types || [];
  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  return (
    <div className="mt-4 flex h-[calc(100vh-11rem)] min-h-[620px] flex-col">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <Link href={`/claims/${id}`} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft className="size-4" aria-hidden="true" /> Back to the claim
          </Link>
          <div className="kicker mt-2">Evidence map</div><h1 className="mt-1 truncate font-serif text-2xl font-medium text-ink">{claim.data?.text || "Evidence map"}</h1>
        </div>
        <VerdictBadge verdict={claim.data?.latest_verdict} />
      </div>

      <div className="mb-3 flex flex-wrap gap-x-6 gap-y-2 border-y border-line py-2 text-xs">
        <fieldset className="flex flex-wrap items-center gap-2">
          <legend className="sr-only">Evidence labels</legend>
          <span className="font-medium">Evidence</span>
          {LABELS.map((l) => (
            <label key={l} className="flex items-center gap-1">
              <input type="checkbox" checked={labels.includes(l)} onChange={() => setLabels(toggle(labels, l))} /> {l.toLowerCase()}
            </label>
          ))}
        </fieldset>
        <fieldset className="flex flex-wrap items-center gap-2">
          <legend className="sr-only">Relationship types</legend>
          <span className="font-medium">Relationships</span>
          {REL_TYPES.map((r) => (
            <label key={r} className="flex items-center gap-1">
              <input type="checkbox" checked={relTypes.includes(r)} onChange={() => setRelTypes(toggle(relTypes, r))} /> {r.replace(/_/g, " ")}
            </label>
          ))}
        </fieldset>
        {allDocTypes.length > 0 && (
          <fieldset className="flex flex-wrap items-center gap-2">
            <legend className="sr-only">Document types</legend>
            <span className="font-medium">Document types</span>
            {allDocTypes.map((t) => (
              <label key={t} className="flex items-center gap-1">
                <input type="checkbox" checked={docTypes.length === 0 || docTypes.includes(t)} onChange={() => setDocTypes(docTypes.length === 0 ? allDocTypes.filter((x) => x !== t) : toggle(docTypes, t))} /> {titleCase(t)}
              </label>
            ))}
          </fieldset>
        )}
      </div>

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-h-[460px] overflow-hidden border-t-2 border-ink bg-card">
          {graph.isError ? (
            <div className="p-4"><ErrorState error={graph.error} onRetry={() => graph.refetch()} /></div>
          ) : !graph.data ? (
            <Skeleton className="h-full rounded-none" />
          ) : (
            <EvidenceGraph graph={graph.data} onSelect={setSel} />
          )}
        </div>
        <Card className="overflow-auto">
          <CardBody className="space-y-3 text-sm">
            {!sel ? (
              <>
                <p className="font-medium">Select a node</p>
                <p className="text-muted-foreground">
                  Click a claim, evidence passage or document to inspect it. Green solid lines support the claim, red dashed lines contradict it,
                  grey lines are document relationships (dotted when inferred).
                </p>
              </>
            ) : sel.nodeType === "evidence" ? (
              <>
                <div className="flex items-center gap-2">
                  <span className="font-mono font-semibold">E{sel.rank}</span>
                  <LabelBadge label={sel.label} />
                  <Badge tone="outline">{sel.scope}</Badge>
                </div>
                <p className="quote whitespace-pre-wrap !text-[0.98rem]">{sel.span}</p>
                <p className="text-xs text-muted-foreground">{sel.rationale}</p>
                <p className="text-xs">weight {num(sel.weight)} · contribution {num(sel.contribution)} {sel.cluster_id && `· lineage ${sel.cluster_id}`}</p>
                <Button asChild size="sm">
                  <Link href={`/documents/${encodeURIComponent(sel.document_id)}?section=${encodeURIComponent(sel.section_id)}`}>
                    <ExternalLink aria-hidden="true" /> Open source passage
                  </Link>
                </Button>
              </>
            ) : sel.nodeType === "claim" ? (
              <>
                <p className="font-medium">Claim</p>
                <p>{sel.text}</p>
                <VerdictBadge verdict={sel.verdict} />
                <p className="text-xs text-muted-foreground">Heuristic score {num(sel.score)}</p>
              </>
            ) : (
              <>
                <p className="font-semibold">{sel.id?.replace("doc:", "")}</p>
                <p>{sel.title}</p>
                <dl className="grid grid-cols-[90px_1fr] gap-1 text-xs">
                  <dt className="text-muted-foreground">Type</dt><dd>{titleCase(sel.doc_type || "")}</dd>
                  <dt className="text-muted-foreground">Status</dt><dd>{sel.status}</dd>
                  <dt className="text-muted-foreground">Date</dt><dd>{sel.doc_date || "—"}</dd>
                  <dt className="text-muted-foreground">Owner</dt><dd>{sel.owner || "—"}</dd>
                  <dt className="text-muted-foreground">Path</dt><dd className="break-all">{sel.rel_path}</dd>
                </dl>
                {(sel.flags || []).map((f: string) => <Badge key={f} tone="ambiguous" className="mr-1">{f.replace(/_/g, " ")}</Badge>)}
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" onClick={() => setExpand((e) => (e.includes(sel.id.replace("doc:", "")) ? e : [...e, sel.id.replace("doc:", "")]))}>
                    <Plus aria-hidden="true" /> Expand relationships
                  </Button>
                  <Button asChild size="sm">
                    <Link href={`/documents/${encodeURIComponent(sel.id.replace("doc:", ""))}`}>Open document</Link>
                  </Button>
                </div>
              </>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

export default function GraphPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <Suspense fallback={<Skeleton className="h-96" />}>
      <GraphView id={id} />
    </Suspense>
  );
}
