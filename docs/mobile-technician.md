# Mobile Technician Experience — PWA, API & Behaviour

A dedicated, installable **technician Progressive Web App** served at `/tech`,
sharing the existing auth + `core.js` helpers but with its own mobile-first
shell and service worker. The desktop SPA at `/` is unchanged. This document
covers the PWA mechanics, the technician API, the offline engine, and Web Push.

## Surfaces

| Path | Purpose |
|---|---|
| `GET /tech` | Technician PWA shell (`frontend/tech.html`). |
| `GET /sw.js` | Root-scoped service worker (`Service-Worker-Allowed: /`, `Cache-Control: no-cache`). |
| `GET /manifest.webmanifest` | Web App Manifest (`application/manifest+json`), `start_url=/tech`, standalone, maskable icon. |
| `GET /offline.html` | Offline fallback for navigations with no cached shell. |
| `GET /static/icons/*` | Real PNG app icons (192, 512, maskable-512, apple-touch-180). |

## Service worker strategy (`frontend/sw.js`, version `ros-tech-v1`)

- **App shell** — precached on install (cache-first) for instant, offline launch.
- **`/static/*`** — stale-while-revalidate.
- **`GET /api/*`** — network-first with cache fallback. This is how assigned
  jobs open offline: the last successful dashboard/order response is replayed
  from cache. A cold miss returns `503 {offline:true}`.
- **Writes** (POST/PATCH/DELETE) — never cached; the page queues them (below).
- **Navigations** — network → cached `/tech` → `offline.html`.
- **`activate`** clears stale caches and claims clients; **`message:SKIP_WAITING`**
  enables one-tap updates.
- **`sync` (`ros-sync`)** — Background Sync wakes the SW, which messages open
  clients to flush their offline queue.
- **`push` / `notificationclick`** — render notifications and deep-link into
  `/tech#/order/<id>`.

## PWA runtime (`frontend/static/js/pwa.js`, `window.RosPWA`)

- **Registration + updates** — registers `/sw.js`; on a new worker reaching
  `installed` while one is active, emits `ros:update-available` (the app shows
  an Update banner that calls `RosPWA.applyUpdate()`). A single
  `controllerchange` reload swaps in the new version.
- **Install prompt** — captures `beforeinstallprompt`; `RosPWA.promptInstall()`
  drives the in-app install button.
- **Offline mutation queue** — IndexedDB (`ros-tech`/`queue`). Each queued
  mutation is **encrypted with AES-GCM** (Web Crypto; non-extractable key
  persisted in the `meta` store) before storage. `RosPWA.mutate(method,url,body)`
  is online-first and falls back to the encrypted queue on failure/offline,
  returning `{ok, queued}`.
- **Auto-sync + retry** — flushes on `online`, on Background-Sync wake, and on
  launch. Replay attaches the Bearer token; `2xx`→delete, `409`→treat as a
  conflict and drop (emits `ros:conflict`), `401`→stop (needs re-auth),
  other `4xx`→drop, `5xx`/network→keep for retry. Pending count + state are
  emitted via `ros:sync`.
- **Push enrolment** — `RosPWA.enablePush()` fetches the VAPID key, requests
  permission, subscribes, and registers with the server (no-op unless VAPID is
  configured).

## Technician API (`/api/tech`, JWT)

### `GET /api/tech/dashboard`
One round-trip for the dashboard (caches cleanly for offline read):
```jsonc
{
  "today": [card…], "upcoming": [card…], "completed": [card…], "overdue": [card…],
  "stats": { "today", "upcoming", "completed", "overdue", "assigned_total" },
  "clock": { "clocked_in", "category", "order_id", "since" },
  "unread_notifications": 0,
  "server_time": "…"
}
```
Cards are the signed-in technician's non-estimate orders (`assigned_to_id = me`).
Bucketing: terminal/closed → **completed**; else `due_at < now` → **overdue**;
scheduled today (or unscheduled active) → **today**; scheduled later → **upcoming**.
Clock comes from the running `TimeEntry`; unread from `StaffNotification`.

### Notifications
| Method | Path | Notes |
|---|---|---|
| GET | `/api/tech/notifications?only_unread=` | Latest 50 for the technician. |
| POST | `/api/tech/notifications/{id}/read` | Mark one read (idempotent). |
| POST | `/api/tech/notifications/read-all` | Mark all read; returns `{marked}`. |

## Web Push (`/api/push`, JWT)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/push/vapid-public-key` | `{public_key, enabled}`. `enabled=false` ⇒ client skips subscribing. |
| POST | `/api/push/subscribe` | Body `{endpoint, keys:{p256dh,auth}, user_agent?}`. Upserts by endpoint (201). |
| POST | `/api/push/unsubscribe` | Body `{endpoint}`. |

