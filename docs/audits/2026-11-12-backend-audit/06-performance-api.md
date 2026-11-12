# 06: Performance, scalability, API inventory, reports

**Snapshot:** `roadmap/orderry-parity` @ d353c67b (`scratchpad/audit-src`). Read-only.
**Appendix:** full route table in `06-api-inventory.md`, next to this file.

**Probes** (all in `scratchpad/probes/`, run on throwaway SQLite, nothing written to the tree):

| Probe | What it does |
|---|---|
| `route_inventory.py`, `inventory_md.py` | Import `main.app` and walk every route's dependency tree |
| `index_probe.py` | Model indexes and FK columns with no index |
| `unbounded_scan.py`, `nplus1_scan.py` | AST hint scans |
| `pt/test_perf_probe.py` | SQL statement count, bytes and SQL text for 64 endpoints, at 3 and 15 seeded jobs |
| `pt/test_explain_probe.py` | SQLite `EXPLAIN QUERY PLAN` of the captured statements |
| `pt/test_tz_probe.py` | Timesheet overtime on a New York shop |
| `pt/test_track_probe.py` | Public tracking payload with photos |

The pytest probes ran with `-p backend.tests.conftest`, with `DATABASE_URL` pointed at the scratchpad.

## Summary

| Severity | PERF | RPT |
|---|---|---|
| CRITICAL | 0 | 0 |
| HIGH | 3 | 1 |
| MEDIUM | 17 | 4 |
| LOW | 3 | 3 |

**Top items:**
- PERF-01: the public tracking page returns every client-visible photo inline, with no bound, and polls every 20 s. Measured 119.7 MB in one unauthenticated response.
- PERF-02: S3 media uses synchronous boto3 inside async handlers.
- PERF-03: product photos (`parts.image_data`) are dragged through stock reports and lists.
- RPT-01: timesheet and overtime pay are grouped by UTC day; the window is read as UTC. Measured: an evening shift is missing and overtime shows 0 instead of 2.5 h.

---

## 1. API and backend inventory

### 1.1 Routes (test-verified by import)

**1,119 routes** (method + path) in 93 router modules plus `main.py`. FastAPI 0.141 registers routers as `_IncludedRouter`, so `app.routes` only shows 28 routes. The script walks `original_router.routes`.

| Auth dependency | Routes |
|---|---|
| employee (`services/auth.get_current_employee`, JWT) | 910 |
| API key (`services/apikeys.require_scope`) | 71 (v1 / v2 / v2_records / v2_bookings) |
| none | 71 (SPA shells, health, `/metrics` token-checked inside, public booking, OAuth callbacks, unsigned-by-dependency webhooks) |
| platform admin (`services/platform_auth.get_current_platform_admin`) | 30 (`/api/admin/*`) |
| public token in path, checked in handler | 25 (track, portal, feedback, unsubscribe, check-in) |
| public, with an in-handler signature, secret or login check | 12 (Stripe/Square/Twilio/Meta webhooks, logins) |

- Of the 910 employee routes, **254 have no declarative permission gate**: no `require_perm`, `record_scope` or inline `assert_perm`. Many are reads, or check permissions some other way. The per-module counts are in the appendix.
- Largest modules: orders 68, inventory 48, admin 34, v2 31, clients 28, staff 28, main 28, public 27, finance 24, timeclock 24, documents 23, payroll 23, v1 23. All 94 modules are in the appendix.

### 1.2 Periodic jobs (`backend/core/scheduled.py`, runner in `backend/core/jobs.py`)

There are 17 periodics, all registered with `@job_runner.every`:

| Job | Interval |
|---|---|
| status-sla-sweep | 600 s |
| notification-sweep | 120 s |
| notification-outbox | 30 s |
| automation-time-sweep | 1 h |
| contact-lifecycle-sweep | 1 h |
| membership-sweep | 1 h |
| webhook-retry-sweep | 60 s |
| revoked-session-sweep | 1 h |
| chat-autocomplete-sweep | 15 min |
| retention-purge | 24 h |
| passcode-encrypt-sweep | 24 h |
| idempotency-sweep | 1 h |
| superseded-charge-sweep | 10 min |
| sense-brief-sweep | 15 min |
| sense-agents-sweep | 1 h |
| integrity-sweep | 24 h, `delay_first` |
| task-overdue-sweep | 15 min |

How they run:
- Each runs in every web worker, in-process.
- On Postgres, `run_exclusive` (`jobs.py:109-138`) elects one runner with a session advisory lock.
- Every job except integrity-sweep runs immediately at every boot.
- `enqueue` is used only by the webhook outbox (`services/webhooks.py:495`), which is backed by a DB outbox plus the retry sweep, so nothing durable lives only in memory.

### 1.3 DB triggers, functions and row-level security (code-read, 249 migrations)

- The **only** trigger/function is `admin_action_log` append-only: a PG `admin_action_log_append_only()` plpgsql function with a BEFORE UPDATE OR DELETE trigger, plus SQLite equivalents (`migrations/versions/ad144s1_admin_panel_sprint1.py:17-28`).
- No stored procedures. No `CREATE POLICY` or `ENABLE ROW LEVEL SECURITY`, and no `set_config` / `current_setting`. **Postgres row-level security is not used at all**: tenancy is application-level only (`db/tenancy.py`).
- One special index: GIN on `tasks.assignee_ids` (`tk02_tasks_myday.py:160`). No `pg_trgm`.

### 1.4 External integrations (code-read)

| Integration | Where | Client style |
|---|---|---|
| Stripe and Square (payments, terminal, billing) | `services/payments.py`, `terminal.py`, `billing.py` | async `httpx` |
| Twilio SMS/WhatsApp | `services/notifications.py:224-262` | async `httpx` |
| Twilio voice | `services/twilio_voice.py` | async `httpx` |
| Email over SMTP | `services/notifications.py:107-180` | `smtplib` in `asyncio.to_thread` |
| Email over OAuth mailbox | `services/email_oauth.py` | async `httpx` |
| QBO | `services/qbo.py` | async `httpx` |
| Xero | `services/xero.py` | simulated (no outbound HTTP) |
| AI (Anthropic Messages API) | `services/ai.py:56-84` | async `httpx` |
| Web Push | `services/push.py:111` | pywebpush in `to_thread` |
| Aircall | `routers/telephony.py` | inbound webhook only |
| e-invoice | `services/einvoice.py` | local UBL XML render, no transmission |
| Outbound webhooks | `services/webhooks.py` | async `httpx`, SSRF-pinned |
| **S3 media** | `services/media.py` | **synchronous boto3** (PERF-02) |

### 1.5 File storage (`services/media.py`)

- `MEDIA_BACKEND` is `db` (the default, base64 in `media_blobs.inline_data`), `local`, or `s3`.
- S3 falls back to `db` when no bucket is set (`media.py:47-51`).
- Only these use `media.store`:
  - feed uploads, when not `db` (`routers/feed.py:160`);
  - `/api/media` uploads;
  - PO supplier invoices (`inventory.py:2046`);
  - the check-in page (`checkin_public.py:275`).
- These stay inline in the DB whatever the backend:
  - `parts.image_data`, `devices.image_data`, `organisations.logo_data`;
  - `orders.signature`, plus the feed `media_data` copy of it;
  - `recommendations.media_data`;
  - feed items written with `media_data=` by public/orders signature paths.
- See PERF-06.

### 1.6 Caching

| Cache | Where | Shared across workers? |
|---|---|---|
| Dashboard tiles, 30 s | Redis `dash:{org}` or in-process `_dash_cache` (`routers/reports.py:26-59`), `/api/reports/dashboard` only | Yes with Redis |
| `sense._HISTORY_CACHE`, 10 min | in-process (`services/sense.py:553`) | No |
| `cost_redaction._role_cache`, 30 s | in-process | No |
| API-key bucket cache, 60 s | in-process (`core/observability.py:183`) | No |
| OpenAPI doc caches | in-process | No |

- Redis holds only rate limits and the dashboard cache.
- There is no HTTP caching: media is served `Cache-Control: private, no-store` (`routers/media.py:93`), and track/portal responses send no ETag.
- `/api/reports/company-dashboard` (the Home page) is **not** cached.

---

## 2. Query-count probe results (test-verified)

Statement counts per GET: 3 seeded jobs, then 15. Each job has a client, two lines, a card payment, a client-visible 200 KB photo, a counter sale, a PO and a closed status.

**Flat counts (no N+1), 3 → 15 jobs.** These 64 endpoints were checked; most are flat:

| Statements | Endpoints |
|---|---|
| 4–12 | orders list (9, with facets 12), orders search (11), board (8), clients (8), inventory parts (5), catalog products (8), invoices (9), cash transactions (10), sales (6), returns (4), tasks (7), notification log (4) |
| 7–20 | 26 `/api/reports/run/*` builders |

**Heavier endpoints:**

