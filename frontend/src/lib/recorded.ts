/**
 * Which recorded run a stranger lands on.
 *
 * Chosen, not random. The exit gate asks that a visitor see verification
 * working within one screen, so the landing run has to make several claims,
 * carry at least one retraction, use more than one verification method, and
 * still fit above the fold: this one makes four grounded claims across
 * entailment and XBRL reconciliation, has two numeric claims caught and cut,
 * and is 914 characters long.
 *
 * **This constant is coupled to `recorded/`, which is regenerated whenever a
 * new eval baseline is accepted.** ADR 0043 replaced the 2026-08-01 export
 * with the 2026-08-13 one and every run id changed, so the previous landing
 * id stopped existing. Nothing failed loudly: Cloudflare Pages answers a
 * missing asset with the SPA shell under HTTP 200, so the page fetched
 * `index.html`, tried to parse it as a run, and broke — a 200 that means "not
 * found", exactly the shape `/healthz` is documented for. Re-point this when
 * the baseline moves; `frontend/src/lib/answer.test.ts` fails if you forget,
 * which is the only automatic warning there is, and it is not run by CI.
 *
 * One run must never be it: a run whose synthesis emitted no answer spans,
 * because its prose cannot be marked and the landing page would demonstrate
 * the one case the product cannot show. The 08-13 corpus contains none —
 * every one of its 200 grounding rows carries a locatable span — so the
 * constant that used to name one is gone rather than re-pointed.
 */
export const LANDING_RUN_ID = "11f727f75ca6";
