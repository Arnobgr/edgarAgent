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
# **It runs in a one-off container from the deployed image, not on the host**,
# which the first deploy settled (2026-08-05): the host had no `uv`, no
# `data/`, and no route to Qdrant, which publishes no port. Installing a host
# toolchain would put a ~600 MB second copy of the image's dependencies on a
# disk with 7.9 GB free, and — the dangerous half — a second copy of the
# corpus, where re-seeding one leaves the eval silently measuring the other.
# Running the deployed image instead means the drift run measures the artifact
# that is actually serving. See the amendment in ADR 0036.
#
# Three things this script is deliberate about:
#   * it reads the **same** encrypted store the service does, but a wider slice
#     of it — including the Anthropic judge key that `fie-api` must never see.
#     One store, two consumers: compose mounts six of the seven secrets into
#     the long-running container, and this passes all of them to a container
#     that exists for one run. A second store (an `/etc/fie/eval.env`) would be
#     a second thing to rotate and a second thing to forget;
#   * it takes the same 1.0 CPU cap the service does, sharing 2 vCPU with five
#     production services for ~75 minutes (the work is I/O-bound on model APIs,
#     but be a good neighbour);
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

# `secrets/<field>` → `FIELD` in this shell, then `-e FIELD` with **no value**
# so docker reads each one out of this environment. The values therefore never
# appear in argv, where `ps` shows them to every user on the box. The serving
# container gets the same fields as files instead (`secrets_dir`); this one has
# no `/run/secrets`, so `get_settings()` falls through to the env — the other
# half of the same mechanism. `tr` rather than `${name^^}` — this has to
# survive being run under a shell older than the one that was tested.
env_args=()
for file in "$FIE_SECRETS_DIR"/*; do
    [[ -f "$file" ]] || continue
    name="$(basename "$file" | tr '[:lower:]' '[:upper:]')"
    export "$name=$(cat "$file")"
    env_args+=(--env "$name")
done

# The one the eval cannot run without, and the one that is absent from the
# service's mounts — so a store that works for `fie-api` proves nothing here.
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "!! No anthropic_api_key in $FIE_SECRETS_DIR/ — eval_judge is the" >&2
    echo "   measuring stick and does not fall back to a cheaper model." >&2
    exit 1
fi

# The report is the one thing worth surviving a `--rm` container, and
# `eval/reports/` is dockerignored, so the image has no such directory to
# inherit. uid 10001 is what the image runs as; a bind mount keeps host
# ownership (the lesson `decrypt-secrets.sh` records).
mkdir -p eval/reports
chown "${FIE_CONTAINER_UID:-10001}:${FIE_CONTAINER_UID:-10001}" eval/reports

echo "=== $(date -u +%FT%TZ) monthly eval starting ==="
# No --write-baseline: a drift run reports, it does not silently move the
# line it is measured against.
#
# The `data` volume is the service's own, deliberately: one corpus, one truth.
# The eval and the service therefore share `checkpoints.db`; SQLite's busy
# timeout plus the runner's retry cover the contention, and at 03:00 on the
# 1st there is nothing to contend with. The alternative — a private volume —
# trades that for a corpus copy that can go stale without saying so.
#
# `|| status=$?` rather than `status=$?`: under `set -e` a failing run exits
# the script before the line below can report it, and the exit status is the
# whole signal this job produces.
status=0
docker run --rm \
    --name fie-monthly-eval \
    --network fie_default \
    --volume fie_fie_api_data:/app/data \
    --volume "$PWD/eval/reports:/app/eval/reports" \
    --env QDRANT_URL=http://fie-qdrant:6333 \
    --env LANGFUSE_HOST=https://cloud.langfuse.com \
    "${env_args[@]}" \
    --cpus 1.0 \
    --memory 640m \
    --pids-limit 256 \
    --security-opt no-new-privileges:true \
    --cap-drop ALL \
    "${FIE_IMAGE:-ghcr.io/arnobgr/fie:latest}" \
    eval-agent || status=$?
echo "=== $(date -u +%FT%TZ) monthly eval finished (exit $status) ==="
exit "$status"
