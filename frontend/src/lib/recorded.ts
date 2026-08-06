/**
 * Which recorded run a stranger lands on.
 *
 * Chosen, not random. The exit gate asks that a visitor see verification
 * working within one screen, so the landing run has to make several claims,
 * carry at least one retraction, use more than one verification method, and
 * still fit above the fold: this one makes four grounded claims across
 * entailment and XBRL reconciliation, has one numeric claim caught and cut by
 * the reconciler, and is a thousand characters long.
 *
 * Two runs must never be it. `2b11fa1d4f9e` is already excluded from the
 * export (ADR 0038) — it answered with a figure and no claims at all.
 * `4d264b627151` is exported and readable, but its synthesis emitted no
 * answer spans, so its prose cannot be marked and the landing page would
 * demonstrate the one case the product cannot show.
 */
export const LANDING_RUN_ID = "ed8e9f299943";

/** Exported, but its prose carries no marks. Fine to read, wrong to land on. */
export const UNMARKABLE_RUN_ID = "4d264b627151";
