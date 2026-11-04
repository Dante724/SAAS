# Testing — Tekly

How the test build is put together, how to run every layer, what each one guarantees, and where the
gaps are (ranked by business risk). Production runs **Postgres 16**; tests default to SQLite for speed,
with Postgres covered by a dedicated CI job and an always-on dialect check.

## TL;DR

```bash
pip install -r requirements-dev.txt     # exact versions CI uses (pytest 8.2, ruff 0.6.9, …)
make ci                                  # EVERY CI gate, in CI order. Green here == green in CI.
make test                                # backend suite, parallel
make test-pg                             # suite on a throwaway Postgres 16 (scripts/test_pg.sh)
cd frontend-react && npm run test:coverage   # frontend unit tests + coverage floor
```

## The layers

| Layer | Tool | Catches | Runs | Gate |
|---|---|---|---|---|
| Backend unit + API integration | pytest (1,140+ tests), xdist | business logic, HTTP layer, tenant isolation, validation | CI every push | **hard** |
| Prod-dialect check | `test_postgres_ddl_compat.py` | tables/indexes that won't compile on Postgres; partial indexes whose `WHERE` differs by DB | CI every push (no server needed) | **hard** |
| Postgres behavioural run | `backend-postgres` CI job (4 shards, xdist) | migrations from scratch + full suite on real Postgres 16 | CI every push | **hard** — require the `backend-postgres (all shards)` check |
| Migrations from scratch | `alembic upgrade head` | broken / non-replayable migrations | CI + `make ci` | **hard** |
| Model ↔ migration drift | `test_144eo_migrated_schema.py` | migrations building a different schema than the models (missing index/FK/column, wrong length or `ondelete`) — invisible to every other test, because they build from the models | CI every push | **hard** (allowlist can only shrink) |
| Lint | ruff 0.6.9 (pinned) | real bugs (unused vars, `== None`, broad `raises`) + import order | CI + pre-commit | **hard** |
| Coverage ratchet | pytest-cov / vitest v8 | coverage silently dropping | CI | **hard** (floor) |
| Frontend unit/component | Vitest + React Testing Library (85 tests) | logic + component behaviour | CI every push | **hard** |
| Content lint | `scripts/check-ui-text.mjs` | raw `snake_case` keys shown as UI labels | CI + pre-commit | **hard** |
| Typecheck + build | tsc, vite | type errors, broken bundle | CI | **hard** |
| E2E + visual regression | Playwright (`frontend-react/e2e/`) | misaligned layouts, rendered-text leaks, real flows | local, against a running app | manual (see `e2e/README.md`) |
| Pre-commit hooks | pre-commit (pinned) | secrets / `.env` / DB files, lint, broken YAML — before commit | local | blocks the commit |

**Local == CI.** Every tool version is pinned once in `requirements-dev.txt` / `package-lock.json`;
`make ci` runs the same steps CI does. Verified in an exact replica of CI's pinned stack.

**CI sharding.** On GitHub the backend suite runs as 4 parallel `test (shard N)` jobs. A hook at the end
of `backend/tests/conftest.py` assigns whole files to shards (balanced by test count) when
`PYTEST_SHARD`/`PYTEST_SHARD_TOTAL` are set. The `coverage` job then checks that the shards ran exactly
the collected suite, combines their coverage data, and enforces the floor. To reproduce one shard locally:
`PYTEST_SHARD=2 PYTEST_SHARD_TOTAL=4 pytest -n auto`. Unset, nothing changes.

## Running tests in parallel safely

Several people (or Claude sessions) running suites on one machine at once is normal. These rules make
it safe; each one exists because breaking it cost us a wave of fake failures.

**Every test process has its own database — automatically.** `backend/tests/conftest.py` turns the
configured `DATABASE_URL` / `TEST_DATABASE_URL` into a private one before the app is imported:

| Configured | What a test process actually uses | Cleaned up |
|---|---|---|
| `sqlite+aiosqlite:///./test.db` | `./test_<run>[_gwN].db` | file deleted at the end of the run |
| `postgresql+asyncpg://…/repairos_test` | database `repairos_test_<run>[_gwN]` on that server | `DROP DATABASE` at the end (`TEST_KEEP_DB=1` keeps it for debugging) |

