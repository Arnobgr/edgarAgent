import { count, usd } from "../lib/format";
import type { RunPayload } from "../types/api";

const SOURCE_NOTE: Record<RunPayload["source"], string> = {
  // The date is the accepted baseline's, so it moves whenever `export-runs`
  // regenerates `recorded/` from a newer one — 2026-08-13 replaced the 08-01
  // set (ADR 0043). `payload.created_at` is right there and would never go
  // stale, but every recorded run shares one date by construction and a strip
  // that renders it per run invites the reader to compare thirty identical
  // timestamps. One sentence, updated with the baseline.
  recorded: "Recorded run — answered on 13 August 2026 and shipped with this page",
  cached: "Cached — this question was answered before, and the stored answer was returned",
  live: "Live run — computed for this request",
};

interface StatsStripProps {
  payload: RunPayload;
  /** Live runs only. A recorded run's trace was written to a Langfuse that no
   *  visitor can reach, so the strip says which run it is looking at instead
   *  of printing a dead identifier (Phase 5 decision 6). */
  traceId?: string | null;
}

export function StatsStrip({ payload, traceId }: StatsStripProps) {
  const { result } = payload;
  const items: [string, string][] = [
    ["Cost", usd(result.budget.cost_usd_used)],
    ["Tokens", count(result.budget.tokens_used)],
    ["Verify loops", String(result.verify_iterations)],
    ["Critic loops", String(result.critic_iterations)],
    ["Sub-questions", String(result.sub_questions.length)],
  ];
  if (payload.source === "live" && traceId) items.push(["Trace", traceId]);

  return (
    <section className="mt-10 border-t border-ink/20 pt-4">
      <dl className="flex flex-wrap gap-x-8 gap-y-3">
        {items.map(([label, value]) => (
          <div key={label}>
            <dt className="font-mono text-[10px] uppercase tracking-wider text-slate">{label}</dt>
            <dd className="mt-0.5 font-mono text-[13px] text-ink">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-4 font-mono text-[11px] text-slate">{SOURCE_NOTE[payload.source]}</p>
    </section>
  );
}
