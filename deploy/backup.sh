#!/usr/bin/env bash
# Nightly tar of the named volumes to a local backup directory.
#
#   0 4 * * *  /opt/fie/deploy/backup.sh >> /var/log/fie-backup.log 2>&1
#
# Local, not offsite — offsite is out of scope for a demo, and saying so is
# more honest than a backup that exists only in a comment. What this protects
# against is a bad deploy or a `docker volume rm`, not the box dying. That
# scope is what decides which volumes are worth archiving, below.
#
# `checkpoints.db` is inside `fie_api_data` and was already 256 MB before
# this phase, so the retention below is deliberately short.
#
# **A tar of a live WAL-mode SQLite file is not a guaranteed-consistent
# snapshot** — the `.db` and its `.db-wal` are read at different instants, so
# a write landing between them yields an archive that needs recovery. Accepted
# rather than fixed: at 04:00 nothing is writing, `ingest.db` is read-only in
# production, and the file this could damage is `checkpoints.db`, which holds
# diagnostics rather than the corpus. `sqlite3 .backup` per file is the fix if
# that ever stops being true.
set -euo pipefail

BACKUP_DIR="${FIE_BACKUP_DIR:-/var/backups/fie}"
KEEP_DAYS="${FIE_BACKUP_KEEP_DAYS:-7}"
STAMP="$(date -u +%Y%m%d)"

mkdir -p "$BACKUP_DIR"

# **`fie_qdrant_storage` is deliberately not backed up.** It was, for one
# night, and it was 48 of the 50 MB. Measured 2026-08-05: the VPS collection
# and the developer's are identical — same 4,181 points, same dense+sparse
# config — because the VPS one *is* a snapshot of it (see "Seeding the corpus"
# in docs/deploy.md). Backing it up spends 96% of the archive on a second copy
# of something that already exists elsewhere, and this backup is local, so the
# only failures it survives are ones where that elsewhere is fine.
#
# What is irreplaceable is all in `fie_api_data`: `checkpoints.db`, which is
# how this project diagnoses a run without re-running it, and `api.db`, which
# holds the spend ledger the governor counts against. That is 2.2 MB a night.
for volume in fie_fie_api_data; do
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
