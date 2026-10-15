# Work Order Management — API & Behaviour

Covers the full work-order lifecycle, the configurable status pipeline and its
automation triggers, estimates and approval (staff + public), customer
signatures, scheduling/calendar, attachments, the activity timeline, warranty,
and the customer portal. All `/api/*` endpoints require a Bearer JWT
(`get_current_employee`) and are tenant-scoped by `org_id` unless noted as
**public** (token-authenticated, no JWT).

## 1. Lifecycle

A work order moves: created → (status pipeline) → terminal/closed. Estimates are
orders with `is_estimate=true` that convert into work orders.

| Method | Path | Notes |
|---|---|---|
| POST | `/api/orders` | Create. Body: `location_id` (required), `client_id?`, `device_id?`, `complaint?`, `priority?`, `status?`, `type_id?`, `scheduled_for?`, `duration_min?` (default 60). Returns the order. |
| GET | `/api/orders` | List, `{total, items}`. Filters incl. `is_estimate`, `status`, `limit`. |
| GET | `/api/orders/{id}` | Detail incl. `lines`, `comments`, `status_logs`, and live SLA fields `minutes_in_status` / `status_time_limit_min` / `status_sla_overdue`. |
| GET | `/api/orders/board` | Kanban grouped by status; each card carries `overdue`. |
| PATCH | `/api/orders/{id}/status` | Move status (see §2/§3). |
| POST | `/api/orders/{id}/convert` | Estimate → work order. |
| PATCH | `/api/orders/{id}/type` | Change `WorkOrderType`. |
| DELETE | `/api/orders/{id}` | Delete. |
| POST | `/api/orders/bulk` | Bulk create. |

`status_entered_at` is stamped on every status change; `closed_at` is set when a
terminal status is entered and cleared on reopen.

## 2. Status pipeline

Statuses are per-org, configurable, and ordered. `OrderStatus` fields:
`status_group`, `is_terminal`, `is_default`, `is_estimate`, `triggers_invoice`,
`triggers_notification` (retired, see §3), `triggers_payment`, `locks_edits`, `shows_accept`,
`converts_to_order`, `time_limit_min`, `moves_asset_to`, `sort_order`, `color`.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/statuses?is_estimate=` | List statuses for the work-order or estimate pipeline. |
| POST `/api/statuses` · PUT `/api/statuses/{id}` · DELETE `/api/statuses/{id}` | | CRUD (`can_manage_settings`). `key` auto-slugifies from `name` if omitted. |
| POST | `/api/statuses/reorder` | Reorder. |
| GET `/api/statuses/transitions` · POST · DELETE `/api/statuses/transitions/{tid}` | | Allowed `from→to` transitions, optionally role-scoped. |
| GET | `/api/statuses/allowed/{order_id}` | Keys the current order may move to (no rules from a status ⇒ unrestricted). |

**Enforcement:** `PATCH /api/orders/{id}/status` calls `is_transition_allowed`
and returns **403** for a disallowed move.

## 3. Automation triggers (on status change)

A single status change can fan out to several side-effects, all in
`PATCH /api/orders/{id}/status`:

- `triggers_invoice` → auto-creates an invoice for the order.
- Customer messages come **only** from the notification rules (Settings ›
  Notifications; `services/notification_rules.evaluate`, event `status_changed`, or
  `closed` for a terminal status). `triggers_notification` is retired: it used to drive
  a legacy template send that only ran when no rule matched, so with the seeded
  "every status change" rule it was nearly always ignored. Flagged statuses were
  converted to equivalent per-status rules (`services/legacy_status_notifs.py`,
  migration `nt144p4`, startup self-heal) and the flag cleared; an old client posting
  `triggers_notification: true` gets the same conversion. The status editor shows what
  the rules send and toggles the status's own rule.
- `triggers_payment` → exposes a payment context.
- `locks_edits` → subsequent line add/edit/delete return **409**.
- `is_terminal` → sets `closed_at` and **activates warranty** (§8).
- Emits the `order.status_changed` **webhook** event.
- **SLA (`time_limit_min`)** → see below.

### SLA / time-in-status
If a non-terminal status defines `time_limit_min`, an order breaches SLA once
`now − status_entered_at` exceeds it.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/orders/sla/breaches` | `{total, items[]}`; each item: `order_id, order_number, status, status_name, minutes_in_status, limit_min, over_by_min`. |