| Endpoint | Statements |
|---|---|
| `/api/reports/company-dashboard` (Home) | **65** |
| `/api/reports/total-profit` | 74 |
| `/api/analytics/location-comparison` | 40 |
| `/api/analytics/business-insights` | 33 |
| `/api/finance/reports/summary` | 27 |
| `/api/finance/pnl` | 26 |

**Not flat:**
- `/api/reports/run/purchase_list`: 12 → 24.
- `/api/reports/run/reorder_forecast`: 14 → 26 (PERF-11).

**SQLite EXPLAIN highlights:**
- `lead_sources`: `SEARCH payments USING INDEX ix_payments_paid_at (paid_at>? AND paid_at<?)`, a range over every shop's payments.
- Home dashboard: `SCAN accounts` (no `accounts.org_id` index).
- Insights payments: `SCAN accounts`, then `payments USING INDEX ix_payments_account_id` with no date bound.
- Orders search: org index, then every row evaluates nested `replace()` LIKE in three subqueries.

**Postgres:** none of the plans were checked on Postgres. See section 6.

---

## 3. Findings

### PERF-01 [HIGH] Public tracking page returns every client-visible attachment inline, unbounded, polled every 20 s
- **Module:** public tracking and portal (`routers/public.py`), frontend `public/PublicTrack.tsx`.
- **Tables/columns:** `event_feed_items.media_data` (Text, base64 data URL), `visibility`, `entity_id`.
- **Functions/APIs:**
  - `public.py:293-296` `track`;
  - `public.py:299` `_track_payload`;
  - feed query at `public.py:314-320`, which has **no limit**;
  - serialisation at `public.py:480-484` (`"media_data": f.media_data if f.item_type in ("photo", "signature", "file")`);
  - `PublicTrack.tsx:153` (`setInterval(() => load(true), 20000)`).
  - The same payload also serves the portal job view (`_track_payload` docstring).
- **Execution flow:**
  1. The customer opens `/track/{token}`.
  2. The page calls `GET /api/public/track/{token}` (no auth) every 20 s while visible.
  3. `_track_payload` loads every client-visible feed item, newest first, with no `LIMIT`.
  4. Each photo, signature or file's whole base64 goes in `updates[]`.
- **Evidence (test-verified, `probes/track_probe.json`):**
  - An order with 2 client-visible 200 KB photos returns **401,557 bytes**.
  - After the probe loop uploaded 598 photos (until the 429), one unauthenticated GET returned **119,689,765 bytes**.
  - **25 SQL statements** per poll.
  - No `ETag`, no `Cache-Control`.
  - When `MEDIA_BACKEND=s3`, `media_data` is NULL (`feed.py:160-166`) and the payload carries no `media_url`, so offloaded photos **do not appear** on the public page. Code-read: the serializer has no `media_blob_id` branch.
- **Verification level:** test-verified (size, query count); code-read (S3 gap).
- **Why it's a problem:**
  - Photos are the main thing customers get on this page, and every poll re-sends all of them from the DB through the app.
  - It is unauthenticated, so a token holder can pull hundreds of MB repeatedly.
- **Real-world failure scenario:**
  - A bodyshop job with 40 phone photos of about 2 MB each gives an 80 MB JSON every 20 s per open tab.
  - A few customers with the page open saturate a worker's memory and the DB, and the 4 GB one-server host OOMs. With S3 switched on, customers instead see no photos at all.
- **Recommended fix:**
  - Return `media_url` (a signed, short-lived public URL, or a token-scoped `/api/public/track/{token}/media/{id}`) instead of inline data.
  - Cap or paginate `updates`.
  - Add an ETag / `If-None-Match` (hash of `updated_at` + feed max id) so polls return 304.
- **Fix dependencies/risks:**
  - The new public media route must check the token, item visibility and org.
  - The frontend `PublicTrack`/`PublicPortal` need to render URLs.
  - M1 (`javascript:` URLs) still applies to the URL form.

### PERF-02 [HIGH] S3 media backend does synchronous boto3 I/O on the event loop, with a new client per call and no caching
- **Module:** media storage.
- **Tables/columns:** `media_blobs.storage_key`, `backend`.
- **Functions/APIs:**
  - `services/media.py:54-85` `store`: `boto3.client(...).put_object` at `:76`;
  - `:88-102` `load`: `boto3.client(...).get_object(...)["Body"].read()` at `:97`;
  - `:105-126` `erase`: `delete_object` at `:121`;
  - `routers/media.py:55-66` `get_media` (`raw, mime = await load(blob)`);
  - `routers/media.py:93` `"Cache-Control": "private, no-store"`.
- **Execution flow:**
  1. Every photo view (`GET /api/media/{id}`) and every upload runs inside the async handler.
  2. That handler builds a boto3 client (credential/endpoint resolution) and does a blocking HTTPS round trip to S3/R2, reading the whole object (up to 8 MB) into memory.
  3. Nothing is offloaded with `to_thread`.
- **Evidence:** `async def load(...)` contains no `await` on the S3 path. Contrast: SMTP uses `asyncio.to_thread` (`notifications.py:180`) and push uses `to_thread` (`push.py:111`).
- **Verification level:** code-read. Latency NOT VERIFIED (needs staging against R2).
- **Why it's a problem:**
  - Each uvicorn worker is a single event loop; one-server runs `WEB_CONCURRENCY=2`.
  - While boto3 waits on the network, that worker serves no other request from any shop.
  - `no-store` means browsers re-download every thumbnail on every view.
- **Real-world failure scenario:**
  1. A work-order board or gallery renders 30 thumbnails.
  2. That is 30 sequential blocking S3 GETs of about 100–300 ms each per worker.
  3. Every other tenant's requests on that worker stall for seconds, and health checks time out.
- **Recommended fix:**
  - Use `aioboto3`, or wrap the calls in `asyncio.to_thread`.
  - Create the client once and reuse it.
  - Better: redirect to presigned S3/R2 URLs (or a CDN) after the access check, and send `Cache-Control: private, max-age=…` for content-addressed blobs.
- **Fix dependencies/risks:**
  - Presigned URLs must be short-lived.
  - Keep the `safe_media_response` content-type rules by setting `ResponseContentType` / `ResponseContentDisposition` on the presign.

### PERF-03 [HIGH] Product photos (`parts.image_data`, up to 400 KB each, inline) are loaded by catalogue-wide reports and lists
- **Module:** inventory, reports.
- **Tables/columns:**
  - `parts.image_data` (Text data URL, not deferred: `models/__init__.py:979`; cap `MAX_IMAGE_BYTES = 400*1024`, `catalog_products.py:396`);
  - `stock_levels`, `stock_movements`.
- **Functions/APIs:**
  - `routers/reports.py`, each running `select(Part)` or `select(X, Part)` with no `defer`:
    - `:538` `_rep_stock_levels` (all active parts);
    - `:500` `_rep_product_turnover` (`select(StockMovement, Part)`: one Part per **movement row**);
    - `:527` `_rep_inventory_writeoffs`;
    - `:688` `_rep_assortment` and `:839` `_rep_stock_valuation` (`select(StockLevel, Part)`);
    - `:716` `_rep_purchase_list`;
    - `:759` `_rep_expiration`;
    - `:869` `_rep_dead_stock`;
    - `:901` `_rep_reorder_forecast`.
  - `routers/inventory.py:2276-2290` `reorder_list` (whole catalogue plus stock levels, filtered in Python).
  - `routers/inventory.py:85-99` `_ser_part` **serialises** `image_data` for every row of `GET /api/inventory/parts` (limit up to 500).
  - The only place it is deferred: `catalog_products.py:286` (`defer(Part.image_data)`).
- **Execution flow:**
  1. The Reports › Inventory tab, or the reorder list, runs one query that selects every column of every active part.
  2. SQLAlchemy materialises them.
  3. Python sums stock and discards the photo.
- **Evidence (test-verified, SQL capture in `probes/perf_probe.json`):** the statement issued by `/api/reports/run/stock_levels` contains `parts.image_data`. Others are code-read.
- **Verification level:** test-verified for stock_levels; code-read for the rest.
- **Why it's a problem:**
  - Memory and DB transfer grow with catalogue × photo size, not with the result.
  - `product_turnover` multiplies it by the number of movement rows in the period: each row re-fetches the column, even though the identity map de-duplicates the objects.
- **Real-world failure scenario:**
  1. A parts-heavy shop has 8,000 SKUs with photos (about 150 KB average).
  2. Opening "Stock levels" pulls about 1.2 GB through asyncpg into a worker on the 4 GB host.
  3. The worker is OOM-killed; every shop on it gets 502s.
- **Recommended fix:**
  - `deferred=True` (or `deferred_raiseload`) on `Part.image_data`, `Device.image_data`, `Organisation.logo_data`, `Order.signature`, `EventFeedItem.media_data` and `Recommendation.media_data` at the mapper level, with `undefer` only in detail endpoints.
  - Better still, move them to `media_blobs` (PERF-06).
  - Select only the needed columns in reports.
