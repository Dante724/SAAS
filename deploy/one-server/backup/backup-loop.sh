#!/bin/sh
# Runs forever in the backup container: one backup a day at BACKUP_HOUR_UTC (default 03:00 UTC).
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/common.sh"

hour=${BACKUP_HOUR_UTC:-3}
log "backup service up: daily at ${hour}:00 UTC, keep ${BACKUP_KEEP_DAYS} days off-site"
offsite_enabled || log "WARNING: BACKUP_S3_BUCKET not set: backups stay on this server only"

while :; do
  now=$(date -u +%s)
  next=$(( now - now % 86400 + hour * 3600 ))
  [ "$next" -gt "$now" ] || next=$(( next + 86400 ))
  log "next backup in $(( (next - now) / 60 )) min"
  sleep $(( next - now ))
  if "$HERE/backup-now.sh" nightly; then ping ""; else log "nightly backup FAILED"; ping "/fail"; fi
done
