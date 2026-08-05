#!/usr/bin/env bash
# SOPS-encrypted YAML in git → one file per secret in ./secrets/, which
# docker compose mounts at /run/secrets (ADR 0029).
#
# One file per value rather than one env file, because that is the shape
# `pydantic-settings`' `secrets_dir` reads and the shape compose secrets
# deliver. The file names are the *field* names in `fie.config.Settings`,
# lowercased — that mapping is the whole contract.
#
# One exception, and it is deliberate: `ghcr_token` names no `Settings` field.
# It belongs to the deploy tooling (`docker login ghcr.io`), and it lives here
# so there is one encrypted store rather than one plus a stray file on the box.
# Compose mounts only the secrets it declares, so the application never sees it.
#
# The files stay on disk: compose bind-mounts them into the container, so
# deleting them after a deploy would break the next restart. They are
# root-owned 0400 and `secrets/` is gitignored.
set -euo pipefail

cd "$(dirname "$0")/.."

: "${SOPS_AGE_KEY_FILE:=/root/.config/sops/age/keys.txt}"
export SOPS_AGE_KEY_FILE

if [[ ! -f "$SOPS_AGE_KEY_FILE" ]]; then
    echo "!! No age key at $SOPS_AGE_KEY_FILE." >&2
    echo "   It is never in git. Restore it from the password manager." >&2
    exit 1
fi

mkdir -p secrets
chmod 700 secrets

# The uid `fie` runs as inside the image (see the `useradd` in the Dockerfile).
# **Mode 0400 alone is not enough**: compose bind-mounts these files into the
# container, which keeps their host ownership, and a root-owned 0400 file is
# unreadable to a non-root process. The container then dies at startup with
# `error getting value for field ... from source "SecretsSettingsSource"`,
# which names the field and not the cause. Found on the first real deploy,
# 2026-08-05. Root still reads these regardless of owner, so `monthly-eval.sh`
# is unaffected.
CONTAINER_UID="${FIE_CONTAINER_UID:-10001}"

# Every key in the decrypted YAML becomes ./secrets/<key>. Adding a secret is
# therefore a one-line change to secrets.enc.yaml plus a line in
# docker-compose.prod.yml — no change here.
sops --decrypt secrets.enc.yaml | while IFS= read -r line; do
    [[ "$line" =~ ^([a-z_]+):[[:space:]]*(.*)$ ]] || continue
    name="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    value="${value%\"}"
    value="${value#\"}"
    printf '%s' "$value" > "secrets/${name}"
    chown "${CONTAINER_UID}:${CONTAINER_UID}" "secrets/${name}"
    chmod 400 "secrets/${name}"
done

echo "Wrote $(ls secrets | wc -l | tr -d ' ') secret(s) to ./secrets/"