A periodic background job `status-sla-sweep` (every 10 min, on the Phase-0 job
runner) records a one-time, deduped `[sla]` event on each breaching order's
timeline. Order detail also exposes `status_sla_overdue`.

## 4 & 5. Estimates and approval

Estimates are `is_estimate=true` orders. Estimate-pipeline statuses can set
`shows_accept` and `converts_to_order`. Approving an estimate (staff via
convert, or customer via the public/portal endpoints) records a client-visible
feed event and, when the target status is `converts_to_order`, auto-creates the
work order and links it via `converted_order_id` / `converted_from_id`.

- `POST /api/orders/{id}/convert` — staff conversion.
- Public/portal approval — §6.

## 6. Public approval page & customer portal  (public, token-auth)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/public/track/{token}` | Single order: status stage, client-visible timeline, and (if estimate) approval payload. Served by `track.html`. |
| POST | `/api/public/track/{token}/respond` | Body `{decision: accept\|decline, signature?, name?}`. Applies the decision, captures the signature, auto-converts on accept. |
| GET | `/api/public/portal/{token}` | Client portal home: the client's orders and estimates. |
| GET | `/api/public/portal/{token}/order/{order_id}` | Portal order detail (`can_approve` for estimates). |
| POST | `/api/public/portal/{token}/order/{order_id}/respond` | Approve/decline a specific estimate from the portal. |

`token` is the order's `public_token` (or the client portal token). Booking is a
separate public page (`book.html`).

## 7. Customer signatures

- `POST /api/orders/{id}/signature` — staff capture. Body `{signature: <data URL>, note?}`. Stored on `Order.signature` and as an `EventFeedItem(item_type="signature")`.
- Public signature is captured through the `respond` endpoints (§6).

## 8. Scheduling & calendar

| Method | Path | Notes |
|---|---|---|
| PATCH | `/api/orders/{id}/schedule` | Set `scheduled_for` / `scheduled_to` (or `duration_min`) and optional `resource_id`. |
| GET | `/api/orders/scheduler/day` | Orders for a given day. |
| GET | `/api/orders/scheduler/range` | Orders across a date range (calendar feed). |

`WorkOrderType` carries `sched_default_duration` and `sched_required`. The SPA
exposes a calendar/scheduler view (`OrdersPage.setView('scheduler')`).

## 9. Attachments

Attachments are timeline media items on the order.

| Method | Path | Notes |
|---|---|---|
| POST | `/api/feed/order/{order_id}` | `item_type` ∈ `comment\|photo\|signature\|file`; media via `media_data` (data URL/base64), `media_name`, `media_mime`. Media items require `media_data`; size-capped. |
| GET | `/api/feed/order/{order_id}` | Timeline incl. attachments. |
| DELETE | `/api/feed/item/{item_id}` | Remove an item. |

## 10. Activity timeline

`GET /api/feed/order/{order_id}` returns the polymorphic timeline: comments,
photos, files, signatures, and **system** events (status changes, SLA breaches,
estimate decisions). Comments: `POST /api/orders/{id}/comments` with
`visible_to_client` to control portal visibility. `OrderStatusLog` records the
status history shown under the order's History tab.

## 11. Warranty

`Order.warranty_until`. When an order enters a terminal status, warranty is
activated automatically from the **longest** `warranty_days` across its lines
(`closed_at + max(warranty_days)`). Surfaced on the order detail and the public
track/portal pages.

## Tests

`backend/tests/test_work_orders.py` (14 tests) covers: lifecycle
create→line→status→close→reopen; status pipeline seeding; automation
(invoice + edit-lock); estimate convert; attachments + timeline; scheduling +
calendar range; warranty activation; signature capture; customer portal list +
detail; portal estimate approval auto-convert; invalid token 404; SLA breach
detection + idempotent sweep; SLA endpoint/detail shape; transition enforcement
(403). Run: `pytest backend/tests/test_work_orders.py -q`.

## Update — checklists, completion gate, mobile payments

The lifecycle now supports **checklists** and a **completion gate**. Closing an
order (entering any terminal status, via the normal `PATCH …/status` or
`POST …/complete`) enforces: all *required* `OrderChecklistItem`s done when the
order has required items or its `WorkOrderType.requires_checklist` is set; and a
captured `Order.signature` when `WorkOrderType.requires_signature` is set —
otherwise **409**. Checklist API and per-order endpoints, plus invoice preview
and order payment links (QR), are documented in `docs/mobile-technician.md`.