**Dispatch** — `backend/services/push.py::notify(db, *, org_id, employee_id,
kind, title, body, deep_link)` persists a `StaffNotification` (powers the unread
count) and best-effort sends Web Push to that employee's devices. Gated like
every provider seam: **without VAPID keys it is simulated/logged**; with
`VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY` + `pywebpush` installed it sends a real
encrypted push and prunes dead (404/410) subscriptions. Valid `kind`s:
`job.assigned`, `schedule.changed`, `chat.message`, `estimate.approved`,
`booking.cancelled`, `payment.received`.

**Wired triggers (live):** new order assigned to another tech → `job.assigned`;
status change on an assigned order → `schedule.changed`. (Remaining triggers are
listed in NEXT_TASK.md.)

## Auth refresh (`POST /api/auth/refresh`, JWT)
Exchanges a still-valid token for a fresh one — a sliding session for the PWA,
which keeps a long-lived on-device session and refreshes on launch.

## Data model
- `StaffNotification(id, org_id, employee_id, kind, title, body, deep_link, read_at, created_at)`
- `PushSubscription(id, org_id, employee_id, endpoint, p256dh, auth, user_agent, created_at, last_used_at)`

Migration `c25e2eee420e` adds both tables (schema total: 78 tables).

## Tests
`backend/tests/test_technician.py` (6): dashboard shape + bucketing (controlled
clock via the `db` fixture), dashboard clock-in reflection, notification flow
(create → list → read → read-all + unread count), assignment-creates-notification
for another tech, push subscribe/upsert/unsubscribe, and auth-refresh returns a
usable token. JS-layer (SW/offline/sync) tests are covered by `node --check`
syntax verification plus the manual test plan in NEXT_TASK.md, since the sandbox
has no headless-browser harness.

## Mobile work order — full surface (Part 3 + 8)

The order view (`/tech#/order/<id>`) now drives a repair end to end. All writes
go through the offline queue unless noted.

