import { useEffect, useState } from "react";

import { fetchRecordedIndex, fetchRecordedRun } from "../lib/client";
import { shortDate } from "../lib/format";
import { LANDING_RUN_ID } from "../lib/recorded";
import { STATUS_META } from "../lib/status";
import type { ClaimStatus, RecordedEntry, RunPayload } from "../types/api";
import { ResultView } from "./ResultView";

const LEGEND: ClaimStatus[] = ["grounded", "partial", "retracted"];

/**
 * What a stranger lands on: a run that already happened.
 *
 * There is no marketing hero above this, on purpose. The page has three
 * seconds to show that every sentence was checked, and a headline band would
 * spend them. The recorded run is also free to serve — it is a static file
 * beside the bundle, so a reader who only reads never touches the VPS
 * (ADR 0027).
 */
export function Landing({ runId }: { runId: string | null }) {
  const [payload, setPayload] = useState<RunPayload | null>(null);
  const [entries, setEntries] = useState<RecordedEntry[]>([]);
  const [failed, setFailed] = useState<string | null>(null);
  const wanted = runId ?? LANDING_RUN_ID;

  useEffect(() => {
    let live = true;
    setPayload(null);
    setFailed(null);
    Promise.all([fetchRecordedRun(wanted), fetchRecordedIndex()])
      .then(([run, index]) => {
        if (!live) return;
        setPayload(run);
        setEntries(index.runs);
      })
      .catch((error: Error) => live && setFailed(error.message));
    return () => {
      live = false;
    };
  }, [wanted]);

  if (failed) {
    return (
      <main className="mx-auto max-w-3xl px-5 py-16 md:px-8">
        <p className="font-mono text-[13px] text-retracted">{failed}</p>
        <a href="#/" className="mt-3 inline-block font-mono text-[13px] text-verified underline">
          Back to the first run
        </a>
      </main>
    );
  }

  if (!payload) {
    return (
      <main className="mx-auto max-w-3xl px-5 py-16 md:px-8">
        <p className="font-mono text-[11px] uppercase tracking-wider text-slate">Loading run…</p>
      </main>
    );
  }

  return (
    <main>
      <ResultView
        payload={payload}
        eyebrow={
          <div>
            <p className="font-mono text-[11px] uppercase tracking-wider text-slate">
              Recorded run · {shortDate(payload.created_at)} · question{" "}
              {entries.findIndex((e) => e.job_id === payload.job_id) + 1} of {entries.length}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5">
              {LEGEND.map((status) => (
                <span key={status} className="flex items-center gap-1.5" data-tone={STATUS_META[status].tone}>
                  <span className="tick" data-lit="false" />
                  <span className="font-mono text-[11px] text-slate">
                    {STATUS_META[status].label.toLowerCase()}
                  </span>
                </span>
              ))}
              <span className="font-mono text-[11px] text-slate/80">
                — hover a marked sentence for its source
              </span>
            </div>
          </div>
        }
      />

      <section className="mx-auto max-w-3xl border-t border-rule px-5 pb-20 pt-8 md:px-8">
        <h2 className="font-mono text-[11px] font-medium uppercase tracking-wider text-slate">
          The other {Math.max(entries.length - 1, 0)} recorded runs
        </h2>
        <p className="mt-2 max-w-prose text-[13px] leading-relaxed text-slate">
          Every question in the evaluation set that this build answered, exactly as it answered
          them — including the three it was asked about things outside its filings.
        </p>
        <ul className="mt-4 divide-y divide-rule border-y border-rule">
          {entries
            .filter((entry) => entry.job_id !== payload.job_id)
            .map((entry) => (
              <li key={entry.job_id}>
                <a
                  href={`#/recorded/${entry.job_id}`}
                  className="flex items-baseline gap-3 py-2.5 hover:bg-rule/30"
                >
                  <span className="flex-1 text-[13px] leading-snug text-ink">{entry.question}</span>
                  <span className="shrink-0 font-mono text-[11px] text-slate">
                    {entry.claims} claim{entry.claims === 1 ? "" : "s"}
                    {entry.retracted > 0 ? (
                      <span className="text-retracted"> · {entry.retracted} cut</span>
                    ) : null}
                  </span>
                </a>
              </li>
            ))}
        </ul>
        <a
          href="#/ask"
          className="mt-8 inline-block border border-verified bg-verified px-4 py-2 font-mono text-[12px] uppercase tracking-wider text-white hover:bg-ink hover:border-ink"
        >
          Ask your own question
        </a>
      </section>
    </main>
  );
}
