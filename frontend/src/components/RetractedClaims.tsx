import { METHOD_META, STATUS_META } from "../lib/status";
import type { GroundingRow } from "../types/api";

/**
 * The claims that were taken out of the answer.
 *
 * Shown prominently and never folded away, because a retraction is the system
 * working rather than a defect to apologise for: the engine wrote a sentence,
 * failed to find it in the filings, and deleted it before the reader saw it.
 * The text is struck through so what was removed is legible — the reader can
 * see the sentence they were spared, which is the whole argument.
 */
export function RetractedClaims({ rows }: { rows: GroundingRow[] }) {
  if (rows.length === 0) return null;
  return (
    <section className="mt-10">
      <h2 className="flex items-baseline gap-3 border-b border-ink/20 pb-2 font-mono text-[11px] font-medium uppercase tracking-wider text-retracted">
        Removed from the answer
        <span className="text-slate">
          {rows.length} claim{rows.length > 1 ? "s" : ""}
        </span>
      </h2>
      <p className="mt-3 max-w-prose text-[13px] leading-relaxed text-slate">
        The engine wrote these, then could not find them in the filings it cited. They were cut out
        of the text above rather than left for you to catch.
      </p>
      <ul className="mt-4 space-y-4">
        {rows.map((row) => {
          const meta = STATUS_META[row.status];
          return (
            <li key={row.claim_id} data-tone={meta.tone} className="border-l-2 border-[var(--tone)] pl-4">
              <p className="font-serif text-[15px] leading-relaxed text-slate line-through decoration-retracted/50">
                {row.text}
              </p>
              <p className="mt-1.5 font-mono text-[11px] text-slate">
                {meta.label}
                {row.method ? ` · ${METHOD_META[row.method].label}` : ""}
                {row.sources[0]?.accession_no ? ` · ${row.sources[0].accession_no}` : ""}
              </p>
              {row.feedback ? (
                <p className="mt-1 text-[12px] leading-snug text-[var(--tone)]">{row.feedback}</p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
