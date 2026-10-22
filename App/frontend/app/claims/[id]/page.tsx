"use client";

import Link from "next/link";
import { use, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ClassifierMode, Evidence, Label } from "@/lib/types";
import { Badge, Button, EmptyState, ErrorState, NativeSelect, Section, Skeleton, Stat, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/primitives";
import { ScoreBar, VERDICT_SENTENCE, VerdictBadge } from "@/components/claims/badges";
import { EvidenceCard } from "@/components/claims/evidence-card";
import { fmtDate, num, pct } from "@/lib/utils";

const GROUPS: { label: Label; title: string; description: string }[] = [
  { label: "SUPPORTS", title: "What backs it up?", description: "Passages that support the claim, strongest first." },
  { label: "CONTRADICTS", title: "What says otherwise?", description: "Passages that contradict the claim, including workarounds and outdated rules." },
  { label: "AMBIGUOUS", title: "What is unclear?", description: "Hedged statements, or passages where the rules and the language model disagree." },
  { label: "NEUTRAL", title: "Related, but not decisive", description: "On the topic without confirming or disputing the claim." },
];

const CLAIM_TYPE: Record<string, string> = {
  normative: "A claim about a rule",
  practice: "A claim about what people actually do",
  factual: "A claim about a fact or event",
};

function List({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <Section title={title}>
      <ul className="readable list-disc space-y-1.5 pl-5 text-[0.95rem] leading-relaxed text-text">
        {items.map((x, i) => (
          <li key={i}>{x}</li>
        ))}
      </ul>
    </Section>
  );
}

export default function ClaimPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<ClassifierMode | "">("");
  const claim = useQuery({
    queryKey: ["claim", id],
    queryFn: () => api.claim(id),
    refetchInterval: (q) => (["queued", "running"].includes(q.state.data?.latest_assessment?.status || "") ? 2000 : false),
  });
  const latest = claim.data?.latest_assessment;
  const assessmentId =
    selected || (latest?.status === "completed" ? latest.id : claim.data?.assessments.find((a) => a.status === "completed")?.id) || null;
  const assessment = useQuery({ queryKey: ["assessment", assessmentId], queryFn: () => api.assessment(assessmentId!), enabled: !!assessmentId });
  const reproduce = useQuery({ queryKey: ["reproduce", assessmentId], queryFn: () => api.reproduce(assessmentId!), enabled: false });
  const rerun = useMutation({
    mutationFn: () => api.reassess(id, { classifier_mode: mode || undefined }),
    onSuccess: () => {
      setSelected(null);
      qc.invalidateQueries({ queryKey: ["claim", id] });
    },
  });

  const clusterSizes = useMemo(() => {
    const m: Record<string, number> = {};
    assessment.data?.evidence.forEach((e) => e.cluster_id && (m[e.cluster_id] = (m[e.cluster_id] || 0) + 1));
    return m;
  }, [assessment.data]);

  if (claim.isError) return <ErrorState error={claim.error} onRetry={() => claim.refetch()} />;
  if (!claim.data) return <Skeleton className="mt-8 h-96" />;
  const c = claim.data;
  const a = assessment.data;
  const r = a?.result;
  const running = ["queued", "running"].includes(latest?.status || "");
  const byLabel = (l: Label) =>
    (a?.evidence || []).filter((e: Evidence) => e.label === l).sort((x, y) => Math.abs(y.contribution) - Math.abs(x.contribution) || (y.features.weight ?? 0) - (x.features.weight ?? 0));
  const decisive = (a?.evidence || []).filter((e) => e.label !== "NEUTRAL").length;

  return (
    <div>
      <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
        <Link href="/claims" className="text-sm text-muted-foreground hover:text-ink">
          ← All claims
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <NativeSelect value={mode} onChange={(e) => setMode(e.target.value as ClassifierMode)} aria-label="How passages are judged on re-run">
            <option value="">Recommended judging</option>
            <option value="rules">Rules only</option>
            <option value="rules+nli">Rules + language model</option>
            <option value="llm+rules">Local AI review + rules (slow)</option>
          </NativeSelect>
          <Button onClick={() => rerun.mutate()} disabled={running || rerun.isPending}>
            {running ? "Checking…" : "Check again"}
          </Button>
          <Button asChild variant="default">
            <Link href={`/claims/${id}/graph${assessmentId ? `?assessment=${assessmentId}` : ""}`}>See the evidence map</Link>
          </Button>
        </div>
      </div>

      <div className="hero">
        <div className="kicker mt-5">
          {c.analysis.claim_type ? CLAIM_TYPE[c.analysis.claim_type] ?? "Claim" : "Claim"}
          {a && ` · checked ${fmtDate(a.completed_at)}`}
        </div>
        <h1 className="!mt-2 !text-[2.4rem] max-sm:!text-[1.8rem]">“{c.text}”</h1>
      </div>

      {running && (
        <div role="status" className="notice mt-2 max-w-3xl">
          Checking the record now: finding passages, judging each one and weighing the sources. This page updates by itself.
        </div>
      )}
      {latest?.status === "failed" && <ErrorState error={new Error(latest.error || "The check failed.")} />}
      {!a && !running && latest?.status !== "failed" && <EmptyState title="This claim hasn't been checked yet." body="Use “Check again” to run it." />}
      {assessment.isError && <ErrorState error={assessment.error} />}

      {a && r && (
        <>
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <VerdictBadge verdict={a.verdict} className="px-2.5 py-1 text-sm" />
            <p className="font-serif text-2xl text-ink">{a.verdict && <span className="hl">{VERDICT_SENTENCE[a.verdict]}</span>}</p>
            {a.needs_review && <Badge tone="ink">needs a person</Badge>}
          </div>
          <p className="readable mt-3 font-serif text-[1.15rem] leading-relaxed text-text">{a.explanation.summary}</p>
          {a.needs_review && (
            <div className="notice readable mt-4">
              <b className="font-semibold">Why a person should decide: </b>
              {r.review_reasons.join(" ")}
            </div>
          )}

          <div className="mt-8 max-w-4xl">
            <ScoreBar score={r.score} interval={r.interval} />
          </div>
          <div className="mt-6 grid grid-cols-2 gap-6 md:grid-cols-5">
            <Stat label="For" value={num(r.support_mass)} hint={`${r.independent_support} independent source${r.independent_support === 1 ? "" : "s"}`} />
            <Stat label="Against" value={num(r.contradict_mass)} hint={`${r.independent_contradiction} independent source${r.independent_contradiction === 1 ? "" : "s"}`} />
            <Stat label="Claim covered" value={pct(r.coverage)} hint="of its key parts are addressed" />
            <Stat label="Conflict" value={num(r.conflict)} hint="0 = one-sided · 1 = evenly split" />
            <Stat label="Followed in practice" value={r.practice_adherence === null ? "—" : pct(r.practice_adherence)} hint="for claims about rules" />
          </div>

          <Tabs defaultValue="evidence" className="mt-10">
            <TabsList>
              <TabsTrigger value="evidence">The evidence ({decisive})</TabsTrigger>
              <TabsTrigger value="why">Why this verdict</TabsTrigger>
              <TabsTrigger value="history">Earlier checks ({c.assessments.length})</TabsTrigger>
              <TabsTrigger value="audit">Audit trail</TabsTrigger>
            </TabsList>

            <TabsContent value="evidence">
              {GROUPS.map(({ label, title, description }) => {
                const items = byLabel(label);
                if (!items.length) return null;
                return (
                  <Section key={label} title={`${title} · ${items.length}`} description={description}>
                    <div className="max-w-5xl">
                      {items.map((e) => (
                        <EvidenceCard key={e.id} ev={e} clusterSize={e.cluster_id ? clusterSizes[e.cluster_id] : 1} />
                      ))}
                    </div>
                  </Section>
                );
              })}
            </TabsContent>

            <TabsContent value="why">
              {a.explanation.llm_narrative?.text && (
                <Section title="In short" description={`Paraphrased by the local AI model (${a.explanation.llm_narrative.model}) and checked against the recorded evidence.`}>
                  <p className="quote">{a.explanation.llm_narrative.text}</p>
                </Section>
              )}
              <List title="How the verdict was reached" items={a.explanation.reasons} />
              <List title="Is the evidence still current?" items={a.explanation.temporal} />
              <List title="What to keep in mind" items={a.explanation.caveats} />
              <List title="What the record doesn't say" items={a.explanation.missing_information} />
              <List title="What to do next" items={a.explanation.next_steps} />
              <Section title="How sure is this?">
                <p className="readable text-[0.95rem] leading-relaxed">{a.explanation.uncertainty}</p>
              </Section>
              <List title="Limits of this check" items={a.explanation.limitations} />
            </TabsContent>

            <TabsContent value="history">
              <Section title="Every check of this claim" description="Each check is kept, with its own evidence and settings.">
                <ul className="divide-y divide-line border-y border-line">
                  {c.assessments.map((h) => (
                    <li key={h.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5 text-sm">
                      <span className="text-muted-foreground">
                        {fmtDate(h.created_at)} · {h.classifier || "—"} · {h.status}
                      </span>
                      <span className="flex items-center gap-3">
                        <VerdictBadge verdict={h.verdict} />
                        <span className="font-serif text-lg tabular-nums text-ink">{h.score?.toFixed(2) ?? ""}</span>
                        {h.status === "completed" && (
                          <Button size="sm" variant={h.id === assessmentId ? "default" : "secondary"} onClick={() => setSelected(h.id)}>
                            {h.id === assessmentId ? "Showing" : "Show"}
                          </Button>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </Section>
            </TabsContent>

            <TabsContent value="audit">
              <Section
                title="Can this result be reproduced?"
                description="Every check stores its evidence, judgements, settings and versions, so the score can be recomputed later."
                action={
                  <Button onClick={() => reproduce.refetch()} disabled={reproduce.isFetching}>
                    {reproduce.isFetching ? "Recomputing…" : "Recompute the score"}
                  </Button>
                }
              >
                {reproduce.data && (
                  <div className="notice readable mb-4">
                    <b className="font-semibold">{reproduce.data.reproducible ? "Reproduced exactly." : "Not reproduced."}</b> Recorded{" "}
                    {reproduce.data.recorded?.verdict} {num(reproduce.data.recorded?.score)}; recomputed {reproduce.data.recomputed?.verdict}{" "}
                    {num(reproduce.data.recomputed?.score)}.
                    {reproduce.data.changed_sections_since?.length > 0 && ` ${reproduce.data.changed_sections_since.length} source passage(s) have changed since.`}
                  </div>
                )}
                <p className="mb-2 text-sm">
                  Settings fingerprint <code className="bg-paper-2 px-1">{a.config_hash}</code>
                </p>
                <pre className="max-h-80 overflow-auto bg-paper-2 p-3 font-mono text-xs text-ink">
                  {JSON.stringify(
                    { versions: a.versions, queries: a.retrieval.queries, limits: a.retrieval.limits, linked_documents: a.retrieval.linked_documents, classifier_notes: a.retrieval.classifier_notes },
                    null,
                    2,
                  )}
                </pre>
              </Section>
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}
