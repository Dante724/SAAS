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
| `FORWARDED_ALLOW_IPS` | yes behind a proxy | Your load balancer's IP/CIDR. Uvicorn then takes the client IP from `X-Forwarded-For` only from it. Leave `TRUSTED_PROXY_HOPS` at 0. |
| `WEB_CONCURRENCY` | no | Worker processes (default 1). More than 1 needs `REDIS_URL`, so rate limits are shared across workers; the app refuses to start otherwise. Background sweeps run once across all workers (Postgres advisory locks). |
| `REDIS_URL` | multi-instance | Shared rate-limit store. |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | no | Postgres connections per worker (default 10 + 10). Keep `WEB_CONCURRENCY` × their sum under the server's `max_connections`. `DB_POOL_TIMEOUT_S` (default 10) is how long a request waits for a free connection before failing. |
| `DB_STATEMENT_TIMEOUT_MS` | no | Postgres cancels any single statement that runs longer (default 15000). `DB_IDLE_IN_TX_TIMEOUT_MS` (default 60000) ends sessions left idle inside a transaction. Set to 0 behind a PgBouncer that rejects startup parameters, and set them on the database role instead. |
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
5. **Roll back** by starting the previous image tag. Migrations are additive; only run
   `alembic downgrade` if a release notes says it's required.

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
