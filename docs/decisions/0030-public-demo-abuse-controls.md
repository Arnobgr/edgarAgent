# ADR 0030 — Public-demo abuse controls: load and spend are different problems

**Date:** 2026-08-05 · **Phase:** 4 · **Status:** accepted ·
**Depends on:** ADR 0035 (nothing sits in front of the box) ·
**Includes:** ADR 0027 as layer 2

## Context

A public endpoint that spends real money per request, on a shared box with no
CDN in front of it (ADR 0035), reachable by anyone with the URL. Measured
cost, from `run_budget` over the last 40 checkpointed runs on the current
GLM-synthesis arm: **$0.023 median, $0.064 p90, $0.075 max**. The operator's ceiling
is **$5/month ≈ 215 live questions** — a budget, not an anxiety.

The frontend is a static SPA on Cloudflare Pages. **It cannot hold a secret.**
Its API key ships inside the bundle where devtools read it. Any design that
treats that key as a control is lying to itself, and saying so out loud is the
first requirement of this ADR.

## Decision

**Nine layers, cheapest first, and deliberately outermost-first — the outer
layers must not be code we wrote, because our code can be wrong.**

| # | layer | caps | where |
|---|---|---|---|
| 1 | provider hard caps (fixed OpenRouter balance, auto-top-up **off**) | spend | outside the repo |
| 2 | recorded runs served from the Pages CDN | **load** | ADR 0027 |
| 3 | Turnstile on submit | load | `api/security.py` |
| 4 | nginx `limit_req`/`limit_conn` + fail2ban | load | ADR 0035 |
| 5 | global USD governor (daily + monthly) | spend | `api/governor.py` |
| 6 | per-IP daily quota | spend | `api/governor.py` |
| 7 | concurrency semaphore + queue cap | spend/OOM | ADR 0026 |
| 8 | exact-match answer cache | spend | `api/governor.py` |
| 9 | input constraints (length, ticker allow-list) | spend | `api/app.py` |

**Layers 2–4 cap load; layers 5–9 cap spend. Neither substitutes for the
other** — the governor only runs after FastAPI has already accepted the
connection.

Layer 1 is the only one that survives a bug in our own accounting or a leaked
key, which is why it is first and why it is done by hand before any of this
ships.

**Turnstile is what actually holds the public path**, because its token is
minted per visitor per challenge and cannot be copied out of a bundle. The API
key authenticates the *tailnet* consumer and nothing else.
`test_turnstile_refuses_a_submission_carrying_a_valid_api_key` encodes that
sentence as an assertion rather than a comment.

**Turnstile being required is explicit config, not inferred from "is a secret
set?"** A control that silently switches itself off when an env var goes
missing is precisely the failure this layer exists to prevent. `create_app`
refuses to start with `turnstile_required` on and no secret, so "off" is
always a decision. Verification **fails closed** when siteverify is
unreachable.

**The governor's daily sub-cap is the point of it.** $5/month with a
$0.35/day sub-cap means one bad day costs 7% of the month rather than all of
it. Spend is recorded through `ApiStore`, so it survives a restart — an
in-memory counter would reset the month's spend on every deploy.

**A run that raised is still charged.** The `BudgetTracker` callback records
usage as it happens, not from the returned `RunResult`, so the one path where
spending could go untracked is the one path where a result never arrives.

**The cache is exact-match only.** A semantic cache can serve the answer to a
*different* question, which for a verification system is the worst failure
available. The key is normalized question + company scope, and it never
crosses scopes.

**The cache is checked before the governor and the quota**, so a cached answer
costs no money and spends no caller's quota. This was found by two tests
failing with `202` where `429` was expected — repeating the same question hit
the cache. The behaviour is correct and now has a test that asserts it
deliberately rather than a test that trips over it.

**Public runs get tighter settings than the CLI.** `public_run_settings()`
returns a copy with `budget_max_cost_usd` 5.0 → **0.25** and
`node_timeout_seconds` 600 → **180**. The loose caps are right for a CLI the operator
drives themselves; on a 2-slot semaphore a 600s stall would park half the public
capacity for ten minutes (ADR 0025 measures that at ~1.5% of synthesize
calls). Tightened in `Settings`, not at the call site, so `answer_question`
stays one function with one behaviour.

**Per-IP quota reads `X-Real-IP`, not `X-Forwarded-For`.**
`proxy_add_x_forwarded_for` *appends* to whatever the client sent, so the
forwarded chain is caller-controlled and useless as an identity. nginx sets
`X-Real-IP` from `$remote_addr`; that line in `deploy/nginx/fie-proxy.conf` is
load-bearing and a test asserts a spoofed chain does not reset the quota.

**No Cloudflare Worker / BFF proxy.** It was considered: a Worker would hold
the API key server-side so it never reaches the browser. Rejected because
Turnstile already makes a copied key useless, so the Worker's one unique win
is worth little against a second runtime, a second deploy pipeline and a new
language — the un-earned complexity `00-overview.md` §3.1 bans by name.
**Upgrade trigger:** observed key-copying abuse that Turnstile and the per-IP
quota do not absorb → move quota enforcement to a Worker *then*.

## Consequences

15 tests in `tests/test_api_abuse.py` cover the spend side against a fake
engine and a hand-advanced clock: the cap crossing and *staying* closed,
reopening on the next UTC day, the month cap holding on a clear day, a raised
run still charged, quota per address and its day-boundary reset, the spoofed
forwarded chain, semaphore peak concurrency, the queue cap reopening, cache
identity and scope isolation, and the ledger surviving a restart.

Layers 2 and 4 are **not** testable in pytest — one is a CDN and one is
nginx. `scripts/burst-test.sh` proves them against the deployed host, and the
exit gate requires it.

## Honest limits

- Every threshold here is reasoned from the cost measurements and the RAM
  table, not from observed abuse. They are sized, not validated.
- The per-IP quota is per *address*. NAT makes it per office; a rotating
  residential proxy defeats it entirely. It raises the cost of casual abuse
  and nothing more — layer 1 is what bounds the determined case.
- Turnstile is a Cloudflare dependency on the submit path. If siteverify is
  down, submissions fail closed, which is the right direction but is still an
  outage the demo did not have before.
- The $5/month cap is enforced against *our* accounting of provider cost. If
  a provider prices something differently than `BudgetTracker` models it, the
  governor is wrong by exactly that error. Layer 1 exists for this.
