# RepairOS — Production Readiness Audit
_Senior staff engineering review. Baseline: repairos-27 (287 routes, 278 API endpoints)._

## 1. Current architecture

```
                       ┌──────────────────────────────────────────┐
   Browser SPA  ──────▶│  FastAPI app (main.py)                    │
   (vanilla JS,        │  • CORS  • StaticFiles  • SPA catch-all   │
    Router.go)         │  • lifespan: init_db + seed_demo (!)      │
                       └───────────────┬──────────────────────────┘
                                       │ includes 38 routers (/api/*)
        ┌──────────────────────────────┼───────────────────────────────┐
        ▼                               ▼                               ▼
  Routers (backend/routers/*)    Services (backend/services/*)    Models (backend/models)
  thin HTTP layer, Depends       auth(JWT), webhooks(HMAC),       76 ORM classes, 1 file
  (get_db, get_current_employee) notifications, ai, payments,     org_id-scoped (multi-tenant)
        │                        integrations_qbo, transactions         │
        └───────────────┬────────────────┬───────────────────────────────┘
                        ▼                 ▼
                AsyncSession (SQLAlchemy 2.0 async)
                        │
                        ▼
        SQLite (dev, aiosqlite)  ──prod target──▶  PostgreSQL (asyncpg)
                        ▲
                  init_db(): create_all + _sync_columns  ← runtime DDL (to replace)

External seams (credential-gated, simulated when keys absent):
  Stripe • Twilio (SMS/WhatsApp) • SMTP • QuickBooks OAuth • Anthropic AI
```

Request flow: SPA → `/api/*` (Bearer JWT) → router → `Depends(get_current_employee)` / `require_perm` → service/model → async DB. Public surfaces: `/api/public/*`, `/api/v1/*` (API-key auth), `/track/{token}`, `/book/{slug}`.

## 2. Folder structure (actual)

```
repairos/
├── main.py                  # app entry (top-level, NOT backend/main.py)
├── requirements.txt         # dev/sqlite pins  ──┐ DIVERGENT (see §5)
├── docker-compose.yml       # postgres compose    │
├── backend/
│   ├── requirements.txt     # prod/postgres pins ─┘
│   ├── config.py            # pydantic-settings Settings (used by auth+db)
│   ├── alembic.ini          # configured, but NO migrations/ dir yet
│   ├── pytest.ini           # asyncio_mode=auto
│   ├── __init__.py
│   ├── db/database.py       # engine, get_db, Base, init_db, _sync_columns
│   ├── models/__init__.py   # 76 models + all_models registry (1325 LOC)
│   ├── routers/*.py         # 37 router files (thin HTTP)
│   ├── services/*.py        # auth, webhooks, notifications, ai, payments…
│   └── tests/               # conftest.py + test_orders.py (BROKEN — see §3)
├── scripts/seed.py          # seed_demo_data() (runs every startup)
├── frontend/
│   ├── index.html           # SPA shell + <script> manifest
│   ├── track.html, book.html
│   └── static/{css,js,js/pages(30)}
├── docs/                    # api + integration docs
└── qa_*.py                  # 5 standalone harnesses (179 checks, not pytest)
```

## 3. Tech stack

Backend: Python 3, FastAPI, SQLAlchemy 2.0 (async), Pydantic v2 + pydantic-settings, python-jose (JWT), passlib+bcrypt, async drivers aiosqlite (dev) / asyncpg (prod), openpyxl (xlsx). Frontend: dependency-free vanilla JS SPA (custom Router/Modal/Toast), CSS design-token theme; no build step. Tests: pytest + pytest-asyncio + httpx ASGI; plus 5 bespoke QA harnesses. Infra: Alembic (configured, unused), docker-compose (postgres).

## 4. Existing features

Work orders/estimates (+convert), clients & devices, inventory (parts, stock levels, **bins, serials, batches/FEFO, reservations, transfers, conversions, write-offs**), product catalog (variants, categories, barcode gen, import/export), services & bundles, sales/POS, finance & cash (double-entry transactions), payroll/wages, time clock, bookings, leads, chats (WhatsApp/SMS seam), reports (catalog incl. labor/utilization, xlsx+csv export), feedback, public booking/tracking pages, document templates, forms editor, statuses, notifications (multi-channel seam), public REST API v1 (API keys), webhooks (HMAC signed), integrations (Stripe, QBO, Twilio, AI). Multi-tenant via `org_id` on every domain table.

## 5. Missing dependencies (defects)

- **`openpyxl` is imported but absent from BOTH requirements files** — `catalog_products.py` import/export and reports xlsx will `ModuleNotFoundError` on a clean prod install. (P0)
- **Two divergent requirements files** with conflicting pins (fastapi 0.111 vs 0.115, sqlalchemy 2.0.30 vs 2.0.36, etc.). No single source of truth. (P0)
- Root file mixes runtime + test deps (no `requirements-dev`).
- Optional SDKs (`anthropic`, `stripe`) not declared though referenced as seams; acceptable only because they're guarded, but should be explicit extras.
- `asyncpg` not installable/installed in the dev sandbox (fine for sqlite dev; required for prod).

## 6. Security issues

