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
| Backend, Postgres ×3 | as `ci.yml` `backend-postgres` runs it: `PYTEST_SHARD=1..4 PYTEST_SHARD_TOTAL=4 pytest -q -p no:xdist`, each shard on its own database, shards in parallel | ✅ run 1: **3607 passed, 3 skipped, 0 failed** · run 2: same · run 3: same (about 18 min per run) |
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
- Backend Postgres ×3: **none**. The same 3607 tests passed in all 3 runs.
- Skipped tests (the same in every run; they need real sandbox credentials): `test_opd_telephony.py:507` (Twilio), `test_opf_card_readers.py:1019` (Stripe), `test_opf_card_readers.py:1039` (Square). SQLite also skips a 4th: `test_deploy_readiness.py:54`, "React build not present". It ran on Postgres after `npm run build`. Each backend all-in is 3610 tests.

### Real failures
None.

### Environment finding (not a product bug)
The Postgres suite needs the DB role to be a **superuser**. `backend/tests/conftest.py:110` runs `SET LOCAL session_replication_role = replica` between tests. With a role that only has `CREATEDB`, nearly every test errors at setup with `InsufficientPrivilegeError: permission denied to set parameter "session_replication_role"`. We saw ~900 errors per shard, 3 of 3 runs. CI doesn't notice because the `postgres:16-alpine` `POSTGRES_USER` is a superuser. `TESTING.md` and `make test-pg` don't say this. The results above are with `ALTER ROLE … SUPERUSER`, matching CI.

Repro: `createuser --createdb u` → `make test-pg PG=postgresql+asyncpg://u:…@localhost/db` → mass setup errors.

## Round 2 — parallel isolation (#92)
⏸ Waiting: #92 is not merged yet.

## Round 3 — `tests-e2e/api_gate/` (#96)
⏸ Waiting: #96 is not merged yet.
