#!/usr/bin/env bash
# Deploy fie on the VPS. Run as root from the repo checkout:
#
#   ./deploy/deploy.sh
#
# **CI holds no credential for this.** GitHub Actions builds the image and
# pushes it to GHCR; a human runs this. The rejected alternative was the
# Tailscale GitHub Action, and the reason is a difference in failure class:
# an API key in CI is bounded by the provider-side spend caps and recoverable
# by rotation, but a tailnet credential grants *network reachability* to
# the operator's Mac and to a box running five production services (ADR 0037).
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.prod.yml --project-name fie"
HEALTH_URL="http://127.0.0.1:${FIE_API_PORT:-8412}/healthz"

echo "==> Decrypting secrets"
./deploy/decrypt-secrets.sh

echo "==> Pulling the image"
$COMPOSE pull

echo "==> Starting"
$COMPOSE up -d

echo "==> Waiting for /healthz"
for _ in $(seq 1 30); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done
curl -fsS "$HEALTH_URL" | tee /dev/stderr | grep -q '"qdrant":true' || {
    echo "!! Qdrant is not ready — the service is up but cannot answer anything." >&2
    exit 1
}

echo "==> Smoke test (one canned question)"
API_KEY="$(cat ./secrets/api_key)"
curl -fsS -X POST "http://127.0.0.1:${FIE_API_PORT:-8412}/v1/questions" \
    -H "X-API-Key: ${API_KEY}" \
    -H 'Content-Type: application/json' \
    -d '{"question":"What was Tesla'"'"'s total revenue in fiscal year 2025?"}'
echo

echo "==> Done. Poll the job id above; watch the trace in Langfuse Cloud."
