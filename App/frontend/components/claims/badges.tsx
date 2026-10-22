import { Badge } from "@/components/ui/primitives";
import type { Label, Verdict } from "@/lib/types";
import { cn, parseHighlight } from "@/lib/utils";

// Data colours carry meaning only as dots / chart marks; text always says it in words too.
export const LABEL_COLOR: Record<Label, string> = {
  SUPPORTS: "var(--supports)",
  CONTRADICTS: "var(--contradicts)",
  AMBIGUOUS: "var(--ambiguous)",
  NEUTRAL: "var(--neutral)",
};

export const VERDICT_COLOR: Record<Verdict, string> = {
  SUPPORTED: "var(--supports)",
  PARTIALLY_SUPPORTED: "var(--supports)",
  CONTESTED: "var(--ambiguous)",
  CONTRADICTED: "var(--contradicts)",
  INSUFFICIENT_EVIDENCE: "var(--neutral)",
};

export const VERDICT_TEXT: Record<Verdict, string> = {
  SUPPORTED: "Supported",
  PARTIALLY_SUPPORTED: "Partly supported",
  CONTESTED: "Contested",
  CONTRADICTED: "Contradicted",
  INSUFFICIENT_EVIDENCE: "Not enough evidence",
};

/** One plain sentence a reader would say about the verdict. */
export const VERDICT_SENTENCE: Record<Verdict, string> = {
  SUPPORTED: "The record backs this up.",
  PARTIALLY_SUPPORTED: "The record partly backs this up.",
  CONTESTED: "The record points both ways.",
  CONTRADICTED: "The record says otherwise.",
  INSUFFICIENT_EVIDENCE: "The record doesn't say enough to tell.",
};

export function VerdictBadge({ verdict, className }: { verdict: Verdict | null | undefined; className?: string }) {
  if (!verdict) return <Badge tone="outline" className={className}>Not checked yet</Badge>;
  return (
    <Badge tone="neutral" dot={VERDICT_COLOR[verdict]} className={className}>
      {VERDICT_TEXT[verdict]}
    </Badge>
  );
}

const LABEL_TEXT: Record<Label, string> = {
  SUPPORTS: "Supports",
  CONTRADICTS: "Contradicts",
  AMBIGUOUS: "Unclear",
  NEUTRAL: "Neutral",
};

export function LabelBadge({ label }: { label: Label }) {
  return (
    <Badge tone="neutral" dot={LABEL_COLOR[label]}>
      {LABEL_TEXT[label]}
    </Badge>
  );
}

/** Heuristic score with its evidence-weight interval, drawn as ink on a paper track. */
export function ScoreBar({ score, interval }: { score: number; interval: [number, number] }) {
  return (
    <div>
      <div className="mb-1.5 flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
        <span>
          <span className="kicker">Credibility score</span>{" "}
          <b className="font-serif text-lg font-medium text-ink">{score.toFixed(2)}</b>
        </span>
        <span>
          range {interval[0].toFixed(2)}–{interval[1].toFixed(2)} · a weighted summary of the evidence, not a probability
        </span>
      </div>
      <div className="relative h-2 bg-paper-2" role="img" aria-label={`Score ${score.toFixed(2)}, range ${interval[0].toFixed(2)} to ${interval[1].toFixed(2)}`}>
        <div className="absolute inset-y-0 bg-hl" style={{ left: `${interval[0] * 100}%`, width: `${(interval[1] - interval[0]) * 100}%` }} />
        <div className="absolute -top-1.5 h-5 w-[3px] bg-ink" style={{ left: `calc(${score * 100}% - 1.5px)` }} />
        <div className="absolute inset-y-0 left-[30%] w-px bg-line" />
        <div className="absolute inset-y-0 left-[70%] w-px bg-line" />
      </div>
      <div className="mt-1 flex justify-between text-[0.68rem] uppercase tracking-[0.08em] text-muted-foreground">
        <span>contradicted</span>
        <span>contested</span>
        <span>supported</span>
      </div>
    </div>
  );
}

/** Renders API highlight fragments safely as text with <mark> segments. */
export function Highlight({ fragment, className }: { fragment: string; className?: string }) {
  return (
    <span className={className}>
      {parseHighlight(fragment).map((seg, i) => (seg.hit ? <mark key={i}>{seg.text}</mark> : <span key={i}>{seg.text}</span>))}
    </span>
  );
}

/** Section text with the evidence span marked by a half-height highlighter stroke. */
export function SpanInContext({ text, start, end, className }: { text: string; start: number; end: number; className?: string }) {
  const before = text.slice(Math.max(0, start - 220), start);
  const after = text.slice(end, end + 220);
  return (
    <p className={cn("passage whitespace-pre-wrap font-serif text-[1.05rem] leading-relaxed text-ink", className)}>
      {start > 220 && "… "}
      <span className="text-muted-foreground">{before}</span>
      <mark>{text.slice(start, end)}</mark>
      <span className="text-muted-foreground">{after}</span>
      {end + 220 < text.length && " …"}
    </p>
  );
}
