# RepairOS — production hardening checklist (build 144cu)

Build, configure, migrate and back up: see `DEPLOY.md`.

Code-side items from the OWASP audit are fixed in the app. The rest are one-time
deployment steps — do these before opening to real customers.

## Fixed in code
- [x] SSRF guard on outbound webhooks — validated on save and on every delivery, pinned to the checked IP (security phase 1)
- [x] Upload size cap, API docs disabled in prod, log redaction (144cq)
- [x] Demo credentials removed from the login page when `ENVIRONMENT != development` (144cu)
- [x] Shared rate-limit store via `REDIS_URL` for multi-instance (144ct)

## Do on the server (one-time)
- [ ] **Set `ENVIRONMENT=production`** — enables fail-closed secret/CORS checks, hides `/docs`, strips demo creds.
- [ ] **Set a strong `SECRET_KEY`** (≥32 chars): `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Rotate the old dev key that once leaked in a tarball `.env`.
- [ ] **Set `CORS_ORIGINS`** to your real domains (no `*`).
- [ ] **Set `REDIS_URL`** if running more than one instance.
- [ ] **DB least-privilege** — the app's Postgres role gets CRUD on its tables only, no superuser/DDL. Migrations run as a separate admin role via `alembic upgrade head`.
- [ ] **Staging auth** — put HTTP auth / IP allowlist in front of any non-prod deploy; never load prod data there.
- [ ] **CI secret masking** — mark all secrets masked; never `echo` them.
- [ ] **Backups** — automated DB backups + a tested restore. Runbook: `DEPLOY.md` §4.
- [ ] **TLS + HSTS** at the edge (the app already sends security headers).
- [ ] Scan history with `gitleaks` before making the repo public.

## Accepted (deliberate, revisit later)
- RBAC is default-allow — flip specific sensitive actions to default-deny per feature, not globally.
- CSP keeps `script-src 'unsafe-inline'` because the UI uses inline handlers; move to nonces in a later pass.
