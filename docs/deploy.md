# Deploying

Written from the real deploys — the private path on 2026-08-05, the public
path on 2026-08-06 — not from the plan. Every step here is one that actually
had to happen, and the six that are not obvious are marked.

## Prerequisites, once

**The image must exist.** CI publishes `ghcr.io/arnobgr/fie:latest` on a push
to `main` only. A deploy from a `dev` commit pulls whatever `main` last built.

**The age key, and its path differs by platform.** `sops` reads the OS config
directory: `~/Library/Application Support/sops/age/` on macOS,
`~/.config/sops/age/` on Linux. The Linux path on a Mac fails with
`identity did not match any of the recipients`, which reads like a wrong key
and is not one.

**A read-only deploy key on the VPS**, because the repository is private and
`ghcr_token` only carries `read:packages` — it can pull an image and cannot
clone code:

```bash
ssh-keygen -t ed25519 -N '' -f /root/.ssh/id_fie_deploy -C 'fie-deploy@vps'
# Add the .pub half at GitHub → repo → Settings → Deploy keys.
# Leave "Allow write access" UNCHECKED.
git clone --branch main git@github.com:Arnobgr/edgarAgent.git /opt/fie
```

**`sops` on the VPS**, matching the version on the Mac:

```bash
curl -sSfL -o /usr/local/bin/sops \
  https://github.com/getsops/sops/releases/download/v3.13.3/sops-v3.13.3.linux.amd64
chmod +x /usr/local/bin/sops
```

## Seeding the corpus

**Not obvious, and the deploy is useless without it.** The image carries code,
not data. Two things must travel from the machine that ingested:

| Item | Size | Read by |
|---|---|---|
| `data/ingest.db` | 13 MB | every numeric verification and every fact lookup |
| the Qdrant collection | 71 MB as a snapshot | every retrieval |

**Re-ingesting on the VPS is not an option, by design.** `run_ingest` always
calls the acquire stage, which raises without `EDGAR_IDENTITY` — and that
value is personal data deliberately kept off the box (ADR 0029). The corpus
therefore travels as data, not as a rebuild.

Dev and production both run Qdrant `v1.18.0`, so a snapshot restores exactly.
Only the collection named by `embedding_provider` is needed; the second
collection is a benchmark arm (ADR 0011).

```bash
# On the Mac
make qdrant-up
COL=fie_chunks__text-embedding-3-small
SNAP=$(curl -sX POST "http://localhost:6333/collections/$COL/snapshots" \
       | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["name"])')
curl -s -o /tmp/qdrant.snapshot "http://localhost:6333/collections/$COL/snapshots/$SNAP"
scp /tmp/qdrant.snapshot data/ingest.db vps:/srv/fie-import/
```

```bash
# On the VPS. /root is mode 700 and curlimages/curl runs as a non-root user,
# so the snapshot must be staged somewhere it can traverse.
chmod 755 /srv/fie-import && chmod 644 /srv/fie-import/qdrant.snapshot
docker run --rm --network fie_default -v /srv/fie-import:/snap:ro docker.io/curlimages/curl \
  -sS -X POST "http://fie-qdrant:6333/collections/$COL/snapshots/upload?priority=snapshot" \
  -F "snapshot=@/snap/qdrant.snapshot"

docker cp /srv/fie-import/ingest.db fie-fie-api-1:/app/data/ingest.db
# The container cannot chown even as root -- `cap_drop: ALL` removes CAP_CHOWN,
# which is the hardening working. Do it host-side, on the volume.
chown -R 10001:10001 "$(docker volume inspect fie_fie_api_data --format '{{.Mountpoint}}')"
```

Verify, and verify the *rows* rather than the file:

```bash
curl -s http://127.0.0.1:8412/healthz
# {"status":"ok","qdrant":true,"facts_store":true,...}
```

## The deploy itself

```bash
cd /opt/fie && git pull
./deploy/deploy.sh                       # public path, Turnstile enforced
FIE_TURNSTILE_REQUIRED=false ./deploy/deploy.sh   # private path, see below
```

