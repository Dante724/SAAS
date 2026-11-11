#!/bin/sh
# Restore drill: prove the newest backup can be restored, and time it. Touches only a throwaway
# database (tekly_restore_drill), never the live one. Run it once now, then every quarter.
#   docker compose exec backup drill.sh
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/common.sh"

drill=tekly_restore_drill
start=$(date +%s)
"$HERE/restore.sh" latest --into "$drill"
took=$(( $(date +%s) - start ))

q() { psql -d "$1" -tAc "$2" 2>/dev/null || echo "?"; }
echo
echo "Restore drill $(date -u +%Y-%m-%d)  (restore took ${took}s, including any download)"
printf '  %-22s %12s %12s\n' "" "live" "restored"
printf '  %-22s %12s %12s\n' "schema version" "$(q "$PGDATABASE" 'SELECT version_num FROM alembic_version')" "$(q "$drill" 'SELECT version_num FROM alembic_version')"
for t in organisations employees clients orders invoices payments; do
  printf '  %-22s %12s %12s\n' "$t" "$(q "$PGDATABASE" "SELECT count(*) FROM $t")" "$(q "$drill" "SELECT count(*) FROM $t")"
done
echo "  (live counts can be higher: they include work done since the backup)"

[ "$(q "$drill" 'SELECT version_num FROM alembic_version')" != "?" ] || die "restored database has no schema version"
psql -d postgres -qc "DROP DATABASE IF EXISTS \"${drill}\""
log "drill passed; ${drill} dropped"
