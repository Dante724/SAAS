# Tekly Public API v1

A token-authenticated REST API for integrating Tekly with external systems
(storefronts, BI tools, custom apps). Base path: `/api/v1`.

Interactive OpenAPI docs are served at `/docs` (Swagger UI) and the raw schema at
`/openapi.json`; the public-API routes are tagged `public-api-v1`.

## Authentication

The public API uses API keys, not the staff session token. Create a key in the
app (Settings, or `POST /api/api-keys` while logged in as staff). The plaintext
key is shown once at creation and looks like `rok_<prefix>_<secret>`.

Send it as a bearer token:

```
Authorization: Bearer rok_3f9a1c22_x7K...　
```

Each key is scoped to one organization and carries scopes (`read`, `write`, or
`*`). Read endpoints require a valid key; write endpoints require the `write`
scope (or `*`). A revoked key returns `401`.

### Managing keys (staff session)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/api-keys` | List keys (no secrets) |
| POST | `/api/api-keys` | Create; body `{name, scopes}`; returns `key` once |
| DELETE | `/api/api-keys/{id}` | Revoke (deactivate) |

## Response envelope

- List: `{"data": [ ... ], "page": 1, "page_size": 25, "total": 137}`
- Single: `{"data": { ... }}`

List endpoints accept `page` (default 1) and `page_size` (default 25, max 100).
Errors use standard HTTP status codes with `{"detail": "..."}`.

## Resources

Discovery: `GET /api/v1` returns the version, your org id, your scopes, and the
resource index.

| Resource | Read | Write | Notes |
|----------|------|-------|-------|
| Contacts | `GET /contacts`, `GET /contacts/{id}` | `POST /contacts` | CRM clients |
| Orders | `GET /orders`, `GET /orders/{id}` | `POST /orders` | `?status=` filter; excludes estimates |
| Estimates | `GET /estimates`, `GET /estimates/{id}` | — | Orders flagged `is_estimate` |
| Products | `GET /products`, `GET /products/{id}` | — | Inventory parts/products |
| Bookings | `GET /bookings` | `POST /bookings` | Online/CRM bookings |
| Invoices | `GET /invoices`, `GET /invoices/{id}` | — | Read-only |
| Time entries | `GET /time-entries` | `POST /time-entries` | `?order_id=` / `?employee_id=` filters |

### Write payloads

`POST /contacts`
```json
{ "name": "Jane Doe", "phone": "+15551234567", "email": "jane@x.com", "client_type": "person" }
```

`POST /orders`
```json
{ "location_id": "<loc>", "client_id": "<optional>", "complaint": "Cracked screen", "duration_min": 90 }
```
Emits the `order.created` webhook event.

`POST /bookings`
```json
{ "location_id": "<loc>", "scheduled_for": "2026-07-01T14:00:00", "client_name": "Jane", "duration_min": 60 }
```
Emits the `booking.created` webhook event.

`POST /time-entries`
```json
{ "employee_id": "<emp>", "order_id": "<optional>", "started_at": "2026-07-01T09:00:00", "duration_min": 60 }
```

## Examples

List the 10 most recent open orders:
```
curl -H "Authorization: Bearer rok_..." \
     "https://app.repairos.dev/api/v1/orders?status=new&page=1&page_size=10"
```

Create a contact:
```
curl -X POST -H "Authorization: Bearer rok_..." -H "Content-Type: application/json" \
     -d '{"name":"Jane Doe","phone":"+15551234567"}' \
     "https://app.repairos.dev/api/v1/contacts"
```

## Webhooks (companion to the API)

Register endpoints under `POST /api/webhooks` (staff session) to receive event
callbacks: `order.created`, `order.status_changed`, `booking.created`,
`time_entry.stopped`, `payment.recorded`. Deliveries are signed with the
endpoint secret and logged at `GET /api/webhooks/deliveries`.

## Reporting read API (for BI tools)

Any registered report can be pulled as JSON, CSV, or XLSX (staff session):

- `GET /api/reports/run/{report_key}` → JSON `{columns, rows}`
- `GET /api/reports/export-csv/{report_key}` → text/csv
- `GET /api/reports/export/{report_key}` → xlsx

Report keys include the labor analytics from the time clock:
`labor_utilization`, `labor_by_job`, `labor_cost`. List all via
`GET /api/reports/catalog`.
