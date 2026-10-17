# Phase 8 (Extension) — MCP Server

**Goal (blueprint §12/§13):** expose the finished engine as an MCP server so Claude Desktop — or any MCP client — can consume it as a tool over a standard protocol. **Precondition, stated by the blueprint in bold: only after the core is fully implemented and evaluated** (Phases 0–7 gates all passed). This is the one place MCP is non-decorative: a real client boundary, external consumers. Do not wrap internal tools in MCP for in-process use — the blueprint explicitly bans that.

## Context (read this if starting cold)

Phases 0–7 are closed (Phase 7's record is at the foot of
`plans/phase-7-cost-and-rigor.md`). The deployed system is a FastAPI service
on a VPS, behind an nginx vhost with Let's Encrypt, Turnstile and
fail2ban (ADR 0035). There is no Cloudflare Tunnel. The static SPA is on
Cloudflare Pages, and tracing goes to Langfuse Cloud (ADR 0032). It runs a
verified-QA engine over TSLA/AAPL/NVDA SEC filings: 85 filings (10-K, 10-Q
and earnings 8-Ks, the last three fiscal years), no transcripts, re-ingested
2026-10-17 (ADR 0048).

API contract, as the code has it (`src/fie/api/app.py`, models in `models.py`):

- `POST /v1/questions` with `{question, companies: [...], turnstile_token}`
  returns `202 {job_id, status, source}`. `source` says whether the answer
  cache served it.
- `GET /v1/jobs/{id}` returns status, cost, tokens and a `progress` block
  read from the checkpoint: `phase`, `step`, `verify_iteration`,
  `critic_iteration`, `cost_usd`, `tokens`.
- `GET /v1/jobs/{id}/result` returns the `RunPayload`: answer plus grounding
  table plus retracted claims. It is the same shape as `recorded/`.
- `GET|POST /v1/jobs/{id}/review` is the human-review gate (ADR 0034). It is
  off by default.
- `POST /v1/jobs/{id}/resume`, `GET /v1/jobs/{id}/trace`, `GET /v1/admin/spend`.

A run takes about 30 s to a few minutes and costs $0.030 mean, $0.069 p90
(10-17 baseline). Evals run locally or on the box (`deploy/monthly-eval.sh`),
not in CI. CI runs ruff, mypy and pytest only.

## Findings and decisions before implementation (2026-10-17)

**Read this first.** It was written after checking this plan against the
deployed code. Where it conflicts with *Design* below, this section wins.

### Blocker: the deployed API has no way in for a non-browser client

- **Turnstile is a deployment-wide switch** (`Settings.turnstile_required`),
  and production has it on. The deploy smoke test shows it on every deploy:
  *"Turnstile refused a keyed submission, which is the point."* An MCP
  client cannot solve a browser challenge.
- **There is one API key, and it is public.** It ships in the SPA bundle, so
  it identifies and does not control (ADR 0030).
  `test_turnstile_refuses_a_submission_carrying_a_valid_api_key` asserts
  exactly that, so letting that key skip Turnstile would open the public path
  to scripts.
- **The private (tailnet) path no longer exists.** Phase 4's private path was
  `FIE_TURNSTILE_REQUIRED=false` with no nginx vhost. `deploy.sh` refuses
  Turnstile-off while the vhost exists, so the two paths are mutually
  exclusive by design. Today the API binds to `127.0.0.1:8412`, and nginx is
  the only route in.
- **The per-IP quota trusts `X-Real-IP`** only because nginx is the sole route
  and overwrites it (`client_ip()` in `app.py`). Any new route in (an ssh
  tunnel, a tailnet listener) must not let a caller set that header.

**Decision 1: how a private client gets in.** Options found:

| | Option | For | Against |
|---|---|---|---|
| A | A second, secret key (never in the bundle). A submission carrying it is accepted without Turnstile. | One deployment, one queue. The public key's behaviour and its test are unchanged. | Turnstile stops being the only control on the public listener, so the new key is a real secret. It needs rotation (sops, as for the other secrets) and its own caps (decision 2). It amends ADR 0030, so it needs an ADR. |
| B | A second API container with Turnstile off, reachable only over the tailnet | Keeps ADR 0030's rule that the public listener has one control | Double memory on a tight box, a second job queue and semaphore over the same corpus, and a second deploy path |
| C | Run the engine in-process inside the MCP server on the Mac | No server change | This plan rejects it (thin client, single deployment), and the corpus would have to live on the Mac |

The 2026-10-17 session recommended **A**. The user has not decided.

**Decision 2: budget and quota for MCP calls.** The governor's caps are
$0.25 per public run, **$0.35 per day** and $5 per month (`Settings.spend_cap_*`,
ADR 0030), with 3 questions per IP per day. $0.35 is about 12 questions a day
across everyone, so MCP use would spend the public demo's budget. Decide:
- a shared or a separate daily cap;
- which per-run cap applies (the public $0.25 or the default $5);
- whether the per-IP quota applies, and what counts as the caller's identity
  on that route.

**Decision 3: the route in from the Mac.** Either the public vhost (HTTPS;
the secret key is the control) or the tailnet (an ssh tunnel to loopback, or
nginx also listening on the tailscale interface). The route constrains
decision 1 and the `X-Real-IP` point above.

### Other decisions

4. **Tool input: `company` versus `companies`.** The plan's tool takes one
   ticker enum, but the API takes a list, and six of the 30 gold questions
   compare companies (g-015 to g-020). The recommendation is an optional
   list of the three-ticker enum, empty meaning "let the engine decide", as
   the API already does.
