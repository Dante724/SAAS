# Batch 2 — Config Editors: honest status + Directories

## Correction to the research doc
The Batch-2 research (from screenshots) flagged Notifications, Statuses, and Forms
editors as "missing." Code inspection shows they were **already substantially
built**:
- **Statuses editor** (`statuses.js`, 232 lines): color, group, drag-reorder,
  add/edit modals, trigger tags (invoice/notify/payment), terminal. Backend
  `OrderStatus` already carries `triggers_*`, `locks_edits`, `time_limit_min`,
  `moves_asset_to`, `shows_accept`, `converts_to_order`.
- **Forms editor** (`forms.js`, 327 lines): per-type field list, drag-reorder,
  required flag, add custom field. Backend `CustomField` (entity_type, sort_order,
  required, options).
- **Notifications editor** (`notifications.js`): template list + create modal over
  the existing `NotificationTemplate` model (channel, trigger_event,
  trigger_status, subject, body_template), and rendering is wired into sending.

So the genuine Batch-2 net-new was narrower than the research implied.

## Shipped this pass
### Directories (net-new)
Org-customizable reference lists that feed dropdowns app-wide.
- **Models** `Directory(key, name, is_system)` + `DirectoryItem(directory_id,
  value, parent_id, sort_order, active)`. System directories seeded on first
  access: asset_type, manufacturer, cancel_reason, lead_source, payment_method.
- **API** `/api/directories` (list/create/delete — system undeletable),
  `/{id}/items` (list/add), `/items/{id}` (soft delete), and
  **`/by-key/{key}/items`** — the runtime resolver dropdowns call.
- **UI** Settings → Directories: master list + per-directory value editor.

### Notifications trigger events widened
The editor now offers `comment_added`, `ready_for_pickup`, `estimate_sent`,
`invoice_sent` in addition to `status_change` / `order_created` (backend matcher
already handled the generic events).

## Still open (Batch-2 UI-parity, deliberately deferred)
- Work-order **Invoices & payments tab** layout parity + per-line discount column.
- POS **gift-card/loyalty tender** surface (redemption endpoints exist in
  `engage.py`; needs the POS UI hook).
- Forms editor reaching **built-in/system fields** with "display on card" toggles
  (custom fields are covered today).
- Wiring directory items into the actual asset-type / manufacturer dropdowns
  (resolver endpoint is ready; consuming it in the order/asset forms is the hook).

Migration +2 tables (schema 94). Tests +3 (suite 93).
