#!/bin/sh
# Shared settings for the backup scripts. Sourced, not run.
set -eu

BACKUP_DIR=${BACKUP_DIR:-/backups}
BACKUP_KEEP_LOCAL=${BACKUP_KEEP_LOCAL:-3}      # newest dumps kept on this server
BACKUP_KEEP_DAYS=${BACKUP_KEEP_DAYS:-30}       # off-site copies older than this are deleted
BACKUP_PREFIX=${BACKUP_PREFIX:-db}             # folder inside the bucket

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
die() { log "ERROR: $*" >&2; exit 1; }

# Off-site store, configured from plain BACKUP_S3_* settings (rclone reads RCLONE_CONFIG_*).
offsite_enabled() { [ -n "${BACKUP_S3_BUCKET:-}" ]; }
if offsite_enabled; then
  : "${BACKUP_S3_ENDPOINT:?BACKUP_S3_ENDPOINT is required with BACKUP_S3_BUCKET}"
  : "${BACKUP_S3_ACCESS_KEY_ID:?BACKUP_S3_ACCESS_KEY_ID is required with BACKUP_S3_BUCKET}"
  : "${BACKUP_S3_SECRET_ACCESS_KEY:?BACKUP_S3_SECRET_ACCESS_KEY is required with BACKUP_S3_BUCKET}"
  export RCLONE_CONFIG_OFFSITE_TYPE=s3
  export RCLONE_CONFIG_OFFSITE_PROVIDER=${BACKUP_S3_PROVIDER:-Cloudflare}
  export RCLONE_CONFIG_OFFSITE_ENDPOINT=$BACKUP_S3_ENDPOINT
  export RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID=$BACKUP_S3_ACCESS_KEY_ID
  export RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY=$BACKUP_S3_SECRET_ACCESS_KEY
  export RCLONE_CONFIG_OFFSITE_REGION=${BACKUP_S3_REGION:-auto}
  export RCLONE_CONFIG_OFFSITE_NO_CHECK_BUCKET=true   # the token may only write objects, not create buckets
  REMOTE="offsite:${BACKUP_S3_BUCKET}/${BACKUP_PREFIX}"
fi

# Heartbeat (healthchecks.io, Better Stack, UptimeRobot heartbeat…): pinged after each good
# backup, and at <url>/fail after a failed one, so silence or a failure pages you.
ping() {
  [ -n "${BACKUP_HEARTBEAT_URL:-}" ] || return 0
  wget -q -T 10 -O /dev/null "${BACKUP_HEARTBEAT_URL}${1:-}" || log "heartbeat ping failed (ignored)"
}
