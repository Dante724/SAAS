# Tekly Public API (v1 and v2)

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

Each key is scoped to one organization and carries scopes. The broad scopes
`read`, `write` and `*` cover every resource (`write` includes `read`). A key can
instead be narrowed to resources with `<resource>:read` or `<resource>:write`,
where the resource is one of `contacts`, `orders`, `estimates`, `products`,
`appointments`, `invoices`, `credit_notes`, `time_entries`, `inquiries`, `tags`,
`services`, `bundles`, `webhooks`. Narrow keys work on `/api/v2` only; v1 needs a
broad scope. A revoked key returns `401`; a missing scope returns `403`.

### Rate limits

Each key may make 120 requests a minute by default (set `rate_limit_per_min`
when creating the key, up to 6000). Over the limit the API answers `429` with a
`Retry-After` header.

### Managing keys (staff session)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/api-keys` | List keys (no secrets) |
| POST | `/api/api-keys` | Create; body `{name, scopes, rate_limit_per_min?}`; returns `key` once |
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
| Appointments | `GET /appointments` | `POST /appointments` | Online/CRM appointments (formerly "bookings") |
| Invoices | `GET /invoices`, `GET /invoices/{id}` | — | Read-only |
| Credit notes | `GET /credit-notes`, `GET /credit-notes/{id}` | — | Read-only |
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

`POST /appointments`
```json
{ "location_id": "<loc>", "scheduled_for": "2026-07-01T14:00:00", "client_name": "Jane", "duration_min": 60 }
```
Emits the `appointment.created` webhook event.

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

## API v2

Base path `/api/v2`, same keys, envelope and paging as v1. `GET /api/v2` lists
the resources and the key's scopes.

| Resource | Read | Write | Scope |
|----------|------|-------|-------|
| Contacts | `GET /contacts`, `GET /contacts/{id}` | `POST /contacts` | `contacts` |
| Inquiries | `GET /inquiries` (`?status=`), `GET /inquiries/{id}` | `POST /inquiries`, `PATCH /inquiries/{id}`, `POST /inquiries/{id}/convert` | `inquiries` |
| Inquiry types | `GET /inquiry-types` | — | `inquiries` |
| Tags | `GET /tags` | `POST /tags`, `PATCH /tags/{id}`, `DELETE /tags/{id}` | `tags` |
| Services | `GET /services`, `GET /services/{id}` | `POST /services`, `PATCH /services/{id}` | `services` |
| Bundles | `GET /bundles`, `GET /bundles/{id}` | `POST /bundles`, `PUT /bundles/{id}`, `DELETE /bundles/{id}` | `bundles` |
| Products | `GET /products`, `GET /products/{id}` | `POST /products`, `PATCH /products/{id}` | `products` |
| Webhooks | `GET /webhooks` | `POST /webhooks`, `DELETE /webhooks/{id}` | `webhooks` |

Contacts in v2 also carry `address_parts` (`street`, `city`, `region`,
`postcode`, `country`) and accept `addr_street`, `addr_city`, `addr_region`,
`addr_postcode`, `addr_country` (a 2-letter code). The single `address` line is
filled from the parts, so v1 and older documents keep working. Postcodes are
checked for countries with a fixed format.

`POST /inquiries` accepts an `Idempotency-Key` header: sending the same key again
returns the inquiry the first request created (`200`) instead of a duplicate.

```json
{ "name": "Jane Doe", "phone": "+15551234567", "inquiry": "Cracked screen",
  "device_brand": "Apple", "device_model": "iPhone 14", "serial": "356938035643809",
  "lead_type_id": "<optional>", "custom_data": { "budget_0": 200 } }
```

`POST /inquiries/{id}/convert` (`?is_estimate=true` for an estimate) opens the
job with the contact and device attached and returns `order_id` and `order_number`.

## Webhooks (companion to the API)

Register endpoints under `POST /api/webhooks` (staff session) or
`POST /api/v2/webhooks` (an API key with `webhooks:write`; the signing secret is
returned once). Subscribe with a comma-separated list of events, or `*`:

| Event | When |
|-------|------|
| `order.created` | A job is created (also from an inquiry or the API) |
| `order.status_changed` | A job's status changes |
| `order.closed` | A job moves to a closing status |
| `estimate.accepted` | The customer accepts an estimate online |
| `payment.recorded` | A payment is recorded |
| `appointment.created` | An appointment is created with the API |
| `time_entry.stopped` | A time entry is stopped |
| `inquiry.created` | An inquiry is created (staff, API, missed call, Tekly Go) |
| `inquiry.updated` | An inquiry is changed |
| `inquiry.converted` | An inquiry becomes a job |
| `tag.created`, `tag.updated`, `tag.deleted` | Tags change |
| `bundle.created`, `bundle.updated`, `bundle.deleted` | Bundles change |
| `product.created`, `product.updated` | Products change through the API |

Older name: an endpoint subscribed to `booking.created` (the name these docs used
before) receives `appointment.created`.

Deliveries are signed with the endpoint secret (`X-Tekly-Signature: sha256=<hmac>`
of the raw body) and logged at `GET /api/webhooks/deliveries`.

## Reporting read API (for BI tools)

Any registered report can be pulled as JSON, CSV, or XLSX (staff session):

- `GET /api/reports/run/{report_key}` → JSON `{columns, rows}`
- `GET /api/reports/export-csv/{report_key}` → text/csv
- `GET /api/reports/export/{report_key}` → xlsx

Report keys include the labor analytics from the time clock:
`labor_utilization`, `labor_by_job`, `labor_cost`. List all via
`GET /api/reports/catalog`.