- **Fix dependencies/risks:**
  - Mapper-level deferral changes lazy-load behaviour: async sessions raise `MissingGreenlet` on access, so tests are needed for every serializer that reads these columns.

### PERF-04 [MEDIUM] Clients list and client detail load full Device rows (photos, encrypted passcodes) and every order
- **Module:** clients.
- **Tables/columns:** `devices.image_data`, `devices.passcode_enc`, `orders.*`.
- **Functions/APIs:**
  - `routers/clients.py:136-139` `list_clients`: `.options(selectinload(Client.devices))`;
  - `_ser_client` only emits id/brand/model/serial/imei/vin/hin/condition/custom_data (`:96-98`);
  - `clients.py:306-311` `get_client`: `selectinload(Client.devices), selectinload(Client.orders)`, i.e. every order the client ever had, as full rows;
  - `clients.py:581-623` `client_purchases`: every order with lines, unbounded.
- **Execution flow:** `GET /api/clients?search=…` (pickers call it per keystroke) → page of 50 clients → SELECT of all their devices with every column.
- **Evidence (test-verified, SQL capture):** the `/api/clients?search=5550` capture contains `devices.image_data AS devices_image_data, devices.passcode_enc AS …`.
- **Verification level:** test-verified (SQL); code-read (detail).
- **Why it's a problem:** fleet and B2B clients have hundreds of assets with photos. The picker and list fetch them all to print brand and model.
- **Real-world failure scenario:** typing a fleet company's name in the order picker loads 300 vehicle photos (about 60 MB) per keystroke. Opening that client loads 5,000 orders.
- **Recommended fix:** `selectinload(Client.devices).load_only(...)` (or `defer` image_data and passcode_enc); count orders with the existing grouped query; paginate client purchases.
- **Fix dependencies/risks:** none significant.

### PERF-05 [MEDIUM] Activity-log report, staff feed and recommendation lists carry base64 media
- **Module:** reports, feed, recommendations.
- **Tables/columns:** `event_feed_items.media_data`, `recommendations.media_data`.
- **Functions/APIs:**
  - `reports.py:552-558` `_rep_activity_log`: `select(EventFeedItem)` `.limit(500)`, but only `body` is output.
  - `feed.py:100-127` `list_feed`: up to 200 items, and `_serialize` (`feed.py:50-63`) **returns `media_data`** for each.
  - `recommendations.py:77` `serialize_rec`: returns `media_data`.
- **Execution flow:**
  - Opening a work order's timeline returns up to 200 inline attachments (each ≤ `MAX_MEDIA_CHARS = 8_000_000`).
  - The activity log report reads up to 500 rows of media.
- **Evidence:** the `/api/reports/run/activity_log` SQL capture contains `event_feed_items.media_data` (test-verified).
- **Verification level:** test-verified (activity log SQL); code-read (feed, recommendations).
- **Why it's a problem:** with `MEDIA_BACKEND=db` (the default) every timeline open moves all of the job's photos. The report reads up to 500 × MB just to print text.
- **Real-world failure scenario:** a job with 30 inspection photos takes 30–60 MB on every drawer open, multiplied by every technician refreshing.
- **Recommended fix:**
  - The report should select `created_at`, `entity_type` and `body` only.
  - Feed and recommendations should return `media_url` and serve bytes from `/api/media` (migrate inline rows with the existing `POST /api/media/migrate-feed`).
- **Fix dependencies/risks:** the frontend must use URLs; keep M1's safe-link rule.

### PERF-06 [MEDIUM] Media offload is partial: most binary data stays inline in Postgres whatever `MEDIA_BACKEND` is
- **Module:** media, models.
- **Tables/columns:**
  - `parts.image_data` (`parts.image_blob_id` exists but no router writes it);
  - `devices.image_data`;
  - `organisations.logo_data`;
  - `orders.signature`;
  - `event_feed_items.media_data` for signatures (`orders.py:2683`, `:2950`, `public.py:199`);
  - `recommendations.media_data`;
  - `media_blobs.inline_data` under `db`.
- **Functions/APIs:**
  - `config.py:196` `MEDIA_BACKEND: str = "db"`;
  - `media.py:135-142` `offload_if_enabled` (no callers: `grep offload_if_enabled` shows only its definition);
  - `orders.py:1090` (`image_data=data.asset_image`);
  - `catalog_products.py:472`.
- **Execution flow:** every upload outside the feed, `/api/media`, PO-invoice and check-in paths writes a data URL into a Text column.
- **Evidence:** `grep -rn image_blob_id backend/routers` returns nothing.
- **Verification level:** code-read.
- **Why it's a problem:**
  - Tables, backups (`pg_dump` nightly to R2), restore time and `shared_buffers` all fill with images.
  - Every PERF-03/04/05 over-fetch exists because of this.
  - `DEPLOY.md:147-163` says S3 is "for production", but it only covers part of the data.
- **Real-world failure scenario:** after a year, a 1,000-shop database is mostly base64. Nightly `pg_dump` and the restore drill exceed their window, and VACUUM on `parts`/`devices` is slow.
- **Recommended fix:**
  - Route every binary field through `media.store` (`offload_if_enabled` exists for this) and store a blob id.
  - Backfill with a batched job.
  - Flip the default to `s3` when deployed.
- **Fix dependencies/risks:** a data migration; GDPR erase paths must cover the new blob ids (`compliance.py:239-272`).

### PERF-07 [MEDIUM] Home "company dashboard": 65 SQL statements per load, uncached, including an all-time aggregation over every closed job
- **Module:** reports / Home.
- **Tables/columns:** `orders`, `order_lines`, `invoices`, `invoice_orders`, `credit_notes`, `payments`, `client_returns`, `refunds`, `transactions`, `sales`, `accounts`.
- **Functions/APIs:** `routers/reports.py:1177-1323` `company_dashboard`:
  - `cash_collected` ×2 (`:1224`, `:1269`);
  - `net_sales` ×2 (`:1225`, `:1270`), each running `job_figures` plus about 10 aggregates;
  - `receivables` (`:1230`);
  - `job_figures(db, closed_q)` over **all closed jobs ever** (`:1276`) for `avg_work_order`.
- **Execution flow:** the Home and Work-orders hubs (`CompanyHub.tsx`, `WorkOrdersHub.tsx`) call it on every visit. `/api/reports/dashboard` has a 30 s cache (`reports.py:62-80`); this endpoint has none.
- **Evidence (test-verified):**
  - 65 statements at both 3 and 15 jobs; 31 distinct.
  - The `order_statuses` "dropped keys" query repeats ×6.
  - 21 statements reference `invoice_orders`.
  - `job_figures` of all closed orders returns one row per job to Python.
- **Verification level:** test-verified (count, SQL); timing NOT VERIFIED on Postgres.
- **Why it's a problem:** cost grows with shop history. The previous version used 4.8 GB (comment at `reports.py:1196-1198`); the all-time `avg_work_order` keeps a history-proportional piece.
- **Real-world failure scenario:**
  1. A 40k-job shop opens Home.
  2. Six `IN (subquery)` aggregates run over 40k jobs plus a 40k-row result set, together with the today and month `net_sales`.
  3. With several staff on Home at 9 am, the 2-worker pool (20 connections each) saturates.
- **Recommended fix:**
  - Cache the money tiles like `/dashboard` (Redis, keyed by org + location + role-visibility, 30–60 s).
  - Compute `avg_work_order` over the month (also RPT-08).
  - Memoise `dropped_keys` per request.
- **Fix dependencies/risks:** the cache key must include location and role finance visibility; the payload is already redacted per role.

### PERF-08 [MEDIUM] `invoice_job_shares()` unions the entire `invoice_orders` table (no tenant column) into every revenue and receivables query
- **Module:** money rules.
- **Tables/columns:** `invoice_orders` (no `org_id`), `invoices`, `orders`.
- **Functions/APIs:** `services/order_money.py:97-116` `invoice_job_shares`, used by:
  - `revenue.job_figures:178-187`;
  - `revenue.net_sales:323-333`;
  - `order_money.balances_due:119-137` (receivables).
- **Execution flow:** `inv_jobs = union(select(InvoiceOrder...), select(Invoice...).where(order_id is not null))`, then GROUP BY `invoice_id`, then join `orders`. The ORM guard adds `org_id` only to `invoices` and `orders`.
- **Evidence (test-verified SQL capture, Home):** `FROM (SELECT invoice_orders.invoice_id …, invoice_orders.order_id … FROM invoice_orders UNION SELECT invoices.id … WHERE … invoices.org_id = ?)`. The `invoice_orders` branch has no filter and appears twice per call.
- **Verification level:** test-verified (SQL text). Planner behaviour on Postgres NOT VERIFIED.
- **Why it's a problem:**
  - A UNION (DISTINCT) feeding a GROUP BY cannot have the outer `order_id IN (…)` pushed into it.
  - Each call hashes every shop's multi-job invoice links. Cost scales with the platform, not the shop, and is repeated about 21 times per Home load.
  - No data leaks: the join to org-filtered `orders` drops other shops.
