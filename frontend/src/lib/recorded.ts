/**
 * Which recorded run a stranger lands on.
 *
 * Chosen, not random. The exit gate asks that a visitor see verification
 * working within one screen, so the landing run has to make several claims,
 * carry at least one retraction, use more than one verification method, and
 * still fit above the fold. This one (g-018 of the 2026-10-10 baseline) makes
 * four grounded claims, two by XBRL reconciliation and two by entailment. It
 * has one numeric claim caught and cut, a cross-company difference that
 * ADR 0044 deliberately does not recompute, and it is 932 characters long. No
 * other run in that export meets all four tests.
 *
 * **This constant is coupled to `recorded/`, which is regenerated whenever a
 * new eval baseline is accepted.** When ADR 0043 replaced the 2026-08-01
 * export with the 2026-08-13 one, every run id changed and the previous
 * landing id stopped existing. The 2026-10-10 export changed them again. Nothing failed loudly: Cloudflare Pages answers a
 * missing asset with the SPA shell under HTTP 200, so the page fetched
 * `index.html`, tried to parse it as a run, and broke — a 200 that means "not
 * found", exactly the shape `/healthz` is documented for. Re-point this when
 * the baseline moves; `frontend/src/lib/answer.test.ts` fails if you forget,
 * which is the only automatic warning there is, and it is not run by CI.
 *
 * One run must never be it: a run whose synthesis emitted no answer spans,
 * because its prose cannot be marked and the landing page would demonstrate
 * the one case the product cannot show. The 2026-10-10 export has one: g-002
 * (`716e05bc2d5c`), whose three rows carry no span because the model omitted
 * the optional `answer_span` field (ADR 0046).
 */
export const LANDING_RUN_ID = "31c483130cbb";
