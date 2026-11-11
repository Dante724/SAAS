# Tekly on one server

Everything on one small server for the beta: the app, Postgres, Redis, a Cloudflare Tunnel for the
public address, and a backup service that copies the database off-site every night.

```
visitors ──HTTPS──> Cloudflare ──tunnel (outbound only)──> cloudflared ─> app ─> Postgres
                                                                           └──> Redis
                    backup service ── nightly pg_dump ──> R2 bucket "tekly-backups"
                    app photos/files ───────────────────> R2 bucket "tekly-media"
```

No port is open to the internet except SSH: the tunnel dials out to Cloudflare, so there's no web
server to attack directly, and Cloudflare handles HTTPS certificates.

**Cost, roughly:** server about $10–20/month, Cloudflare free, R2 a few dollars, Sentry free.

## What you need first (owner)

| Account | What to create | Goes in `.env` as |
|---|---|---|
| Server (Hetzner, DigitalOcean…) | Ubuntu 24.04, 2 vCPU / 4 GB RAM, your SSH key | — |
| Cloudflare (free) | Add your domain. Zero Trust → Networks → Tunnels → *Create* (type cloudflared) → copy the token. Public hostname: `staging.<domain>` → `http://app:8000` | `CLOUDFLARE_TUNNEL_TOKEN` |
| Cloudflare R2 | Buckets `tekly-media` and `tekly-backups`. Two API tokens, each *Object Read & Write* on **one** bucket only | `AWS_*`, `BACKUP_S3_*` |
| Sentry (free) | A Python project and a React project | `SENTRY_DSN`, `VITE_SENTRY_DSN` |
| Email (Resend or Amazon SES) | SMTP credentials, sending domain verified | `SMTP_*` |
| Heartbeat (healthchecks.io free) | One check, period 1 day, grace 2 hours | `BACKUP_HEARTBEAT_URL` |

Type secrets into the server's `.env` yourself. Don't paste them into chat, email or tickets.

## First install

1. **Server basics** (as root):
   ```bash
   apt update && apt -y upgrade && apt -y install git ufw unattended-upgrades
   curl -fsSL https://get.docker.com | sh
   ufw allow OpenSSH && ufw --force enable          # only SSH in; the tunnel needs no inbound port
   adduser --disabled-password tekly && usermod -aG docker tekly
   ```
2. **Get the code** (as `tekly`):
   ```bash
   git clone <repo-url> tekly && cd tekly && git checkout roadmap/orderry-parity
   cd deploy/one-server
   ```
3. **Settings:** `cp .env.example .env && chmod 600 .env`, then fill it in.
4. **Secrets** (paste each output into `.env`):
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"          # SECRET_KEY
   python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"  # TOKEN_ENCRYPTION_KEY
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"          # POSTGRES_PASSWORD
   ```
   Keep a copy of `TOKEN_ENCRYPTION_KEY` in your password manager: without it, a restored backup
   can't read the shops' saved integration keys.
5. **Start:** `./deploy.sh`. It builds, starts, runs migrations and ends with a smoke test.

## Staging first, then production

1. With `ENVIRONMENT=staging` and Stripe **test** keys, run the load test from your laptop:
   ```bash
   python scripts/loadtest.py --base https://staging.<domain> --shops 6 --users 50 --duration 300
   ```
   (Behind the tunnel sign-up allows 10 per address per hour, so use at most 9 shops.) Watch
   `docker stats` on the server while it runs. Write down p95 and errors.
2. **Restore drill:** `docker compose exec backup backup-now.sh first && docker compose exec backup drill.sh`.
   Note how long the restore took: that's your real recovery time.
3. **Wipe the test data** before real shops arrive:
   ```bash
   docker compose down && docker volume rm tekly_pgdata tekly_backups
   ```
   Also empty both R2 buckets in the Cloudflare dashboard.
4. **Switch to production:** in `.env` set `ENVIRONMENT=production`, the real hostname in
   `PUBLIC_BASE_URL` and `CORS_ORIGINS`, and Stripe **live** billing keys. In Cloudflare, point
   the tunnel's public hostname at the real domain. Then `./deploy.sh`.

## Day to day

| Task | Command (in `deploy/one-server`) |
|---|---|
| Update to the latest code | `git pull && ./deploy.sh` (backs up first) |
| Roll back | `git checkout <previous commit> && ./deploy.sh` |
| Is it up? | `./smoke.sh` |
| Logs | `docker compose logs -f app` (or `tunnel`, `backup`, `db`) |
| Backup now | `docker compose exec backup backup-now.sh manual` |
| List backups | `docker compose exec backup list.sh` |
| Restore drill (quarterly) | `docker compose exec backup drill.sh` |
| Look at an old backup | `docker compose exec backup restore.sh <name> --into tekly_old` |
| **Disaster: restore live** | `./restore-live.sh latest` (asks you to type RESTORE) |

`restore-live.sh` never deletes the current database: it's kept as `tekly_before_<time>` until
you drop it yourself.

## Backups

- Every night at `BACKUP_HOUR_UTC` (03:00 UTC by default): `pg_dump`, then a check that the dump
  reads back, then an upload to R2 with a size check. Off-site copies older than
  `BACKUP_KEEP_DAYS` (30) are deleted, and the 3 newest stay on the server for fast restores.
- Every deploy takes a `pre-<commit>` backup first.
- If a night fails or is missed, the heartbeat check alerts you.
- **Encrypted backups (recommended for production):** on your laptop run `age-keygen -o tekly-backup.key`
  and keep that file safe off the server (password manager). Put its public key (`age1…`) in
  `BACKUP_AGE_RECIPIENT`. The server can then write backups but can't read them, which matters if
  the server is ever compromised. To restore, copy the key file to the server temporarily and run
  with `-e BACKUP_AGE_IDENTITY_FILE=/backups/tekly-backup.key`.

## What this setup doesn't cover yet

- **Point-in-time recovery:** you can lose up to a day of data, everything since the last nightly
  backup. Before paying shops depend on it, move to a managed Postgres with PITR (DEPLOY.md §4)
  or add WAL archiving.
- **Server failure:** one server means downtime while you rebuild. With the runbook above, a new
  server plus the latest backup is about 30–60 minutes of work.
- **Uptime alerts:** add an UptimeRobot or Better Stack monitor on `https://<domain>/api/readyz`.