- **Real-world failure scenario:** with 3,000 shops and 2M `invoice_orders` rows, each Home or P&L load sorts or hashes 2M rows ×21. p95 goes to seconds and statements approach the 15 s `statement_timeout`.
- **Recommended fix:**
  - Join `invoice_orders` to `invoices` with `invoices.org_id = :org` inside the union branch, or add `org_id` to `invoice_orders`.
  - Use UNION ALL plus DISTINCT after filtering.
- **Fix dependencies/risks:** shares must stay identical; this is covered by the existing revenue tests.

### PERF-09 [MEDIUM] Big ledger tables have no `org_id`: payments and stock movements are reached only through global indexes or joins
- **Module:** models, revenue, inventory.
- **Tables/columns:**
  - `payments`: indexes only on `paid_at` (global), `invoice_id`, `client_id`, `account_id`, `order_id` (`models/__init__.py:1559-1576`).
  - `stock_movements`: `(part_id, created_at)`.
  - 30 tables in total lack `org_id`, including `order_lines`, `invoice_lines`, `sale_lines`, `chat_messages`, `order_status_logs`, `stock_levels`.
- **Functions/APIs:**
  - `revenue.cash_collected:393-423`: an OR of three `IN` subqueries plus a `paid_at` range;
  - `reports._rep_lead_source_revenue:257-262`;
  - `insights._records:113-122`;
  - `inventory.py:2552-2559` movements feed (`join(Part) … order_by(StockMovement.created_at.desc()).limit(300)`).
- **Evidence (test-verified EXPLAIN, SQLite):** `lead_sources` runs `SEARCH payments USING INDEX ix_payments_paid_at (paid_at>? AND paid_at<?)`, then joins to filter the shop. The read guard can't cover these tables either (not `OrgScoped`).
- **Verification level:** test-verified (SQLite plan); Postgres NOT VERIFIED.
- **Why it's a problem:**
  - A date-ranged money report reads every shop's payments in the range.
  - The movements feed must collect and sort the shop's whole movement history to return 300 rows.
- **Real-world failure scenario:** at 2,000 shops a "last 12 months, lead sources" report scans about 20M payment rows, every time anyone runs it.
- **Recommended fix:**
  - Add a denormalised `org_id` (NOT NULL, back-filled) to `payments` and `stock_movements`, with `(org_id, paid_at)` and `(org_id, created_at)` indexes.
  - Make them `OrgScoped` so the guard covers them.
