import type { RunResult } from "../types/api";

export function usd(amount: number): string {
  if (amount === 0) return "$0.00";
  return amount < 0.01 ? `$${amount.toFixed(4)}` : `$${amount.toFixed(2)}`;
}

export function count(value: number): string {
  return value.toLocaleString("en-US");
}

export function shortDate(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export interface ClaimTally {
  grounded: number;
  partial: number;
  unsupported: number;
  retracted: number;
  accepted: number;
  pending: number;
  total: number;
}

/**
 * Count the rows. Deliberately a tally and not a rate.
 *
 * `grounding_rate` is an eval metric with a definition — it pools claims
 * across a 30-question run — and a per-run number computed here would carry
 * the same name while meaning something else. Counting the rows the payload
 * already contains cannot drift from it; coining a rate would.
 */
export function tally(result: RunResult): ClaimTally {
  const rows = [...result.grounding, ...result.retracted];
  return {
    grounded: rows.filter((r) => r.status === "grounded").length,
    partial: rows.filter((r) => r.status === "partial").length,
    unsupported: rows.filter((r) => r.status === "unsupported").length,
    retracted: rows.filter((r) => r.status === "retracted").length,
    accepted: rows.filter((r) => r.status === "accepted").length,
    pending: rows.filter((r) => r.status === "pending").length,
    total: rows.length,
  };
}
