#!/usr/bin/env bash
# The monthly full eval, on the VPS.
#
#   0 3 1 * *  /opt/fie/deploy/monthly-eval.sh >> /var/log/fie-eval.log 2>&1
#
# **This is what a PR gate structurally cannot be.** With nothing in the repo
# changed, it measures *external* drift: OpenRouter re-routing
# `z-ai/glm-5.2` to another provider or quantization, a `-lite` alias updated
# underneath us, a model deprecated, the embedding endpoint shifting. Monthly
# rather than weekly because it watches for drift we cannot cause (ADR 0036).
#
# It runs here rather than in GitHub Actions for two reasons: the corpus is
# on this box and `data/` is gitignored, and keeping it off CI means **GitHub
# Actions never receives a secret** — the "no secret in CI logs" exit item
# satisfied by construction rather than by discipline.
#
# Three things this script is deliberate about:
#   * it reads the **same** encrypted store the service does, but a wider slice
#     of it — including the Anthropic judge key that `fie-api` must never see.
#     One store, two consumers: compose mounts six of the seven secrets into
#     the container, and this reads all seven off the host. A second store
#     (an `/etc/fie/eval.env`) would be a second thing to rotate and a second
#     thing to forget;
#   * it runs `nice`d, sharing 2 vCPU with five production services for ~75
#     minutes (the work is I/O-bound on model APIs, but be a good neighbour);
#   * its ~$0.69/run is **not** governed by the public $5/month counter — that
#     governs the HTTP path, and this calls `answer_question()` directly. Size
#     the provider balance for the eval *plus* the demo.
set -euo pipefail

cd "$(dirname "$0")/.."

: "${FIE_SECRETS_DIR:=secrets}"
if [[ ! -d "$FIE_SECRETS_DIR" ]]; then
    echo "!! No $FIE_SECRETS_DIR/ — run deploy/decrypt-secrets.sh first." >&2
    exit 1
fi

# `secrets/<field>` → `FIELD` in the environment. The container reads the same
# files as files (pydantic-settings' `secrets_dir`); here there is no container,
# so the same field names go in as env vars and `Settings` picks them up by the
# other half of the same mechanism. `tr` rather than `${name^^}` — this has to
# survive being run under a shell older than the one that was tested.
for file in "$FIE_SECRETS_DIR"/*; do
    [[ -f "$file" ]] || continue
    name="$(basename "$file" | tr '[:lower:]' '[:upper:]')"
    export "$name=$(cat "$file")"
done

# The one the eval cannot run without, and the one that is absent from the
# service's mounts — so a store that works for `fie-api` proves nothing here.
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "!! No anthropic_api_key in $FIE_SECRETS_DIR/ — eval_judge is the" >&2
    echo "   measuring stick and does not fall back to a cheaper model." >&2
    exit 1
fi

echo "=== $(date -u +%FT%TZ) monthly eval starting ==="
# No --write-baseline: a drift run reports, it does not silently move the
# line it is measured against.
nice -n 10 uv run eval-agent
status=$?
echo "=== $(date -u +%FT%TZ) monthly eval finished (exit $status) ==="
exit "$status"
