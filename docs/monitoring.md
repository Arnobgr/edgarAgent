# Monitoring and operations

What to watch, where it lives, and the checks that must be run **on the box**
rather than encoded in a file.

The box is the constraint behind most of this: 2 vCPU, ~1.8 GB available RAM,
11 GB free disk, five other production services. Nothing here has headroom to
be casual with.

## Health

```bash
curl -s https://api.example.com/healthz | jq
```

```json
{"status": "ok", "qdrant": true, "facts_store": true,
 "jobs_in_flight": 0, "langfuse_configured": true, "spend_ok": true}
```

`status` is `degraded` when Qdrant or the facts store is unreachable — those
are on the answer path. **Two fields sit outside that verdict, for opposite
reasons.** `langfuse_configured` is below it (ADR 0032): letting an external
SaaS turn this service red would page someone for another company's outage.
`spend_ok` is ahead of it — it goes false at 80% of the month while the
service is still answering, so folding it into `status` would report an outage
that has not happened and put a budget decision inside the uptime figure. The
endpoint is unauthenticated and reports no secret; `spend_ok` is a boolean and
not the figures for that reason.

**External probing is a human setup task**, and it is **two monitors on this
one URL**, because the two signals mean different things and deserve different
urgency:

| monitor | keyword, alert on **absence** | means |
|---|---|---|
| liveness | `"status":"ok"` | the service cannot answer — box, nginx, container, Qdrant, or an empty facts store |
| budget | `"spend_ok":true` | the month is ≥80% spent and will go dark before the 1st |

Keyword type, not plain HTTP: a status-line check is green over
`"status":"degraded"`, since this endpoint returns **200 either way**. That
is deliberate — the container healthcheck and `deploy.sh`'s health wait both
key off the 200 — so the assertion belongs in the prober, not the endpoint.

A prober on the same box tells you nothing when the box is the problem. The
keyword is matched against the serialized body, so if the response is ever
pretty-printed the check breaks **loud** (a false down, never a false up).

Neither monitor watches the static half. `https://edgaragent.pages.dev/` can
be broken while all of the above is green — that is how the 2026-08-07 key
rotation failed, and it needs a third, plain HTTP check.

## Spend

```bash
# From the box. Two things keep this off the internet: the container publishes
# to 127.0.0.1:8412 only, and /v1/admin/ is `deny all` in the public vhost.
# Reaching it therefore means SSH (over the tailnet), not a tailnet HTTP call.
ssh vps 'curl -s -H "X-API-Key: $FIE_API_KEY" \
  http://127.0.0.1:8412/v1/admin/spend' | jq
```

Returns `day_usd` / `month_usd` against their caps and an `exhausted` flag.
The ledger is in `data/api.db` and survives restarts (ADR 0030). `exhausted`
going true means submissions are returning 429 — expected once a month at
most, worth looking at if it happens on a Tuesday.

**This is the diagnosis, not the alarm.** By the time `exhausted` is true the
demo is already dark and the only choice left is which month to be sorry
about; the `spend_ok` monitor above is what fires while raising the cap is
still an option. Note that `exhausted` ORs the two caps, and the **day** cap
($0.35 against a $5.00 month) is reached on any busy day and reopens at
midnight UTC on its own — alerting on `exhausted` would page for the system
working. Only the month is worth waking up for.

The monthly eval's ~$0.69 is **not** counted here (ADR 0036). Size the
provider balance for the eval plus the demo.

## Logs

JSON on stdout, so `docker logs` is the interface. Every line inside a request
carries `request_id`; every line inside a job carries `job_id` and the
Langfuse `trace_id` (ADR 0028), so any one of the three finds the other two.

**The container is `fie-fie-api-1`, not `fie-api`.** Compose names containers
`<project>-<service>-<index>`, and the project is `fie`. The service name
`fie-api` is what the ADRs and the compose network use; the container name is
what `docker logs` and `docker stats` want.

```bash
docker logs --since 1h fie-fie-api-1 | jq -c 'select(.job_id == "…")'
docker logs --since 1h fie-fie-api-1 | jq -c 'select(.level == "error")'
```

Rotation for the container logs is compose's `json-file` driver at
`max-size: 10m`, `max-file: 3` — 30 MB ceiling, set because 11 GB free is not
a lot.

### Log rotation for the nginx vhost — verify, do not add a file

**No logrotate config ships for `fie.access.log` / `fie.error.log`, and that
is deliberate.** Debian's stock `/etc/logrotate.d/nginx` globs
`/var/log/nginx/*.log`, which already covers them. A second config naming the
same files makes logrotate **error** rather than rotate twice, so shipping one
would break rotation for all six vhosts.

