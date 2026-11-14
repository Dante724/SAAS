# Deploying Tekly

The runbook for putting Tekly on a server. Read it with `PROD-HARDENING.md` (one-time security
settings).

## 1. Build

```bash
docker build -t repairos:$(git rev-parse --short HEAD) .
```

The image builds the React app (`frontend-react/dist`), installs the hash-pinned
`requirements.lock`, and runs as a non-root user. It refuses to start in production/staging if
the React build is missing.

Dependencies: edit `requirements.txt` / `requirements-dev.txt`, then regenerate the locks:

```bash
uv pip compile requirements.txt --universal --python-version 3.11 --generate-hashes -o requirements.lock
uv pip compile requirements-dev.txt --universal --python-version 3.11 --generate-hashes -o requirements-dev.lock
```

## 2. Configure (environment variables)

| Variable | Required | Notes |
|---|---|---|
| `ENVIRONMENT` | yes | `production` or `staging`. Both enforce every fail-closed check. |
| `DATABASE_URL` | yes | `postgresql+asyncpg://…` (SQLite is for development only). |
| `SECRET_KEY` | yes | ≥32 random chars. Signs logins. |
| `TOKEN_ENCRYPTION_KEY` | yes | Fernet key(s), comma-separated, newest first. Encrypts OAuth tokens and shop credentials. Keep the old key in the list while rotating. |
| `CORS_ORIGINS` | yes | Your real domains, comma-separated. |
| `PUBLIC_BASE_URL` | yes | Public URL (links in e-mails, webhook signatures). |
| `FORWARDED_ALLOW_IPS` | yes behind a proxy | Your load balancer's IP/CIDR. Uvicorn then takes the client IP from `X-Forwarded-For` only from it. Leave `TRUSTED_PROXY_HOPS` at 0. A deployed instance **refuses to start** while proxy trust is unconfigured (`FORWARDED_ALLOW_IPS` unset or loopback only, e.g. the image default `127.0.0.1`, and `TRUSTED_PROXY_HOPS=0`): behind a balancer every anonymous user would share one rate limit. |
| `TRUSTED_PROXY_HOPS` | alternative | Instead of `FORWARDED_ALLOW_IPS`: the number of proxies in front (load balancer = 1); the app reads the client IP that many entries from the right of `X-Forwarded-For`. `0` (the default, and the value in `.env.example`) does **not** count as configured. |
| `NO_REVERSE_PROXY` | only without a proxy | `true` says clients connect straight to this server (nothing in front), so the peer address is the client. This is the only way to start deployed without `FORWARDED_ALLOW_IPS` or `TRUSTED_PROXY_HOPS`. Never set it behind a load balancer. |
| `WEB_CONCURRENCY` | no | Worker processes (default 1). More than 1 needs `REDIS_URL`, so rate limits are shared across workers; the app refuses to start otherwise. Background sweeps run once across all workers (Postgres advisory locks). |
| `REDIS_URL` | yes, or `SINGLE_WORKER` | Shared rate-limit store, and the reports dashboard cache (`dash:{org_id}`, 30 s TTL) so every worker serves the same cached tiles. A deployed instance **refuses to start** without it, because `--workers N` and extra replicas can't be detected and would each enforce their own copy of every limit. If Redis becomes unreachable at runtime both fall back to per-worker memory (retried every 30 s); requests never fail because of it. |
| `SINGLE_WORKER` | only without Redis | `true` promises exactly **one web worker on one instance** (the beta setup until `REDIS_URL` is set), and lets a deployed instance start without `REDIS_URL`. `WEB_CONCURRENCY` > 1 is still refused. Don't add `--workers`, a second replica, or a second container behind the balancer while it is set. |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | no | Postgres connections per worker (default 10 + 10). Keep `WEB_CONCURRENCY` × their sum under the server's `max_connections`. `DB_POOL_TIMEOUT_S` (default 10) is how long a request waits for a free connection before failing. |
| `DB_STATEMENT_TIMEOUT_MS` | no | Postgres cancels any single statement that runs longer (default 15000). `DB_IDLE_IN_TX_TIMEOUT_MS` (default 60000) ends sessions left idle inside a transaction. Set to 0 behind a PgBouncer that rejects startup parameters, and set them on the database role instead. |
| `SLOW_QUERY_MS` | no | Any SQL statement slower than this (default 500) logs one WARNING on `tekly.db.slow`: duration, the first 200 characters of the statement and the request id (bound values are never logged), and counts in `repairos_slow_queries_total` at `/metrics`. 0 turns it off. For the full picture per query shape, enable Postgres's `pg_stat_statements` extension (`shared_preload_libraries = 'pg_stat_statements'`, then `CREATE EXTENSION pg_stat_statements;`) and review it by `total_exec_time`. |
| `MIGRATION_LOCK_TIMEOUT` | no | How long a migration waits for a table lock before failing (default `5s`). Migrations run with no statement timeout. See "Migrations and live traffic" below. |
| `MEDIA_BACKEND` | recommended | `s3` in production (see §5). |
| `SENTRY_DSN` | recommended | Sends unhandled errors to Sentry/GlitchTip (headers, cookies, bodies and query strings are scrubbed). Optional `SENTRY_TRACES_SAMPLE_RATE` (default 0) and `RELEASE` (git SHA). |
| `SUPPORT_EMAIL` / `SUPPORT_URL` | no | Where the in-app Help & support menu and "contact support" errors point (default `hello@tekly.app`; a `SUPPORT_URL` help page wins over the email). |
| `VITE_SENTRY_DSN` | recommended (build arg) | Browser error tracking: pass with `docker build --build-arg VITE_SENTRY_DSN=…` (optional `VITE_RELEASE`). Unset, the Sentry SDK is never downloaded. Events carry only the user id; bodies, cookies, headers, query strings are dropped and emails masked. |
| `METRICS_TOKEN` | to scrape `/metrics` | Prometheus bearer token. Without it a deployed instance returns 404 at `/metrics`. |
| `MAX_REQUEST_BYTES` | no | Request body cap (default 16 MB). Raise it only together with `MEDIA_MAX_BYTES`. |
| `REFRESH_TOKEN_EXPIRE_MINUTES` | no | Absolute session length (default 30 days). Refreshing a token never extends a sign-in past this. |
| `STRIPE_*`, `SQUARE_*`, `TWILIO_*`, `META_*`, `SMTP_*` | per integration | Shops can also enter their own keys in Settings → Integrations; each needs its webhook signing secret there. |

