#!/bin/sh
# Restore a backup into a database.
#
#   restore.sh <backup-name|latest> --into <database>     restore into a separate database (safe)
#   restore.sh <backup-name|latest> --replace-live         make the backup the live database
#
# --replace-live never deletes anything: the backup is restored into a new database first, then
# the live database is renamed to <name>_before_<time> and the restored one takes its name. The
# app must be stopped (./restore-live.sh on the server does stop -> restore -> start).
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/common.sh"

usage() { echo "usage: restore.sh <backup-name|latest> (--into <database> | --replace-live)" >&2; exit 2; }
[ $# -ge 2 ] || usage
want=$1; shift
target=""; replace_live=0
case "$1" in
  --into) [ $# -eq 2 ] || usage; target=$2 ;;
  --replace-live) replace_live=1; target="${PGDATABASE}_restoring" ;;
  *) usage ;;
esac
[ "$target" != "$PGDATABASE" ] || die "use --replace-live to restore over the live database"
echo "$target" | grep -Eq '^[a-z_][a-z0-9_]*$' || die "database name must be lower-case letters, digits, _"

# 1. Find the file: on this server first, else off-site.
if [ "$want" = latest ]; then
  want=$(ls -1t "$BACKUP_DIR"/tekly-*.dump* 2>/dev/null | head -1 | xargs -r basename || true)
  if offsite_enabled; then
    remote_latest=$(rclone lsf --include "tekly-*.dump*" "$REMOTE" | sort | tail -1)
    # Names start with a UTC timestamp, so the larger name is the newer backup.
    want=$(printf '%s\n%s\n' "$want" "$remote_latest" | grep -v '^$' | sort | tail -1)
  fi
  [ -n "$want" ] || die "no backups found"
fi
file="${BACKUP_DIR}/${want}"
if [ ! -f "$file" ]; then
  offsite_enabled || die "${want} is not on this server and no off-site store is set"
  log "downloading ${want} from off-site"
  rclone copyto "${REMOTE}/${want}" "$file" || die "download failed"
fi

# 2. Decrypt if needed.
dump=$file
case "$file" in
  *.age)
    [ -n "${BACKUP_AGE_IDENTITY_FILE:-}" ] && [ -f "$BACKUP_AGE_IDENTITY_FILE" ] \
      || die "${want} is encrypted: mount the private key and set BACKUP_AGE_IDENTITY_FILE"
    dump="${BACKUP_DIR}/.restore-$$.dump"
    trap 'rm -f "$dump"' EXIT
    age --decrypt --identity "$BACKUP_AGE_IDENTITY_FILE" --output "$dump" "$file" ;;
esac

# 3. Restore into a fresh database.
start=$(date +%s)
log "restoring ${want} into ${target}"
psql -v ON_ERROR_STOP=1 -d postgres -qc "DROP DATABASE IF EXISTS \"${target}\"" \
  -c "CREATE DATABASE \"${target}\""
pg_restore --no-owner --no-privileges --exit-on-error --jobs=2 --dbname="$target" "$dump"
log "restored into ${target} in $(( $(date +%s) - start ))s"

# 4. Swap it in, keeping the old live database.
if [ "$replace_live" = 1 ]; then
  old="${PGDATABASE}_before_$(date -u +%Y%m%d%H%M%S)"
  busy=$(psql -d postgres -tAc "SELECT count(*) FROM pg_stat_activity WHERE datname='${PGDATABASE}' AND pid <> pg_backend_pid()")
  [ "$busy" = 0 ] || die "${busy} connections still open on ${PGDATABASE}: stop the app first (restored copy kept as ${target})"
  psql -v ON_ERROR_STOP=1 -d postgres -qc "ALTER DATABASE \"${PGDATABASE}\" RENAME TO \"${old}\"" \
    -c "ALTER DATABASE \"${target}\" RENAME TO \"${PGDATABASE}\""
  log "live database is now ${want}; the previous one is kept as ${old} (drop it once you're sure)"
fi
