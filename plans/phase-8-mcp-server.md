# Phase 8 (Extension) — MCP Server

**Goal (blueprint §12/§13):** expose the finished engine as an MCP server so Claude Desktop — or any MCP client — can consume it as a tool over a standard protocol. **Precondition, stated by the blueprint in bold: only after the core is fully implemented and evaluated** (Phases 0–7 gates all passed). This is the one place MCP is non-decorative: a real client boundary, external consumers. Do not wrap internal tools in MCP for in-process use — the blueprint explicitly bans that.

## Context (read this if starting cold)

The deployed system (Phases 0–7): a FastAPI service on a VPS (Tailscale + Cloudflare Tunnel) running a verified-QA engine over TSLA/AAPL/NVDA SEC filings. API contract: `POST /v1/questions` → job id; poll `GET /v1/jobs/{id}`; `GET /v1/jobs/{id}/result` → answer + per-claim grounding table; `awaiting_review` + review endpoints for the human-in-the-loop gate; `X-API-Key` auth. Runs take minutes. Full eval baselines exist and gate CI.

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
