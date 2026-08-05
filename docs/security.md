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

`EDGAR_IDENTITY` is the most sensitive value in the project — it is personal
data, required by SEC's user-agent policy, and this repo's history has been
scrubbed of it once. It is needed only for **ingest**, so neither the serving
path nor the eval holds it.

### First-time setup

```bash
# Once, on the Mac. The private half goes in a password manager as well —
# two machines can both fail, and losing this key loses every secret in the
# file's history, not just today's values.
age-keygen -o ~/.config/sops/age/keys.txt

# Paste the printed public key into .sops.yaml, replacing the placeholder.
cp secrets.example.yaml secrets.enc.yaml
sops --encrypt --in-place secrets.enc.yaml
sops secrets.enc.yaml        # edit values; commit the encrypted file

# On the VPS: copy the same key to /root/.config/sops/age/keys.txt (0600),
# then deploy/deploy.sh decrypts into ./secrets/ automatically.
```

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

## Threat model, briefly

**The realistic threat is a drive-by script, not a targeted attacker.** This
is a portfolio demo over public SEC filings; there is no user data and nothing
to steal but money. The controls are sized for that.

| threat | control | residual |
|---|---|---|
| someone copies the SPA's API key | Turnstile token is per-visitor, per-challenge | none material — the key alone buys nothing |
| scripted flood of submissions | nginx `limit_req` (3r/m), fail2ban, queue cap | NAT and rotating proxies dilute per-IP limits |
| slow burn of many cheap requests | per-IP quota, $0.35/day sub-cap | a distributed caller reaches the daily cap; that is what the cap is for |
| our own accounting is wrong | fixed OpenRouter balance, auto-top-up **off** | the only layer that survives a bug in our code |
| the demo starves the five neighbours | `limit_conn $server_name 100`, `mem_limit`, `cpus`, `pids_limit` | a fail2ban ban is IP-wide and blocks that address from all six services |
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
