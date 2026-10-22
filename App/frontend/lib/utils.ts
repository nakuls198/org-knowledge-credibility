import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmtDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return value.length <= 10
    ? d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" })
    : d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function pct(value?: number | null, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function num(value?: number | null, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Decode the few HTML entities Elasticsearch's html encoder produces. */
function decodeEntities(s: string): string {
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&#x2F;/g, "/")
    .replace(/&amp;/g, "&");
}

/**
 * Split a highlight fragment ("text <mark>hit</mark> text", HTML-escaped by the API)
 * into plain segments. Rendering these as React text nodes means no HTML from the
 * corpus is ever injected into the page.
 */
export function parseHighlight(fragment: string): { text: string; hit: boolean }[] {
  const out: { text: string; hit: boolean }[] = [];
  const re = /<mark>(.*?)<\/mark>/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(fragment)) !== null) {
    if (m.index > last) out.push({ text: decodeEntities(fragment.slice(last, m.index)), hit: false });
    out.push({ text: decodeEntities(m[1]), hit: true });
    last = m.index + m[0].length;
  }
  if (last < fragment.length) out.push({ text: decodeEntities(fragment.slice(last)), hit: false });
  return out;
}
