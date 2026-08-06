import { useMemo, useState } from "react";

import { METHOD_META, STATUS_META, TYPE_LABEL } from "../lib/status";
import type { GroundingRow } from "../types/api";
import { Tick } from "./Tick";

type SortKey = "order" | "status" | "type" | "source";

interface GroundingTableProps {
  rows: GroundingRow[];
  lit: string | null;
  onLit: (claimId: string | null) => void;
}

const STATUS_RANK: Record<GroundingRow["status"], number> = {
  unsupported: 0,
  retracted: 1,
  partial: 2,
  pending: 3,
  accepted: 4,
  grounded: 5,
};

const HEADERS: { key: SortKey; label: string; className: string }[] = [
  { key: "order", label: "Claim", className: "w-auto" },
  { key: "type", label: "Type", className: "w-[7.5rem] hidden sm:table-cell" },
  { key: "source", label: "Source", className: "w-[9rem] hidden md:table-cell" },
  { key: "status", label: "Verdict", className: "w-[9rem]" },
];

/**
 * Every claim the run made, and what happened to it.
 *
 * The prose above shows the reader where the claims are; this shows them that
 * the list is complete — including the claims the prose cannot mark, which is
 * why the table is the authority and not the decoration.
 */
export function GroundingTable({ rows, lit, onLit }: GroundingTableProps) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "order", dir: 1 });

  const sorted = useMemo(() => {
    const indexed = rows.map((row, index) => ({ row, index }));
    indexed.sort((a, b) => {
      const by = (() => {
        switch (sort.key) {
          case "status":
            return STATUS_RANK[a.row.status] - STATUS_RANK[b.row.status];
          case "type":
            return a.row.claim_type.localeCompare(b.row.claim_type);
          case "source":
            return (a.row.sources[0]?.ticker ?? "").localeCompare(b.row.sources[0]?.ticker ?? "");
          default:
            // Document order, so toggling it reverses rather than doing
            // nothing visible.
            return a.index - b.index;
        }
      })();
      return by !== 0 ? by * sort.dir : a.index - b.index;
    });
    return indexed.map((entry) => entry.row);
  }, [rows, sort]);

  const toggle = (key: SortKey) =>
    setSort((current) =>
      current.key === key ? { key, dir: current.dir === 1 ? -1 : 1 } : { key, dir: 1 },
    );

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-[13px]">
        <thead>
          <tr className="border-b border-ink/20">
            <th className="w-6" />
            {HEADERS.map((header) => (
              <th key={header.key} scope="col" className={`py-2 pr-3 ${header.className}`}>
                <button
                  type="button"
                  onClick={() => toggle(header.key)}
                  className="font-mono text-[11px] font-medium uppercase tracking-wider text-slate hover:text-ink focus-visible:outline-2 focus-visible:outline-ink"
                  aria-label={`Sort by ${header.label.toLowerCase()}`}
                >
                  {header.label}
                  <span aria-hidden className="ml-1 text-[9px]">
                    {sort.key === header.key ? (sort.dir === 1 ? "▲" : "▼") : "·"}
                  </span>
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const meta = STATUS_META[row.status];
            const source = row.sources[0];
            return (
              <tr
                key={row.claim_id}
                data-tone={meta.tone}
                className="border-b border-rule align-top transition-colors hover:bg-[var(--tone-wash)] data-[lit=true]:bg-[var(--tone-wash)]"
                data-lit={lit === row.claim_id}
                onMouseEnter={() => onLit(row.claim_id)}
                onMouseLeave={() => onLit(null)}
              >
                <td className="py-2.5 pt-3.5">
                  <Tick status={row.status} lit={lit === row.claim_id} />
                </td>
                <td className="py-2.5 pr-3 leading-snug text-ink">
                  {row.text}
                  {row.feedback ? (
                    <span className="mt-1 block text-[12px] leading-snug text-[var(--tone)]">
                      {row.feedback}
                    </span>
                  ) : null}
                </td>
                <td className="hidden py-2.5 pr-3 font-mono text-[11px] text-slate sm:table-cell">
                  {TYPE_LABEL[row.claim_type]}
                  {row.method ? (
                    <span className="mt-0.5 block text-slate/70">
                      {METHOD_META[row.method].short}
                    </span>
                  ) : null}
                </td>
                <td className="hidden py-2.5 pr-3 font-mono text-[11px] text-slate md:table-cell">
                  {source ? (
                    <>
                      <span className="text-ink">
                        {[source.ticker, source.form_type].filter(Boolean).join(" ")}
                      </span>
                      <span className="mt-0.5 block break-all text-slate/70">
                        {source.accession_no}
                      </span>
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="py-2.5 font-mono text-[11px] font-medium uppercase tracking-wide text-[var(--tone)]">
                  {meta.label}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
