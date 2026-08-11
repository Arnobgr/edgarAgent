# ADR 0035 — Public exposure through the nginx already on the box

**Date:** 2026-08-05 · **Phase:** 4 · **Status:** accepted ·
**Measured:** 2026-08-03

## Context

No domain is owned. `neighbour-c.example` uses third-party nameservers and the other
four projects run off `sslip.io` on the raw IP, so **no Cloudflare zone
exists** — which rules out Cloudflare Tunnel, because a named tunnel hostname
requires one. The `cloudflared` container went with it.

What is already on the box, measured:

- **nginx 1.24.0**, `worker_processes auto` on 2 cores, `worker_connections
  768` → **~1536 connections shared by all six vhosts**. That pool, not RAM,
  is what a flood exhausts first.
- `include /etc/nginx/conf.d/*.conf` in the `http` block, and **`conf.d` is
  empty**.
- **fail2ban** active with only an `sshd` jail.
- **certbot** renewing four certs, three of them on `sslip.io`.

> **Correction (2026-08-11): it is five vhosts, not six, and the pool is
> split five ways.** Re-measured on the box: `sites-enabled` holds `fie`,
> `neighbour-a`, `neighbour-b`, `rec.neighbour-c.example` and
> `neighbour-d`, with no `default_server` and one certificate per name.
> The bullet above is self-contradicting on this — *four* certs before fie
> means four vhosts before fie. **Six** is the count of docker services on the
> box, one of which serves no vhost, and the two numbers were conflated.
> Nothing sized here changes: `limit_conn $server_name 100` bounds
> fie's share of the pool whatever the divisor is, and a larger share per
> neighbour only makes the sizing safer. What changes is the blast-radius
> sentence — fie has **four** neighbours, and "the other five projects" below
> is right only when read as services.

## Decision

**The public API is `https://api.example.com`, served by the nginx
already on the box.** It matches the pattern `neighbour-a` and
`neighbour-d` already use, with tooling — nginx, certbot, fail2ban — the operator
already operates four times here.

**It is additive, not a shared-config edit.** One new file in `conf.d` for the
rate-limit zones and one new file in `sites-enabled` for the vhost. **No
existing nginx file is modified**, and `nginx -t` gates a graceful reload.

**Two request zones, because the paths have opposite profiles.**
`POST /v1/questions` is rare and expensive (`rate=3r/m`, `burst=2 nodelay`);
`GET /v1/jobs/*` is frequent and cheap (`rate=60r/m`, generous burst) because
the frontend polls (ADR 0026). A single zone would have to be loose enough for
the poll path, which makes it useless for submit.

Plus `limit_conn` 4 per IP, `limit_req_status`/`limit_conn_status` set to
**429** so nginx and the API agree on the contract, `client_max_body_size 8k`,
and 10–15 s client timeouts — connections are the scarce resource at ~1536
shared, and slow clients park them for free otherwise.

**`limit_conn $server_name 100` is the line that protects the other five
projects.** It bounds fie's share of that shared pool so a flood on the demo
cannot starve `rec.neighbour-c.example`. The zones cost ~21 MB of shared memory
(~160k IPs).

**`X-Real-IP` is set from `$remote_addr`** in the proxy snippet. It is the
only trustworthy client address the app sees, because
`proxy_add_x_forwarded_for` *appends* to a caller-controlled header
(ADR 0030).

**`/v1/admin/` is `deny all`** at the vhost. It is behind the API key anyway,
but an endpoint reporting spend does not need to be internet-reachable just
because it shares a prefix. Two independent barriers now cover it — the vhost
denies the path, and the container publishes to `127.0.0.1:8412` — so
reaching it means SSH to the box, which is itself tailnet-only.

**The fail2ban jail uses the stock Debian `nginx-limit-req` filter** — no
custom filter to maintain — reading `limiting requests, excess:` from
`fie.error.log`, and moves a persistent abuser from nginx (userspace, cost per
request) to nftables (kernel, cost per packet).

**Three accepted costs, recorded so they are decisions and not oversights:**

1. **The hostname embeds the machine's IP.** If the box is rebuilt, migrated
   or reassigned an address, every published link dies — README, CV, anywhere
   it was pasted. This is the strongest argument for buying a domain later.
2. **First issuance competes globally.** `sslip.io` is not on the Public
   Suffix List, so Let's Encrypt counts it as one registered domain and the
   50-new-certs-per-week limit is shared with every sslip.io user. *Renewals
   are exempt*, so the three existing certs are safe, but issuing `fie.…` can
   fail for reasons unrelated to us, with no remedy but waiting. **Do not
   schedule the deploy against a hard deadline.**
3. **Nothing sits in front of the box.** Traffic lands directly on the shared
   nginx and the 2 vCPU. This is why ADR 0030's layers 2 and 4 are not
   optional, and why the exit gate proves them against the running service.

**A fail2ban ban is IP-wide**, so it also blocks that address from the other
five services. That is the accepted price of one shared IP and port; a short
`bantime` (1 h) and a high threshold (20 in 600 s) are the mitigation.

## Consequences

`deploy/nginx/` holds the three files and `deploy/fail2ban/` the jail.
`scripts/burst-test.sh` is the proof, and it checks the thing that actually
matters: a burst of submissions is refused **by nginx** (told apart from the
app's 429 by the response body), a burst of polls is not, and **the neighbour
keeps serving throughout**.

**Rejected alternatives.** Tailscale Funnel: needs no domain, no open port and
no `cloudflared`, and `tailscaled` 1.98.10 already runs here with MagicDNS on
— it remains the cheapest escape hatch, and it was not chosen because a
`*.ts.net` hostname is no more permanent than the IP one and it puts the
demo's availability under Tailscale's Funnel throttling policy. Caddy on an
open 443: more surface, duplicates a working nginx. Moving `neighbour-c.example`'s
nameservers to Cloudflare: repoints DNS for a live production service in order
to serve a demo.

**Upgrade trigger:** sustained volumetric abuse that nginx + fail2ban do not
absorb, or a change of IP → buy a domain (Cloudflare Tunnel) or enable
Tailscale Funnel, which needs only the tailnet HTTPS-certificates toggle and
the `funnel` node attribute in the ACL.

## Honest limits

- Every threshold here is reasoned from the connection-pool measurement, not
  from observed abuse. `burst-test.sh` has not run against a deployed host
  yet; until it does, these are sized and not validated.
- `limit_req` keys on `$binary_remote_addr`, so NAT shares a bucket and a
  rotating proxy defeats it. Same limit as the per-IP quota, same answer:
  provider-side caps bound the determined case.
- The vhost ships on port 80 because certbot rewrites it in place on the box.
  What is committed here is therefore not byte-identical to what runs.
- **No logrotate file ships for the new logs.** Debian's stock
  `/etc/logrotate.d/nginx` globs `/var/log/nginx/*.log`, which already covers
  `fie.access.log`, and a second config for the same file makes logrotate
  error rather than rotate twice. It is a deploy-time *verification*, recorded
  in `docs/monitoring.md`, not a file.
