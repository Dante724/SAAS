#!/bin/sh
# Disaster recovery: make a backup the live database.
#   ./restore-live.sh latest            or   ./restore-live.sh tekly-20261111T030000Z-nightly.dump
# Stops the app, restores into a new database, swaps it in (the old live database is kept,
# renamed *_before_<time>), starts the app and smoke-tests it.
set -eu
cd "$(dirname "$0")"
backup=${1:?usage: ./restore-live.sh <backup-name|latest>   (list them: docker compose exec backup list.sh)}

printf 'This replaces the LIVE database with backup "%s". Shops lose anything entered since it was taken.\nType RESTORE to continue: ' "$backup"
read -r answer
[ "$answer" = RESTORE ] || { echo "Cancelled."; exit 1; }

docker compose stop app tunnel
if docker compose exec -T backup restore.sh "$backup" --replace-live; then
  docker compose up -d app tunnel
  ./smoke.sh
else
  echo "Restore failed: the live database was not changed. Starting the app again."
  docker compose up -d app tunnel
  exit 1
fi
