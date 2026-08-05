#!/usr/bin/env bash
# Nightly tar of the named volumes to a local backup directory.
#
#   0 4 * * *  /opt/fie/deploy/backup.sh >> /var/log/fie-backup.log 2>&1
#
# Local, not offsite — offsite is out of scope for a demo, and saying so is
# more honest than a backup that exists only in a comment. What this protects
# against is a bad deploy or a `docker volume rm`, not the box dying.
#
# `checkpoints.db` is inside `fie_api_data` and was already 256 MB before
# this phase, so the retention below is deliberately short.
set -euo pipefail

BACKUP_DIR="${FIE_BACKUP_DIR:-/var/backups/fie}"
KEEP_DAYS="${FIE_BACKUP_KEEP_DAYS:-7}"
STAMP="$(date -u +%Y%m%d)"

mkdir -p "$BACKUP_DIR"

for volume in fie_fie_api_data fie_fie_qdrant_storage; do
    # A throwaway container is the only way to read a named volume without
    # reaching into /var/lib/docker by hand.
    docker run --rm \
        -v "${volume}:/data:ro" \
        -v "${BACKUP_DIR}:/backup" \
        docker.io/library/debian:bookworm-slim \
        tar czf "/backup/${volume}-${STAMP}.tar.gz" -C /data .
done

find "$BACKUP_DIR" -name '*.tar.gz' -mtime "+${KEEP_DAYS}" -delete
echo "$(date -u +%FT%TZ) backup ok: $(ls -1 "$BACKUP_DIR" | wc -l | tr -d ' ') archive(s)"
