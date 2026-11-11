#!/bin/sh
# Is Tekly up? Checks the app on this server, then (if set) through the public address.
set -eu
cd "$(dirname "$0")"
ok() { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fail=1; }
fail=0

echo "== Smoke test"
for i in $(seq 1 60); do curl -sf http://127.0.0.1:8000/api/health >/dev/null && break; sleep 2; done
curl -sf http://127.0.0.1:8000/api/health >/dev/null && ok "app is running" || bad "app is not answering (docker compose logs app)"
curl -sf http://127.0.0.1:8000/api/readyz | grep -q '"ready"' && ok "app reaches the database" || bad "database not reachable"
curl -sf http://127.0.0.1:8000/ | grep -qi 'id="root"' && ok "web app is served" || bad "web app not served"

url=$(sed -n 's/^PUBLIC_BASE_URL=//p' .env | tail -1 | sed 's/[[:space:]]*#.*//')
if [ -n "$url" ]; then
  for i in $(seq 1 15); do curl -sf "$url/api/health" >/dev/null && break; sleep 2; done
  curl -sf "$url/api/health" >/dev/null && ok "public address $url works" || bad "public address $url not reachable (docker compose logs tunnel)"
fi
docker compose ps --status running --services | grep -qx backup && ok "backup service running" || bad "backup service not running"
exit $fail
