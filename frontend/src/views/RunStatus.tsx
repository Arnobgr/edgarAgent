import { useEffect, useState } from "react";

import { ApiError, MOCK, jobResult, jobStatus, jobTrace } from "../lib/client";
import { count, usd } from "../lib/format";
import type { JobStatusResponse, RunPayload, RunPhase } from "../types/api";
import { ResultView } from "./ResultView";

const POLL_MS = 2000;

/**
 * The six phases the API reports, in order.
 *
 * `reviewing` is shown and marked off rather than hidden. The pipeline really
 * does have a human review gate — the endpoints, the graph interrupt and their
 * tests all exist — and this deployment really does run with it disabled,
 * because on a page with no login the reviewer would be any visitor and an
 * abandoned review would hold a concurrency slot forever (ADR 0034). Hiding
 * the stage would misdescribe the system; showing it as passed would
 * misdescribe the run.
 */
const PHASES: { phase: RunPhase; label: string; note?: string }[] = [
  { phase: "starting", label: "Starting" },
  { phase: "researching", label: "Researching", note: "retrieval and filed figures, per sub-question" },
  { phase: "synthesizing", label: "Drafting", note: "one claim per factual statement" },
  { phase: "verifying", label: "Verifying", note: "XBRL, entailment, attribution" },
  { phase: "reviewing", label: "Human review", note: "off in this deployment" },
  { phase: "finalizing", label: "Retracting and checking" },
];

export function RunStatus({ jobId }: { jobId: string }) {
  const [status, setStatus] = useState<JobStatusResponse | null>(null);
  const [payload, setPayload] = useState<RunPayload | null>(null);
  const [traceId, setTraceId] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    let timer: number | undefined;

    const tick = async () => {
      try {
        const next = await jobStatus(jobId);
        if (!live) return;
        setStatus(next);
        if (next.status === "succeeded") {
          setPayload(await jobResult(jobId));
          if (!MOCK) {
            // Live runs only (Phase 5 decision 6). A failure here is not the
            // reader's problem — the answer is already on screen.
            jobTrace(jobId)
              .then((trace) => live && setTraceId(trace.trace_id))
              .catch(() => undefined);
          }
          return;
        }
        if (next.status === "failed" || next.status === "interrupted") return;
        timer = window.setTimeout(tick, POLL_MS);
      } catch (error) {
        if (!live) return;
        setFailure(
          error instanceof ApiError ? error.message : "Lost contact with the run.",
        );
      }
    };

    void tick();
    return () => {
      live = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [jobId]);

  if (payload) {
    return (
      <main>
        <ResultView
          payload={payload}
          traceId={traceId}
          eyebrow={
            <p className="font-mono text-[11px] uppercase tracking-wider text-slate">
              Live run · {jobId}
            </p>
          }
        />
      </main>
    );
  }

  if (failure || status?.status === "failed" || status?.status === "interrupted") {
    return (
      <main className="mx-auto max-w-3xl px-5 py-16 md:px-8">
        <div data-tone="retracted" className="border-l-2 border-[var(--tone)] bg-[var(--tone-wash)] px-4 py-3">
          <p className="text-[13px] font-semibold text-ink">The run did not finish.</p>
          <p className="mt-1 text-[13px] leading-relaxed text-slate">
            {failure ?? status?.error ?? "It stopped before producing an answer."}
          </p>
        </div>
        <a href="#/ask" className="mt-4 inline-block font-mono text-[12px] uppercase tracking-wider text-verified underline">
          Ask again
        </a>
      </main>
    );
  }

  const current = status?.progress?.phase ?? "starting";
  const reached = PHASES.findIndex((entry) => entry.phase === current);

  return (
    <main className="mx-auto max-w-3xl px-5 py-10 md:px-8">
      <p className="font-mono text-[11px] uppercase tracking-wider text-slate">
        Live run · {jobId}
      </p>
      <h1 className="mt-3 font-serif text-2xl leading-snug text-ink md:text-[1.75rem]">
        {status?.question ?? "Working…"}
      </h1>

      <ol className="mt-8 border-t border-rule">
        {PHASES.map((entry, index) => {
          const off = entry.phase === "reviewing";
          const active = index === reached;
          const done = index < reached && !off;
          return (
            <li
              key={entry.phase}
              className="flex items-baseline gap-3 border-b border-rule py-3"
              aria-current={active ? "step" : undefined}
            >
              <span
                aria-hidden
                data-tone={done || active ? "verified" : "anomaly"}
                className={`tick mt-1 shrink-0 ${off ? "opacity-25" : done || active ? "" : "opacity-25"}`}
                style={active ? { animation: "tick-in 700ms ease-in-out infinite alternate" } : undefined}
              />
              <span
                className={`font-mono text-[12px] uppercase tracking-wider ${
                  off ? "text-slate/50 line-through" : active ? "text-ink" : done ? "text-verified" : "text-slate/60"
                }`}
              >
                {entry.label}
              </span>
              {entry.note ? (
                <span className="ml-auto text-right font-mono text-[11px] text-slate/70">
                  {entry.note}
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>

      <dl className="mt-6 flex flex-wrap gap-x-8 gap-y-3">
        <Stat label="Verify loops" value={String(status?.progress?.verify_iteration ?? 0)} />
        <Stat label="Critic loops" value={String(status?.progress?.critic_iteration ?? 0)} />
        <Stat label="Spent" value={usd(status?.progress?.cost_usd ?? status?.cost_usd ?? 0)} />
        <Stat label="Tokens" value={count(status?.progress?.tokens ?? status?.tokens ?? 0)} />
      </dl>

      <p className="mt-8 max-w-prose text-[13px] leading-relaxed text-slate">
        A full run takes a couple of minutes. Every claim in the draft is checked before you see
        it, and the ones that fail are cut out rather than flagged.
      </p>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-mono text-[10px] uppercase tracking-wider text-slate">{label}</dt>
      <dd className="mt-0.5 font-mono text-[13px] text-ink">{value}</dd>
    </div>
  );
}