## 3. Release steps (every deploy)

1. **Back up** the database (or confirm point-in-time recovery is on).
2. **Migrate** with the new image, before starting it:
   `docker run --rm --env-file .env repairos:<tag> alembic -c backend/alembic.ini upgrade head`
3. **Start** the new containers. Two probes:
   - `GET /api/health` (also `/health`) is **liveness**: the process is up. It never touches the
     database and always answers fast. The Docker `HEALTHCHECK` uses it, so a database blip does
     not restart every container at once.
   - `GET /api/readyz` is **readiness**: 200 `{"status": "ready", "database": "up"}` when the
     database answers, 503 `{"status": "unready", "database": "down"}` when it doesn't. Point the
     load balancer's target health check (and uptime monitors that should page on DB loss) here.
4. **Smoke test:** sign in, open a work order, take a test payment in Stripe test mode.
5. **Roll back** by starting the previous image tag, with no `alembic downgrade`, unless
   "Rolling back" below names that release. Read that list before every deploy.

### Rolling back (which releases are image-rollback-safe)

Between step 2 and step 3 the OLD containers serve against the NEW schema, and after a rollback
the previous image runs against it for as long as it takes. So every migration must leave a
schema the previous image still works with.

| Release | Migrations | Old image on the new schema |
|---|---|---|
| Tenant org_id (#219) | corg01, corg02 | **Safe on Postgres** from corg01/corg02 on. org_id is NOT NULL on 23 child tables (payments, order_lines, stock_movements…) and code from before #219 doesn't set it; a BEFORE INSERT trigger fills it from the parent row, so its inserts keep working. A database that ran the first corg01 (before the triggers) is **not** rollback-safe until `upgrade head` has run corg02. SQLite (dev) has no triggers: don't roll a dev database back across #219. |
| Payment soft-void (#218) | pv01 | Safe: new nullable columns only. |
| Wave 2 schema rules (#221) | w2b01 | Safe for normal use. The new unique indexes and CHECKs also bind the old code: an action the old code allowed and the new rules refuse (a second open drawer at a location, a duplicate code, a negative stock count) gets a database error (500) instead of a bad row. |
| Exact decimals (#222) | dec08 | Safe, not verified against the old image: the rate/qty columns are NUMERIC now; old code declares them Float, and SQLAlchemy reads NUMERIC into float and binds floats into it. Values are stored rounded to the new scale. |
| Tenant keys, FKs, hashed track links (audit DB-03/10/12–15/17, SEC-12) | dbi01, dbi02, dbi03 | **Safe on Postgres.** devices/purchase_orders.org_id became NOT NULL behind the same BEFORE INSERT fill trigger as corg01, so the old image's inserts keep working. The new keys bind the old code too: a write naming another shop's record, or a link to a coupon/membership/order line/transaction that doesn't exist, gets a database error (500) instead of a bad row; normal use never does. Tracking links: the old plaintext column is kept and still filled for links issued before the upgrade, so the old image opens those; a link the new image issued (or rotated) has no plaintext and opens again only after rolling forward. dbi03 adds order_lines.created_at with a server default, so lines the old image inserts are stamped too. SQLite (dev) has no triggers: don't roll a dev database back across this. |
| Redemption roles, money CHECKs, one open shift (wave 4, FIN-03/FIN-09/DB-04/DB-07/DB-16) | w4s01 | **Safe on Postgres.** New columns are nullable or have constant defaults. A job line the old image inserts has no line_role: the `fill_line_role` BEFORE INSERT trigger gives it one by the old description rule, so its gift-card/coupon/points lines stay redemptions. membership_assets.plan_id/live and storage_bins.is_default are kept by triggers whichever image writes. The new rules bind the old code too: a negative payment, an unknown payment kind or transaction status, a zero-qty document line, a second open attendance shift or a second live membership of a plan on one asset gets a database error (500) instead of a bad row. A gift card the NEW image redeemed on a job is a `gift_card` payment; the old image shows it as a payment without a cashbox and would refund it as cash on archive, so don't keep the old image running for long. SQLite (dev): only the old image's line roles aren't filled; don't roll a dev database back across this. |

A database `alembic downgrade` across these is never needed for an image rollback. If one is
needed anyway, it runs in reverse order (w4s01, dbi03, dbi02, dbi01, dec08, w2b01, pv01, then corg01), and corg01's downgrade
drops org_id from 23 tables: take a backup first.

**Rule for new migrations: NOT NULL and type changes go expand -> contract.** A migration that
makes a column NOT NULL, renames or drops a column, or changes a type the previous image reads or
writes differently, ships in steps, and each step is safe with the previous image:

1. **Expand**: add the column nullable (or the new column next to the old one). The new code writes
   it; a trigger or the app fills it for the old code's writes; backfill in batches, committing per
   batch.
2. **Contract**, in a LATER release, once no image without the expand code can be started again:
   `SET NOT NULL` (on Postgres via `CHECK … NOT VALID` + `VALIDATE` first, so the scan doesn't
   block writes), drop the old column or the bridging trigger.

A release that can't be split this way is listed in the table above as **not rollback-safe**,
with what to do instead. The corg01/corg02 triggers are such a bridge: drop them in a later
release once no image from before #219 can be started again. dbi01's fill triggers on devices and
purchase_orders are the same kind of bridge. dbi02 is the expand half of hashing the tracking-link
tokens; its **contract** (a later release, once no image from before dbi02 can start) hashes any row
still holding only a plaintext token, then empties `orders.public_token` and drops it.

w4s01 is the expand half of two changes:

- **order_lines.line_role** (FIN-03). Contract, once no image from before w4s01 can start: give any
  row still NULL its role (`UPDATE order_lines SET line_role = 'item' WHERE line_role IS NULL`, in
  batches), `SET NOT NULL` through a `CHECK (line_role IS NOT NULL) NOT VALID` + `VALIDATE`, set the
  column default to `'item'`, and drop the `fill_line_role` trigger and function.
- **The warehouses <-> storage_bins cycle** (DB-16). `warehouses.default_bin_id` points at a bin and
  `storage_bins.warehouse_id` at its warehouse, so neither table can be created, loaded or dropped
  before the other (the FK is added with `use_alter`). Release 1 (w4s01, this one) adds
  `storage_bins.is_default` (one per warehouse, partial unique index) and keeps it equal to
  `default_bin_id` with triggers; the app still reads and writes `default_bin_id`. **Release 2**, once
  no image from before w4s01 can start: check the flags still equal `default_bin_id` (the triggers
  keep them whichever image writes; re-copy them if not), switch
  `routers/bins.py` (`_default_bin_ids`, `_set_default`) and `services/stock_rows.py` (the default-bin
  lookup) to read and write `is_default`, drop the triggers and the `fk_warehouses_default_bin`
  constraint (that breaks the cycle), and drop `warehouses.default_bin_id` in a later release still
  (release-2 code no longer reads it, but a release-1 image does).

### Migrations and live traffic

- On Postgres every migration session runs with `lock_timeout` (`MIGRATION_LOCK_TIMEOUT`, default
  `5s`) and no `statement_timeout`. A migration that can't get its table lock quickly fails
  instead of queueing live queries behind it; rerun step 2 when traffic is quieter.
- Several migrations build indexes with `CREATE INDEX CONCURRENTLY IF NOT EXISTS` (aqp01, ix144dh01,
  ix144p4, id144p4, sc144eo01). If one of those builds fails, Postgres keeps the index but marks
  it INVALID, and a retry skips it because the name exists. After every `upgrade` the migration
  runner rebuilds any INVALID index with `REINDEX INDEX CONCURRENTLY` and logs it. That rebuild
  waits for older open transactions, bounded by the same lock timeout; if it gives up it logs a
  warning, the deploy continues, and the next upgrade retries. Leftovers of a
  failed REINDEX (`*_ccnew`, `*_ccold`) are only logged: drop those with `DROP INDEX CONCURRENTLY`.
  To check by hand: `SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;`

### Migrations that lock tables (plan a maintenance window)

Some migrations hold heavy locks for as long as they run. `MIGRATION_LOCK_TIMEOUT` only limits
how long they *wait* for a lock; once a migration holds one, the app's queries on that table queue
behind it until it commits. Time each one on a restored copy of production first (section 4).

| Migration | What is locked, and for how long | Window? |
|---|---|---|
| corg01 (#219) | One table at a time, briefly: adding the column is instant, the backfill commits every 5000 rows, the NOT NULL check is validated without blocking writes, indexes build `CONCURRENTLY`. | No. It runs outside a transaction: if stopped, rerun it, and it resumes. |
| w2b01 (#221) | One transaction: the data repairs, then the CHECKs (ACCESS EXCLUSIVE on stock_levels, invoices, time_entries, stock_transfers, gift_cards, coupons, purchase_returns, write_offs, postings, memberships, each for a full scan) and the unique indexes (SHARE on cash_shifts, time_entries, memberships, sales, stock_transfers, write_offs, purchase_returns, postings, coupons, gift_cards: reads go on, writes wait). All held until the end. | Yes at scale: the till (sales, stock, invoices) can't write meanwhile. Seconds at today's sizes. |
| dbi01 | One short step at a time, each committed on its own: devices/purchase_orders backfilled 5000 rows per batch and made NOT NULL through a CHECK added NOT VALID and VALIDATEd; 32 indexes built and 46 dropped `CONCURRENTLY`; 58 foreign keys added `NOT VALID` (a SHARE ROW EXCLUSIVE lock on the table and its parent for milliseconds, no scan: writes on those two wait that long) and then VALIDATEd (reads and writes go on). The two cascade swaps (credit_note_lines, serial_events) are one ALTER each (drop + add NOT VALID), then VALIDATE. | No. It runs outside a transaction: if stopped, rerun it, and it resumes. |
| dbi02 | Three nullable columns on orders and one on organisations (instant), tokens hashed 2000 orders per batch (row locks only), one unique index built `CONCURRENTLY`. | No. |
| dbi03 | order_lines.created_at added and given a default (both catalog-only, instant), then existing lines stamped 1000 jobs per batch, each batch its own transaction (row locks only). | No. |
| w4s01 | One short step at a time, each committed on its own: five columns added (catalog-only: nullable or a constant default), triggers created, repairs and backfills 5000 rows per statement walking the primary key (row locks only), 11 CHECKs added `NOT VALID` (a brief ACCESS EXCLUSIVE lock, no scan) then VALIDATEd (reads and writes go on), three partial unique indexes built `CONCURRENTLY`. | No. It runs outside a transaction: if stopped, rerun it, and it resumes. |
| dec08 (#222) | One transaction: 30 tables rewritten, one ALTER each (organisations, orders, order_lines, transactions, stock_movements, stock_levels, sale_lines, invoice_lines are the busy ones), all ACCESS EXCLUSIVE (no reads or writes) until the end. Peak extra disk: about one copy of each table. | **Yes.** Stop the app (or show maintenance) while it runs. |

w2b01, dec08, dbi01 and w4s01 check the data first and stop with counts (nothing changed) on what they
can't repair: w2b01 on unknown status values, dec08 on values NUMERIC can't hold (out of range,
±Infinity, NaN), dbi01 on rows that point at another shop's record, devices/POs that no link gives
a shop, links to transactions or required links that point at nothing, and payroll idempotency
keys used twice in one shop. Before deploying dbi01 run `python scripts/find_cross_shop_rows.py`
against production (read-only): it should print only `ok` lines; it lists the ids dbi01 only
counts. dbi01 sets other links that point at a row that doesn't exist to NULL (a dead id is all
that is lost) and logs the count per column (`alembic.dbi01`). w2b01 renames duplicate document numbers and gift-card/coupon codes (the oldest
keeps its code, the others get `-2`, `-3`…) and records each rename in the shop's audit log
(`gift_card.code_renamed`, `coupon.code_renamed`, `sale.sale_number_renamed`…): tell the shop to
check it, since a customer holding a renamed card or coupon must now use the new code.

w4s01 stops on a negative payment or prepayment, an unknown payment kind or transaction status, a
document line (sale, client return, transfer, purchase return, write-off, posting, credit note) with
qty 0, and an asset covered by two live memberships of one plan; it lists counts per table only.
It repairs, logging counts (`alembic.w4s01`): a refund stored negative is stored positive, and an
employee's duplicate open attendance shifts close
at their own start with 0 hours and `needs_review` (the newest stays open), as clock-out already did.
Run the "Duplicate open attendance shifts" query below before deploying it and check those employees'
hours.

### Duplicate open attendance shifts (check before and after deploy)

Before the clock-in lock, a double tap on "Clock in" could leave an employee with two open
attendance shifts. Run this before and after a deploy; it should return no rows:

```sql
SELECT e.org_id, s.employee_id, e.name, e.email, COUNT(*) AS open_shifts,
       MIN(s.started_at) AS oldest_start, MAX(s.started_at) AS newest_start
FROM shifts s
JOIN employees e ON e.id = s.employee_id
WHERE s.ended_at IS NULL
GROUP BY e.org_id, s.employee_id, e.name, e.email
HAVING COUNT(*) > 1
ORDER BY oldest_start;
```

Any rows are leaks from before the fix. The employee's next clock-out closes all of them: the
newest gets its worked hours and each older duplicate closes at its own start with 0 hours, so
payroll pays the time once. Check those employees' hours before running payroll for the period.

### Connection pooling (PgBouncer)

Periodic background jobs (notification and webhook sweeps) elect one runner across all workers
and replicas with a **session-level** Postgres advisory lock, held on one connection for the
whole run. That needs a real session: connect the app to Postgres directly, or through
PgBouncer in **session** pooling mode. PgBouncer **transaction** pooling hands the connection to
another client between statements, so the lock can be held by, or released on, the wrong session
and the same sweep can run twice. Migrations also need a direct (or session-pooled) connection:
`CREATE INDEX CONCURRENTLY` and `REINDEX CONCURRENTLY` run outside a transaction.

## 4. Backups and restore

**One-server setup:** `deploy/one-server/` has nightly off-site backups, a restore drill and a
one-command live restore built in; see its README. The notes below apply to any other setup.

Use a managed Postgres with **point-in-time recovery** (AWS RDS, GCP Cloud SQL, Neon,
Supabase…) and a retention of at least 7 days. In addition, take a nightly logical dump kept
off-site:

```bash
pg_dump --format=custom --no-owner "$DATABASE_URL_SYNC" > repairos-$(date +%F).dump
```

(`DATABASE_URL_SYNC` is the same URL without `+asyncpg`.)

**Restore drill (do it once now, then quarterly):**

```bash
createdb repairos_restore_test
pg_restore --no-owner --dbname=repairos_restore_test repairos-YYYY-MM-DD.dump
DATABASE_URL=postgresql+asyncpg://…/repairos_restore_test alembic -c backend/alembic.ini current
```

Then start a container against the restored DB and sign in. Write down how long it took — that
is your real recovery time.

`scripts/restore_drill.py` automates the restore and the checks (migration version, every table's
row count, the integrity sweep) and times each step; CI runs it on every push. Full procedure,
troubleshooting and the per-shop restore: [docs/runbooks/restore-drill-and-integrity.md](docs/runbooks/restore-drill-and-integrity.md).

**Integrity sweep (nightly, automatic).** The `integrity-sweep` job checks that every till balance,
client balance, invoice `amount_paid` and on-hand count equals the ledger it summarises. A mismatch
is logged at ERROR on `tekly.reconcile` and shown as `repairos_integrity_mismatches` in `/metrics`;
alert on either. It reads only. What to do when it fires: the runbook above.

## 5. Media (photos, signatures, attachments)

`MEDIA_BACKEND=db` (default) stores files in Postgres — simple, but it bloats the database and
its backups. `local` writes to `MEDIA_LOCAL_DIR` (default `/tmp`, **not durable** in containers;
production/staging refuse to boot with `local` under `/tmp` — mount a volume or use S3).
For production use S3 (or any S3-compatible store):

```
MEDIA_BACKEND=s3
MEDIA_S3_BUCKET=your-bucket
MEDIA_S3_REGION=eu-west-1
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY   (or an instance/task role)
```

Enable bucket versioning and keep the bucket private (the app streams files through
`/api/media/{id}` with access checks). Move existing inline media with
`POST /api/media/migrate-feed` after switching.

## 6. After go-live

- Run once against the production DB (both read-only):
  `python scripts/find_default_passwords.py` and `python scripts/find_duplicate_invoice_numbers.py`.
- Turn on error tracking and alerts (see `PROD-HARDENING.md`).

## 7. Closing a shop (offboarding)

`backend/services/offboarding.offboard_org` exports every row the shop owns (secrets left out) and
then switches it off: the shop is inactive with `offboarded_at` set, its staff are deactivated and
signed out, and its API keys stop working. Nothing is deleted: there is no hard purge, because
payments and invoices must usually be kept for years. If and when offboarded shops are purged is an
owner/legal decision still to be made. The docstring shows how to run it.