- **Fix dependencies/risks:** a large backfill migration; every write path must set `org_id` (the flush guard's `_fill_missing_org` helps).

### PERF-10 [MEDIUM] Missing indexes for filters actually used
- **Module:** models / migrations.
- **Tables/columns:** 208 of 525 FK columns have no leading index (`probes/indexes.json`). Hot gaps:
  - `accounts.org_id`. Verified `SCAN accounts` in the Home and insights plans.
  - `employees.org_id`, `locations.org_id`, `roles.org_id`, `taxes.org_id`, `suppliers.org_id`, `payroll_runs.org_id`, `saved_filters.org_id`. Every report does `select(Employee).where(Employee.org_id==…)`.
  - Date filters with no composite index:
    - `sales (org_id, created_at)` (only `org_id`);
    - `transactions (org_id, actual_date)` (only `(org_id, txn_type)`);
    - `orders (org_id, closed_at)` (wo_closed, cancellations, earned jobs);
    - `clients (org_id, created_at)` (lead sources);
    - `expenses (org_id, expense_date)`;
    - `time_entries (org_id, started_at)` (separate single-column indexes only);
    - `event_feed_items (entity_type, entity_id, created_at)`.
- **Functions/APIs:**
  - `revenue.net_sales` sales/transaction filters (`revenue.py:341-375`);
  - `reports._rep_cash_flow:464-479`;
  - `_rep_work_orders_closed:231`;
  - `_rep_lead_source_revenue:250-252`;
  - `reports._rep_labor_*` (`:562-685`).
- **Evidence:** from the index probe and EXPLAIN (`SEARCH sales USING INDEX ix_sales_org_id (org_id=?)` with the date range as a residual filter).
- **Verification level:** test-verified (metadata, SQLite plans).
- **Why it's a problem:** per-shop scans of whole history on every report. Platform-wide scans for `accounts`/`employees`.
- **Real-world failure scenario:** a busy POS shop with 300k sales runs "today" net sales by scanning 300k rows; every Home load does it twice.
- **Recommended fix:** add the composites above plus `org_id` indexes on the listed tables (CREATE INDEX CONCURRENTLY in Alembic).
- **Fix dependencies/risks:** migration lock time on big tables, so use CONCURRENTLY outside a transaction.
- **Prior-audit status:** "220 of 450 FKs unindexed" → **PARTIAL**: now 208 of 525. `po_lines.part_id`, `stock_reservations.part_id` and `tasks.order_id` are now indexed (**FIXED**).

### PERF-11 [MEDIUM] Remaining N+1: purchase list and reorder forecast (per PO); location comparison (net sales per location)
- **Module:** reports, analytics.
- **Tables/columns:** `purchase_orders`, `po_lines`; all `net_sales` tables.
- **Functions/APIs:**
  - `reports.py:730-738` `_rep_purchase_list` and `reports.py:917-924` `_rep_reorder_forecast`: `for po in open_pos: … select(POLine).where(POLine.po_id == po.id)`;
  - `analytics.py:113-115` `location_comparison`: `for lid in …: await money_rules.net_sales(db, org, start, end, lid)`, about 12 statements per location.
- **Evidence (test-verified):**
  - purchase_list goes 12 → 24 statements and reorder_forecast 14 → 26 when open POs go 3 → 15.
  - location_comparison is 40 statements with one location.
- **Verification level:** test-verified.
- **Why it's a problem:** linear in open POs or locations.
- **Real-world failure scenario:** a chain with 25 branches gives about 300 statements on the location-comparison tab. A shop with 400 open POs gives 400 round trips per report run.
- **Recommended fix:** one `select(POLine).where(POLine.po_id.in_(select(open_po_ids)))` grouped by `part_id`; a location-grouped `net_sales` (GROUP BY `location_id`).
- **Fix dependencies/risks:** `net_sales` per location must keep its tie-out to the shop total ("Not tied to a location" row).
- **Prior-audit status:** "48 list endpoints with N+1; returns ~600/page" → **mostly FIXED**. Returns is now batched (`returns.py:302-320`), and all 64 probed endpoints are flat except these.

### PERF-12 [MEDIUM] Reports and several lists are unbounded; aggregation is done in Python over ORM rows
- **Module:** reports, analytics, insights, lists.
- **Tables/columns:** `orders`, `order_lines`, `transactions`, `time_entries`, `stock_movements`, `sales`, `sale_lines`.
- **Functions/APIs:**
  - **No row cap or date-span cap:** `reports.py:1061-1086` `run_report`, `:1088-1140` export, `:1142-1175` CSV. Only `activity_log` is limited (500), and only total-profit caps the span (731 days, `:1508`).
  - **Python aggregation over full ORM rows:**
    - `_rep_technicians:354-376` (`select(OrderLine, Order)`);
    - `_line_sales:379-440`;
    - `_rep_labor_*:562-685` (`select(TimeEntry)`);
    - `_rep_cash_flow:464-479`;
    - `analytics.trends:134-161`;
    - `insights._records:69-123`.
  - **Unpaginated lists:**
    - `catalog_products.py:269-300` `list_products` (whole active catalogue; the React product list);
    - `appointments.py:40-55` (all appointments ever when no date is given);
    - `chats.py:100-110` `thread_messages`;
    - `inventory.py:1247-1258` `list_reservations` (which also writes, via `_release_expired`, on GET);
    - `inventory.py:2276` `reorder_list`;
    - `inventory.py:904` `to-order`;
    - `analytics.py:164-188` `fleet`;
    - client detail and purchases (PERF-04).
- **Evidence:** code-read; the `unbounded_scan.py` hint list (188 GET handlers that `.all()` without a visible limit, many of them small settings tables).
- **Verification level:** code-read.
- **Why it's a problem:**
  - `statement_timeout` (15 s) bounds SQL but not Python loops or JSON size.
  - A year-long `wo_created` over 30k jobs serialises 30k rows into one JSON and one xlsx in memory (`openpyxl`; only `wb.save` is threaded, at `:1134`).
- **Real-world failure scenario:** an owner exports "Technicians performance" for 2 years. 200k order lines are materialised as ORM objects (about 1 GB of Python), the worker stalls, and other shops on it time out.
- **Recommended fix:**
  - GROUP BY in SQL for technicians, line sales, labour and cash flow.
  - Cap report spans (as total-profit does) and rows, with server-side pagination for detail reports.
  - Stream CSV/xlsx (`write_only=True` workbook, generator responses).
  - Paginate catalogue, appointments and chat messages.
- **Fix dependencies/risks:** the frontend Reports table must page; exports must stay complete (stream them).

### PERF-13 [MEDIUM] Insights "Payments" reads every payment the shop ever took and filters dates in Python
- **Module:** insights.
- **Tables/columns:** `payments`, `accounts`.
- **Functions/APIs:** `routers/insights.py:112-122`:
  ```
  q = select(Payment).where(Payment.account_id.in_(acct_ids), Payment.kind != "refund")
  for p in …: if p.paid_at < start: continue
  ```
- **Execution flow:** each saved insight card on Reports › Insights calls `/data`, which loads full Payment rows for all time.
- **Evidence:** the EXPLAIN shows `SCAN accounts` then `SEARCH payments USING INDEX ix_payments_account_id (account_id=?)` with no date bound (test-verified plan).
- **Verification level:** test-verified (plan), code-read.
- **Why it's a problem:** linear in total payment history, per card, per view.
- **Real-world failure scenario:** a 5-year-old shop with 200k payments and 6 cards on the page loads 1.2M ORM objects per visit.
- **Recommended fix:** filter `paid_at >= start` in SQL, aggregate with GROUP BY in SQL (bucket expressions), select columns only. See RPT-02 for the correctness side.
- **Fix dependencies/risks:** none.

### PERF-14 [MEDIUM] Search: contains-LIKE over nested `replace()` on orders, clients and devices; two full passes and an audit write per keystroke
- **Module:** search (`services/search_match.py`), orders, clients.
- **Tables/columns:** `orders.order_number`, `clients.name/phone/email/discount_card`, `devices.brand/model/serial/imei/vin/hin`, `audit_log`.
- **Functions/APIs:**
  - `search_match.py:23-27` `stripped` (7 nested `replace`);
  - `:51-56` `phone_match`, `:59-74` `number_match`, `:93-100` `code_match`;
  - `orders.py:493-526` `_order_search_clause` (3 subquery families);
  - `orders.py:602-610` (page plus `count(*)` with the same clause);
  - `orders.py:647-653` `pilot.record_lookup`, which writes or updates an `audit_log` row and **commits** on every first-page search (`services/pilot.py:61-88`);
  - `clients.py:102-109`, `:145-158` (page plus count).
- **Evidence (test-verified EXPLAIN):** `SEARCH orders USING INDEX ix_orders_org_created (org_id=?) | LIST SUBQUERY … SEARCH clients USING INDEX ix_clients_org_name (org_id=?) … SEARCH devices USING …`. Only `org_id` is indexable; every row then evaluates `replace(replace(…lower(col)…)) LIKE '%…%'`.
- **Verification level:** test-verified (SQLite plan); Postgres cost NOT VERIFIED.
- **Why it's a problem:**
  - Per keystroke, the scan cost is about (orders + clients×3 + devices×5 columns) × 2 (page and count) for the whole shop.
  - Global search (`q=`) fires as the user types.
  - The audit write puts a commit on a read path.
- **Real-world failure scenario:** a shop with 80k orders, 50k clients and 60k devices does roughly 0.5M nested-replace evaluations per keystroke ×2. Ten counter staff typing at once means seconds-long searches.
- **Recommended fix:**
  - Store normalised columns (`phone_digits`, `order_number_norm`, `serial_norm`) maintained on write.
  - Index them with `pg_trgm` GIN (or a btree prefix for numbers); match a short numeric query by equality only.
  - Skip the count when `skip=0` and the page isn't full.
  - Move `record_lookup` to a sampled or async counter.
- **Fix dependencies/risks:** a backfill migration; enable the `pg_trgm` extension; keep SQLite fallbacks for tests.

### PERF-15 [MEDIUM] Python id lists inlined into `IN (...)` can exceed asyncpg's 32,767 bind-parameter limit
- **Module:** analytics, reports.
- **Tables/columns:** `orders`, `sale_lines`, `order_lines`.
- **Functions/APIs:**
  - `analytics.py:153` `job_figures(db, [o for o, _ in rows])`. `revenue._ids_cond:105-109` expands a list into one bind per id, and `job_figures` uses it in about 6 statements.
  - `reports.py:417` `SaleLine.sale_id.in_([x.id for x in sales])`.
  - `reports.py:600-601` `Order.id.in_(list(by_order.keys()))`.
  - `reports.py:1312-1317` client/location id sets.
- **Evidence:** code-read. The SQLite tests can't hit this (limit 32,766 but rarely approached).
- **Verification level:** NOT VERIFIED (needs Postgres with large data).
- **Why it's a problem:** asyncpg rejects statements with more than 32,767 parameters, so the report 500s.
- **Real-world failure scenario:** a multi-till store with about 40k counter sales in a year runs "Sold products" for the year and gets a 500.
- **Recommended fix:** pass SELECTs (as the `_ids_cond` Select branch already allows) or `= ANY(:array)`.
- **Fix dependencies/risks:** none.

### PERF-16 [MEDIUM] Background jobs share the web workers' event loop and DB pool; all start at once on every boot
- **Module:** `core/jobs.py`, `core/scheduled.py`, `db/database.py`.
- **Tables/columns:** n/a (pool).
- **Functions/APIs:**
  - `jobs.py:79-90` `_periodic_loop` and `start`: every periodic runs immediately at start unless `delay_first`.
  - `jobs.py:109-138` `run_exclusive`: holds **its own pooled connection** (`engine.connect()`) for the whole job, while the job opens more sessions.
  - `config.py:59-62` `DB_POOL_SIZE=10`, `DB_MAX_OVERFLOW=10`, `DB_POOL_TIMEOUT_S=10`.
  - `scheduled.py:50-97` notification-sweep: per **active org**, every 120 s, sequential, a session per org (its own docstring: "ponytail … fine at pilot scale").
  - No setting turns the runner off for web workers (no `RUN_JOBS`-style flag in `config.py` or `main.py:147`).
- **Execution flow:**
  1. A deploy restarts N workers.
  2. Each starts 16 loops immediately.
  3. The first worker to reach Postgres wins most advisory locks, so it holds about 16 lock connections plus the jobs' work sessions.
- **Evidence:** code-read.
- **Verification level:** NOT VERIFIED (needs staging with Postgres; watch `pg_stat_activity` and pool timeouts at boot).
- **Why it's a problem:**
  - 16 lock connections + 16 work sessions = 32 > pool 20, so requests on that worker wait 10 s and fail (`TimeoutError`) right after each deploy.
  - CPU-heavy Python in sweeps (sense agents, membership invoices) blocks request handling.
  - The per-org loops are O(active orgs): at 3,000 shops the notification sweep is about 12k statements and 3k session opens per 2 minutes.
- **Real-world failure scenario:** every deploy brings 30–60 s of 500s and timeouts on one worker. Notification reminders drift later as the tick length passes 120 s.
- **Recommended fix:**
  - Run the scheduler as a separate process or container (web workers with jobs off).
  - Give the lock connection its own small engine/pool.
  - Stagger first runs (`delay_first` / jitter).
  - Make notification-sweep use a cross-org "due" query, as notification-outbox already does (`notification_rules.orgs_with_due`).
- **Fix dependencies/risks:** DEPLOY/compose changes; the advisory lock still needs session pooling.

### PERF-17 [MEDIUM] Integrity sweep runs platform-wide full-table aggregations as single statements under a 15 s statement timeout
- **Module:** `services/reconcile.py`.
- **Tables/columns:** `transactions`, `accounts`, `client_ledger_entries`, `clients`, `payments`, `invoices`, `stock_levels` ∪ `stock_movements`, `locations`.
- **Functions/APIs:**
  - `reconcile.py:64-125` `_accounts`, `_clients`, `_invoices` and `_stock` (`union_all(levels, moves)` grouped by part and location over **all** shops);
  - `scheduled.py:372-382`;
  - `config.py:64` `DB_STATEMENT_TIMEOUT_MS = 15000`.
- **Evidence:** code-read. `skip_tenant_filter=True`, with no `org_id` passed from the sweep.
- **Verification level:** NOT VERIFIED (needs staging-size data).
- **Why it's a problem:** at scale, the stock check aggregates the entire movements table. It will exceed 15 s, be cancelled every night, and silently stop reporting mismatches (just "periodic job raised").
- **Real-world failure scenario:** with 50M stock movements the sweep never completes, so ledger drift (the thing it exists to page on) goes unnoticed.
- **Recommended fix:**
  - Run per org in batches (`find_mismatches(org_id=…)` exists).
  - Or raise `statement_timeout` for that session only (`SET LOCAL`).
  - Alert when the sweep fails, not only on mismatches.
- **Fix dependencies/risks:** none.

### PERF-18 [MEDIUM] Retention is opt-in, covers only 6 tables, and purges every shop in one unbatched transaction that swallows errors
- **Module:** `services/retention.py`.
- **Tables/columns:**
  - Covered: `notification_logs`, `staff_notifications`, `calls`, `chat_messages`, `inbox_messages`, `audit_log` (`retention.py:45-66`).
  - **Not covered:** `event_feed_items` (photos), `media_blobs`, `stock_movements`, `webhook_deliveries`, `payment_webhook_events`, `order_status_logs`, `time_entry_audits`.
  - `audit_log` also gains a row per search (PERF-14).
- **Functions/APIs:**
  - `retention.py:26-42` `clean` (nothing kept or deleted by default);
  - `:81-97` `purge_all`: one session, a savepoint per org, **single `commit` at the end**, and `except Exception: continue` with no log (`:92-93`);
  - `:69-78` `purge_org`: a single DELETE per kind, no batching.
- **Evidence:** code-read.
- **Verification level:** code-read.
- **Why it's a problem:**
  - Default growth is unbounded.
  - The first purge for an old shop deletes millions of rows in one statement, which is likely to hit the 15 s timeout. It is then rolled back silently, every night, forever.
  - One transaction across all shops holds locks and bloats WAL.
- **Real-world failure scenario:** an owner sets 90-day retention on 3 years of audit log. The DELETE times out and is skipped silently every night. The owner believes data is being purged (a GDPR promise) while nothing is.
- **Recommended fix:**
  - Batched deletes (`DELETE … WHERE id IN (SELECT id … LIMIT 5000)`) with a commit per batch or per shop.
  - Log failures.
  - Set a platform default for system tables (webhook deliveries, idempotency, payment webhook events).
  - Consider time-partitioning `audit_log` and `notification_logs`.
- **Fix dependencies/risks:** legal and owner expectations about defaults.
- **Prior-audit status:** "No data-retention purge jobs" → **PARTIAL**.

### PERF-19 [MEDIUM] Sense service-history cache: up to 5,000 shops × 20,000 rows per worker, unshared
- **Module:** `services/sense.py`.
- **Tables/columns:** `orders`, `order_lines`.
- **Functions/APIs:** `sense.py:553-570` `_HISTORY_CACHE` (TTL 600 s, cleared only when it passes 5,000 entries); `:572-594` `_read_service_history` (up to 20,000 rows into nested dicts of `date` sets plus price lists).
- **Evidence:** code-read.
- **Verification level:** code-read; memory size NOT VERIFIED.
- **Why it's a problem:**
  - Per-worker memory grows with active shops, roughly several MB per large shop, which could reach GBs at 1,000+ shops.
  - Each worker builds its own copy.
  - The 10-minute staleness differs per worker.
- **Real-world failure scenario:** on the 4 GB host, as more shops open work orders the workers' RSS climbs until the OOM killer restarts them.
- **Recommended fix:** an LRU with a byte budget; or precompute intervals per shop in a table, refreshed by a job; or Redis.
- **Fix dependencies/risks:** none.

### PERF-20 [LOW] Offset pagination everywhere; inventory parts pages have no ORDER BY and a wrong total
- **Module:** lists, public API.
- **Tables/columns:** `orders`, `clients`, `invoices`, `tasks`, `parts`; v1/v2 resources.
- **Functions/APIs:**
  - `.offset(` in 20 handlers, including `orders.py:607`, `clients.py:150`, `finance.py:196`, `tasks.py:225`, `v1.py:344`, `v1.py:759`, `v2.py:58`, `v2_records.py:393`;
  - `inventory.py:123` `q.offset(skip).limit(limit)` with **no `order_by`**, and `return {"total": len(items)…}` (`:146`).
- **Evidence:** code-read.
- **Verification level:** code-read.
- **Why it's a problem:**
  - Deep offsets cost O(offset) on Postgres.
  - API integrators syncing all orders page by page do O(n²) work.
  - Unordered offset pages can repeat or skip rows.
- **Real-world failure scenario:** a v2 API integration pulling 100k orders at 100 per page does 1,000 progressively slower queries; parts sync misses items.
- **Recommended fix:** keyset (`created_at, id`) cursors for the API and big lists; add `order_by(Part.name, Part.id)` and a real count.
- **Fix dependencies/risks:** an API contract change for v1/v2 (add a cursor alongside the page).

### PERF-21 [LOW] Some in-process caches diverge between workers
- **Module:** core.
- **Tables/columns:** n/a.
- **Functions/APIs:**
  - `core/cost_redaction.py:20-27` `_role_cache` (30 s; `invalidate_role` clears only the local worker);
  - `core/observability.py:183` `_KEY_CACHE` (rate-limit bucketing only; auth re-checks the DB, so revoked keys still fail);
  - the `reports._dash_cache` fallback when Redis is down.
- **Evidence:** code-read.
- **Verification level:** code-read.
- **Why it's a problem:** after a role loses "see costs", other workers keep showing costs for up to 30 s, on the fallback path (non-JWT auth) only.
- **Real-world failure scenario:** minor and short-lived.
- **Recommended fix:** publish invalidations via Redis or shorten the TTL.
- **Fix dependencies/risks:** none.
- **Prior-audit status:** rate limits per worker without Redis → **FIXED**. A deployed instance refuses to boot without `REDIS_URL` unless `SINGLE_WORKER` is set (`config.py:404-428`).

### PERF-22 [MEDIUM] Feed/attachment size limits disagree and the default backend stores up to about 6 MB base64 per item in hot tables
- **Module:** feed.
- **Tables/columns:** `event_feed_items.media_data`.
- **Functions/APIs:**
  - `feed.py:25` `MAX_MEDIA_CHARS = 8_000_000` ("~6 MB base64 ceiling for the demo");
  - `config.py:197` `MEDIA_MAX_BYTES = 8 MB`, enforced only inside `media.store`, which the `db` backend path skips (`feed.py:160-166` only calls `store` when not `db`);
  - `MAX_REQUEST_BYTES` 16 MB.
- **Evidence:** code-read. The track probe stored 598 items × 200 KB through the feed without hitting a size or quantity limit before the rate limit.
- **Verification level:** code-read plus test-verified (no per-order cap).
- **Why it's a problem:** there is no per-job or per-shop storage cap, so one shop can grow the shared database without bound. It compounds PERF-01, 05 and 06.
- **Real-world failure scenario:** a shop uploads its video walkarounds as "files", and the DB and backups grow by GBs a week.
- **Recommended fix:** per-org storage quotas tracked on `media_blobs.size_bytes`; route all uploads through `media.store`.
- **Fix dependencies/risks:** billing plan limits.

### PERF-23 [LOW] Single-host production topology
- **Module:** deploy (`deploy/one-server/compose.yml`, `Dockerfile`, `DEPLOY.md`).
- **Tables/columns:** n/a.
- **Functions/APIs:**
  - `compose.yml:14-24`: Postgres 16 on the same 4 GB host, `max_connections=100`, `shared_buffers=512MB`.
  - `:35-37`: Redis with no persistence (fine for rate limits).
  - `:58`: `WEB_CONCURRENCY` 2.
  - `Dockerfile:55`: a single `uvicorn --workers` process group.
- **Evidence:** code-read.
- **Verification level:** code-read.
- **Why it's a problem:**
  - App, DB, Redis and backups share one 4 GB box; it is a single point of failure.
  - The scale path (more replicas, managed Postgres with PgBouncer in session mode, separate job runner) is documented only partly (`DEPLOY.md:36-40`, `:102-110`).
  - The memory-heavy paths above (PERF-01, 03, 12) are what would kill it first.
- **Real-world failure scenario:** one OOM takes down every shop.
- **Recommended fix:** before scaling past pilot, use managed Postgres, at least 2 app replicas, and a separate scheduler container.
- **Fix dependencies/risks:** cost.

---

### RPT-01 [HIGH] Timesheets and overtime pay are computed by UTC day, and the date window is read as UTC (the PR #200 bug class, unfixed here)
- **Module:** timeclock / payroll, reports.
- **Tables/columns:** `time_entries.started_at`, `duration_sec`, `state`; `employee_balance_ledger` (hourly_wage rows).
- **Functions/APIs:**
  - `routers/timeclock.py:24-31` `_udt`: a date-only value becomes a naive datetime, i.e. UTC midnight.
  - `:987-1022` `GET /api/timeclock/timesheet`: window `start=_udt(date_from)`, `end=_udt(date_to)+1d`.
  - `:956-966` `_overtime_split`: `by_day[e.started_at.date()]`, a UTC date.
  - `:1032-1095` `POST /timesheet/approve`: the same window; `days = sorted({e.started_at.date() …})` at `:1052`; posts wages per UTC day with `ref_id=f"{emp}:{day}:{day}"`.
  - `reports.py:659-685` `_rep_labor_cost`: the window is shop-day (`range_edge`), but overtime is `by_day[e.started_at.date()]`.
- **Execution flow:**
  1. A manager approves a New York shop's timesheet for 3 Nov.
  2. The window is 3 Nov 00:00–24:00 **UTC** (19:00 on 2 Nov to 19:00 on 3 Nov local).
  3. Evening work after 19:00 local falls into the next window.
  4. Overtime (more than 8 h per day, `:190`) is split at UTC midnight.
- **Evidence (test-verified, `probes/tz_probe.json`):**
  - Setup: shop timezone America/New_York; entries 09:00–17:00 and 19:30–22:00 local on 3 Nov.
  - `GET /api/timeclock/timesheet?date_from=2026-11-03&date_to=2026-11-03` returned `total_hours: 8.0, overtime_hours: 0.0, entry_count: 1`. It missed the evening entry; should be 10.5 h with 2.5 h overtime.
  - `/api/reports/run/labor_cost` for the same day returned total 10.5 h and **overtime 0.0**.
- **Verification level:** test-verified.
- **Why it's a problem:**
  - Wrong overtime, so wrong pay posted to the ledger.
  - Approvals for "a day" cover the wrong hours.
  - The labour report disagrees with the shop's own day.
- **Real-world failure scenario:**
  - US shops (UTC−4/−5) whose staff work past 19:00–20:00 local are systematically under-paid overtime (a wage-law exposure).
  - Kolkata-default shops (UTC+5:30) see early-morning entries before 05:30 attributed to the previous day.
- **Recommended fix:**
  - Read the window with `services/dates.range_edge(value, tz, end=…)`.
  - Group by `to_wall_clock(e.started_at, tz).date()` (location zone).
  - Key the ledger `ref_id` and idempotency by shop date.
  - Do the same in `_rep_labor_cost`.
- **Fix dependencies/risks:**
  - Changing day keys alters the idempotency keys for already-approved days, so migrate or guard against re-posting.
  - Add a regression test pinned to an instant (as PR #200 did).

### RPT-02 [MEDIUM] Company Insights: UTC buckets, "closed orders" by creation date, and payments that disagree with "Paid"
- **Module:** `routers/insights.py`.
- **Tables/columns:** `orders.created_at`, `status`; `sales.created_at`; `payments.paid_at`, `method`, `account_id`.
- **Functions/APIs:**
  - `insights.py:62-67` `_range_start` (rolling from `now_utc()`);
  - `:139-170` `_bucket_labels` and `_bucket_key` (`ts.strftime("%Y-%m-%d")` on naive UTC);
  - `:79-90` closed_orders: `Order.created_at >= start`, grouped by `created_at` month;
  - `:112-122` payments: only `account_id IN shop accounts`, `kind != "refund"`, method "balance" included.
- **Evidence:** code-read. Compare `revenue.cash_collected:380-424`, which excludes `method == "balance"` and attributes payments by order, invoice **or** account.
- **Verification level:** code-read.
- **Why it's a problem:**
  - A Kolkata shop's sale at 01:00 local lands in the previous day or month bucket.
  - "Closed orders (last 30 days)" counts jobs **created** in the last 30 days that are closed now, not jobs closed in the period.
  - Payments with no account are dropped, and balance-paid amounts are counted as money in, so the totals differ from Home's "Paid".
- **Real-world failure scenario:** the owner compares the Insights monthly payments chart with Finance and the two never match. Month-end sales shift into the wrong month.
- **Recommended fix:**
  - Bucket on shop wall-clock dates (as `services/sense.py:590` does).
  - Use `earned_jobs_query(with_date=True)` for closed orders.
  - Reuse the `cash_collected` predicate for payments.
- **Fix dependencies/risks:** saved cards keep their definitions; values will change.

### RPT-03 [MEDIUM] Analytics trends and turnaround use UTC days
- **Module:** `routers/analytics.py`.
- **Tables/columns:** `orders.created_at`, the earned date.
- **Functions/APIs:**
  - `analytics.py:31-33` `_window` (now − days);
  - `:145-157` `trends`: `series.setdefault(at.date().isoformat(), …)` for created and earned;
  - `:338` `fleet_statement` `closed_at.date()`.
- **Evidence:** code-read.
- **Verification level:** code-read.
- **Why it's a problem:** the daily series is keyed by UTC date, so the Home and Analytics daily chart disagrees with the shop-day reports fixed in PR #200.
- **Real-world failure scenario:** a US West-coast shop's 5–8 pm jobs show up on "tomorrow".
- **Recommended fix:** convert with `to_wall_clock(at, tz)` before `.date()`; start the window at shop midnight.
- **Fix dependencies/risks:** none.

### RPT-04 [MEDIUM] Attendance wages select shifts by UTC date; ledger period fallbacks also use UTC dates
- **Module:** payroll.
- **Tables/columns:** `shifts.started_at`; `employee_balance_ledger.created_at`.
- **Functions/APIs:**
  - `routers/payroll.py:994-997`: `func.date(Shift.started_at) >= start, func.date(Shift.started_at) <= end`. The column function also defeats any index.
  - `payroll.py:423`, `:571`, `:646`: `ps <= row.created_at.date() <= pe`.
- **Evidence:** code-read.
- **Verification level:** code-read.
- **Why it's a problem:** a shift that starts before the shop's UTC offset boundary is paid in the wrong period. The idempotency key is per range, so it is moved rather than double-paid, but period totals and statements are wrong.
- **Real-world failure scenario:** a Kolkata shop's 02:00 shift on the 1st is paid in the previous month's run.
- **Recommended fix:** `started_at >= day_start_utc(start, tz)` and `< day_start_utc(end+1, tz)`.
- **Fix dependencies/risks:** periods already posted.

### RPT-05 [LOW] Report cells print naive UTC timestamps as if they were local
- **Module:** reports.
- **Tables/columns:** `orders.created_at/closed_at`, `stock_movements.created_at`, `event_feed_items.created_at`.
- **Functions/APIs:**
  - `reports.py:225` and `:240` (`"%Y-%m-%d %H:%M"`);
  - `:322`, `:534`, `:557`;
  - `:895` dead stock "Last sold";
  - `insights` labels;
  - `printing.py:191-445` document dates.
- **Evidence:** code-read.
- **Verification level:** code-read.
- **Why it's a problem:** the "Created 2026-11-03 02:10" cell is UTC; a Kolkata user reads it as local (actually 07:40).
- **Real-world failure scenario:** confusing timestamps on exports given to accountants.
- **Recommended fix:** format with `utc_to_wall(ts, tz)`.
- **Fix dependencies/risks:** none.

### RPT-06 [LOW] The report window's end drops the last second of the day
- **Module:** `services/dates.py`.
- **Tables/columns:** every date column compared with `<= dt`.
- **Functions/APIs:** `dates.py:86-87` `range_edge(..., end=True)` returns `day_end_utc(...) - timedelta(seconds=1)`; `revenue._in:96-102` uses `col <= dt`.
- **Evidence:** code-read. Timestamps carry microseconds (`datetime.utcnow()`).
- **Verification level:** code-read.
- **Why it's a problem:** rows stamped `23:59:59.000001`–`.999999` local are excluded from both that day and the next day's window.
- **Real-world failure scenario:** rare missing transactions in a day's totals.
- **Recommended fix:** use a half-open window: `< day_end_utc(...)`.
- **Fix dependencies/risks:** touches every report; covered by existing tests.

### RPT-07 [LOW] Orders list date filters read dates as UTC
- **Module:** orders.
- **Tables/columns:** `orders.created_at`.
- **Functions/APIs:** `orders.py:484-487` `parse_dt(created_from)` / `parse_dt(created_to)`. A date-only `created_to` means 00:00 UTC, so it excludes that day entirely.
- **Evidence:** code-read. The React app doesn't send these (`grep created_from frontend-react/src` finds nothing); this is API-only.
- **Verification level:** code-read.
- **Why it's a problem:** inconsistent with the reports' rule, and `created_to=2026-11-03` returns nothing from the 3rd.
- **Real-world failure scenario:** an API consumer's daily export misses a day.
- **Recommended fix:** `range_edge(value, tz, end=…)`.
- **Fix dependencies/risks:** none.

### RPT-08 [MEDIUM] Home "This month › Average job" is the all-time average
- **Module:** reports / Home.
- **Tables/columns:** `orders.status`; `job_figures` revenue.
- **Functions/APIs:**
  - `reports.py:1272-1277`: `closed_q = select(Order.id).where(Order.org_id == oid, Order.is_estimate == False, Order.status.in_(closed keys))`, with no date bound, then `avg_wo`.
  - It is returned under `"monthly"` (`reports.py:1313`) and rendered under the "This month" heading (`CompanyHub.tsx:250-257`, `<Metric l="Average job" …>`).
- **Evidence:** code-read (backend and frontend).
- **Verification level:** code-read.
- **Why it's a problem:** a mislabelled KPI, and the main cost driver of PERF-07.
- **Real-world failure scenario:** after a price rise, the owner sees "Average job (this month)" barely move, because it averages five years.
- **Recommended fix:** use `earned_jobs_query(df=month0)` for the month's average.
- **Fix dependencies/risks:** none.

---

## 4. Scalability assessment (hundreds to thousands of shops)

**DB pool** (`db/database.py:49-54`, `config.py:59-65`):
- 10 + 10 connections per worker, 10 s pool timeout, `pre_ping`, recycle 1800 s.
- `statement_timeout` 15 s and idle-in-transaction 60 s are set via asyncpg `server_settings`.
- This is sensible for a pilot. Background jobs share the pool (PERF-16).
- PgBouncer must use session mode for the advisory locks (`DEPLOY.md:102-110`), which limits connection multiplexing as replicas grow.

**Job runner:**
- In-process in every web worker; Postgres advisory locks make each sweep run once. It does not scale out: one worker does all of one sweep, sequentially per org.
- Per-org loops: notification-sweep (every active org, every 2 min), contact-lifecycle (hourly, every active org), membership, sense and task sweeps (the subset with features on).
- Fine for tens to hundreds of shops; needs cross-org "due" queries and a dedicated worker for thousands (PERF-16).

**In-memory state that breaks with multiple instances:**
- Rate limiting is Redis-backed. Deploy refuses to start otherwise (prior item **FIXED**).
- The dashboard cache is Redis-backed.
- Still per-worker: the sense history cache (PERF-19), the cost-role cache (PERF-21), and `_DELIVERY["busy"]` (`scheduled.py:35`). The last is per process, but correctness relies on the advisory lock, which is fine.

**Table growth with no default retention** (PERF-18):

| Table | Growth driver |
|---|---|
| `audit_log` | Plus one row per collapsed search lookup |
| `notification_logs`, `staff_notifications` | Every message and alert |
| `chat_messages`, `inbox_messages` | Every conversation |
| `stock_movements` | No `org_id` |
| `event_feed_items` | Inline photos |
| `media_blobs` | `db` backend |
| `webhook_deliveries` | Outbound webhooks |
| `payments` | No `org_id` |

Retention is opt-in per shop; system tables have no TTL.

**Media:** default inline in Postgres; S3 path partial and blocking (PERF-02, 06).

**Deploy:**
- One 4 GB host: app (2 workers), Postgres, Redis, tunnel and backup (PERF-23).
- Non-root image, hash-pinned dependencies, health check on `/api/health` (liveness), `/api/readyz` checks the DB.

**Verdict:** the hot paths (lists, board, most reports) are now batched and have flat query counts. The scale limits are:
1. inline binary data in hot tables and responses (PERF-01, 03, 05, 06, 22);
2. history-proportional work on every Home/report load (PERF-07, 08, 12, 13);
3. cross-tenant global indexes and tables without `org_id` (PERF-09, 10);
4. the in-process scheduler (PERF-16, 17).

**Fix 1 and 2 before onboarding hundreds of shops.**

## 5. Prior audit (2026-10-29) items in scope

| Prior item | Status | Evidence |
|---|---|---|
| N+1: 48 list endpoints; returns ~600 queries/page | **Mostly FIXED** | 64 endpoints probed flat 3→15 jobs; `returns.py:302-320` batched. Remaining: PERF-11 |
| Whole-table loads (14 places) | **PARTIAL** | `names_by_id` used for name lookups. `reorder_list` and stock reports still load the full catalogue with photos (PERF-03) |
| Missing indexes 220/450 incl. `POLine.part_id`, `StockReservation.part_id`, `Task.order_id` | **PARTIAL** | Now 208/525; those three are indexed (FIXED); others remain (PERF-10) |
| Timestamps: "today" in server tz | **PARTIAL** | Reports, P&L, accountant CSV, custom reports and cash use the shop day (`range_edge`, PR #200). Timesheet/overtime (RPT-01), insights (RPT-02), trends (RPT-03), attendance (RPT-04) still UTC |
| R2: one shop's error stops the sweep | **FIXED** | Per-org session plus rollback (`scheduled.py:73-93`, `:118-127`, `:152-158`, `:178-188`) |
| R3: webhook stuck at "sending" | Out of my scope | `retry_sweep` docstring claims reclaim (`scheduled.py:217-226`); not verified |
| Rate limits per worker without Redis | **FIXED** | `config.py:404-428` refuses to boot |
| No data-retention purge jobs | **PARTIAL** | `retention-purge` exists; opt-in, limited, one transaction (PERF-18) |

## 6. End matter

### Inspected
- **Core and deploy:**
  - `main.py`;
  - `backend/core/jobs.py`, `scheduled.py`;
  - `backend/db/database.py`, `tenancy.py` (read guard mechanics);
  - `backend/config.py` (DB, Redis, media sections);
  - `Dockerfile`, `docker-compose.yml`, `deploy/one-server/compose.yml`, `DEPLOY.md` (§§ env, pooling, media).
- **Services:**
  - `media.py`, `search_match.py`, `revenue.py` (full), `order_money.py` (`invoice_job_shares`, `balances_due`);
  - `finance_pnl.py`, `valuation.py`, `profit.py` (outline), `custom_reports.py` (outline);
  - `retention.py`, `reconcile.py`, `sense.py` (cache), `sla.py` (`sweep_breaches`), `pilot.py` (`record_lookup`);
  - `auth.py` (`get_current_employee`, `require_perm`), `apikeys.py` (`require_scope`), `sessions.touch`;
  - integration client styles in `payments`, `notifications`, `ai`, `push`, `qbo`, `xero`, `einvoice`, `email_oauth`, `twilio_voice`, `webhooks`.
- **Routers:**
  - `reports.py` (full);
  - `insights.py` (full);
  - `analytics.py` (most);
  - `orders.py` (list, search, board, archived);
  - `clients.py` (list, detail, purchases);
  - `feed.py`;
  - `public.py` (track payload);
  - `media.py`;
  - `inventory.py` (parts list, reorder list, reservations, movements);
  - `catalog_products.py` (list);
  - `returns.py` (lists);
  - `timeclock.py` (timesheet, approve);
  - `payroll.py` (attendance);
  - `platform.py`.
- **Frontend:** `PublicTrack.tsx` (polling), `CompanyHub.tsx` (labels).
- **Migrations:** all 249 grepped for triggers, functions, RLS and GIN.

### Not inspected
- `orders.py` beyond list/board (5,715 lines), `documents.py`, `sales.py` POS paths, `finance.py` beyond listing.
- `cash.py` internals; `payroll.py` beyond attendance and period fallbacks.
- `services/profit.total_profit` internals (74 statements, flat).
- `notification_rules` per-org functions in depth.
- `webhooks.retry_sweep`, `admin.py` panel queries, the v1/v2 API handlers beyond pagination.
- Frontend data-fetch patterns other than the two files above.
- Security aspects of the 71 no-auth routes (other reviewers).

### Verified (by test/probe)
- Route inventory (1,119 routes, auth and gate classification).
- Index/FK metadata (208/525 unindexed FKs; 30 tables without `org_id`).
- Flat statement counts on 64 endpoints; N+1 on purchase_list and reorder_forecast; 65 statements on the Home dashboard.
- SQL text showing:
  - `parts.image_data` in stock_levels;
  - `devices.image_data`/`passcode_enc` in the clients list;
  - `event_feed_items.media_data` in activity_log;
  - unfiltered `invoice_orders` in revenue subqueries.
- SQLite EXPLAIN: global `ix_payments_paid_at` range, `SCAN accounts`, search plans.
- Public track payload: 401,557 bytes with 2 photos and 119,689,765 bytes with 598; 25 statements; no ETag.
- Timesheet/labor_cost overtime by UTC day (RPT-01).

### Uncertain
- Postgres planner behaviour for PERF-08 (the union) and PERF-14 (search cost); whether it chooses `ix_payments_paid_at` versus `order_id`/`invoice_id` in `cash_collected`.
- Real memory footprint of PERF-19.
- Whether `/api/inventory/parts` (which serialises `image_data`) is used by any live UI (the React product list uses `/api/catalog/products`).
- PERF-16 pool exhaustion at boot.

### Needs staging to test properly
- Postgres `EXPLAIN (ANALYZE, BUFFERS)` with multi-tenant, staging-sized data for:
  - Home `company-dashboard`, `net_sales`, `receivables` (`invoice_job_shares`);
  - `lead_sources` and `cash_collected` (`payments.paid_at`);
  - orders and clients search;
  - the movements feed;
  - the integrity sweep's `_stock`;
  - retention DELETEs on a large `audit_log`.
- The asyncpg 32,767-parameter limit (PERF-15) with a shop of over 33k sales or jobs in the window.
- S3/R2 latency under concurrent photo loads with sync boto3 (PERF-02).
- Pool and `pg_stat_activity` during a deploy restart with all periodics firing (PERF-16).
- RSS growth of workers with many shops opening work orders (PERF-19).
- Load test of public track polling with realistic photo counts (PERF-01).
