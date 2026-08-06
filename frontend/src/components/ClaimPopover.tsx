import { useEffect, useLayoutEffect, useRef, useState } from "react";

import type { GroundingRow } from "../types/api";
import { METHOD_META, STATUS_META, TYPE_LABEL } from "../lib/status";

interface ClaimPopoverProps {
  rows: GroundingRow[];
  anchor: DOMRect;
  onDismiss: () => void;
}

const WIDTH = 380;
const GAP = 10;

/**
 * What a claim rests on, said in full.
 *
 * The popover exists because the coloured sentence alone is a verdict without
 * evidence — a reader has to be able to get from "this is green" to the
 * quoted line of the filing, the form it came from and the method that judged
 * it, without leaving the sentence. It takes a list of rows rather than one,
 * because a sentence can carry several claims.
 */
export function ClaimPopover({ rows, anchor, onDismiss }: ClaimPopoverProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 0, top: 0 });

  useLayoutEffect(() => {
    const height = ref.current?.offsetHeight ?? 0;
    const left = Math.min(
      Math.max(GAP, anchor.left + anchor.width / 2 - WIDTH / 2),
      window.innerWidth - WIDTH - GAP,
    );
    // Above the sentence when there is room, below when there is not, so the
    // popover never covers the text it is explaining.
    const above = anchor.top - height - GAP;
    setPosition({ left, top: above > GAP ? above : anchor.bottom + GAP });
  }, [anchor]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onDismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onDismiss]);

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label="Claim verification"
      className="fixed z-50 rounded-md border border-rule bg-raised shadow-[0_8px_30px_-12px_rgba(18,24,31,0.35)]"
      style={{ left: position.left, top: position.top, width: WIDTH, maxWidth: "calc(100vw - 20px)" }}
    >
      {rows.map((row, index) => {
        const meta = STATUS_META[row.status];
        const method = row.method ? METHOD_META[row.method] : null;
        const source = row.sources[0];
        return (
          <div
            key={row.claim_id}
            data-tone={meta.tone}
            className={index > 0 ? "border-t border-rule p-4" : "p-4"}
          >
            <div className="flex items-baseline gap-2">
              <span className="tick" data-lit="false" />
              <span className="font-mono text-[11px] font-medium uppercase tracking-wider text-[var(--tone)]">
                {meta.label}
              </span>
              <span className="ml-auto font-mono text-[11px] text-slate">
                {TYPE_LABEL[row.claim_type]}
                {method ? ` · ${method.label}` : ""}
              </span>
            </div>

            <p className="mt-2 text-[13px] leading-snug text-ink">{row.text}</p>

            {row.supporting_span ? (
              <blockquote className="mt-3 border-l-2 border-rule pl-3 font-serif text-[13px] leading-relaxed text-slate">
                “{row.supporting_span}”
              </blockquote>
            ) : (
              <p className="mt-3 text-[12px] text-slate">
                {method?.detail ?? "No quoted passage was recorded."}
              </p>
            )}

            {row.feedback ? (
              <p className="mt-2 text-[12px] leading-snug text-[var(--tone)]">{row.feedback}</p>
            ) : null}

            {source ? (
              <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-t border-rule pt-3 font-mono text-[11px] text-slate">
                <dt className="text-slate/70">Filing</dt>
                <dd className="text-ink">
                  {[source.ticker, source.form_type].filter(Boolean).join(" ") || source.doc_id}
                  {source.filing_date ? ` · filed ${source.filing_date}` : ""}
                </dd>
                {source.section ? (
                  <>
                    <dt className="text-slate/70">Section</dt>
                    <dd className="text-ink">{source.section}</dd>
                  </>
                ) : null}
                {source.accession_no ? (
                  <>
                    <dt className="text-slate/70">Accession</dt>
                    <dd className="text-ink">{source.accession_no}</dd>
                  </>
                ) : null}
                {row.sources.length > 1 ? (
                  <>
                    <dt className="text-slate/70">Also</dt>
                    <dd className="text-ink">
                      {row.sources.length - 1} further source
                      {row.sources.length > 2 ? "s" : ""}
                    </dd>
                  </>
                ) : null}
              </dl>
            ) : (
              <p className="mt-3 border-t border-rule pt-3 font-mono text-[11px] text-slate">
                No source was cited.
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
