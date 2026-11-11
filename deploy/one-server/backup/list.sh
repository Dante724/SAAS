#!/bin/sh
# Show the backups on this server and off-site, newest first.
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/common.sh"
echo "On this server (${BACKUP_DIR}):"
ls -1th "$BACKUP_DIR"/tekly-*.dump* 2>/dev/null | while read -r f; do echo "  $(basename "$f")  $(du -h "$f" | cut -f1)"; done
if offsite_enabled; then
  echo "Off-site (${REMOTE}):"
  rclone lsf --format "pst" --separator "  " "$REMOTE" | sort -r | sed 's/^/  /'
fi
