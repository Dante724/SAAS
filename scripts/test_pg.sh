#!/usr/bin/env bash
# Run the backend suite against a throwaway Postgres 16 that exists only for this run.
#
#   scripts/test_pg.sh                                   # whole suite, 4 xdist workers
#   scripts/test_pg.sh backend/tests/test_x.py -k foo    # any pytest arguments
#   TEST_PG_WORKERS=0 scripts/test_pg.sh                 # serial
#
# Each run gets its own cluster: a fresh data dir, its own socket dir and port, no TCP listener — so it
# cannot touch, or be touched by, any other Postgres on the machine (another session's, the demo DB).
# The server is stopped and its files deleted when the run ends, however it ends; a server left behind
# by a run that was SIGKILLed is stopped by the next run that reclaims its slot.
#
# At most TEST_PG_MAX_RUNS (default 2) of these run at once on one machine; more wait for a free slot.
# Several full suites at once is what overloaded the machine before (see TESTING.md).
#
# Exit code: pytest's (0 pass, 1 failures, …). 75 = the database itself failed (infrastructure: rerun);
# 2 = this script could not set up (missing Postgres or Python).
set -uo pipefail

die() { echo "test_pg: $1" >&2; exit "${2:-75}"; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -z "${PG_BIN:-}" ]]; then
  for d in /opt/homebrew/opt/postgresql@16/bin /usr/local/opt/postgresql@16/bin /usr/lib/postgresql/16/bin; do
    [[ -x "$d/postgres" ]] && { PG_BIN="$d"; break; }
  done
fi
[[ -x "${PG_BIN:-}/postgres" ]] || die "Postgres 16 not found; set PG_BIN=/path/to/postgresql@16/bin" 2
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  # this checkout's .venv, else the main checkout's (when this is a git worktree), else python3
  main="$(cd "$REPO" && cd "$(git rev-parse --git-common-dir 2>/dev/null)/.." 2>/dev/null && pwd)"
  for c in "$REPO/.venv/bin/python" "${main:-$REPO}/.venv/bin/python" "$(command -v python3 || true)"; do
    [[ -n "$c" && -x "$c" ]] && { PY="$c"; break; }
  done
fi
[[ -n "$PY" ]] && "$PY" -c "import pytest, asyncpg" 2>/dev/null \
  || die "no Python with the dev requirements found; set PYTHON=/path/to/venv/bin/python" 2
[[ -d /private/tmp ]] && TMPROOT=/private/tmp || TMPROOT=/tmp
ROOT="${TEST_PG_ROOT:-$TMPROOT/claude-$(id -u)}"   # short: a unix socket path must fit in ~100 bytes
MAX_RUNS="${TEST_PG_MAX_RUNS:-2}"
WORKERS="${TEST_PG_WORKERS:-4}"
mkdir -p "$ROOT/test_pg_slots" || die "cannot create $ROOT" 2

stop_cluster() { [[ -d "$1/data" ]] && "$PG_BIN/pg_ctl" -D "$1/data" -m immediate stop >/dev/null 2>&1; rm -rf "$1"; }

# --- take a slot (mkdir is atomic) --------------------------------------------------------------
# A slot whose owner died is reclaimed: renamed away first (atomic, so only one waiter wins it), then
# its leftover server is stopped and its files removed.
SLOT=""
while [[ -z "$SLOT" ]]; do
  for i in $(seq 1 "$MAX_RUNS"); do
    d="$ROOT/test_pg_slots/$i"
    if mkdir "$d" 2>/dev/null; then echo $$ > "$d/pid"; SLOT="$d"; break; fi
    owner="$(cat "$d/pid" 2>/dev/null || true)"
    if [[ -n "$owner" ]] && ! kill -0 "$owner" 2>/dev/null; then
      stale="$d.stale.$$"
      if mv "$d" "$stale" 2>/dev/null; then
        [[ "$(cat "$stale/pid" 2>/dev/null)" == "$owner" ]] || { mv "$stale" "$d" 2>/dev/null; continue; }
        old="$(cat "$stale/run" 2>/dev/null || true)"; [[ -n "$old" ]] && stop_cluster "$old"
        rm -rf "$stale"
      fi
    fi
  done
  [[ -n "$SLOT" ]] || { echo "test_pg: all $MAX_RUNS slots busy, waiting…"; sleep 10; }
done

RUN="$(mktemp -d "$ROOT/tpg.XXXXXX")" || { rm -rf "$SLOT"; die "mktemp failed"; }
echo "$RUN" > "$SLOT/run"
PORT=$(( 57000 + RANDOM % 1000 ))
PYTEST_PID=""
cleanup() {
  [[ -n "$PYTEST_PID" ]] && kill "$PYTEST_PID" 2>/dev/null && wait "$PYTEST_PID" 2>/dev/null
  stop_cluster "$RUN"
  rm -rf "$SLOT"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

# The cluster's locale only (the suite itself runs with the caller's environment, like CI).
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 "$PG_BIN/initdb" -D "$RUN/data" -U postgres -A trust -E UTF8 >"$RUN/initdb.log" 2>&1 \
  || { tail -5 "$RUN/initdb.log" >&2; die "initdb failed"; }
# Throwaway data: durability off for speed. No TCP (listen_addresses=''): the socket in $RUN is the only way in.
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 "$PG_BIN/pg_ctl" -D "$RUN/data" -l "$RUN/server.log" -w -t 60 start -o \
  "-k $RUN -p $PORT -c listen_addresses='' -c fsync=off -c synchronous_commit=off -c full_page_writes=off \
   -c max_connections=100 -c shared_buffers=128MB" >/dev/null \
  || { tail -20 "$RUN/server.log" >&2; die "Postgres failed to start"; }

URL="postgresql+asyncpg://postgres@/tekly_test?host=$RUN&port=$PORT"
"$PG_BIN/createdb" -h "$RUN" -p "$PORT" -U postgres tekly_test || die "createdb failed"
echo "test_pg: Postgres 16 at $RUN (port $PORT), slot $(basename "$SLOT")/$MAX_RUNS, python $PY"

XDIST=(-p no:xdist); [[ "$WORKERS" != 0 ]] && XDIST=(-n "$WORKERS")
# In the background + wait, so a signal to this script stops the run at once (cleanup kills pytest).
(cd "$REPO" && ENVIRONMENT=test SEED_DEMO=false DATABASE_URL="$URL" TEST_DATABASE_URL="$URL" \
  exec "$PY" -m pytest -q "${XDIST[@]}" "$@") &
PYTEST_PID=$!
wait "$PYTEST_PID"; code=$?
PYTEST_PID=""
if grep -qE 'PANIC|terminated by signal|server process .* was terminated' "$RUN/server.log"; then
  echo "test_pg: the Postgres server crashed during this run — see its log below. Results are not trustworthy." >&2
  grep -E 'PANIC|FATAL|terminated|recovery' "$RUN/server.log" | tail -20 >&2
  [[ $code == 0 ]] && code=75
fi
exit $code
