# Final-gate results — `roadmap/orderry-parity`

This file records only what was actually run and seen. ⏳ means the check is running or not yet run. It is never a pass.

## Environment
- Cloud box: 4 vCPU, 15 GB RAM. Python **3.11** (same as CI), installed with `pip install --require-hashes -r requirements-dev.lock`. Node 22.
- **Postgres 16**: a new cluster with `--locale=en_US.UTF-8` (every database is UTF8 / en_US.UTF-8), run with `LC_ALL=en_US.UTF-8`. Role `tekly` has `LOGIN CREATEDB`. Its password is generated at runtime with `openssl rand` and is not in the repo.
- Env vars are the ones `ci.yml` uses: `ENVIRONMENT=test SEED_DEMO=false`.
  - SQLite: `DATABASE_URL=sqlite+aiosqlite:///./test.db`.
  - Postgres: `DATABASE_URL` and `TEST_DATABASE_URL` set to `postgresql+asyncpg://…`.

## Round 1 — commit `5f30059087c7fe734f745c1fb0eecd94864c4217`

| Check | Command | Result |
|---|---|---|
| ruff (CI scope) | `ruff check main.py backend/config.py backend/core backend/db backend/tests` | ✅ All checks passed |
| ruff (security.yml scope) | `ruff check backend main.py scripts --select F,E9` | ✅ All checks passed |
| Backend, SQLite | `pytest -q -n auto` | ✅ **3606 passed, 4 skipped, 0 failed** (53 min) |
| Backend, Postgres ×3 | as `ci.yml` `backend-postgres` runs it: `PYTEST_SHARD=1..4 PYTEST_SHARD_TOTAL=4 pytest -q -p no:xdist`, each shard on its own database | ⏳ running |
| Alembic heads | `alembic -c backend/alembic.ini heads` | ✅ exactly 1 head: `ce144w3` |
| Upgrade from an empty Postgres | `alembic upgrade head` on a new database | ✅ exit 0, 239 migrations applied, no errors |
| Downgrade −1, then upgrade head | `alembic downgrade -1 && alembic upgrade head` | ✅ both exit 0: `ce144w3 → ret01 → ce144w3` |
| `npm ci` | | ✅ |
| `npx tsc --noEmit` | | ✅ exit 0 |
| `npx vitest run` ×3 | | ✅ run 1: 741/741 · run 2: 741/741 · run 3: 741/741 (134 files each) |
| `npm run build` | | ✅ |
| `npm run lint:uitext` | | ✅ |

### Flaky tests
- Frontend (vitest ×3): **none**.
- Backend Postgres ×3: ⏳ not yet known.

### Real failures
None so far.

## Round 2 — parallel isolation (#92)
⏸ Waiting: #92 is not merged yet.

## Round 3 — `tests-e2e/api_gate/` (#96)
⏸ Waiting: #96 is not merged yet.
