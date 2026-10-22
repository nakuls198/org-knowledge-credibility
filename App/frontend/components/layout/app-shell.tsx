"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Front page" },
  { href: "/claims", label: "Check a claim" },
  { href: "/search", label: "Search the record" },
  { href: "/documents", label: "Documents" },
  { href: "/evaluation", label: "How accurate is it?" },
  { href: "/ingestion", label: "Data health" },
];

function today(): string {
  return new Date().toLocaleDateString("en-AU", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

function ServiceStatus() {
  const { data, isError } = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 30_000 });
  const ok = data?.status === "ok";
  const label = isError ? "Service unreachable" : !data ? "Checking services…" : ok ? "All services running" : "Running with reduced features";
  return (
    <span role="status" className="inline-flex items-center gap-1.5">
      <i aria-hidden="true" className={cn("inline-block size-2 rounded-full", isError ? "bg-contradicts" : ok ? "bg-supports" : "bg-neutral")} />
      {label}
    </span>
  );
}

function CorpusBadge() {
  const { data } = useQuery({ queryKey: ["overview"], queryFn: api.overview, staleTime: 60_000 });
  if (!data) return null;
  const docs = data.corpus.documents_total as number;
  const sections = data.corpus.sections_total as number;
  return (
    <span className="ml-auto hidden text-[0.78rem] font-medium text-muted-foreground sm:inline">
      {data.corpus.is_synthetic_sample ? "Sample data · " : ""}
      {docs} documents · {sections} sections
    </span>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="mx-auto min-h-screen max-w-[1600px] px-4 pb-16 pt-5 sm:px-[clamp(1rem,3.5vw,3.5rem)]">
      <header>
        <div className="mast">
          <Link href="/" className="wordmark">
            The Credibility Record
          </Link>
          <CorpusBadge />
        </div>
        <div className="dateline">
          <span>{today()}</span>
          <ServiceStatus />
        </div>
        <nav aria-label="Main" className="no-scrollbar -mx-1 flex gap-x-6 overflow-x-auto border-b border-line px-1">
          {NAV.map(({ href, label }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "-mb-px shrink-0 py-2.5 text-[0.92rem] font-medium",
                  active ? "font-semibold text-ink shadow-[inset_0_-2px_0_var(--ink)]" : "text-muted-foreground hover:text-ink",
                )}
              >
                {label}
              </Link>
            );
          })}
        </nav>
      </header>
      <main className="min-w-0">{children}</main>
      <footer className="mt-16 border-t border-ink pt-2 text-xs text-muted-foreground">
        Scores summarise the evidence found in the indexed records; they are not probabilities. Every finding links to its source.
      </footer>
    </div>
  );
}