`<run>` is unique per run and shared by its xdist workers; `gwN` is the worker. So two runs in the same
folder, or against the same server, can never drop or empty each other's tables, and xdist works on
Postgres too. A crashed run can leave a `*_<run>*` database or file behind; it is never reused, and is
safe to delete. Nothing to do on your side — just don't hard-code a database name in a test (use the
`db` fixture or `os.environ["DATABASE_URL"]`).

**A database outage is an infrastructure error, never a test result.** If the server is unreachable,
in recovery, out of connections, or crashes and restarts mid-run, the run stops at once with one
`INFRASTRUCTURE ERROR — …` message and **exit code 75**, instead of hundreds of 500s. That run proves
nothing either way: fix the server and rerun. Only conftest's own code that *opens a connection* is
classified this way, never a statement or a test body, so a real failure is never written off as
"infra" — a missing table, a constraint error, a lock-table overflow or a statement timeout is always a
real failure. A setup problem a rerun won't fix (the role can't `CREATE DATABASE`, wrong password)
stops with `TEST SETUP ERROR` and exit code 4. "Too many clients" is reported as infra but says to
check for a connection leak: if few runs share the server, it is a real bug.

**Postgres locally: use `scripts/test_pg.sh`** (or `make test-pg`). It starts a throwaway Postgres 16
just for this run (fresh data dir, its own socket dir under `/private/tmp/claude-$UID`, its own port, no
TCP listener), runs the suite with 4 xdist workers, then stops the server and deletes its files.
Any pytest arguments pass through: `scripts/test_pg.sh backend/tests/test_x.py -k foo`.
`TEST_PG_WORKERS=0` runs serially; `PYTHON=.venv/bin/python` picks the interpreter. If that run's
Postgres crashed, the script says so and fails even if pytest passed.

**Cap the load.** At most `TEST_PG_MAX_RUNS` (default **2**) `test_pg.sh` runs go at once per
machine; a third waits for a free slot. Rough cost of one full run: one Postgres server plus one
Python process per worker (~250 MB each, at most 2 connections each). Connections and memory are not
the limit; CPU and disk are (a SQLite run rebuilds the schema twice per test). Eight full Postgres runs
plus SQLite runs at once drove this Mac to a load average of 26.
- Never delete or re-`initdb` a Postgres data dir while its server is running (`pg_ctl stop` first).
  That is what crashed Postgres on 2026-11-04: the running server lost its files, PANICked, and the
  workers of 8 runs sharing it failed ~190 tests. `test_pg.sh` gives every run a fresh `mktemp` dir. Prefer one full run plus
targeted `-k`/file runs while iterating.

**Don't:**
- point tests at a database you care about (the demo DB `tekly_retest` on :55610, staging, prod) —
  isolation creates a sibling database there, but it still needs CREATEDB rights on that server;
- pass `-p no:logging` — tests use `caplog`; conftest refuses to start with that flag;
- `rm -f *test*.db` globs in a shared folder while other runs are going — the fixtures clean up;
- treat a flaky-looking failure as infra without the `INFRASTRUCTURE ERROR` banner: rerun it alone,
  and if it passes alone, it is a real ordering/timing bug to fix, not to retry.

**Frontend (Vitest) under load.** Wait with Testing Library's `waitFor` / `findBy*`, not
`vi.waitFor`: a failing `getByRole` inside `vi.waitFor` builds a full accessibility-tree dump on every
50 ms poll and starves the render it is waiting for. Never rely on wall-clock gaps between events
(set `timeStamp` explicitly, as `SalesPOS.test.tsx` does for the scanner).

## Coverage (ratchet)

Measured on the full suite. Floors live in config; **raise them as coverage improves, never lower them.**

| | Measured | Floor | Where |
|---|---|---|---|
| Backend (lines + branches combined) | 50.2% (58.7% lines, 20.3% branches) | 50.0 (re-measured 144ep, 1154 tests) | `pyproject.toml` → `[tool.coverage.report] fail_under` |
| Frontend lines / statements | 2.8% | 2.5 | `frontend-react/vite.config.ts` → `coverage.thresholds` |
| Frontend functions | 25.2% | 24 | same |

The backend floor has a small cushion because some code paths depend on today's date (e.g. "overdue").
The frontend floor leaves headroom so adding one new untested component doesn't fail CI — the fix for a
low frontend number is more tests, not a tighter gate.

