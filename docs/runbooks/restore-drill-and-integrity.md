## Runbook: Restore drill and integrity sweep
**Owner:** whoever is on call for the deploy | **Frequency:** restore drill quarterly and after every Postgres major/minor upgrade; integrity sweep runs nightly by itself
**Last Updated:** 2026-11-06 | **Last Run:** CI runs the drill on every push (backend-postgres, shard 1) against a freshly migrated database. A drill against a real production dump has **not** been run yet — record it in History below.

### Purpose
Prove a backup can actually be restored, measure how long it takes (your real recovery time),
and catch money/stock totals that have drifted from their ledgers before a customer does.

- `scripts/restore_drill.py` dumps a database (or takes an existing nightly dump), restores it into a scratch database next to it, and checks the copy:
  - migration version matches the source and the code's head;
  - every table has the same row count;
  - the integrity sweep finds no new mismatches.
- The **integrity sweep** (`backend/services/reconcile.py`, scheduled as `integrity-sweep`, daily) checks:

  | Check | Stored total | Must equal |
  |---|---|---|
  | `account` | `Account.balance` (each till) | Σ its transactions that aren't `deleted` |
  | `client` | `Client.balance` | Σ the client's ledger entries |
  | `invoice` | `Invoice.amount_paid` | Σ payments on it − Σ refunds on it |
  | `stock` | on-hand per (product, location) | Σ stock movements |

  It logs each mismatch at ERROR on `tekly.reconcile` (ids and amounts only, no names) and exposes `repairos_integrity_mismatches{check=…}` in `/metrics`. It never changes data.

### Prerequisites
- [ ] Postgres 16 client tools (`pg_dump`, `pg_restore` 16+) on the machine running the drill: `sudo apt-get install -y postgresql-client-16`
- [ ] A role that can `CREATE DATABASE` on the server the drill runs against. Use the migrations/admin role, not the app's CRUD-only role.
- [ ] A checkout of the release that is deployed, with its Python environment (the drill imports the app's models and its migration head).
- [ ] For a drill against production data: a **non-production** Postgres server to restore into. Never restore production data onto a shared staging server (PROD-HARDENING.md).

### Procedure

#### Step 1: Get the latest nightly dump
```bash
# the nightly job (DEPLOY.md §4) writes repairos-YYYY-MM-DD.dump; copy the newest one locally
ls -lh repairos-*.dump | tail -1
```
**Expected result:** a dump from the last 24 h, size roughly in line with previous ones.
**If it fails:** no recent dump means the nightly backup is broken — that is the incident; escalate before continuing.

#### Step 2: Run the drill against the restore server
```bash
DATABASE_URL=postgresql+asyncpg://ADMIN_USER:…@RESTORE_HOST:5432/postgres \
ENVIRONMENT=test python scripts/restore_drill.py --dump repairos-YYYY-MM-DD.dump --no-head-check --keep
```
(`--dump` checks the file instead of dumping; with `--dump` the "source" is only used for the
server address, so point it at the restore server. `--no-head-check` because a dump of
yesterday's production is at the deployed head, which may lag the checkout. `--keep` leaves the
restored database for step 3.)
**Expected result:** `restore drill: PASS — N tables, M rows, dump X MB` and a timings line. Note `total_s`: that is the restore time for this size.
**If it fails:** the report lists each problem (`table X: a row(s) in the source, b restored`, a version mismatch, or new integrity mismatches). Keep the scratch database and escalate; do not delete the dump.

> Without `--dump`, the drill dumps the database at `DATABASE_URL` itself and compares source vs restored row by row — use this form to check a live replica or staging database end to end.

#### Step 3: Boot the app against the restored copy and sign in
```bash
DATABASE_URL=postgresql+asyncpg://…@RESTORE_HOST:5432/tekly_restore_drill_XXXXXXXX \
ENVIRONMENT=staging NO_REVERSE_PROXY=true SINGLE_WORKER=true … uvicorn main:app --port 8000
```
**Expected result:** `/api/readyz` answers ready; you can sign in as a known staff account and open a recent job, its invoice and the till.
**If it fails:** capture the error, keep the database, escalate.

#### Step 4: Drop the scratch database
```bash
psql "postgresql://ADMIN_USER:…@RESTORE_HOST:5432/postgres" -c 'DROP DATABASE "tekly_restore_drill_XXXXXXXX" WITH (FORCE)'
```

### Verification
- [ ] The drill printed PASS.
- [ ] You signed in on the restored copy and saw yesterday's data.
- [ ] You wrote the date, dump size and `total_s` in History below.

### Integrity sweep: when it reports a mismatch
1. Find the line: `integrity mismatch check=<check> org=<org> id=<id> stored=… derived=… diff=…` in the logs (or `repairos_integrity_mismatches > 0` in metrics).
2. Re-run it for that shop to see if it is still there:
   ```bash
   python -c "import asyncio; from backend.db.database import AsyncSessionLocal; from backend.services.reconcile import find_mismatches
   async def m():
       async with AsyncSessionLocal() as db: print([x.as_dict() for x in await find_mismatches(db, org_id='ORG_ID')])
   asyncio.run(m())"
   ```
3. Do **not** "fix" the stored total to match. Work out which side is wrong from the ledger rows (the transactions, ledger entries, payments or stock movements for that id) and the audit log, then correct it the way the app does (a correction transaction, a ledger adjustment, a stock adjustment), so the trail stays complete.
4. A new mismatch means a code path moved a total without its ledger, or the other way round. Open a bug with the ids; it is the class of bug the hardening council found (H1, H4, H5, H7).

### Troubleshooting
| Symptom | Likely Cause | Fix |
|---|---|---|
| `setup error: pg_dump is … use client 16+` | Older client tools on PATH | Install `postgresql-client-16`, or put its `bin` first on PATH |
| `setup error: … permission denied to create database` | Running as the app's CRUD-only role | Use the admin/migrations role |
| `table X: N row(s) in the source, 0 restored` | Restore stopped early, or the dump excluded the table | Re-run `pg_restore --exit-on-error` by hand to see the first error |
| `restored schema is at […], the code's migration head is […]` | The checkout is newer or older than the deployed release | Check out the deployed tag, or pass `--no-head-check` for an old dump |
| `integrity check 'stock': n mismatch(es) after restore, 0 before` | Partial restore of `stock_levels` or `stock_movements` | Treat the restore as failed |

### Rollback
The drill only creates and drops its own `tekly_restore_drill_*` database. It never writes to
the source. If a run is interrupted, drop any leftover `tekly_restore_drill_*` database.

### Escalation
| Situation | Contact | Method |
|---|---|---|
| No recent nightly dump, or the drill fails | Owner | Direct message, same day |
| Integrity mismatch on money (account/client/invoice) | Owner + whoever shipped the last money change | Direct message, same day; pause money-related deploys |

### Per-shop restore (one tenant)
There is no automated single-shop restore. To recover one shop's data: restore the whole dump
into a scratch database with Step 2 (`--keep`), inspect that shop's rows there read-only, and
copy back only what was lost with reviewed SQL, inside a transaction, after a fresh backup. Every
table carries `org_id` (or reaches it through a parent row), so scope every statement by it.

### History
| Date | Run By | Notes |
|---|---|---|
| 2026-11-06 | cloud session | CI drill on a migrated empty schema: PASS, 191 tables, ~7 s. Production dump drill still to do. |