5. **`search_filings` needs a new endpoint** (`GET /v1/search`). Decide its
   auth (the same as decision 1, and never the public key alone, because each
   search embeds a query), its rate limit, and whether to ship it with the
   dossier tool or after. It costs one embedding call and no LLM call.
6. **The answer cache serves MCP calls too.** It is exact-match on question
   plus companies and never expires. After a corpus refresh it keeps serving
   the old corpus's answer (see `docs/deploy.md`, *Replacing a live corpus*).
   Decide whether the dossier tool says when a result came from the cache
   (`source` is already in the API response) and whether the cache needs a
   corpus or version key first. Apple's FY2026 10-K, due around
   2026-10-31, is the next refresh.
7. **Long runs against Claude Desktop's tool timeout.** Unmeasured. Progress
   notifications may or may not reset the client's timeout. Test early with a
   real run. If the timeout is too short, use the `submit`/`check` tool pair
   the design already names.
8. **The `mcp` SDK is a new dependency.** Lock a release more than two weeks
   old (the reasoning of ADR 0041's npm hold, and the 2026-10-13
   `httpxthrottlecache` choice in ADR 0048).
9. **How Claude Desktop starts the server.** The package is not published,
   so `uvx` from an index is not available. The likely command is
   `uv run --directory <repo> fie-mcp` with the key in the environment.
   Decide whether the key goes in Claude Desktop's config file, which stores
   it in plain text, or in a local env file the entry point reads.
10. **The review gate stays unexposed**, as *Design* says. It is off in
    production, so `awaiting_review` will not occur unless it is enabled.
    Test the passthrough shape anyway.

### Already settled, do not re-decide

- Thin client over the HTTP API; no in-process engine (see *Architecture*).
- Read-only tool surface; no review actions.
- stdio first; Streamable HTTP only when a real remote consumer appears.

## Design

### Architecture: MCP server as a thin client of the HTTP API

The MCP server (`src/fie/mcp/` or `mcp_server/`) calls the deployed FastAPI service — it does **not** import the graph and run it in-process. Reasons: the engine stays single-deployment (one place for budgets, HITL state, traces); the MCP layer stays what it is — a protocol adapter at a client boundary; and the same server binary works locally (stdio) and remotely. Python MCP SDK (`FastMCP`).

### Tools (blueprint §12: crisp schema, precise docstrings — the description IS the interface the calling model reasons over; separate read from action)

1. **`get_verified_dossier(company, question)` — the headline tool.**
   - `company`: enum of supported tickers (TSLA/AAPL/NVDA) — enumerate in the schema so the client model can't ask for what the corpus lacks; `question`: the analyst question.
   - Docstring states precisely: what the engine does (multi-agent research + per-claim verification over SEC filings), what comes back (answer + grounding table + retracted claims), the corpus boundary (which companies, which forms, which years, **no transcripts**), and that runs take minutes.
   - **Long-run handling:** submits the job, then polls internally, streaming MCP progress notifications (current node / verify iteration from the status endpoint) until done. If the client's timeout budget is a problem in practice, add a `submit`/`check` split tool pair — decide by testing against Claude Desktop, note the outcome.
   - Result: structured content — the answer, per-claim table (claim, verdict, source citation with accession number, supporting span), grounding stats. Never strip verdicts to make output "cleaner": the verification detail is the product.
2. **`check_dossier_status(job_id)` / `get_dossier(job_id)`** — the read-only escape hatch for long runs and for retrieving past results.

**Read vs action & the HITL gate (blueprint §12):** all exposed tools are read-only against the engine. The human-review action (`accept`/`retract` on unresolved claims) is deliberately **not** exposed as an MCP tool — a calling model must not resolve the human gate. A run that stops at `awaiting_review` is returned as such, with the unresolved claims listed and a pointer to the review UI. State this in the tool docstring and in the ADR; it's the security-relevant design line.

### Bonus reuse (blueprint §12): `search_filings(query, company?, form_type?)`

Expose the retrieval layer (hybrid search over the corpus, returning chunks with citation metadata, no synthesis) as a second tool — the honest second justification: reuse of the retrieval layer across clients. Requires a thin read-only retrieval endpoint on the API (add `GET /v1/search` in this phase). Cheap, fast, and demos well next to the heavyweight dossier tool.

### Transports & deployment

- **stdio** for local Claude Desktop use (`uvx`-runnable entry point; setup snippet in the README) — talks to the deployed API over the tunnel with an API key from local env.
- **Streamable HTTP** variant on the VPS (same server code) for remote MCP clients, Tailscale-only initially; public exposure only with auth and only if a real consumer appears (earn it).
- Budget note: MCP callers spend real money per dossier — the Phase 4 per-run/daily caps are the backstop; give the MCP key its own rate limit.

## Tests

- Unit: tool schemas validate; corpus-boundary errors (unsupported ticker) return clean, model-legible messages; `awaiting_review` passthrough shape.
- Integration: MCP client harness (the SDK's test client) against a mocked API; one manual end-to-end from Claude Desktop → real engine, transcript saved to `docs/`.

## Exit gate

- From Claude Desktop: ask a question naturally, watch progress notifications, receive a verified dossier whose grounding table matches what the web frontend shows for the same job.
- `search_filings` returns cited chunks in Claude Desktop.
- The HITL boundary holds: an `awaiting_review` run cannot be resolved via MCP.
- README gains an "Use it from Claude Desktop" section; ADR records the thin-client architecture and the read-only tool-surface decision.
