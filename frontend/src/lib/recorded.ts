/**
 * Which recorded run a stranger lands on.
 *
 * Chosen, not random. The exit gate asks that a visitor see verification
 * working within one screen, so the landing run has to make several claims,
 * carry at least one retraction, use more than one verification method, and
 * still fit above the fold. This one (g-011 of the 2026-10-17 baseline) makes
 * ten grounded claims, four by XBRL reconciliation and six by entailment, and
 * cuts one: a nine-month Data Center figure from a 10-Q that is not a filed
 * XBRL fact. At 1,587 characters it is the shortest run that passes all four
 * tests. g-016 is the next candidate and is the wrong first impression: the
 * claim it retracts repeats R&D figures the same answer grounds elsewhere.
 *
 * **This constant is coupled to `recorded/`, which is regenerated whenever a
 * new eval baseline is accepted.** When ADR 0043 replaced the 2026-08-01
 * export with the 2026-08-13 one, every run id changed and the previous
 * landing id stopped existing. The 2026-10-10, 2026-10-11 and 2026-10-17
 * exports changed them again. Nothing failed loudly: Cloudflare Pages answers a
 * missing asset with the SPA shell under HTTP 200, so the page fetched
 * `index.html`, tried to parse it as a run, and broke — a 200 that means "not
 * found", exactly the shape `/healthz` is documented for. Re-point this when
 * the baseline moves; `frontend/src/lib/answer.test.ts` fails if you forget,
 * which is the only automatic warning there is, and it is not run by CI.
 *
 * One run must never be it: a run whose synthesis emitted no answer spans,
 * because its prose cannot be marked and the landing page would demonstrate
 * the one case the product cannot show. The 2026-10-17 export has none; the
 * 2026-10-11 one had two, both in g-020, where the model quoted inexactly and
 * the one repair did not fix it (ADR 0023). Since ADR 0046's addendum, a span
 * can no longer be lost by being omitted.
 */
export const LANDING_RUN_ID = "134f94cfdd58";
