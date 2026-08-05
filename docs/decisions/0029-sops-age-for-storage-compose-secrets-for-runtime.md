# ADR 0029 — SOPS + age for storage, compose secrets for runtime

**Date:** 2026-08-05 · **Phase:** 4 · **Status:** accepted ·
**Confirmed by the operator:** 2026-08-03 (they do not use 1Password, so `op run`
injection is out)

## Context

The blueprint §7 asks for "a real secrets manager, not env files in the repo".
Two separate questions hide in that sentence, and answering only one is the
common failure: **where a secret is stored** and **how it reaches the
process**. A vault that ends by exporting `FOO=bar` has solved the first and
ignored the second.

The most sensitive value in this project is not an API key. It is
`EDGAR_IDENTITY` — a real name and a real email, required by SEC's user-agent
policy, and already scrubbed from this repo's history once.

## Decision

**Storage: SOPS + age.** An encrypted `secrets.enc.yaml` is committed. The age
private key lives on the VPS and on the operator's Mac, never in git. This buys
version history for secrets, makes rotation a visible commit, and adds no SaaS
dependency and no runtime dependency that can take a deploy down.

**Runtime: compose secrets, not environment variables.** Env leaks into
`docker inspect`, crash dumps, child processes, and any library that dumps
`environ`. `pydantic-settings` reads a `secrets_dir` natively, so `config.py`
took a four-line change rather than a rewrite:

```python
if COMPOSE_SECRETS_DIR.is_dir():
    return Settings(_secrets_dir=COMPOSE_SECRETS_DIR)
return Settings()
```

Guarded on `is_dir()` because on a dev machine `/run/secrets` does not exist
and pydantic-settings warns on every run otherwise.

**The contract is file-name = lowercased `Settings` field name.**
`deploy/decrypt-secrets.sh` writes one file per YAML key into `secrets/`,
compose bind-mounts them at `/run/secrets`, pydantic-settings looks for files
named after its own fields. Three components agree with no shared code. The
decrypted files **stay on disk** at mode 0400 — compose bind-mounts them, so
deleting them after a deploy breaks the next restart.

**Which secret exists in which context** — measured against `config.py`, where
exactly one role is on Anthropic (`eval_judge`) and every other role is
OpenRouter:

| | `fie-api` (serving) | VPS eval cron | GitHub Actions |
|---|---|---|---|
| `OPENROUTER_API_KEY` | ✓ | ✓ | — |
| `OPENAI_API_KEY` (query-time embeddings) | ✓ | ✓ | — |
| `LANGFUSE_*` (Cloud) | ✓ | ✓ | — |
| `API_KEY`, `TURNSTILE_SECRET_KEY` | ✓ | — | — |
| `ANTHROPIC_API_KEY` | **✗** | ✓ (judge only) | — |
| `EDGAR_IDENTITY` | **✗** | **✗** | — |

Two things this table is load-bearing about. `EDGAR_IDENTITY` is needed only
to reach SEC during **ingest**, so both the serving path and the eval run
without the most sensitive value present. And the serving container never
holds the judge key, because `eval_judge` is the measuring stick, not a
product dependency.

**One encrypted store, two consumers with different subsets.** Compose mounts
six of the seven secrets into the container; `deploy/monthly-eval.sh` reads
all seven off the host and exports them as env vars, because there is no
container there and `Settings` picks up either form. A second store — an
`/etc/fie/eval.env` — was written first and then removed: it is a second thing
to rotate and a second thing to forget.

**Do not move `eval_judge` off Anthropic to drop a key.** It saves nothing on
the serving path (see the table), ADR 0014 makes an Anthropic model via
OpenRouter raise by design, and CLAUDE.md records the judge flipping on ~1 in
27 byte-identical spans — a different judge shifts the whole scored
population, so `eval/baselines.json` would stop being comparable and the
2026-08-01 baseline would have to be re-earned.

**GitHub Actions holds no secret at all** (ADR 0037). Note that GH log masking
would not be a control anyway: it **fails open on transformed values**, so a
base64'd secret prints in the clear.

## Consequences

`.gitleaks.toml` runs over full history in CI. Two findings shaped it, both
measured rather than anticipated:

- A **custom `edgar-identity` rule**, because no default rule looks for
  personal data. It matches the *variable*, never the address — writing the
  address into a scanner config to detect the address would be the leak.
- `uv.lock`'s wheel digests are high-entropy and one matches the
  `square-access-token` rule, so the scan failed on every push until the
  digest line was allowlisted.

Both allowlist entries are **line shapes, not paths**. `secrets.enc.yaml` is
deliberately not path-allowlisted: that would silence the one failure that
matters, committing it before encrypting. Matching the `ENC[AES256_GCM,data:`
envelope means an encrypted value passes and a plaintext one in the same file
still trips the default rules. Verified: full history is clean, the custom
rule fires on a planted identity, and the `.env.example` placeholder does not.

**Rejected alternatives.** A plain `.env` on the VPS: no history, no recovery
if the box dies, and it demonstrates nothing. Infisical / Vault / Doppler:
overkill for one VPS and seven secrets, and each adds a runtime dependency
that can break a deploy.

## Honest limits

- **Losing the age private key loses everything**, including the history. It
  must be in a password manager, not only on two machines that can both fail.
- `.sops.yaml` ships with a placeholder recipient that SOPS will refuse. That
  is the intended failure: a syntactically valid stand-in would encrypt to a
  key nobody holds and surface at *decrypt* time on the VPS, mid-deploy.
- The image-layer third of the "no secret in history / image / CI log" gate is
  **not** checked in CI. The Dockerfile takes no build args and mounts no
  secrets, so the only route in is the build context, which `.dockerignore`
  excludes. It is a documented release check in `docs/security.md`
  (`docker history --no-trunc`) rather than a job, because pulling a 1.07 GB
  image on every push to assert a structural impossibility is not worth the
  minute.
- A typo in a key name does not error. It produces a setting left at its
  default — which for `api_key` means the service refuses to boot and for
  `turnstile_secret_key` means it refuses every submission. Loud in both
  cases, but by luck of which defaults are unsafe, not by design.
