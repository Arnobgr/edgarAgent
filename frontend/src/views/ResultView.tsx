import { useState } from "react";

import { AnswerProse } from "../components/AnswerProse";
import { ClaimStrip } from "../components/ClaimStrip";
import { GroundingTable } from "../components/GroundingTable";
import { RetractedClaims } from "../components/RetractedClaims";
import { StatsStrip } from "../components/StatsStrip";
import type { RunPayload } from "../types/api";

interface ResultViewProps {
  payload: RunPayload;
  traceId?: string | null;
  /** Sits above the question — what the reader is looking at and why. */
  eyebrow?: React.ReactNode;
}

/**
 * One renderer for a live run and a recorded one.
 *
 * `fie export-runs` writes byte-for-byte what `GET /v1/jobs/{id}/result`
 * returns, so there is one view and no second code path that could drift into
 * showing a recorded run more kindly than a live one (ADR 0027).
 */
export function ResultView({ payload, traceId, eyebrow }: ResultViewProps) {
  const { result } = payload;
  const [lit, setLit] = useState<string | null>(null);
  const all = [...result.grounding, ...result.retracted];

  return (
    <div className="mx-auto max-w-3xl px-5 pb-24 pt-8 md:px-8">
      {eyebrow ? <div className="mb-3">{eyebrow}</div> : null}

      <h1 className="font-serif text-2xl leading-snug text-ink md:text-[1.75rem]">
        {result.question}
      </h1>

      <div className="mt-5 border-y border-rule py-4">
        <ClaimStrip rows={all} lit={lit} onLit={setLit} />
      </div>

      {result.awaiting_review ? (
        // Unreachable in production: `API_REVIEW_MODE` is off, so no run can
        // park at the gate. Treated as a fault rather than given a screen
        // (Phase 5 decision 2) — if it ever renders, the deployment is not
        // configured the way the security model assumes.
        <Notice tone="anomaly" title="This run stopped at a human review gate.">
          Production runs never do this. The grounding table below is not settled and should not be
          read as final.
        </Notice>
      ) : null}

      {result.error ? (
        <Notice tone="retracted" title="This run did not finish cleanly.">
          {result.error}
        </Notice>
      ) : null}

      {result.answer ? (
        <div className="mt-8">
          <AnswerProse
            answer={result.answer}
            grounding={result.grounding}
            lit={lit}
            onLit={setLit}
          />
        </div>
      ) : (
        <Notice tone="retracted" title="The run produced no answer.">
          Nothing was written, so there is nothing to verify.
        </Notice>
      )}

      <RetractedClaims rows={result.retracted} />

      <section className="mt-10">
        <h2 className="border-b border-ink/20 pb-2 font-mono text-[11px] font-medium uppercase tracking-wider text-slate">
          Every claim, and what it rests on
        </h2>
        <div className="mt-1">
          <GroundingTable rows={all} lit={lit} onLit={setLit} />
        </div>
      </section>

      <StatsStrip payload={payload} traceId={traceId ?? null} />
    </div>
  );
}

function Notice({
  tone,
  title,
  children,
}: {
  tone: "retracted" | "anomaly";
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div
      data-tone={tone}
      className="mt-6 border-l-2 border-[var(--tone)] bg-[var(--tone-wash)] px-4 py-3"
    >
      <p className="text-[13px] font-semibold text-ink">{title}</p>
      <p className="mt-1 text-[13px] leading-relaxed text-slate">{children}</p>
    </div>
  );
}
