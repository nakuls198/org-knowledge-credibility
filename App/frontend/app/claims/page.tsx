"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { api } from "@/lib/api";
import type { ClassifierMode, Verdict } from "@/lib/types";
import { Badge, Button, EmptyState, ErrorState, Input, NativeSelect, PageHeader, Section, Skeleton, Textarea } from "@/components/ui/primitives";
import { VERDICT_TEXT, VerdictBadge } from "@/components/claims/badges";
import { fmtDate } from "@/lib/utils";

const claimSchema = z.object({
  text: z
    .string()
    .trim()
    .min(10, "Write at least 10 characters.")
    .max(600, "Keep claims under 600 characters; split a compound claim into separate claims.")
    .refine((t) => t.split(/\s+/).length >= 3, "Write the claim as a full sentence (at least three words)."),
  classifier_mode: z.enum(["rules", "rules+nli", "llm", "llm+rules"]).optional(),
  as_of: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
});

const EXAMPLES = [
  "Discounts above 15% require Finance (Head of Finance & People Ops) approval before the deal is closed.",
  "In practice, the Head of Sales sometimes approves urgent discounts herself without waiting for Finance sign-off.",
  "The revised (v2) support escalation policy has been formally approved.",
  "Feature requests are currently tracked on a Trello board.",
];
const VERDICTS = Object.keys(VERDICT_TEXT) as Verdict[];

function NewClaim({ preset }: { preset: string }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [text, setText] = useState(preset);
  useEffect(() => setText(preset), [preset]);
  const [mode, setMode] = useState<ClassifierMode | "">("");
  const [asOf, setAsOf] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: api.createClaim,
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["claims"] });
      router.push(`/claims/${c.id}`);
    },
    onError: (e: Error) => setError(e.message),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = claimSchema.safeParse({ text, classifier_mode: mode || undefined, as_of: asOf || undefined });
    if (!parsed.success) return setError(parsed.error.issues[0].message);
    setError(null);
    create.mutate(parsed.data);
  }

  return (
    <form onSubmit={submit} className="mt-4 max-w-4xl space-y-3" noValidate>
      <label htmlFor="claim" className="kicker">
        The claim
      </label>
      <Textarea
        id="claim"
        rows={3}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setError(null);
        }}
        placeholder="All leave requests require manager approval."
        aria-invalid={!!error}
        aria-describedby={error ? "claim-error" : "claim-help"}
      />
      {error ? (
        <p id="claim-error" className="text-sm text-contradicts">
          {error}
        </p>
      ) : (
        <p id="claim-help" className="text-xs text-muted-foreground">
          One statement per claim. It is checked as written; nothing is rephrased.
        </p>
      )}
      <div className="flex flex-wrap items-end gap-4">
        <label className="text-xs font-medium text-ink">
          How passages are judged
          <NativeSelect className="mt-1 block" value={mode} onChange={(e) => setMode(e.target.value as ClassifierMode)}>
            <option value="">Recommended (rules + language model)</option>
            <option value="rules">Rules only (fastest)</option>
            <option value="rules+nli">Rules + language model</option>
            <option value="llm+rules">Local AI review + rules (slow on this computer)</option>
          </NativeSelect>
        </label>
        <label className="text-xs font-medium text-ink">
          As of date (optional)
          <Input type="date" className="mt-1 w-44" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
        </label>
        <Button type="submit" variant="default" className="h-9" disabled={create.isPending}>
          {create.isPending ? "Checking…" : "Check the record"}
        </Button>
      </div>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5 pt-1">
        <span className="kicker">Try</span>
        {EXAMPLES.map((ex) => (
          <button key={ex} type="button" onClick={() => setText(ex)} className="max-w-xs truncate border-b border-line text-left text-sm text-ink hover:bg-hl-soft" title={ex}>
            {ex}
          </button>
        ))}
      </div>
    </form>
  );
}

