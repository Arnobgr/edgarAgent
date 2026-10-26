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

# The one combination that must never ship: Turnstile off *and* the public
# vhost installed. Turnstile is what makes a copied API key worthless
# (ADR 0030), and the key is copyable by design — the SPA ships it. Turning it
# off is legitimate only while nothing but the tailnet can reach the service,
# so the interlock is between the two facts rather than a warning in a doc.
if [[ "${FIE_TURNSTILE_REQUIRED:-true}" == "false" ]] \
   && compgen -G "/etc/nginx/sites-enabled/fie*" > /dev/null; then
    echo "!! Refusing to deploy: TURNSTILE_REQUIRED=false with the public" >&2
    echo "   vhost installed. Either remove /etc/nginx/sites-enabled/fie*" >&2
    echo "   or configure Turnstile and drop the override." >&2
    exit 1
fi

echo "==> Decrypting secrets"
./deploy/decrypt-secrets.sh

# The package is private, so an anonymous pull gets a 403. The token is
# scoped to `read:packages` and nothing else — it cannot write a package and
# it cannot reach a machine, which is what makes it a different animal from
# the tailnet credential ADR 0037 refused. **The package stays private although
# the repository is public**: images built before the history was rewritten
# for publication carry files the public history no longer has, because
# `.dockerignore` excluded `docs/` and `plans/` but not the rest. Making the
# package public would publish every one of them.
if [[ -f ./secrets/ghcr_token ]]; then
    echo "==> Logging in to ghcr.io"
    docker login ghcr.io -u "${GHCR_USER:-Arnobgr}" --password-stdin < ./secrets/ghcr_token
fi

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

# Which answer proves a good deploy depends on the path this one is on, and
# the two are exact opposites. With Turnstile on, a submission carrying only
# an API key must be *refused* — that is ADR 0030's entire claim — so the 202
# that proves the private path is the public path's failure signature. Asserted
# the old way this step failed every public deploy after it had already
# succeeded. The refusal is the better assertion regardless: d5e9f01 found this
# test returning `source: cached`, which exercised nothing.
echo "==> Smoke test"
if [[ "${FIE_TURNSTILE_REQUIRED:-true}" == "false" ]]; then expect=202; else expect=403; fi
body="$(mktemp)"
code="$(curl -s -o "$body" -w '%{http_code}' \
    -X POST "http://127.0.0.1:${FIE_API_PORT:-8412}/v1/questions" \
    -H "X-API-Key: $(cat ./secrets/api_key)" \
    -H 'Content-Type: application/json' \
    -d '{"question":"What was Tesla'"'"'s total revenue in fiscal year 2025?"}')"
cat "$body"; echo; rm -f "$body"
[[ "$code" == "$expect" ]] || {
    echo "!! Smoke test: expected HTTP $expect, got $code" >&2
    exit 1
}

# Every deploy used to leave the previous image behind, ~900 MB each, until
# superseded images held 7.5 GB of a 38 GB disk. Pruning only after the smoke
# test passes means a failed deploy keeps the old image for a local rollback;
# after a good one, rollback pulls a `sha-*` tag back from GHCR. **The label
# filter is load-bearing**: a bare `prune -a` would also delete the
# neighbours' images that have no container running at that moment.
echo "==> Pruning superseded fie images"
docker image prune -af \
    --filter "label=org.opencontainers.image.source=https://github.com/Arnobgr/edgarAgent"

if [[ "$expect" == 202 ]]; then
    echo "==> Done. Poll the job id above; watch the trace in Langfuse Cloud."
else
    echo "==> Done. Turnstile refused a keyed submission, which is the point."
fi
