#!/bin/sh
# Take one backup now: dump, check it can be read back, copy it off-site, prune old copies.
#   docker compose exec backup backup-now.sh [label]
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/common.sh"

label=${1:-nightly}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
name="tekly-${stamp}-${label}.dump"
tmp="${BACKUP_DIR}/.${name}.part"
mkdir -p "$BACKUP_DIR"

log "dumping ${PGDATABASE} -> ${name}"
start=$(date +%s)
pg_dump --format=custom --compress=6 --no-owner --no-privileges --file="$tmp"
# A dump that pg_restore can't list is not a backup.
pg_restore --list "$tmp" > /dev/null || die "dump is unreadable"

if [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
  age --encrypt --recipient "$BACKUP_AGE_RECIPIENT" --output "${tmp}.age" "$tmp"
  rm -f "$tmp"; tmp="${tmp}.age"; name="${name}.age"
fi
mv "$tmp" "${BACKUP_DIR}/${name}"
size=$(du -h "${BACKUP_DIR}/${name}" | cut -f1)
log "dump ok: ${name} (${size}, $(( $(date +%s) - start ))s)"

if offsite_enabled; then
  rclone copyto --s3-no-check-bucket "${BACKUP_DIR}/${name}" "${REMOTE}/${name}" \
    || die "off-site upload failed"
  # Confirm the copy is really there and the same size.
  remote_size=$(rclone size --json "${REMOTE}/${name}" | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')
  local_size=$(stat -c %s "${BACKUP_DIR}/${name}")
  [ "$remote_size" = "$local_size" ] || die "off-site copy size ${remote_size} != ${local_size}"
  log "off-site ok: ${REMOTE}/${name}"
  rclone delete --min-age "${BACKUP_KEEP_DAYS}d" --include "tekly-*.dump*" "$REMOTE" \
    || log "off-site prune failed (ignored)"
else
  log "WARNING: BACKUP_S3_BUCKET not set: this backup lives only on this server"
fi

# Keep the newest few locally (fast restores); the off-site store keeps the rest.
ls -1t "$BACKUP_DIR"/tekly-*.dump* 2>/dev/null | tail -n +$((BACKUP_KEEP_LOCAL + 1)) | xargs -r rm -f
echo "$name"
