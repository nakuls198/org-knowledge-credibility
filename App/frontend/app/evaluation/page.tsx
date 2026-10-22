"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { api } from "@/lib/api";
import { Badge, Card, CardBody, CardHeader, EmptyState, ErrorState, NativeSelect, PageHeader, Skeleton } from "@/components/ui/primitives";
import { fmtDate, num, pct, titleCase } from "@/lib/utils";

/* eslint-disable @typescript-eslint/no-explicit-any */
function Table({ head, rows }: { head: string[]; rows: (string | number | React.ReactNode)[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted-foreground">
          <tr className="border-b border-border">{head.map((h) => <th key={h} className="py-2 pr-3 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-border last:border-0">{r.map((c, j) => <td key={j} className="py-1.5 pr-3 tabular-nums">{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Confusion({ labels, matrix, colLabels }: { labels: string[]; matrix: number[][]; colLabels?: string[] }) {
  const max = Math.max(1, ...matrix.flat());
  const cols = colLabels || labels;
  return (
    <div className="overflow-x-auto">
      <table className="text-xs" aria-label="Confusion matrix: rows expected, columns predicted">
        <thead>
          <tr>
            <th className="p-1 text-left text-muted-foreground">expected ↓ / predicted →</th>
            {cols.map((l) => <th key={l} className="p-1 font-medium">{titleCase(l.toLowerCase())}</th>)}
          </tr>
        </thead>
        <tbody>
          {matrix.map((row, i) => (
            <tr key={i}>
              <th className="p-1 text-left font-medium">{titleCase(labels[i].toLowerCase())}</th>
              {row.map((v, j) => (
                <td key={j} className={`h-9 w-16 border border-border text-center tabular-nums ${i === j ? "font-semibold" : ""}`} style={{ background: v ? `color-mix(in oklch, var(--primary) ${Math.round((v / max) * 45)}%, transparent)` : undefined }}>
                  {v}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function EvaluationPage() {
  const ev = useQuery({ queryKey: ["evaluation"], queryFn: api.evaluation });
  const [split, setSplit] = useState("test");
  const [clf, setClf] = useState<string>("");
  if (ev.isError) return <ErrorState error={ev.error} onRetry={() => ev.refetch()} />;
  if (!ev.data) return <Skeleton className="h-96" />;
  if (!ev.data.available)
    return (
      <div className="">
        <PageHeader title="Evaluation" />
        <EmptyState title="No evaluation has been run" body="Run `python -m app.cli evaluate` in the backend. This page shows only metrics computed from that run; nothing is pre-filled." />
      </div>
    );
  const rep = ev.data.report;
  const modes = Object.keys(rep.classifiers);
  const mode = clf || modes[modes.length - 1];
  const b = rep.classifiers[mode];
  const v = b.verdict[split] || b.verdict.all;
  const st = b.stance[split] || b.stance.all;
  const cal = b.calibration[split] || b.calibration.all;
  const ret = rep.retrieval;
  const relData = cal?.available ? cal.reliability.filter((r: any) => r.n > 0).map((r: any) => ({ x: r.mean_score, y: r.observed_rate, n: r.n })) : [];

  return (
    <div className="space-y-4">
      <PageHeader
        title={<>How <span className="hl">accurate</span> is it?</>}
        description={<>Computed by the evaluation pipeline on {fmtDate(rep.created_at)} · {rep.dataset.n_claims} labelled claims ({Object.entries(rep.dataset.by_split).map(([k, n]) => `${n} ${k}`).join(", ")})</>}
        actions={
          <div className="flex gap-2">
            <NativeSelect value={mode} onChange={(e) => setClf(e.target.value)} aria-label="Classifier">{modes.map((m) => <option key={m} value={m}>{m}</option>)}</NativeSelect>
            <NativeSelect value={split} onChange={(e) => setSplit(e.target.value)} aria-label="Split">
              <option value="test">Held-out test</option><option value="dev">Dev (used for tuning)</option><option value="all">All</option>
            </NativeSelect>
          </div>
        }
      />
      <div className="notice readable">
        Labels: {Object.entries(rep.dataset.label_sources).map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`).join(", ")}. Author-drafted labels are provisional and need independent review. Rules were tuned on the dev split only; the test split was held out. Small samples, so treat differences as indicative.
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        {modes.map((m) => (
          <Card key={m}><CardBody>
            <div className="text-xs text-muted-foreground">Verdict accuracy · {m}</div>
            <div className="mt-1 text-2xl font-semibold">{pct(rep.classifiers[m].verdict[split]?.accuracy)}</div>
            <div className="text-xs text-muted-foreground">macro-F1 {num(rep.classifiers[m].verdict[split]?.macro_f1)} · 95% CI {rep.classifiers[m].verdict[split]?.accuracy_ci95?.map((x: number) => pct(x)).join("–")}</div>
          </CardBody></Card>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Retrieval" description="Document-level Recall@K / MRR / nDCG against labelled sources (no passage-level labels exist)" />
          <CardBody>
            <Table head={["Mode", "R@5", "R@10", "R@20", "MRR", "nDCG@10", "Queries"]} rows={Object.entries(ret).map(([m, r]: [string, any]) => r.available ? [m, num(r["recall@5"]), num(r["recall@10"]), num(r["recall@20"]), num(r.mrr), num(r["ndcg@10"]), r.n_queries] : [m, <span key={m} className="text-muted-foreground">{r.note}</span>, "", "", "", "", ""])} />
            <p className="mt-2 text-xs text-muted-foreground">Full investigation (expansion + counter-evidence + linked documents) evidence recall: {pct(b.investigation_evidence_recall)}</p>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Evidence classification (document stance)" description={`${st.n_scored} labelled (claim, document) pairs retrieved of ${st.labelled_pairs}`} />
          <CardBody className="space-y-3">
            <Table head={["Class", "Precision", "Recall", "F1", "Support"]} rows={Object.entries(st.per_class).map(([k, r]: [string, any]) => [k === "S" ? "Supports" : "Contradicts", num(r.precision), num(r.recall), num(r.f1), r.support])} />
            <p className="text-xs text-muted-foreground">Accuracy (mixed counts as match) {pct(st.accuracy_lenient)} · strict {pct(st.accuracy_strict)}</p>
            <Confusion labels={st.confusion.labels_true} colLabels={st.confusion.labels_pred} matrix={st.confusion.matrix} />
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Verdict confusion matrix" description={`${v.n} claims · accuracy ${pct(v.accuracy)} · supported/partial merged ${pct(v.accuracy_partial_as_supported)}`} />
          <CardBody><Confusion labels={v.confusion.labels} matrix={v.confusion.matrix} /></CardBody>
        </Card>
        <Card>
          <CardHeader title="Calibration (indicative)" description={cal?.available ? `Brier ${num(cal.brier, 3)} (constant baseline ${num(cal.brier_constant_base_rate, 3)}) · ECE ${num(cal.ece_5bin, 3)} · AUC ${num(cal.auc)}` : cal?.note} />
          <CardBody className="h-64">
            {cal?.available ? (
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ left: 0, right: 10 }}>
                  <CartesianGrid stroke="var(--border)" />
                  <XAxis type="number" dataKey="x" domain={[0, 1]} name="Mean score" tick={{ fontSize: 11 }} label={{ value: "mean heuristic score", position: "insideBottom", offset: -2, fontSize: 11 }} />
                  <YAxis type="number" dataKey="y" domain={[0, 1]} name="Observed rate" tick={{ fontSize: 11 }} />
                  <ZAxis dataKey="n" range={[40, 300]} name="Claims" />
                  <Tooltip />
                  <Scatter data={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} line={{ stroke: "var(--border)", strokeDasharray: "4 4" }} fill="transparent" />
                  <Scatter data={relData} fill="var(--primary)" />
                </ScatterChart>
              </ResponsiveContainer>
            ) : <p className="text-sm text-muted-foreground">Not available for this split.</p>}
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Stage B: logistic regression vs baseline score" description={b.stage_b.available ? `Trained on dev (${b.stage_b.n_train}), scored on test (${b.stage_b.n_test})` : b.stage_b.note} />
          <CardBody>
            {b.stage_b.available ? (
              <>
                <Table head={["Model", "Test Brier", "Test log loss", "Test AUC", "LOO Brier (all)"]} rows={[
                  ["Baseline heuristic", num(b.stage_b.test.baseline_score.brier, 3), num(b.stage_b.test.baseline_score.log_loss, 3), num(b.stage_b.test.baseline_score.auc), num(b.stage_b.leave_one_out_all.baseline_score.brier, 3)],
                  ["Logistic regression", num(b.stage_b.test.logreg.brier, 3), num(b.stage_b.test.logreg.log_loss, 3), num(b.stage_b.test.logreg.auc), num(b.stage_b.leave_one_out_all.logreg.brier, 3)],
                ]} />
                <p className="mt-2 text-xs text-muted-foreground">{b.stage_b.note}</p>
              </>
            ) : null}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Sensitivity analysis" description={`Verdicts re-scored under perturbed assumptions · base accuracy ${pct(b.sensitivity.base_accuracy)}`} />
          <CardBody>
            <Table head={["Scenario", "Verdict flip rate", "Accuracy"]} rows={Object.entries(b.sensitivity.scenarios).map(([k, s]: [string, any]) => [k.replace(/_/g, " "), pct(s.flip_rate), pct(s.accuracy)])} />
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Per-claim results" description={`${mode} · ${split}`} />
        <CardBody>
          <Table head={["Claim", "Split", "Type", "Expected", "Predicted", "Score"]} rows={b.claims.filter((c: any) => split === "all" || c.split === split).map((c: any) => [
            c.claim_id, c.split, c.claim_type, c.expected.toLowerCase().replace(/_/g, " "),
            <Badge key={c.claim_id} tone={c.expected === c.predicted ? "supports" : "contradicts"}>{c.predicted.toLowerCase().replace(/_/g, " ")}</Badge>, num(c.score),
          ])} />
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Known limitations" />
        <CardBody className="text-sm text-muted-foreground">
          <ul className="list-disc space-y-1 pl-5">
            <li>{rep.dataset.n_claims} claims is a small evaluation set; confidence intervals are wide.</li>
            <li>Most labels were drafted by the project author and have not been independently reviewed.</li>
            <li>Retrieval is evaluated at document level only; there are no passage-level relevance labels.</li>
            <li>The credibility score is a heuristic summary of weighted evidence, not a calibrated probability.</li>
            <li>The local LLM classifier was not evaluated at scale: on CPU it takes roughly 40 s per passage.</li>
          </ul>
        </CardBody>
      </Card>
    </div>
  );
}
