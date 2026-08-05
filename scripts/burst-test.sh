#!/usr/bin/env bash
# Prove the load controls against the deployed host.
#
#   ./scripts/burst-test.sh [https://fie.159-69-91-118.sslip.io]
#
# **These limits are not pytest-testable — they live above the app.** nginx
# and fail2ban never see a request in `make test`, so the only honest check is
# a burst at the real host. Keep this a script, not a CI job: it needs the
# live box.
#
# Without a CDN in front, this is the gate that replaces one. It checks three
# things at once:
#   1. a burst of submissions is refused by **nginx**, not by the app — the
#      two are told apart by the response body, since both return 429;
#   2. a burst of *polls* is not refused, because the frontend polls and the
#      two paths have opposite profiles;
#   3. **the other five projects on this box keep serving throughout** — that
#      is what `limit_conn $server_name` exists for, and it is a hard gate.
set -euo pipefail

HOST="${1:-https://fie.159-69-91-118.sslip.io}"
NEIGHBOUR="${FIE_NEIGHBOUR_URL:-https://rec.neighbour-c.example}"
API_KEY="${FIE_API_KEY:?set FIE_API_KEY}"

codes() { sort | uniq -c | sort -rn | sed 's/^/    /'; }

echo "==> 20 submissions at $HOST/v1/questions (nginx allows 3r/m, burst 2)"
for _ in $(seq 1 20); do
    curl -s -o /tmp/fie-burst-body -w '%{http_code}\n' \
        -X POST "$HOST/v1/questions" \
        -H "X-API-Key: $API_KEY" \
        -H 'Content-Type: application/json' \
        -d '{"question":"What was Tesla revenue in FY2025?"}'
done | codes
echo "    last body (nginx 429 is HTML; the app's is JSON with a detail field):"
head -c 200 /tmp/fie-burst-body; echo

echo "==> 40 polls at $HOST/healthz (should stay 200 — polling is the cheap path)"
for _ in $(seq 1 40); do
    curl -s -o /dev/null -w '%{http_code}\n' "$HOST/healthz"
done | codes

echo "==> The neighbour is still serving ($NEIGHBOUR)"
curl -s -o /dev/null -w '    %{http_code} in %{time_total}s\n' "$NEIGHBOUR"

cat <<'EOF'

Now check on the box, in this order:
    grep -c 'limiting requests, excess:' /var/log/nginx/fie.error.log
    fail2ban-client status fie-nginx-limit-req      # banned, then expiring in 1h
    docker stats --no-stream fie-fie-api-1 fie-fie-qdrant-1   # inside mem_limit / cpus
    free -h && dmesg -T | grep -i 'oom' | tail      # headroom, no OOM events
A quiet error log means the limits are sized right. A noisy one is the
upgrade trigger firing.
EOF
