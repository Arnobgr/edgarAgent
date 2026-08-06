/**
 * How a verdict looks, and the two places the palette is load-bearing.
 *
 * **`accepted` is never the colour of `grounded`.** ADR 0034 exists so a human
 * waving a claim through is not laundered into `grounding_rate`; a palette
 * that collapses the two would defeat it in the one place a reader would
 * believe it. It gets the violet reserved for anomalies — neither good nor
 * bad, just "look at this" — and with review mode off in production it should
 * never appear at all.
 *
 * **`unsupported` in the grounding table is worse than `retracted`, not
 * better.** Retracted means the sentence was cut out of the answer.
 * Unsupported means excision failed and the text is still there, unbacked,
 * which is exactly the state `hallucination_rate` counts. Both are red and the
 * wording says which is which.
 */

import type { ClaimStatus, ClaimType, VerifyMethod } from "../types/api";

export type Tone = "verified" | "partial" | "retracted" | "anomaly";

export interface StatusMeta {
  label: string;
  tone: Tone;
  /** Said in full in the popover and the legend, in the interface's voice. */
  detail: string;
}

export const STATUS_META: Record<ClaimStatus, StatusMeta> = {
  grounded: {
    label: "Grounded",
    tone: "verified",
    detail: "Checked against the cited filing and supported by it.",
  },
  partial: {
    label: "Partly grounded",
    tone: "partial",
    detail: "The filing supports part of this claim, not all of it.",
  },
  unsupported: {
    label: "Unsupported",
    tone: "retracted",
    detail: "The filing does not support this, and it is still in the answer.",
  },
  retracted: {
    label: "Retracted",
    tone: "retracted",
    detail: "The filing does not support this. It was removed from the answer.",
  },
  accepted: {
    label: "Accepted by a reviewer",
    tone: "anomaly",
    detail: "A person passed this claim. The verifier did not.",
  },
  pending: {
    label: "Not checked",
    tone: "anomaly",
    detail: "The run ended before the verifier reached this claim.",
  },
};

/** Worst first. Used when one sentence carries several claims: the mark takes
 *  the most serious verdict on it, and the popover lists them all. Reading the
 *  softer verdict onto a shared sentence would hide the harder one. */
const SEVERITY: ClaimStatus[] = [
  "unsupported",
  "retracted",
  "partial",
  "pending",
  "accepted",
  "grounded",
];

export function severest(statuses: ClaimStatus[]): ClaimStatus {
  for (const candidate of SEVERITY) {
    if (statuses.includes(candidate)) return candidate;
  }
  return "grounded";
}

export const METHOD_META: Record<VerifyMethod, { short: string; label: string; detail: string }> = {
  numeric_reconciliation: {
    short: "XBRL",
    label: "XBRL reconciliation",
    detail: "Matched to a filed XBRL fact at the precision the claim states. No model judged it.",
  },
  semantic: {
    short: "SEM",
    label: "Entailment",
    detail: "A model judged whether the cited passage entails the claim.",
  },
  attribution: {
    short: "ATTR",
    label: "Attribution",
    detail: "A forward-looking statement, checked as attributed to the filing rather than as true.",
  },
};

export const TYPE_LABEL: Record<ClaimType, string> = {
  numeric: "Numeric",
  qualitative: "Qualitative",
  forward_looking: "Forward-looking",
};
