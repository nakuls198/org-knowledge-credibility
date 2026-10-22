"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Claim, Verdict } from "@/lib/types";
import { Button, EmptyState, ErrorState, Section, Skeleton, Stat } from "@/components/ui/primitives";
import { VERDICT_COLOR, VERDICT_TEXT, VerdictBadge } from "@/components/claims/badges";
import { fmtDate, pct, titleCase } from "@/lib/utils";

const DOC_TYPE_LABEL: Record<string, string> = {
  policy: "Policies",
  procedure: "Procedures",
  meeting_note: "Meeting notes",
  interview: "Interviews",
  communication: "Emails and chats",
  org_reference: "Org charts and roles",
  records: "System records (CRM, tasks, audit)",
};

function QuickCheck() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: api.createClaim,
    onSuccess: (c) => router.push(`/claims/${c.id}`),
    onError: (e: Error) => setErr(e.message),
  });
  return (
    <form
      className="mt-6 max-w-3xl"
      onSubmit={(e) => {
        e.preventDefault();
        const t = text.trim();
        if (t.split(/\s+/).length < 3 || t.length < 10) return setErr("Write the claim as a full sentence (at least three words).");
        setErr(null);
        create.mutate({ text: t });
      }}
      noValidate
    >
      <label htmlFor="quick-claim" className="kicker">
        Check a claim
      </label>
      <div className="mt-1.5 flex flex-col gap-2 sm:flex-row">
        <input
          id="quick-claim"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setErr(null);
          }}
          placeholder="Expense claims over $500 need approval from the Head of Finance."
          className="h-12 min-h-12 w-full sm:flex-1 rounded-[2px] border-[1.5px] border-ink bg-card px-3.5 font-serif text-lg text-ink placeholder:text-muted-foreground focus:shadow-[0_0_0_3px_var(--hl)] focus:outline-none"
          aria-invalid={!!err}
          aria-describedby={err ? "quick-err" : undefined}
        />
        <Button type="submit" variant="default" className="h-12 px-6" disabled={create.isPending}>
          {create.isPending ? "Checking…" : "Check the record"}
        </Button>
      </div>
      {err && (
        <p id="quick-err" className="mt-1.5 text-sm text-contradicts">
          {err}
        </p>
      )}
    </form>
  );
}

function Bars({ rows }: { rows: { label: string; value: number; color?: string; href?: string }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="bars">
      {rows.map((r) => (
        <div key={r.label} className="row">
          {r.href ? (
            <Link href={r.href} className="truncate hover:bg-hl-soft">
              {r.label}
            </Link>
          ) : (
            <div className="truncate">{r.label}</div>
          )}
          <span>{r.value}</span>
          <i style={{ ["--w" as string]: `${Math.max(2, (r.value / max) * 100)}%`, ["--bar" as string]: r.color ?? "var(--ink)" }} />
        </div>
      ))}
    </div>
  );
}

