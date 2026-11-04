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

## Round 3 — `tests-e2e/api_gate/` on commit `f64d3865a00c99052e8d6a7ea0a1955cf74dbfee`
Round 1 still applies to this head: `5f30059..f64d386` only adds `tests-e2e/api_gate/` (#96).

Server: fresh database `repairos_gate`, then `alembic upgrade head` (clean), then `uvicorn main:app --port 8090 --workers 2`. Env: `ENVIRONMENT=development`, `DATABASE_URL=postgresql+asyncpg://…`, and a runtime-random `SECRET_KEY`. `/health` → `{"status":"ok","database":"up"}`. The scripts ran one after another, with `TEKLY_URL=http://localhost:8090`.

| Script | Summary line (verbatim) | Owner's pass bar | Verdict |
|---|---|---|---|
| `regress.py` | `81 checks: 73 PASS, 1 FAIL, 7 SKIP (failed: W3-E-7)` | 0 FAIL | ❌ **1 FAIL**: harness and W3-E disagree on intent, not a product bug (finding 2) |
| `isolation.py` | `356 probes … blocked 325, leaks 0 (body 0, A data changed 0), 2xx-without-A-data 21, 5xx 0, refused-before-lookup 10` | 0 leaks | ✅ **0 leaks**, 0 × 5xx. The script still exits 1, because its own README bar also needs 0 × 2xx. See below |
| `countries.py` | `7 countries: 6 PASS, 1 FAIL, 0 SKIP` | all PASS | ❌ **CA FAIL**: harness and W3-E disagree on intent, see below |
| `load.py` | `2017 requests in 60s = 33.3 req/s, 5xx 0, endpoints with p95 >= 1s: none` | 0 × 5xx, p95 < 1 s | ✅ |

**load.py, p95 per endpoint (ms):** `GET /api/clients` 43 · `GET /api/inventory/parts` 30 · `GET /api/orders` 43 · `GET /api/orders/board` 43 · `GET /api/orders/{id}` 51 · `GET /api/reports/dashboard` 109 · `POST /api/cash/orders/{id}/pay` 132 · `POST /api/clients` 53 · `POST /api/orders` 163 · `POST /api/orders/{id}/lines` 141. Every status was 200 or 201.

**regress.py SKIPs (7)** all print `pending W3-C`: W3-C-1, 2, 3, 4, 5, 7 and 8. That's expected, because #93 (W3-C) is not merged. While it's open these checks are **not** evidence either way, and they must be re-run after #93 merges.

### Findings

**1. countries.py CA: expected GST 5%, got HST 13%. The harness and the shipped W3-E intent conflict; the owner needs to decide.**
- What the harness says: `CA  default tax HST 13.0% (want GST 5.0%); tax name 'HST' (want GST); job line tax_rate 13.0 (want 5.0)`. This is the row for CA sign-up **without** a province.
- What the code does (W3-E, b8c0d3e): `backend/services/regions.py` `ca_province_tax()` guesses the province from the time zone when none is sent. `America/Toronto`, the CA default, gives Ontario, so HST 13%.
- The backend tests require that behaviour: `backend/tests/test_qa_w3e_jobs_roles.py:240` (`({}, "HST", 13.0)  # no province: the Toronto default`), `test_launch_stream_a.py:126` and `test_qa_e_signup_au_industry.py:82`. `countries.py:20` still expects the pre-W3-E value, GST 5%.
- Not edited (rule: never edit a test to pass). **Owner decision:** either update `countries.py:20` to the W3-E default, or change the default to federal GST and update the 3 backend tests.
- Repro: `TEKLY_URL=http://localhost:8090 python3 tests-e2e/api_gate/countries.py`, or sign up with `{"country":"CA"}` and no province, then GET the default tax.

**2. regress.py W3-E-7: `AU receipt tax 0.0 on a 110 sale with GST included`. The harness and the shipped W3-E behaviour don't match; this is not a product bug, and the owner needs to decide.**
- Minimal repro, against the same server: an AU shop, `PUT /api/staff/company {"company_details":"ABN 51 824 753 556"}`, a till sale of 1 × 110, then `GET /api/sales/{id}/receipt`. It returns:
  ```
  tax = 0.0
  tax_lines = [{'name': 'GST', 'rate': 10.0, 'amount': 10.0, 'included': True}]
  shop_tax_id = None
  line tax: [(110.0, 10.0)]
  text: … Total  $110 / Includes GST 10%  $10 / Paid …
  ```
- **GST is correct on the receipt.** W3-E reports included tax in `tax_lines` and in the text. The top-level `tax` is by definition only the tax added on top (`sale_tax_added()`, `backend/routers/sales.py`), so it is 0 for a GST-inclusive sale. The harness asserts `tax > 0`, which is the pre-W3-E contract.
- **ABN is missing because of where the harness puts it.** W3-E reads the shop tax number from the default legal entity, `vat_no` or `tax_id` (`shop_tax_id()`). The harness writes it into the free-text `company_details`, and receipts never read that field. The shipped path is covered by `backend/tests/test_qa_w3e_jobs_roles.py:272`: `POST /api/settings/legal-entities {"vat_no":…,"is_default":true}` gives `shop_tax_id == {"label":"ABN",…}` and the ABN appears in the text, for AU, DE and GB. **That test passed in all 3 Postgres runs and on SQLite.**
- Not edited. **Owner decision:**
  - (a) update `regress.py` `w3e_7` to read `tax_lines` and set the ABN via legal entities; or
  - (b) if an ABN typed into Company details is meant to print, that is a product gap. It would need a test first, then a fix.
- Repro: `TEKLY_URL=http://localhost:8090 python3 tests-e2e/api_gate/regress.py W3-E-7`.

**3. isolation.py: 21 × `2xx-without-A-data`. Not a leak, but it misses the README's "0 2xx" bar.**
- 13 GETs return an empty list or zero for shop A's id instead of 404. For example `GET /api/clients/{id}/calls`, `/coupons`, `/documents`, `/feedback`, `GET /api/payroll/balance/{employee_id}`, and `GET /api/directories/{id}/export`, which returns a CSV header only.
- 3 POST/PATCH return `{"ok":true}`: directories reorder, auto-print and form-fields reorder.
- 5 DELETEs return `{"ok":true}`: bundles, document templates, pricing types, print templates and resources.
- I read all 5 DELETE handlers. Each one filters on `org_id == current.org_id`, so on a foreign id it is a no-op. I checked the database afterwards: shop A's bundle and resource are still `active = t`.
- Leaks: 0. A data changed: 0. Hardening idea, not a beta blocker: return 404 when the id isn't the caller's. Full list in `isolation.py` output (`2XX-EMPTY` lines).

**4. Low: a start-up race seeding the platform admin with `--workers 2` on Postgres.**
- On the first boot both workers ran `ensure_seed_admin`. One got `UniqueViolationError … "uq_platform_admins_email"`, which `main.py:140` caught and logged as `ERROR tekly :: platform admin seed skipped` with a traceback.
- Both workers then reached `Application startup complete`, and there is exactly 1 `platform_admins` row.
- Impact: a misleading ERROR plus a traceback on the first multi-worker boot. In the losing worker, `ensure_builtin_roles` also didn't run (the winner's did). SQLite and a single worker hide this.
- Repro: empty database, `alembic upgrade head`, `uvicorn main:app --workers 2`, then grep the log for `seed skipped`.
