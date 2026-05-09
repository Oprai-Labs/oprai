#!/usr/bin/env bash
# Daily Postgres backup → local file → optional offsite upload (S3-compatible).
#
# Cron suggestion (3am UTC nightly, keep 30 days):
#
#     0 3 * * *  /opt/oprai/scripts/db/backup.sh >> /var/log/oprai-backup.log 2>&1
#
# Required env:
#   DATABASE_URL          — full pg connection string
#   BACKUP_DIR            — local destination dir (default: ./backups)
#   BACKUP_RETENTION_DAYS — how many days of dumps to keep locally (default: 30)
#
# Optional env (offsite upload via `aws` CLI):
#   BACKUP_S3_BUCKET      — bucket name (e.g. oprai-prod-backups)
#   BACKUP_S3_PREFIX      — key prefix (default: postgres)
#   AWS_PROFILE / AWS_*   — standard AWS auth env vars
#
# Exit codes:
#   0  ok
#   1  pg_dump failed
#   2  upload failed (dump kept locally)

set -euo pipefail

DB_URL="${DATABASE_URL:?DATABASE_URL is required}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

mkdir -p "$BACKUP_DIR"

ts="$(date -u +%Y%m%d_%H%M%S)"
out="${BACKUP_DIR%/}/oprai_${ts}.dump"

echo "[backup] dumping → ${out}"
if ! pg_dump --format=custom --compress=9 --no-owner --dbname="$DB_URL" --file="$out"; then
    echo "[backup] pg_dump failed" >&2
    rm -f "$out"
    exit 1
fi

size="$(du -h "$out" | awk '{print $1}')"
echo "[backup] dump complete: ${out} (${size})"

# ── Offsite upload (S3 / R2 / B2) ─────────────────────────────────────────────
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
    prefix="${BACKUP_S3_PREFIX:-postgres}"
    s3_uri="s3://${BACKUP_S3_BUCKET}/${prefix}/$(basename "$out")"
    echo "[backup] uploading → ${s3_uri}"
    # `--storage-class STANDARD_IA` for cheap infrequent access.
    if ! aws s3 cp --storage-class STANDARD_IA "$out" "$s3_uri"; then
        echo "[backup] S3 upload failed; local copy retained at ${out}" >&2
        exit 2
    fi
    echo "[backup] upload complete"
fi

# ── Retention sweep ──────────────────────────────────────────────────────────
# Find lets us delete dumps older than RETENTION_DAYS while leaving newer ones
# untouched. We do NOT delete remote copies — bucket lifecycle policy should
# handle that (defence in depth).
deleted="$(find "$BACKUP_DIR" -maxdepth 1 -name 'oprai_*.dump' \
    -type f -mtime "+${RETENTION_DAYS}" -print -delete | wc -l | awk '{print $1}')"
if [ "$deleted" -gt 0 ]; then
    echo "[backup] purged ${deleted} dump(s) older than ${RETENTION_DAYS} days"
fi

echo "[backup] done"