export default function FrontPage() {
  const ov = useQuery({ queryKey: ["overview"], queryFn: api.overview });
  const review = useQuery({ queryKey: ["claims", "review"], queryFn: () => api.claims({ needs_review: true, limit: 6 }) });
  const recent = useQuery({ queryKey: ["claims", "recent"], queryFn: () => api.claims({ limit: 6 }) });

  if (ov.isError) return <ErrorState error={ov.error} onRetry={() => ov.refetch()} />;
  const o = ov.data;

  const byType: Record<string, number> = {};
  for (const d of (o?.corpus.documents_by_type || []) as { doc_type: string; kind: string; count: number }[]) {
    if (d.kind === "container") continue;
    const key = d.kind === "record" ? "records" : d.doc_type;
    byType[key] = (byType[key] || 0) + d.count;
  }
  const typeRows = Object.entries(byType)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => ({ label: DOC_TYPE_LABEL[k] || titleCase(k), value: v, href: k === "records" ? "/documents" : `/documents?type=${k}` }));
  const verdictRows = (Object.keys(VERDICT_TEXT) as Verdict[])
    .map((v) => ({ label: VERDICT_TEXT[v], value: (o?.claims.by_verdict?.[v] as number) || 0, color: VERDICT_COLOR[v] }))
    .filter((r) => r.value > 0);
  const issues = ((o?.ingestion.issues || []) as { severity: string; issue_type: string; count: number }[]).sort((a, b) => b.count - a.count);
  const records = byType.records || 0;
  const evalSummary = o?.evaluation?.summary as Record<string, any> | undefined; // eslint-disable-line @typescript-eslint/no-explicit-any
  const headline = evalSummary?.["rules+nli"] ?? (evalSummary ? Object.entries(evalSummary).find(([k]) => k !== "retrieval")?.[1] : undefined);

  return (
    <div>
      <div className="hero">
        <h1>
          Is it <span className="hl">really</span> how the organisation works?
        </h1>
        <p>
          State a claim about how things are done. The record is searched for policies, decisions, interviews, messages and system
          logs that back it up or say otherwise, and each source is weighed for authority, date and independence.
        </p>
      </div>
      <QuickCheck />

      <div className="mt-10 grid grid-cols-2 gap-6 lg:grid-cols-4">
        {!o ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20" />)
        ) : (
          <>
            <Stat label="Documents in the record" value={o.corpus.documents_total} hint={`${o.corpus.documents_total - records} documents · ${records} system records`} />
            <Stat label="Passages indexed" value={o.corpus.sections_total} hint={`${o.relationships.reduce((s: number, r: { count: number }) => s + r.count, 0)} links between documents`} />
            <Stat label="Claims checked" value={o.claims.total} hint={`${o.claims.contradicting_evidence_items} contradicting passages found`} />
            <Stat label="Waiting for a person" value={o.claims.needs_review} hint="conflicts the record can't settle" tone={o.claims.needs_review ? "warn" : undefined} />
          </>
        )}
      </div>

      <div className="grid gap-x-12 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <Section title="Which claims need a person to decide?" description="The evidence conflicts, no document clearly governs, or the uncertainty is wide.">
          {review.data?.items.length ? (
            <ul className="divide-y divide-line border-y border-line">
              {review.data.items.map((c: Claim) => (
                <li key={c.id}>
                  <Link href={`/claims/${c.id}`} className="flex items-start justify-between gap-4 py-3 hover:bg-hl-faint">
                    <span className="font-serif text-[1.08rem] leading-snug text-ink">{c.text}</span>
                    <VerdictBadge verdict={c.latest_verdict as Verdict} />
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="Nothing is waiting for review." body="Claims that need a human decision appear here." />
          )}
        </Section>

        <Section title="Recently checked" action={<Link className="text-sm underline decoration-[#e0c200] decoration-2 underline-offset-[3px]" href="/claims">All claims</Link>}>
          {recent.data?.items.length ? (
            <ul className="divide-y divide-line border-y border-line">
              {recent.data.items.map((c: Claim) => (
                <li key={c.id}>
                  <Link href={`/claims/${c.id}`} className="block py-2.5 hover:bg-hl-faint">
                    <span className="text-[0.95rem] text-ink">{c.text}</span>
                    <span className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                      <VerdictBadge verdict={c.latest_verdict as Verdict} /> {fmtDate(c.updated_at)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No claims checked yet." body="Use the box above to check your first claim." />
          )}
        </Section>
      </div>

      <div className="grid gap-x-12 lg:grid-cols-3">
        <Section title="What is in the record?" description="Document types are read from each file's metadata or folder.">
          {typeRows.length ? <Bars rows={typeRows} /> : <Skeleton className="h-40" />}
        </Section>
        <Section title="How have claims come out?" description="Latest verdict for each claim checked.">
          {verdictRows.length ? <Bars rows={verdictRows} /> : <p className="text-sm text-muted-foreground">No verdicts yet.</p>}
        </Section>
        <Section
          title="Is the record in good shape?"
          description={o?.ingestion.latest_job ? `Last read ${fmtDate(o.ingestion.latest_job.finished_at)}` : undefined}
          action={<Link className="text-sm underline decoration-[#e0c200] decoration-2 underline-offset-[3px]" href="/ingestion">Details</Link>}
        >
          {issues.length ? (
            <Bars rows={issues.slice(0, 6).map((i) => ({ label: titleCase(i.issue_type), value: i.count, href: `/ingestion?type=${i.issue_type}` }))} />
          ) : (
            <p className="text-sm text-muted-foreground">No problems found in the source files.</p>
          )}
        </Section>
      </div>

      <Section
        title="How accurate is it?"
        description={
          o?.evaluation
            ? `Measured on ${o.evaluation.dataset?.n_claims ?? "labelled"} labelled claims on ${fmtDate(o.evaluation.created_at)}. Only computed results are shown.`
            : "Results appear here after the evaluation has been run."
        }
        action={<Link className="text-sm underline decoration-[#e0c200] decoration-2 underline-offset-[3px]" href="/evaluation">Full report</Link>}
      >
        {headline ? (
          <div className="grid grid-cols-2 gap-6 lg:grid-cols-4">
            <Stat label="Right verdict · held-out claims" value={pct(headline.verdict_accuracy?.test)} hint="claims never used while building" />
            <Stat label="Right verdict · development claims" value={pct(headline.verdict_accuracy?.dev)} hint="used while building the rules" />
            <Stat label="Passages judged correctly" value={pct(headline.stance_accuracy_lenient?.test)} hint="held-out claims" />
            <Stat label="Score accuracy (Brier)" value={headline.brier?.test?.toFixed(3) ?? "—"} hint="lower is better · 0.25 = guessing" />
          </div>
        ) : (
          <p className="readable text-sm text-muted-foreground">
            Run <code>python -m app.cli evaluate</code> in the backend to measure accuracy. Nothing is shown until it has been computed.
          </p>
        )}
      </Section>
    </div>
  );
}