Confirm it on the box the first time, rather than trusting this paragraph:

```bash
grep -r 'log/nginx' /etc/logrotate.d/          # expect the *.log glob
logrotate -d /etc/logrotate.d/nginx 2>&1 | grep -i 'fie\.'   # dry run: sees our files
```

If the stock glob is ever narrowed, that dry run is where it shows up.

## The box

Run these together — they are one picture, not five checks:

```bash
docker stats --no-stream fie-fie-api-1 fie-fie-qdrant-1   # inside mem_limit / cpus?
free -h                                        # headroom; swap in use?
dmesg -T | grep -i oom | tail                  # nothing, ever
df -h /                                        # 11 GB was the starting point
fail2ban-client status fie-nginx-limit-req     # bans, and they expire in 1h
grep -c 'limiting requests, excess:' /var/log/nginx/fie.error.log
```

**A quiet nginx error log means the limits are sized right. A noisy one is the
upgrade trigger firing** (ADR 0035): sustained abuse nginx and fail2ban do not
absorb means buying a domain or enabling Tailscale Funnel.

The memory and CPU numbers in `docker-compose.prod.yml` are **sized, not
measured under load**. `docker stats` during `scripts/burst-test.sh` is the
first real reading; treat the limits as provisional until then.

## Cron

| what | when | log | ADR |
|---|---|---|---|
| `deploy/backup.sh` | `0 4 * * *` | `/var/log/fie-backup.log` | tar of `fie_api_data`, 7-day retention — ~16 MB steady state |
| `deploy/monthly-eval.sh` | `0 3 1 * *` | `/var/log/fie-eval.log` | 0036 |

```cron
0 4 * * *   /opt/fie/deploy/backup.sh       >> /var/log/fie-backup.log 2>&1
0 3 1 * *   /opt/fie/deploy/monthly-eval.sh >> /var/log/fie-eval.log 2>&1
```

Backups are **local, not offsite** — that protects against a bad deploy or a
`docker volume rm`, not against the box dying. Offsite is out of scope for a
demo, and saying so is more honest than a backup that exists only in a
comment.

That scope is also why **only `fie_api_data` is archived**. Qdrant's volume was
48 of the first night's 50 MB and is a snapshot of the developer's collection,
verified identical; a local backup only survives failures where that original
is fine, so archiving it buys nothing. Restore it from `docs/deploy.md`
instead. What cannot be recreated — `checkpoints.db` and the spend ledger in
`api.db` — is 2.2 MB a night.

The monthly eval is drift detection against things we cannot cause: a model
re-routed to another provider or quantization, an alias updated underneath us,
a deprecation. **It does not write a baseline.** Its exit status is the signal;
wiring that to the same alert destination as the uptime probe is a deploy-time
task.

It runs as `fie-monthly-eval`, a one-off container off the same image and the
same data volume as the service (ADR 0036), so `docker stats fie-monthly-eval`
works while it runs and its report lands in `/opt/fie/eval/reports/`. Nothing
about it lives on the host.

## Traces

Langfuse Cloud. Every model call is tagged `fie-role:<role>`, so a trace shows
which role made it — `fact_lookup` and `query_rewrite` are separate roles
precisely so a trace distinguishes what research looked up from what verify
checked.

## When something is wrong

**Diagnose from checkpoints, not by re-running.** `data/checkpoints.db` holds
every run's full state; CLAUDE.md records this as the habit that made Phase 3
debuggable, and it applies unchanged here. `fie resume <run-id>` continues a
killed run from its checkpoint.

| symptom | first look |
|---|---|
| `/healthz` degraded, `qdrant:false` | `docker logs fie-fie-qdrant-1`; is the container up and inside its `mem_limit`? |
| `/healthz` degraded, `facts_store:false` | `ingest.db` is absent or empty. Re-seed it — see §"Seeding the corpus" in `docs/deploy.md`. |
| submissions 429 but the box is idle | `/v1/admin/spend` — the governor, not nginx |
| 429 with an HTML body | nginx `limit_req`, not the app. The body is how they are told apart |
| a job stuck `running` | the process died; restart marks it `interrupted` and `POST /v1/jobs/{id}/resume` replays from the checkpoint |
| a job stuck `awaiting_review` | it holds a semaphore slot until someone rules on it (ADR 0034) |
| answers wrong, service fine | this is an eval question, not an ops one — `./deploy/monthly-eval.sh` on the box, which is the same full run cron does |