function Suggestions({ onPick }: { onPick: (text: string) => void }) {
  const [q, setQ] = useState("");
  const cands = useQuery({ queryKey: ["candidates", q], queryFn: () => api.candidates({ q: q || undefined, limit: 40 }) });
  return (
    <Section
      title="What do the documents claim?"
      description="Statements taken from policy requirements and meeting decisions. They are proposals to check, not facts."
      action={<Input placeholder="Filter" value={q} onChange={(e) => setQ(e.target.value)} className="w-56" aria-label="Filter suggested claims" />}
    >
      {cands.isError ? (
        <ErrorState error={cands.error} />
      ) : !cands.data ? (
        <Skeleton className="h-24" />
      ) : cands.data.items.length === 0 ? (
        <EmptyState title="No suggestions match." />
      ) : (
        <ul className="max-h-[26rem] divide-y divide-line overflow-auto border-y border-line">
          {cands.data.items.map((c) => (
            <li key={c.section_id + c.text} className="flex items-start justify-between gap-4 py-2.5">
              <div className="min-w-0">
                <p className="font-serif text-[1.05rem] leading-snug text-ink">{c.text}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  <Link className="underline decoration-line underline-offset-2 hover:bg-hl-soft" href={`/documents/${encodeURIComponent(c.document_id)}?section=${encodeURIComponent(c.section_id)}`}>
                    {c.document_id} · {c.section_heading}
                  </Link>{" "}
                  · {c.kind === "decision" ? "meeting decision" : "written rule"}
                  {c.caution && <span className="ml-1 bg-hl-soft px-1 text-ink">{c.caution}</span>}
                </p>
              </div>
              <Button size="sm" onClick={() => onPick(c.text)}>
                Check this
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function ClaimList() {
  const params = useSearchParams();
  const [q, setQ] = useState("");
  const [verdict, setVerdict] = useState<string>("");
  const [review, setReview] = useState(params.get("review") === "1");
  const list = useQuery({
    queryKey: ["claims", q, verdict, review],
    queryFn: () => api.claims({ q: q || undefined, verdict: verdict ? [verdict] : undefined, needs_review: review || undefined, limit: 100 }),
  });
  return (
    <Section
      title="Claims already checked"
      description={list.data ? `${list.data.total} claim${list.data.total === 1 ? "" : "s"}` : undefined}
      action={
        <div className="flex flex-wrap items-center gap-2">
          <Input placeholder="Search claims" value={q} onChange={(e) => setQ(e.target.value)} className="w-56" aria-label="Search claims" />
          <NativeSelect value={verdict} onChange={(e) => setVerdict(e.target.value)} aria-label="Filter by verdict">
            <option value="">Any verdict</option>
            {VERDICTS.map((v) => (
              <option key={v} value={v}>
                {VERDICT_TEXT[v]}
              </option>
            ))}
            <option value="UNASSESSED">Not checked yet</option>
          </NativeSelect>
          <label className="flex items-center gap-1.5 text-sm text-ink">
            <input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} className="accent-[var(--ink)]" /> Needs a person
          </label>
        </div>
      }
    >
      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : !list.data ? (
        <Skeleton className="h-40" />
      ) : list.data.items.length === 0 ? (
        <EmptyState title="No claims match." body="Check a claim above, or clear the filters." />
      ) : (
        <ul className="divide-y divide-line border-y border-line">
          {list.data.items.map((c) => (
            <li key={c.id}>
              <Link href={`/claims/${c.id}`} className="grid gap-x-6 gap-y-1 py-3 hover:bg-hl-faint sm:grid-cols-[minmax(0,1fr)_auto_4rem_8rem] sm:items-baseline">
                <span className="font-serif text-[1.05rem] leading-snug text-ink">
                  {c.text}
                  {c.needs_review && <Badge tone="hl" className="ml-2 align-middle">needs a person</Badge>}
                </span>
                <VerdictBadge verdict={c.latest_verdict} />
                <span className="font-serif text-lg tabular-nums text-ink">{c.latest_score?.toFixed(2) ?? "—"}</span>
                <span className="text-xs text-muted-foreground">{fmtDate(c.updated_at)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

export default function ClaimsPage() {
  const [preset, setPreset] = useState("");
  return (
    <div>
      <PageHeader
        title={
          <>
            Does the record <span className="hl">back it up</span>?
          </>
        }
        description="Write a claim about how the organisation works. You'll see the passages for and against it, how much each source counts, and why."
      />
      <NewClaim preset={preset} />
      <Suggestions
        onPick={(t) => {
          setPreset(t);
          window.scrollTo({ top: 0, behavior: "smooth" });
        }}
      />
      <Suspense fallback={<Skeleton className="h-40" />}>
        <ClaimList />
      </Suspense>
    </div>
  );
}
