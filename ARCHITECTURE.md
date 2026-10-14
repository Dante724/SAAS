# RepairOS — Architecture

A repair-shop management SaaS: FastAPI + SQLAlchemy 2.0 (async) backend with a
dependency-free vanilla-JS SPA, plus an installable technician PWA.

## Backend

- **Entry**: top-level `main.py` builds the FastAPI app — loads settings,
  configures logging, registers every router, mounts middleware/CORS, defines
  `lifespan` (DB init → optional demo seed → job runner start/stop), health
  endpoints, the PWA asset routes, and an SPA catch-all.
- **Config** (`backend/config.py`): `pydantic-settings`. Derived
  `is_production`/`is_sqlite`/`cors_origin_list`; a `model_validator` **fails
  closed in production** (placeholder/short `SECRET_KEY` or wildcard CORS raise).
  Provider credentials absent ⇒ that integration runs in simulated/stub mode
  (Stripe, QBO, AI, Twilio, **Web Push/VAPID**).
- **Database** (`backend/db/database.py`): driver-aware async engine. In
  production Alembic owns the schema (no runtime DDL); in dev it `create_all`s.
- **Models** (`backend/models/__init__.py`): all tables (78) + an `all_models`
  registry used by metadata.
- **Routers** (`backend/routers/*`): orders, statuses, public/portal, feed,
  timeclock, payments, inventory, integrations, **tech**, **push**, auth, etc.
- **Services** (`backend/services/*`): cross-router logic — `notifications`,
  `webhooks`, `sla` (per-status SLA sweep), **`push`** (staff notifications +
  Web Push), etc.
- **Core** (`backend/core/*`): structured logging, request-context middleware
  (`x-request-id`, access log, 500 guard), a **job runner** (`job_runner`:
  enqueue with retry/backoff + `@every(interval,name)` periodics) and
  `scheduled.py` periodics (`status-sla-sweep`, `webhook-retry-sweep`).
- **Migrations** (`backend/migrations/`): async Alembic; CI builds the schema
  from empty and asserts zero model/DB drift.

## Auth & tenancy
JWT `{sub: employee.id, org_id}`; `get_current_employee` resolves the employee,
`require_perm(perm)` gates writes (default-allow; null role = owner). Every query
is scoped by `org_id`. `POST /api/auth/refresh` issues a sliding-session token
for the PWA.

## Frontends

### Desktop SPA (`/`)
`frontend/index.html` + `frontend/static/js/core.js` (global `Auth`, `get/post/
put/patch/del`, `apiLogin`, `Toast`, `Modal`, `fmt`, `icon`) and self-registering
pages via a hash `Router`. Build-less — no bundler.

### Technician PWA (`/tech`)
A **separate installable app** with its own mobile-first shell
(`frontend/tech.html`), controller (`static/js/tech/app.js`), dashboard
(`static/js/tech/dashboard.js`), a root-scoped **service worker** (`sw.js`), and
the **PWA runtime** (`static/js/pwa.js`: registration/update/install, encrypted
IndexedDB offline queue, Background Sync, Web Push enrolment). It reuses
`core.js` + auth, so it shares the backend with the desktop app but degrades
gracefully offline. See `docs/mobile-technician.md`.

```
Browser (phone/tablet)
  ├─ /tech  ── tech.html ── core.js + pwa.js + tech/app.js + tech/dashboard.js
  │              │            └─ IndexedDB queue (AES-GCM) ──┐ replay on reconnect
  │              └─ registers /sw.js (cache shell, network-first API, push)
  └─ /      ── index.html ── core.js + pages/*  (desktop SPA)
                 │
FastAPI (main.py) ── routers ── services ── SQLAlchemy async ── Postgres/SQLite
                 └─ job_runner (SLA sweep, webhook retry, push dispatch)
```

## Offline & sync model
Reads are served network-first with a cache fallback (assigned jobs open
offline). Writes go through `RosPWA.mutate`, which tries the network first and,
on failure/offline, encrypts and enqueues the mutation in IndexedDB. On
reconnect (online event, Background-Sync wake, or launch) the queue replays with
the Bearer token; conflicts (`409`) are surfaced and dropped, transient `5xx`
are retried, and sync state is shown in-app.

## Testing & CI
`pytest` (29 tests: smoke, tenant isolation, work-order management, technician).
`ruff` lint, Alembic build + drift check, and three QA harnesses (catalog 26,
phase 2/3 47, now-phase 70) run on each packaging. JS is `node --check`ed; a
browser/Lighthouse plan lives in NEXT_TASK.md.