`deploy.sh` decrypts secrets, logs in to GHCR, pulls, starts, waits for
`/healthz` to report `qdrant:true`, and submits one canned question — asserting
the status code that the path it is deploying is supposed to return.

### The private path

Turnstile validates the hostname its widget rendered on, so no widget can
exist before the Cloudflare Pages hostname does. Until then the service runs
with `FIE_TURNSTILE_REQUIRED=false` **and no nginx vhost installed** — nothing
but the tailnet reaches it. `deploy.sh` refuses the combination of Turnstile
off with a `fie` vhost present, so the interlock is between the two facts
rather than a warning in a document.

### The public path

**Turnstile goes on before the vhost, which is not the order the plan gave.**
`plans/phase-5-frontend.md` puts the vhost first, but certbot's HTTP-01
challenge needs it live on port 80 — so that order publishes the service for
the length of the issuance with whatever Turnstile setting happens to be
running. Flipping first costs nothing, because the box is still tailnet-only at
that moment, and it closes the window rather than relying on `deploy.sh`'s
interlock to catch a mistake.

**1. Pull, then check the key and not the file.** The checkout carries
`secrets.enc.yaml`, so a box that has not pulled decrypts the *old* one and
`sops` reports success either way. On 2026-08-06 that left a 5-byte placeholder
where the Turnstile secret belonged. Compare lengths, never contents:

```bash
cd /opt/fie && git pull --ff-only && ./deploy/decrypt-secrets.sh
wc -c < ./secrets/turnstile_secret_key   # 35. A placeholder is 5.
```

**2. Turnstile on**, with nothing but the tailnet able to reach it. No
override, so compose's default applies. The smoke test expects **403** here,
not 202 — see the sixth item below.

```bash
./deploy/deploy.sh
```

**3. nginx.** Three new files; no existing vhost is edited (ADR 0035).

```bash
install -m 644 deploy/nginx/fie-limits.conf /etc/nginx/conf.d/fie-limits.conf
install -m 644 deploy/nginx/fie-proxy.conf  /etc/nginx/snippets/fie-proxy.conf
install -m 644 deploy/nginx/fie.conf        /etc/nginx/sites-available/fie
ln -sfn /etc/nginx/sites-available/fie /etc/nginx/sites-enabled/fie
nginx -t && systemctl reload nginx
```

**4. certbot**, which rewrites `sites-available/fie` in place. It appends the
`listen 443` lines to the **bottom** of the existing block and adds a second
block for the redirect, so a check that slices the file from `listen 443`
onwards sees no `location` at all and looks alarming for no reason.

```bash
certbot --nginx -d api.example.com --non-interactive --agree-tos --redirect
```

sslip.io is not on the Public Suffix List, so Let's Encrypt counts every
sslip.io name as one registered domain and the 50-new-certs-per-week ceiling is
shared with strangers. Renewals are exempt. Retry; do not deploy to a deadline.

**5. fail2ban, verified against a real refusal.** The filter check is
meaningless until nginx has logged one, so provoke it — every request is
refused before the app does any work, so this costs nothing:

```bash
for i in $(seq 1 8); do curl -s -o /dev/null -w '%{http_code} ' \
  -X POST https://api.example.com/v1/questions \
  -H "X-API-Key: $(cat ./secrets/api_key)" \
  -H 'Content-Type: application/json' -d '{"question":"probe"}'; done
# 403 403 403 429 429 429 429 429   — the app, then nginx taking over

fail2ban-regex /var/log/nginx/fie.error.log /etc/fail2ban/filter.d/nginx-limit-req.conf
# Lines: 7 lines, 0 ignored, 5 matched, 2 missed

install -m 644 deploy/fail2ban/jail.d/fie-nginx.local /etc/fail2ban/jail.d/
fail2ban-client reload && fail2ban-client status fie-nginx-limit-req
```

That last line must report `File list: /var/log/nginx/fie.error.log`. If it
says `Journal matches:`, the jail is reading nothing — see below. Eight probes
is well under `maxretry = 20`, so the box does not ban itself.