## Gaps, ranked by business risk

Branch coverage is the honest signal here: most untested code is error/edge branches inside large
router handlers — exactly where production bugs hide.

| Priority | Area | Branch cov. | Why it matters |
|---|---|---|---|
| **P0** | `routers/apikeys.py`, `routers/v1.py` (public API) | **0%** | credentials + the externally-consumed contract |
| **P0** | `routers/public.py` (no login) | 9.6% | unauthenticated surface: booking, tracking, payments links |
| **P0** | `routers/cash.py`, `finance.py`, `sales.py` | 7–13% | money movement; errors here are financial errors |
| **P1** | `routers/auth.py`, `services/auth.py`, `admin.py` | 13–22% | login, tokens, platform admin |
| **P1** | `routers/payroll.py`, `payments.py` | 15–16% | money out / money in |
| **P2** | `routers/orders.py` (1,197 missed lines), `inventory.py`, `stock_docs.py`, `timeclock.py` | 8–13% | core daily operations |
| **P2** | Frontend pages (overall 2.8%) | — | UI behaviour beyond the tested helpers |

### First test cases to write (P0)

- **API keys:** create → key shown once → stored hashed (never plaintext); revoked key → 401; key from
  org A can't read org B; scope/permission enforced per endpoint.
- **Public v1 API:** each endpoint rejects missing/invalid key (401) and wrong-org data (404, not 403 —
  don't leak existence); response shape matches the documented contract (a contract test per endpoint).
- **Public pages:** rate/size limits on unauthenticated POSTs; unknown slug → 404 without leaking data;
  tracking token can't be enumerated; a disabled shop's public page is not served.
- **Cash / finance / sales:** totals balance (debits == credits); refunds can't exceed the original;
  rounding uses the money helper (no float drift); concurrent payment on one invoice can't double-pay;
  every money mutation is org-scoped; financial history is never hard-deleted.

### Schema debt (tracked by the drift gate)

None outstanding: `KNOWN_MISSING_FKS` is empty since `fk144ep01` created the last 11 FKs. Keep it
empty — a new model FK needs a migration.

**Before deploying `fk144ep01` (or any FK-adding migration):** run the read-only audit on production,
`DATABASE_URL=… python scripts/check_fk_orphans.py` (SELECT-only, READ ONLY transaction). Exit 0 = safe.
If orphans exist the migration aborts on purpose with the list and changes nothing — re-point or NULL
them, then deploy again.

**Local dev DB built by `create_all`** (never migrated) can hold leftovers migrations would have fixed:
`python scripts/repair_dev_db.py repairos.db` (dry run) then `--apply` (backs up first, one
transaction, idempotent, refuses `ENVIRONMENT=production`).

### Plan

1. P0 tests above (security + money). Raise the backend floor after each batch.
2. Once the `backend-postgres` CI job is green on GitHub, remove its `continue-on-error`.
3. Capture Playwright baselines (`npm run e2e -- --update-snapshots`) and add a few journey tests:
   create work order → invoice → payment; public booking → appointment appears.
4. Frontend: component tests for the highest-traffic pages (work orders, invoices) via RTL with the
   API mocked (see `src/pages/reports/CustomView.test.tsx` for the pattern).

## Conventions

- **TDD for fixes:** write the failing test first, then the fix (RED → GREEN). Name tests by build:
  `test_144xx_<what>.py`.
- **Every endpoint test checks tenant isolation** where data is org-scoped (org A can't see org B).
- **DB URL contract:** tests read `DATABASE_URL`; `TEST_DATABASE_URL` is optional (falls back to it).
  Never hard-require `TEST_DATABASE_URL` — CI doesn't set it.
- **Partial indexes** must declare `sqlite_where` *and* `postgresql_where` (enforced by test).
- **No real network** in tests: integrations are simulated when their keys are absent.
- **Prove guards can fire:** when adding a check (test, lint, hook), plant a violation once and confirm
  it fails.

## One-time local setup still needed

- **Pre-commit hooks:** copy the provided `pre-commit-config.yaml` to the repo root as
  `.pre-commit-config.yaml`, then `pip install -r requirements-dev.txt && pre-commit install`.
- **Playwright:** see `frontend-react/e2e/README.md`.
