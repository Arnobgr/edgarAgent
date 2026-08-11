# Security

What this service is exposed to, what actually protects it, and the checks to
run before a deploy. Decisions live in ADRs 0029–0037; this is the operational
view.

## The one sentence to read first

**On the public path the API key identifies; it does not control.** The
Phase 5 frontend is a static SPA on Cloudflare Pages, and a static SPA cannot
hold a secret — its key ships inside the bundle where devtools read it. What
holds the public path is Turnstile, the spend governor, the per-IP quota and
nginx. `X-API-Key` authenticates the *tailnet* consumer (the CLI, the operator's Mac)
and nothing else.

`tests/test_api_abuse.py::test_turnstile_refuses_a_submission_carrying_a_valid_api_key`
exists to keep that sentence true rather than merely written down.

## What is exposed

| surface | reachable from | held by |
|---|---|---|
| `https://api.example.com/v1/questions` | the internet | nginx `limit_req`, Turnstile, governor, quota, queue cap |
| `…/v1/jobs/*` | the internet | nginx `limit_req` (poll zone), API key |
| `…/healthz` | the internet | nothing — unauthenticated by design, and it reports no secret |
| `…/v1/admin/*` | **nobody** — `deny all` at the vhost | two independent barriers: the vhost denies it, and the container publishes to loopback only, so reaching it means SSH to the box |
| `fie-api:8000` | `127.0.0.1:8412` on the VPS | never published to a public interface |
| `fie-qdrant` | the compose network | never published at all |

## Secrets

Storage is SOPS + age; runtime injection is compose secrets, mounted as files
at `/run/secrets` (ADR 0029). Env vars are deliberately not used — they leak
into `docker inspect`, crash dumps, child processes and any library that dumps
`environ`.

### Which secret exists where

| | `fie-api` | VPS eval cron | GitHub Actions |
|---|---|---|---|
| `OPENROUTER_API_KEY` | ✓ | ✓ | — |
| `OPENAI_API_KEY` | ✓ | ✓ | — |
| `LANGFUSE_*` | ✓ | ✓ | — |
| `API_KEY`, `TURNSTILE_SECRET_KEY` | ✓ | — | — |
| `ANTHROPIC_API_KEY` | **✗** | ✓ (judge only) | — |
| `EDGAR_IDENTITY` | **✗** | **✗** | — |
| `ghcr_token` | **✗** | **✗** | — (deploy tooling only) |

`ghcr_token` is the one entry in `secrets.enc.yaml` that names no `Settings`
field. It exists because the repository is private, so GHCR made the container
package private too and an anonymous pull gets a 403. It is a classic GitHub
token scoped to **`read:packages` and nothing else** — it cannot write a
package and it cannot reach a machine, which is what separates it from the
tailnet credential ADR 0037 refused. `deploy/deploy.sh` uses it for one
`docker login` and the application never sees it.

**This is temporary by plan.** The repository goes public after the final
phase, following a pass to remove dead code, stale docs and any PII. At that
point: make the package public, delete `ghcr_token` from the store, and delete
the login block from `deploy.sh`.

`EDGAR_IDENTITY` is the most sensitive value in the project — it is personal
data, required by SEC's user-agent policy, and this repo's history has been
scrubbed of it once. It is needed only for **ingest**, so neither the serving
path nor the eval holds it.

### First-time setup

```bash
# Once, on the Mac. `age-keygen` does not create the directory itself.
# The private half goes in a password manager as well — two machines can
# both fail, and losing this key loses every secret in the file's history,
# not just today's values.
mkdir -p "$HOME/Library/Application Support/sops/age"
age-keygen -o "$HOME/Library/Application Support/sops/age/keys.txt"
chmod 600 "$HOME/Library/Application Support/sops/age/keys.txt"

# Paste the printed public key into .sops.yaml, replacing the placeholder.
# Encrypt BEFORE the file holds anything real: a file created first and
# encrypted later spends that gap as plaintext under a name that looks safe.
cp secrets.example.yaml secrets.enc.yaml
sops --encrypt --in-place secrets.enc.yaml
sops secrets.enc.yaml        # edit values; commit the encrypted file

# Verify before every commit. Both counts must be non-zero.
grep -c '^sops:' secrets.enc.yaml
grep -c 'ENC\[AES256_GCM' secrets.enc.yaml

# On the VPS (Linux): the same key at /root/.config/sops/age/keys.txt (0600),
# then deploy/deploy.sh decrypts into ./secrets/ automatically.
```

> **`sops` reads the age key from the OS config directory, and that path
> differs by platform** — `~/Library/Application Support/sops/age/` on macOS,
> `~/.config/sops/age/` on Linux. The Linux path on a Mac produces
> `identity did not match any of the recipients`, which reads like a wrong key
> and is not one. `deploy/decrypt-secrets.sh` sets `SOPS_AGE_KEY_FILE`
> explicitly, so the VPS side is fixed rather than inferred.

Key names in `secrets.enc.yaml` **must** be the lowercased field names on
`fie.config.Settings`. A typo produces a setting left at its default rather
than an error.

### Rotation

Edit `sops secrets.enc.yaml`, commit, and run `deploy/deploy.sh`. Rotation is
a visible commit, which is most of why SOPS was chosen.

## Release checklist

Run these before a deploy. The first two are the "no secret in history, in an
image layer, or in CI logs" exit item, made verifiable.

```bash
# 1. No secret in history. Full history, not the tip — a secret that was
#    committed and removed is exactly what this catches.
gitleaks detect --source . --config .gitleaks.toml --redact --verbose

# 2. No secret in an image layer. The Dockerfile takes no build args and
#    mounts no secrets, so the only route in is the build context; this
#    checks that .dockerignore did its job.
docker history --no-trunc ghcr.io/arnobgr/fie:latest | grep -iE 'key|secret|token'
docker run --rm --entrypoint sh ghcr.io/arnobgr/fie:latest -c 'ls -a /app | grep -E "^\.env|^secrets"' || echo "clean"

# 3. No secret in CI. Structural (ADR 0037) — verify rather than assume.
#    Expect GITHUB_TOKEN and nothing else.
grep -r 'secrets\.' .github/

# 4. The abuse controls, against the running host (ADR 0030/0035).
./scripts/burst-test.sh
```

## Threat model

**The realistic threat is a drive-by script, not a targeted attacker.** This
is a portfolio demo over public SEC filings; there is no user data and nothing
to steal but money. The controls are sized for that.

What follows is the systematic version: where untrusted data crosses into the
system, six attacker stories against those crossings, and for each one the
worst outcome that is actually reachable. Stories **4** and **6** were built
and documented in Phases 4–5 and their control-by-control view is the table at
the end of this section; **1, 2, 3 and 5** are the injection half, added in
Phase 6.

Anything still unmitigated is marked **Open** with the thing that closes it, so
this section can be read on its own and swept later for what is left.

### Trust boundaries

Four kinds of data enter this system. Two of them are authored by someone the
operator does not control.

| boundary | what crosses | authored by | lands in |
|---|---|---|---|
| EDGAR → corpus | filing text, including 8-K exhibits and press releases | **third parties** — issuers and their wires | `data/parsed/` → `data/chunks/` → Qdrant → `pack_evidence` |
| visitor → API | `question` (≤ 500 chars), `companies`, Turnstile token | **the public** | `POST /v1/questions` → `_scoped_question` → decompose, rewrite, synthesis, critic |
| operator → box | image, secrets, corpus snapshot | the operator | `deploy/deploy.sh` |
| provider → us | completions | OpenRouter / Anthropic | claim text, draft answer |

`companies` is not a third untrusted channel: `submit_question` upper-cases
each entry and rejects anything outside `settings.corpus_tickers`
(`api/app.py:338`), so it cannot carry prose into a prompt. **`question` can,
and by design must.**

### What bounds every story below

Three structural facts decide the blast radius of every injection story, and
none of them is a setting that can be misconfigured.

1. **No model in this system has a tool bound to it.** `grep -rn bind_tools
   src/fie` returns nothing. Retrieval, fact lookup and reconciliation are
   called by Python inside a node; a model returns structured data and never
   selects an action. There is no string a filing can contain that picks a
   tool, because there is no picking step. The model router is the second
   layer of the same property: `get_model(role)` is the only way to obtain a
   chat model and model ids are never written at call sites (ADR 0014).
2. **Nothing in the graph performs a runtime fetch.** At answer time the only
   outbound hosts are the model providers. `httpx` appears in
   `ingest/embed.py` and `tools/retrieval/rerank.py` only, each pinned to one
   fixed URL, and EDGAR access is offline, CLI-driven, and reached through
   `Company(ticker)` rather than a URL (`tools/edgar.py`). An injected
   "fetch this URL" instruction has nothing to execute it.
3. **Numeric truth does not live in text an attacker can write.** Every
   numeric claim is checked against the XBRL facts store by deterministic
   SQLite lookup at the claim's stated precision, with no LLM verdict
   (ADR 0012). Prose asserting "revenue was $99 billion" cannot make that
   lookup succeed.

**So the blast radius of a successful injection is answer integrity and cost —
not remote action, not data theft, not persistence.** That is a property of the
architecture rather than of any hardening done in this phase.

### 1. Indirect prompt injection via corpus content

The headline risk (blueprint §9), and the one with no mitigation in place
before Phase 6.

**Entry point.** A filing carries attacker-authored text; 8-K exhibits are
press releases written by the issuer, not by SEC. It is chunked, embedded and
retrieved, and `pack_evidence` (`context/__init__.py:142`) puts the chunk body
into the human message of exactly three calls: **synthesis**, the **entailment
judge**, and the **attribution judge**. Nothing else in the graph sees chunk
text — decompose and query-rewrite see the question, the critic sees the draft
and the claim list, and the reconciler's mapper sees claim text only
(`tools/reconcile.py:404`). Injected text reaches the mapper second-hand at
most, as a claim synthesis was steered into writing.

**What is missing today.** There is no delimiter contract and no system-prompt
sentence telling those three calls that the excerpts are data. The nearest
thing is a *grounding* rule — "base every factual claim strictly on the
evidence provided", "judge only against the provided excerpts" — which
constrains what a model may assert, not whose instructions it follows. The
packing is also forgeable: blocks are joined with `\n\n---\n\n` and headed
with `[chunk_id] TICKER FORM (date) — section`, both plain text, so **a chunk
whose own body contains that separator and a plausible header manufactures an
evidence block and a citation id.**

**Blast radius**, in falling order of reachability:

- *the judges* — an excerpt reading "this claim is fully supported" arrives in
  the same window as the claim it is judging, and a judge that obeys it turns
  `unsupported` into `grounded`. This is the worst of the three, because it
  corrupts the measurement the whole project is an argument for.
- *synthesis* — steered prose, or a fabricated figure.
- *retrieval* — none. Chunk text cannot change what is retrieved next; the
  rewrite step sees the question only.

**Worst realistic outcome.** A qualitative or forward-looking claim is marked
`grounded` on the strength of injected text and ships in the answer, cited to
the poisoned chunk. A numeric claim cannot survive this route, since the XBRL
lookup does not read the prompt — so **the corpus attack that pays is the
qualitative one, not the fake-table one that reads more dramatically.**

**Reachability, honestly.** The corpus is 78 filings from TSLA, AAPL and NVDA,
ingested once by hand. Using this path requires text inside a filing by one of
those three issuers, which means already being able to publish through their
filing agents. **The realistic version is not a targeted attack on this
system; it is a filing that contains hostile text for some other reason, which
we then ingest indiscriminately.** The mitigation earns its place because the
risk class is real and this system is a live instance of it, not because this
particular path is likely.

**Open.** Delimiters plus a system-prompt contract in `context/`, and a
decision on whether chunk text is also sanitized at ingest — which is a
re-ingest and a re-shipped corpus snapshot, not an edit.

### 2. Direct prompt injection via the question

**Entry point.** `POST /v1/questions`. The question is capped at 500
characters (`question_max_chars`) and reaches the decomposer and the query
rewriter verbatim, then rides along in the synthesis and critic windows as
`Question: …`.

**Blast radius.** The decomposer can be made to emit sub-questions nobody
asked for, and synthesis can be steered in tone or topic. What it cannot do is
reach past the answer path: a claim with no citation is rejected by
verification, retraction excises it from the prose, and the numeric route
still ends at the facts store. The 500-character cap is a real constraint —
there is no room for a long jailbreak scaffold, and no multi-turn state to
poison, since every run is independent.

**Worst realistic outcome.** An off-topic or prompt-reciting answer, produced
at the same cost as any other run and counted against the same governor and
quota. It is not a route to secrets — see story 5 — and it is the cheapest way
to *test* the system, which is why the adversarial set puts its direct-injection
cases through the real API rather than through a fake.

### 3. Output-channel attacks

**Entry point.** The answer is LLM-generated text, rendered by the SPA on a
page whose whole promise is "this was verified".

**Current state**, audited 2026-08-07 and re-checked 2026-08-11:
`dangerouslySetInnerHTML` appears nowhere in `frontend/src`;
`frontend/src/lib/answer.ts` is a hand-written Markdown subset — headings,
bullets, ordered items, `**bold**`, `[citation]` — that emits React text nodes
and never HTML, so there is no sink to inject into. **All eight anchors in the
SPA are internal hash routes** (`#/`, `#/ask`, `#/recorded/{id}`), citation
brackets render as text rather than links, and `sec.gov` URL construction
exists in neither `src/fie` nor `frontend/src`.

**Blast radius today: nil — and that is the finding.** It holds because the
renderer emits no external link at all, not because it filters one, and **a
property that is true by absence breaks silently.**

**Worst realistic outcome.** Not reachable now. It becomes reachable the first
time a later phase renders a source link from an accession without a
constructed path: an attacker-shaped citation then becomes a live `href`, i.e.
a phishing link carried by a page that says its contents are verified.

**Open.** Two things. An assertion that no anchor with an external target
survives rendering, so the property stops being a coincidence. And the response
headers on the static host: `curl -I https://edgaragent.pages.dev/` returns
`referrer-policy` and `x-content-type-options` and nothing else — no
`content-security-policy`, no `strict-transport-security`, no
`x-frame-options`, no `cross-origin-opener-policy` (measured 2026-08-07, and
the whole of the Lighthouse Best Practices 77 in the Phase 5 gate). Until those
ship the browser enforces none of this, and framing the ask page is possible.

### 5. Secrets and context leakage

**Entry points.** An answer, a Langfuse Cloud trace, the job store, a log
line, an error body.

**Current state.** Seven fields on `Settings` are `SecretStr` — the Anthropic,
OpenRouter, Voyage and OpenAI keys, the Langfuse secret key, `api_key` and
`turnstile_secret_key` — so a `repr` or an accidental interpolation prints
`**********`. **`secrets.enc.yaml` holds eight entries, and the difference is
not an omission**: the eighth is `ghcr_token`, which names no `Settings` field,
is read straight off disk by `deploy.sh` for one `docker login`, and never
enters the process (see the matrix above). Seven is therefore the number of
secrets the application can hold and the number `SecretStr` has to cover.
A secret reaches a model client as a constructor argument
(`llm/__init__.py:147`) and never through `context/`. **No prompt-assembly
function in `context/` takes a `Settings` at all**: the module's inputs are the
question, the evidence, the claims and the drafts. That is the real control.

**System-prompt recovery is possible and is not a secret leak.** The five
prompts live in `context/__init__.py` in a repo that is public by plan, so
extracting one costs an attacker nothing they could not read later anyway.

**The genuine data item is elsewhere, and it belongs on the record.**
`data/api.db` stores the visitor's question and their `client_ip` in the same
row (`api/store.py:34`, `:119`) with no retention policy, and
`deploy/backup.sh` archives that volume nightly on a 7-day rotation. Langfuse
Cloud additionally receives the question and the retrieved chunk text as prompt
content (ADR 0032) — a third party, off the box, under their retention rather
than ours. Neither is a leak, and the per-IP quota needs the address to work at
all. Both are stated here because this file elsewhere says there is no user
data, and a question plus an IP is the one place that is not quite true.

**Worst realistic outcome.** Someone holding the box, or a Langfuse Cloud
compromise, learns which address asked which question about which public
company.

**Open.** The `context/` property is unasserted. A test that a prompt-assembly
path cannot reach a `SecretStr` value is what keeps it true through a future
refactor.

### 4 and 6. Resource abuse, supply chain, infra — the Phase 4–5 pair

Both were built and documented during the Phase 4 and 5 deploys, and the table
below is their control-by-control view; it is not restated here. Two points
belong with the stories rather than in that table.

**4 — the entry point is the public submit path, and the blast radius includes
the neighbours.** Worst realistic outcome is a fully-spent $5.00 month
(`spend_cap_monthly_usd`, with a $0.35 daily sub-cap) and a fail2ban ban that
is **IP-wide across all five vhosts**, taking the four unrelated production
sites off for that address too. A refusal is `429`, and on submissions only;
`/healthz` keeps returning `200` when degraded, by design, because the
container healthcheck and `deploy.sh` both key off that 200.

**Five vhosts, six services — the two counts are different and were
conflated.** Measured on the box 2026-08-11: `sites-enabled` holds `fie`,
`neighbour-a`, `neighbour-b`, `rec.neighbour-c.example` and `neighbour-d`,
certbot renews exactly one certificate per name, and there is no
`default_server`. So the shared nginx connection pool is split **five** ways,
not six, and fie has **four** neighbours — the figure ADR 0035 and
`deploy/nginx/fie-limits.conf` give as six counts *docker services*, one of
which serves no vhost at all. The directory has not changed since the
fie symlink was added at 12:53 on 2026-08-06, so this is a miscount in the
original write-up and not drift since. Two operational notes follow from it:
`neighbour-d` returns **502** — its container is stopped — yet its vhost is
still enabled, so it still holds a share of that pool and is still taken down
for a banned address; and an nftables ban is IP-wide on the *box*, so it also
blocks SSH from that address, which is the part that matters if the address is
ever the operator's.

**6 — dependency risk has no inventory today. Open.** `.github/workflows/`
holds one file and no audit step, so nothing in this repo answers "which of our
dependencies has a known advisory". Adding one collides with the deliberate npm
hold policy in `frontend/.npmrc` (`ignore-scripts=true`, `before=2026-07-23`),
which means an audit will report advisories that are being waited out on
purpose — so whether the audit fails the build or only reports is a decision to
take before the step exists, not after it first goes red.

### Controls, and what each one leaves behind

| threat | control | residual |
|---|---|---|
| someone copies the SPA's API key | Turnstile token is per-visitor, per-challenge | none material — the key alone buys nothing |
| scripted flood of submissions | nginx `limit_req` (3r/m), fail2ban, queue cap | NAT and rotating proxies dilute per-IP limits |
| slow burn of many cheap requests | per-IP quota, $0.35/day sub-cap | a distributed caller reaches the daily cap; that is what the cap is for |
| our own accounting is wrong | fixed OpenRouter balance, auto-top-up **off** | the only layer that survives a bug in our code |
| the demo starves the four neighbours | `limit_conn $server_name 100`, `mem_limit`, `cpus`, `pids_limit` | a fail2ban ban is IP-wide and blocks that address from every service on the box, SSH included |
| container escape / privilege gain | non-root uid 10001, `cap_drop: ALL`, `no-new-privileges` | not a hardened sandbox; the box is the boundary |

Deliberately **not** in scope: authenticated multi-user access, audit trails
with reviewer identity (ADR 0034 names this), per-tenant isolation, offsite
backups (ADR 0031 / `deploy/backup.sh` say so plainly), and WAF-class request
inspection.

## If a key leaks

1. Rotate at the provider first — the leaked key stops working before
   anything else happens.
2. `sops secrets.enc.yaml`, update, commit, `deploy/deploy.sh`.
3. Check spend: `GET /v1/admin/spend` from the box, and the provider
   dashboard. The governor's ledger is in `data/api.db` and survives restarts.
4. If it was in a commit, rotating is the fix. Rewriting history is not —
   the value is already public the moment it is pushed.