**6. Prove the controls from outside.** Run the keyed cases *on the VPS against
its own public hostname*: the request still traverses nginx, and the API key
never leaves the box.

| request | expected |
|---|---|
| valid API key, no Turnstile token | 403 — this is ADR 0030 |
| no API key | 401 |
| forged Turnstile token | 403 |
| `GET /v1/admin/spend` | 403 |
| `GET /` | 404 |
| `GET /healthz`, and `http://` → `https://` | 200, 301 |

## Six things that bit on a real deploy

**Secret files need the container's uid.** Mode 0400 owned by root is
unreadable to a non-root process, and compose bind-mounts preserve host
ownership. The container dies with
`error getting value for field "openrouter_api_key" from source
"SecretsSettingsSource"`, which names the field and not the cause.
`decrypt-secrets.sh` now chowns to uid 10001.

**The container cannot chown its own volume.** `cap_drop: ALL` removes
CAP_CHOWN. Fix ownership from the host, at the volume mountpoint.

**A nested `ssh` inside a heredoc eats the rest of the script.** `ssh` reads
stdin, and `bash -s <<EOF` *is* stdin. Redirect the inner command from
`/dev/null`, or put the script in a file.

**`/healthz` used to go green over an empty facts store.** SQLite creates a
database on connect, so the app made a 0-byte `ingest.db` and `path.exists()`
said yes. It now checks for a row. If it ever reports `facts_store: true`
while numeric claims fail, suspect this check first.

**Debian's fail2ban default silently outranks `logpath`.**
`jail.d/defaults-debian.conf` sets `backend = systemd` under `[DEFAULT]`, and a
jail that does not override it starts, reports healthy, and reads the journal —
which never carries these lines, because nginx writes `limit_req` errors to the
per-vhost `error_log`. The jail could not have banned anyone, and its status
output said it was fine. `fie-nginx.local` now sets `backend = polling`. This
is the same failure as the `/healthz` one above: a green light over nothing.

**The smoke test failed every public deploy, after the deploy had succeeded.**
`deploy.sh` posted with only an API key and expected 202. Under Turnstile that
is a 403 *by design* — the key identifies and does not control (ADR 0030) — so
`curl -fsS` failed and `set -e` aborted at the last line of a working deploy.
It now expects 202 or 403 according to `FIE_TURNSTILE_REQUIRED`, which turns
the refusal into a permanent assertion instead of a one-off check.

## Cron, after the first successful deploy

```cron
0 4 * * *   /opt/fie/deploy/backup.sh       >> /var/log/fie-backup.log 2>&1
0 3 1 * *   /opt/fie/deploy/monthly-eval.sh >> /var/log/fie-eval.log 2>&1
```

Install them through a file, not through a pipe:

```bash
{ crontab -l 2>/dev/null || true; } | grep -v 'fie/deploy/' > /tmp/fie.cron
cat >> /tmp/fie.cron <<'CRON'
0 4 * * *   /opt/fie/deploy/backup.sh       >> /var/log/fie-backup.log 2>&1
0 3 1 * *   /opt/fie/deploy/monthly-eval.sh >> /var/log/fie-eval.log 2>&1
CRON
crontab /tmp/fie.cron && crontab -l && rm -f /tmp/fie.cron
```

**The obvious `( crontab -l | grep -v …; echo … ) | crontab -` installs an
empty crontab** when there is no crontab yet and the shell has `set -e`:
`grep` finds nothing, exits 1, kills the subshell before the `echo`, and
`crontab -` faithfully accepts the empty input. It reports no error. Always
`crontab -l` afterwards.

Then prove the backup rather than meeting it at 04:00 — `./deploy/backup.sh`
takes seconds and writes ~50 MB to `/var/backups/fie`.

Both are self-contained. `backup.sh` needs only docker; `monthly-eval.sh` runs
`eval-agent` in a one-off container off the deployed image and the service's
own data volume, so the host needs no Python toolchain and no second copy of
the corpus. See ADR 0036 for why it runs here rather than in CI.