### Checklists
Reusable templates plus a per-order snapshot (applying a template copies its
items, so editing the template never rewrites a job's history).

| Method | Path | Notes |
|---|---|---|
| GET | `/api/checklist-templates?type_id=` | List active templates. |
| POST | `/api/checklist-templates` | `{name, type_id?, items:[{label,required}]}` (`can_manage_settings`). |
| DELETE | `/api/checklist-templates/{id}` | Soft-delete. |
| GET | `/api/orders/{id}/checklist` | `{items[], summary{total,done,required,required_done,complete}}`. |
| POST | `/api/orders/{id}/checklist` | Add `{label,required}` **or** apply `{template_id}`. |
| PATCH | `/api/orders/{id}/checklist/{item_id}` | `{done}` — stamps `done_by`/`done_at`. |
| DELETE | `/api/orders/{id}/checklist/{item_id}` | Remove an item. |

### Completion gate
Enforced inside `PATCH /api/orders/{id}/status` whenever the target status is
terminal (so it cannot be bypassed). It triggers only when the order has
required checklist items, or its `WorkOrderType` sets `requires_checklist` /
`requires_signature`:
- any required checklist item not done → **409** `"…required checklist item(s) not done."`
- `requires_signature` and no `Order.signature` → **409** `"…customer signature required."`

`POST /api/orders/{id}/complete` `{status?, signature?}` is the one-call mobile
button: optionally captures a signature, then moves to the given terminal status
(or the first configured terminal status), passing through the same gate.

### Labor
`GET /api/orders/{id}/labor` → `{total_seconds, running, entries[]}` aggregated
from the time clock. The mobile timer starts/stops `POST /api/timeclock/start?
switch=true {category:"job", order_id}` / `/stop`.

### Parts
The picker searches `GET /api/catalog/products?search=` and adds the chosen item
as an order line via `POST /api/orders/{id}/lines`
(`{line_type:"part", kind:"custom", description, qty, unit_price}`). (Linking to
a stock product for auto-deduction is a follow-up; see NEXT_TASK.)

### Attachments
Photos via the camera input and video/PDF via a file input, both posted to the
feed (`POST /api/feed/order/{id}`, `item_type` `photo`/`file`). Mobile uploads
are capped at ~8 MB client-side.

### Payments (Part 8)
| Method | Path | Notes |
|---|---|---|
| GET | `/api/orders/{id}/invoice-preview` | Read-only: lines, totals, `balance = total − prepaid`. |
| POST | `/api/orders/{id}/payment-link` | Creates a `PaymentIntent` for the balance; returns `{intent_id, amount, url, qr_svg, gateway}` (`can_view_finance`). Live gateway → its checkout URL; simulated → hosted pay page `/pay/{intent_id}`. |
| GET | `/api/public/pay/{intent_id}` | **Public** info for the pay page (amount, status, order #). |
| POST | `/api/payments/intents/{intent_id}/simulate-pay` | **Public** confirm for the simulated gateway. |

`qr_svg` is a real QR (segno) as an SVG data URI. `/pay/{intent_id}` serves
`pay.html`, which pays via the gateway's checkout URL (live) or the simulated
confirm hook. The mobile flow captures a signature before generating the link.

Migration `…checklists and completion gate` adds `checklist_templates`,
`checklist_template_items`, `order_checklist_items`, and
`work_order_types.requires_signature/requires_checklist` (schema total: 81).

### Tests (added)
`backend/tests/test_work_order_mobile.py` (7): template apply + toggle + summary;
completion gate blocked-until-checklist-done; signature-required gate;
`/complete` default-terminal; labor summary; invoice preview + payment link +
public pay-info + simulated confirm; zero-balance link rejected.

## Notification preferences & triggers (Part 9)

`NotificationPreference(org_id, employee_id, kind, enabled)` — absence of a row
means **on**. `services.push.notify(..., respect_prefs=True)` skips creating the
in-app StaffNotification *and* the push when the employee has muted that `kind`,
so muting silences both channels.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/tech/preferences` | All kinds with the tech's on/off state. |
| PUT | `/api/tech/preferences` | `{kind, enabled}` (422 on unknown kind). |

**Live triggers** (in addition to `job.assigned` + `schedule.changed`):
- `payment.received` — fired from `payments.mark_succeeded` to the order's assigned tech.
- `estimate.approved` — fired from the public/portal estimate accept path.
- `booking.cancelled` — fired when a booking is set to `canceled`, to the booked tech.

`chat.message` fires on an **inbound** customer message (`POST /api/chats/threads/{id}/inbound` and the Twilio webhook): Tekly notifies the technician assigned to that client's most recent open order, deep-linking to it. No such tech ⇒ no-op.

**Real push:** with `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY` set and `pywebpush`
installed (now in `requirements.txt`), `_dispatch_push` sends a real encrypted
Web Push and prunes dead (404/410) subscriptions; any send error is caught so it
never breaks the request. Without keys it stays simulated/logged.

## Customer & device history (Part 7)

Read-only aggregation for the bench, reached by tapping the customer or device in
the mobile order view.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/tech/client/{client_id}/history` | `client`, `stats{orders,outstanding_balance,open_invoices}`, `orders[]` (with per-order balance + warranty), `invoices[]`, `devices[]`, `warranties[]`, `recent_activity[]` (feed), `recommendations[]` (client-visible comments). |
| GET | `/api/tech/device/{device_id}/history` | `device`, `stats{repairs,outstanding_balance}`, `orders[]`, `warranties[]`. |

Outstanding balance sums `max(0, total − prepaid)` over the customer's open
orders. Previous recommendations surface client-visible `OrderComment`s.

Migration `…notification preferences` adds `notification_preferences`
(schema total: 82).

### Tests (added)
`backend/tests/test_notifications_part79.py` (7): preferences default-on + toggle
+ unknown-kind 422; muting suppresses a notification; `payment.received`,
`estimate.approved`, and `booking.cancelled` triggers each notify the right
technician; client-history aggregation (orders, outstanding, recommendations,
404); device-history.

## Hardening — security, GPS, offline conflicts, e2e

**Lost-device logout (Part 10).** Tokens carry a `tv` (token-version) claim.
`get_current_employee` rejects a token whose `tv` is behind
`Employee.token_version`; tokens minted before the feature (no `tv`) stay valid
until expiry. `POST /api/auth/logout-all-devices` bumps the version, signing the
employee out everywhere. Access-token TTL (`ACCESS_TOKEN_EXPIRE_MINUTES`) is the
session-expiry policy.

**GPS (Part 5).** `POST /api/timeclock/start` accepts optional `gps_lat`/`gps_lng`,
stored on `TimeEntry`. The mobile clock captures them when the browser grants
geolocation; `/switch` ignores them safely.

**Offline conflict resolution (Part 4).** When a queued mutation replays and the
server returns **409**, `RosPWA` records the mutation plus the server's reason in
a persisted conflict store and raises a review sheet. Per conflict the tech can
**Re-apply** (re-queues to run against the latest state) or **Discard**.
API: `RosPWA.getConflicts()`, `RosPWA.resolveConflict(id, 'retry'|'discard')`;
`RosPWA.status().conflicts` feeds the banner.

**End-to-end tests (Part 11).** `tests-e2e/` holds a Playwright suite
(manifest + SW scope, login, offline→queue→sync, offline navigation fallback)
and a Lighthouse audit spec, wired via `.github/workflows/e2e.yml`. They require
a real browser, so they run in CI rather than the build sandbox.
