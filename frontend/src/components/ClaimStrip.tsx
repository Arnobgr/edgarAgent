import { STATUS_META } from "../lib/status";
import type { GroundingRow } from "../types/api";
import { Tick } from "./Tick";

interface ClaimStripProps {
  rows: GroundingRow[];
  lit: string | null;
  onLit: (claimId: string | null) => void;
}

/**
 * The whole run as a row of marks, in the order the claims appear.
 *
 * This is the three-second read the blueprint asks for: before a word of the
 * answer is read, the reader can see how many claims it makes and how they
 * fared. It is the tally rendered as the artifact rather than as a headline
 * figure, which matters — a big "97%" would be a number the reader has to
 * trust, while a row of marks they can hover is the evidence itself.
 */
export function ClaimStrip({ rows, lit, onLit }: ClaimStripProps) {
  if (rows.length === 0) return null;
  const groups = new Map<string, number>();
  for (const row of rows) {
    const label = STATUS_META[row.status].label;
    groups.set(label, (groups.get(label) ?? 0) + 1);
  }
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
      <div className="tick-strip flex flex-wrap items-center gap-1.5">
        {rows.map((row, index) => (
          <Tick
            key={row.claim_id}
            status={row.status}
            lit={lit === row.claim_id}
            order={index}
            title={`${STATUS_META[row.status].label}: ${row.text}`}
            onEnter={() => onLit(row.claim_id)}
            onLeave={() => onLit(null)}
          />
        ))}
      </div>
      <p className="font-mono text-[11px] uppercase tracking-wider text-slate">
        {[...groups].map(([label, n]) => `${n} ${label.toLowerCase()}`).join(" · ")}
      </p>
    </div>
  );
}
