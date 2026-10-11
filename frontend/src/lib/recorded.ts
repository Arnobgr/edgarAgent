/**
 * Which recorded run a stranger lands on.
 *
 * Chosen, not random. The exit gate asks that a visitor see verification
 * working within one screen, so the landing run has to make several claims,
 * carry at least one retraction, use more than one verification method, and
 * still fit above the fold. This one (g-019 of the 2026-10-11 baseline) makes
 * six grounded claims, four by XBRL reconciliation and two by entailment. It
 * has three numeric claims caught and cut, cross-company differences that
 * ADR 0044 deliberately does not recompute, and it is 1,146 characters long.
 * One shorter run also meets all four tests, but it is g-029, the unanswerable
 * stock-price question, which is the wrong first impression.
 *
 * **This constant is coupled to `recorded/`, which is regenerated whenever a
 * new eval baseline is accepted.** When ADR 0043 replaced the 2026-08-01
 * export with the 2026-08-13 one, every run id changed and the previous
 * landing id stopped existing. The 2026-10-10 and 2026-10-11 exports changed
 * them again. Nothing failed loudly: Cloudflare Pages answers a
 * missing asset with the SPA shell under HTTP 200, so the page fetched
 * `index.html`, tried to parse it as a run, and broke — a 200 that means "not
 * found", exactly the shape `/healthz` is documented for. Re-point this when
 * the baseline moves; `frontend/src/lib/answer.test.ts` fails if you forget,
 * which is the only automatic warning there is, and it is not run by CI.
 *
 * One run must never be it: a run whose synthesis emitted no answer spans,
 * because its prose cannot be marked and the landing page would demonstrate
 * the one case the product cannot show. The 2026-10-11 export has two such
 * rows, both in g-020 (`db1877c98db4`): the model quoted those sentences
 * inexactly and the one repair did not fix them (ADR 0023). Since ADR 0046's
 * addendum, a span can no longer be lost by being omitted.
 */
export const LANDING_RUN_ID = "411d11c10311";
