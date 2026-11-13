#!/bin/sh
# Deploy (or update) Tekly on this server:
#   1. build the app image from this checkout
#   2. back up the database (if it is already running)
#   3. start everything; migrations run first and the app only starts if they succeed
#   4. smoke-test it
# Roll back: git checkout <previous commit> && ./deploy.sh   (the database is not downgraded:
#   check DEPLOY.md "Rolling back" first; it lists which releases are safe to roll back across)
set -eu
cd "$(dirname "$0")"
[ -f .env ] || { echo "No .env here: cp .env.example .env and fill it in (README step 4)"; exit 1; }
perm=$(stat -c %a .env 2>/dev/null || stat -f %Lp .env)
[ "$perm" = 600 ] || { echo "Run: chmod 600 .env  (it holds secrets)"; exit 1; }

rev=$(git rev-parse --short HEAD)
sentry=$(sed -n 's/^VITE_SENTRY_DSN=//p' .env | tail -1)
echo "== Building tekly:${rev}"
docker build -t "tekly:${rev}" -t tekly:latest \
  --build-arg VITE_SENTRY_DSN="${sentry}" --build-arg VITE_RELEASE="${rev}" ../..
docker compose build backup

if docker compose ps --status running --services 2>/dev/null | grep -qx db; then
  echo "== Backing up before the update"
  docker compose exec -T backup backup-now.sh "pre-${rev}"
fi

echo "== Starting"
RELEASE="$rev" docker compose up -d --remove-orphans
./smoke.sh
echo "== Deployed ${rev}"
