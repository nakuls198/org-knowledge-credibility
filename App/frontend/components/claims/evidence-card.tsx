"use client";

import Link from "next/link";
import { useState } from "react";
import type { Evidence } from "@/lib/types";
import { Badge } from "@/components/ui/primitives";
import { LabelBadge, SpanInContext } from "./badges";
import { fmtDate, num, titleCase } from "@/lib/utils";

const FEATURES: { key: string; label: string; help: string }[] = [
  { key: "authority", label: "Authority", help: "How much this kind of source counts for this kind of claim" },
  { key: "temporal", label: "Still applies", help: "Lower if superseded, out of date or past its review date" },
  { key: "directness", label: "Directness", help: "A direct statement or record vs hearsay or a hedged account" },
  { key: "provenance", label: "Provenance", help: "Date, author, status and path are known" },
  { key: "strength", label: "Strength", help: "How strongly the passage supports or contradicts the claim" },
];

const SCOPE_TEXT: Record<string, string> = {
  formal: "written rule",
  decision: "meeting decision",
  proposal: "proposal or draft",
  practice: "what actually happened",
  testimony: "first-hand account",
  belief: "second-hand belief",
  history: "past version",
  discussion: "discussion",
};

// Fields that carry the substance of a system record; the rest stay one click away.
const KEY_FIELDS = ["discount pct", "approval record", "approval role", "stage", "outcome", "notes", "action", "actor name",
  "actor role", "object", "new state", "comments", "status history", "owner name", "project", "close date", "timestamp"];

function RecordFields({ text }: { text: string }) {
  const rows = text.split("\n").map((l) => {
    const i = l.indexOf(":");
    return i > 0 ? [l.slice(0, i).trim(), l.slice(i + 1).trim()] : [l, ""];
  });
  const shown = rows.filter(([k]) => KEY_FIELDS.includes(k.toLowerCase()));
  return (
    <dl className="grid grid-cols-[9rem_minmax(0,1fr)] gap-x-4 gap-y-0.5 font-sans text-sm">
      {(shown.length ? shown : rows.slice(0, 8)).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function EvidenceCard({ ev, clusterSize }: { ev: Evidence; clusterSize: number }) {
  const [open, setOpen] = useState(false);
  const doc = ev.document;
  const href = `/documents/${encodeURIComponent(ev.document_id)}?section=${encodeURIComponent(ev.section_id)}${ev.span_line ? `&line=${ev.span_line}` : ""}`;
  return (
    <article className="border-b border-line py-4" aria-label={`Evidence ${ev.evidence_ref}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs font-semibold text-ink">{ev.evidence_ref}</span>
        <LabelBadge label={ev.label} />
        <Badge tone="outline">{SCOPE_TEXT[ev.scope] ?? ev.scope}</Badge>
        {(doc?.flags || []).map((f) => (
          <Badge key={f} tone="hl">
            {f.replace(/_/g, " ")}
          </Badge>
        ))}
        {ev.section_changed_since && <Badge tone="hl">source changed since</Badge>}
      </div>
      <div className="quote">
        {doc?.kind === "record" ? (
          <RecordFields text={ev.section_text} />
        ) : (
          <SpanInContext text={ev.section_text} start={ev.span_start} end={ev.span_end} className="line-clamp-6 text-[1.05rem]" />
        )}
        <div className="cite">
          <b>{doc?.title ?? ev.document_id}</b>
          {doc && ` · ${titleCase(doc.doc_type)} · ${doc.status}`}
          {doc?.doc_date && ` · ${fmtDate(doc.doc_date)}`} ·{" "}
          <Link href={href} className="text-ink underline decoration-[#e0c200] decoration-2 underline-offset-[3px]">
            {ev.source_path}
            {ev.span_line ? `, line ${ev.span_line}` : ""}
          </Link>
        </div>
      </div>
      <p className="readable mt-2 text-sm text-muted-foreground">{ev.rationale}</p>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          counts <b className="font-serif text-sm font-medium text-ink">{num(ev.features.weight)}</b> · contribution{" "}
          <b className="font-serif text-sm font-medium text-ink">
            {ev.contribution >= 0 ? "+" : ""}
            {num(ev.contribution)}
          </b>
        </span>
        {ev.cluster_id && clusterSize > 1 && (
          <span className="bg-hl-soft px-1 text-ink">
            same origin as {clusterSize - 1} other passage{clusterSize > 2 ? "s" : ""}, so they count once
          </span>
        )}
        <button className="ml-auto font-medium text-ink underline decoration-line underline-offset-2 hover:bg-hl-soft" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? "Hide the working" : "Show the working"}
        </button>
      </div>
      {open && (
        <div className="mt-3 grid gap-6 border-t border-line pt-3 text-xs md:grid-cols-2">
          <div>
            <div className="kicker mb-2">How much it counts</div>
            <dl className="space-y-1.5">
              {FEATURES.map((f) => (
                <div key={f.key} className="grid grid-cols-[6.5rem_minmax(0,1fr)_2.5rem] items-center gap-2" title={f.help}>
                  <dt className="text-ink">{f.label}</dt>
                  <dd className="h-1.5 bg-paper-2">
                    <div className="h-1.5 bg-ink" style={{ width: `${Math.min(100, (ev.features[f.key] ?? 0) * 100)}%` }} />
                  </dd>
                  <span className="text-right tabular-nums">{num(ev.features[f.key])}</span>
                </div>
              ))}
            </dl>
            <p className="mt-2 text-muted-foreground">The weight is the product of these five factors.</p>
          </div>
          <div>
            <div className="kicker mb-1">Why it was found</div>
            <ul className="list-disc space-y-0.5 pl-4">
              {(ev.retrieval.reasons || []).map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
            <div className="kicker mb-1 mt-3">Signals behind the judgement</div>
            <pre className="max-h-44 overflow-auto bg-paper-2 p-2 font-mono text-[11px] leading-snug text-ink">{JSON.stringify(ev.signals, null, 1)}</pre>
          </div>
        </div>
      )}
    </article>
  );
}