- **CORS `allow_origins=["*"]` with `allow_credentials=True`** — invalid+unsafe combo; must be an env-driven allowlist. (P0)
- **Demo data (incl. `admin@repairos.dev / admin123`) seeded on every startup**, unconditionally — would create a known-credential admin in production. (P0)
- **`SECRET_KEY` has a usable default** (`dev-secret-key-change-in-production`); app boots in prod with a guessable JWT signing key. Must fail-closed when `ENVIRONMENT=production`. (P0)
- No security headers, no rate limiting on auth, no request-size limits.
- `python-jose` + `passlib(bcrypt 1.7.4)` need the bcrypt==4.0.1 pin workaround — fragile; document/pin.
- SPA catch-all returns `index.html` (200) for unknown `/api/*` paths — masks 404s and can leak HTML to API clients. (P0 routing)

## 7. Technical debt

- **`_sync_columns` performs runtime `ALTER TABLE`** on startup (SQLite-only DDL, additive). Replace with Alembic. (P0)
- **`connect_args={"check_same_thread": False}`** is hardcoded — **breaks asyncpg/Postgres** the moment `DATABASE_URL` is non-sqlite. (P0)
- 76 models in one 1325-line file; no domain packages.
- No logging configuration; relies on `print()` (seed, auto-migrate).
- No `/health`/`/ready` endpoints.
- No background-job mechanism; webhook delivery + notifications run inline in request path.
- Tests broken & not in CI; QA harnesses separate from pytest.
- No CI, no linter config, no pre-commit.

## 8. Database schema (76 models, org-scoped)

Core: Organisation, Location, Employee, Role, Shift, WageRecord. Sales/work: Client, Device, Order, OrderLine, Estimate↔Order conversion, Sale, Payment, PaymentIntent. Catalog: **Category** (shared tree: product|service|bundle), Part (+variants via parent_id/variant_attrs), Service, Bundle, BundleComponent, PriceType, Tax. Inventory: StockLevel, StorageBin, SerializedItem, StockItemBatch (expiry/FEFO), StockMovement, StockReservation, PurchaseOrder. Finance: Account, Transaction (double-entry), CashFlowItem. Engagement: Booking, Lead, ChatThread, ChatMessage, EventFeedItem, Notification, Feedback. Platform: ApiKey, WebhookEndpoint, WebhookDelivery, QboConnection, IntegrationMapping, IntegrationSyncLog, DocumentTemplate, Form, Status. Every domain row carries `org_id` (tenant key). PKs are string UUIDs.

## 9. API endpoints

278 API endpoints across 38 routers under `/api`. Auth: `POST /api/auth/token` (OAuth2 password form) → JWT; `get_current_employee` + `require_perm(perm)` (default-allow; null role = owner). Representative groups: `/api/orders*`, `/api/clients*`, `/api/inventory*`, `/api/catalog/{products,services,categories,…}`, `/api/bundles`, `/api/sales`, `/api/finance`, `/api/cash`, `/api/reports/{catalog,run,export,export-csv}`, `/api/chats*`, `/api/webhooks*`, `/api/api-keys`, `/api/payments*`, `/api/integrations/qbo/*`, `/api/ai/*`. Public: `/api/public/*`, `/api/v1/*` (API-key). No `/health` (gap).

## 10. Frontend architecture

Build-less vanilla-JS SPA. `index.html` loads `core.js` (fetch helpers `get/post/put/patch/del`, `Auth.token` from localStorage, `Toast`, `Modal`, `fmt`, `confirm2`), `icons.js`, `app.js` (Router: `register(name,fn)`/`go(name)`, title map, nav active state), then 30 page modules in `static/js/pages/*` that self-register routes and render via global `setContent`/`setTopbarActions`. Nav groups: **My Company** (Dashboard, Products, Services, Bundles), Overview, Operations, Insights (Reports, Settings). Settings is a sub-rail hub (General, Employees, …, Integrations, API keys, Webhooks). No state library; server is source of truth; JWT in localStorage.

---

# Prioritized implementation plan

**Phase 0 — Production foundation (THIS PASS).** Fix imports & routing; fix failing tests + add smoke tests; replace `_sync_columns` with Alembic; remove hardcoded secrets + fail-closed config; env configuration; structured logging; background-jobs foundation; verify/enforce tenant isolation; CI-ready (workflow, Makefile, merged deps, linter). Exit criteria: `pytest` green, `alembic upgrade head` builds schema, app boots with no default secret in prod mode, CI workflow runs lint+tests.

**Phase 1 — Hardening.** Security headers + auth rate limiting + request limits; move webhook/notification dispatch onto the job runner with retries/backoff; split models into domain packages; per-tenant DB query audit + automated cross-tenant tests; observability (metrics, Sentry seam).

**Phase 2 — Scale & ops.** Connection pooling/tuning for Postgres; read replicas seam; caching; pagination audit; container image + healthcheck + migration init-container; blue/green or rolling deploy; backup/restore runbook.

**Phase 3 — Product depth (existing roadmap).** Technician PWA/field mode; true scannable barcodes; e-commerce import; telephony/call logging; AI photo-to-product.

Severity legend used in CHECKLIST.md: P0 blocks production; P1 needed before scale; P2 quality.
