# API v2

API v2 adds inquiries, inquiry types, tags, services, bundles, writable products, contacts with structured addresses and search, locations and employees to look ids up, work orders you can update and move through the shop's statuses, invoices with their lines and payments, and webhook subscriptions managed with a key. [v1](04-resources.md) is unchanged and keeps working; use whichever version has the resource you need. The same keys work on both.

Paths below are relative to `{BASE_URL_V2}`, which is `https://<your-shop-domain>/api/v2` (`$TEKLY_BASE_URL_V2` in the `curl` examples).

## What's the same as v1

Everything on [Requests and errors](03-requests-and-errors.md) and [Security](07-security.md) applies to v2 too:

- **HTTPS only**, and **no CORS**: call v2 from your server.
- **Envelope**: lists are `{"data": [...], "page", "page_size", "total"}`, single records `{"data": {...}}`, errors `{"detail": ...}`.
- **Paging**: `page` 1–10000, `page_size` 1–100 (default 25). Lists are newest first.
- **Strict input**: unknown fields are refused with `422`, and every text field has a length limit (below).
- **Tenant isolation**: an id from another shop gets `404`, the same as an id that doesn't exist. That includes ids inside a body: an inquiry's `client_id`, `manager_id` or `lead_type_id`, and a bundle's `category_id` or components.
- **Constraint failures** that the checks don't catch are `422`, never a `500`.
- **`Idempotency-Key`** on every `POST`, with the same rules as v1 (24 hours; the same body replays with `Idempotent-Replayed: true`, a different body is `422`, a request still running is `409`).
- **Rate limits**: the key's own per-minute limit, with `X-RateLimit-*` headers. See [Rate limits](03-requests-and-errors.md#rate-limits).
- **Auditing**: every write is in the shop's audit log, attributed to the key (`via_api_key`).
- **Cost fields** (`cost_price` on services, products and work-order lines) are read and written only by keys with `costs:read`. Without it they are left out of responses (not set to `null`), and setting them returns `403`.

## Scopes

v2 checks a scope per resource. A key can hold the broad scopes, resource scopes, or both. See [Authentication](02-authentication.md#scopes) for the full rules.

| A key with | Can use on v2 |
|---|---|
| `read` | Every `GET` below except webhooks. |
| `write` (or the legacy `*`) | Every route below except webhooks. |
| `<resource>:read` | The `GET`s of that resource. |
| `<resource>:write` | Every route of that resource (it includes `<resource>:read`). |
| `webhooks:read` / `webhooks:write` | The webhook routes. **Broad scopes never cover webhooks.** |
| `costs:read` | Adds cost fields. It allows nothing on its own. |

A key with only resource scopes can't call v1 at all: v1 needs `read` or `write`.

## Endpoints

| Resource | Read | Write | Scope resource |
|---|---|---|---|
| [Discovery](#discovery) | `GET /` | | any valid key |
| [Contacts](#contacts) | `GET /contacts` (with search), `GET /contacts/{id}` | `POST /contacts`, `PATCH /contacts/{id}` | `contacts` |
| [Locations](#locations) | `GET /locations`, `GET /locations/{id}` | | `locations` |
| [Employees](#employees) | `GET /employees`, `GET /employees/{id}` | | `employees` |
| [Work orders](#work-orders) | `GET /orders`, `GET /orders/{id}`, `GET /orders/{id}/lines` | `PATCH /orders/{id}`, `POST /orders/{id}/status` | `orders` |
| [Invoices](#invoices) | `GET /invoices`, `GET /invoices/{id}`, `GET /invoices/{id}/payments` | | `invoices` |
| [Inquiries](#inquiries) | `GET /inquiries`, `GET /inquiries/{id}` | `POST /inquiries`, `PATCH /inquiries/{id}`, `POST /inquiries/{id}/convert` | `inquiries` |
| [Inquiry types](#inquiry-types) | `GET /inquiry-types` | | `inquiries` |
| [Tags](#tags) | `GET /tags` | `POST /tags`, `PATCH /tags/{id}`, `DELETE /tags/{id}` | `tags` |
| [Services](#services) | `GET /services`, `GET /services/{id}` | `POST /services`, `PATCH /services/{id}` | `services` |
| [Bundles](#bundles) | `GET /bundles`, `GET /bundles/{id}` | `POST /bundles`, `PUT /bundles/{id}`, `DELETE /bundles/{id}` | `bundles` |
| [Products](#products) | `GET /products`, `GET /products/{id}` | `POST /products`, `PATCH /products/{id}` | `products` |
| [Webhooks](#webhooks) | `GET /webhooks` | `POST /webhooks`, `DELETE /webhooks/{id}` | `webhooks` (explicit only) |

`POST` creates return `201`, except convert and the order status change, which return `200`. `PATCH`, `PUT` and `DELETE` return `200`.

The machine-readable contract is `GET /api/v2/openapi.json` (OpenAPI 3.1, public, no key needed, served in production). It covers v2 only; v1 has its own at `/api/v1/openapi.json`. `GET /api/v2/docs` redirects to it.

---

## Discovery

### `GET /`

Any valid key can call it, whatever its scopes. It shows only the key's own shop and scopes.

```json
{
  "data": {
    "version": "2.0",
    "org_id": "3c2b8f1e-7a45-4d0b-9e61-2f8a5c7d9b10",
    "scopes": ["inquiries:write", "products:read"],
    "resources": {
      "contacts": "/api/v2/contacts",
      "inquiries": "/api/v2/inquiries",
      "inquiry_types": "/api/v2/inquiry-types",
      "tags": "/api/v2/tags",
      "services": "/api/v2/services",
      "bundles": "/api/v2/bundles",
      "products": "/api/v2/products",
      "webhooks": "/api/v2/webhooks",
      "locations": "/api/v2/locations",
      "employees": "/api/v2/employees",
      "orders": "/api/v2/orders",
      "invoices": "/api/v2/invoices"
    },
    "v1": "/api/v1",
    "docs": "/api/v2/openapi.json",
    "openapi": "/api/v2/openapi.json"
  }
}
```

`scopes` lists what the key was created with, not what they expand to.

---

## Contacts

The [v1 contact](04-resources.md#contacts), plus `address_parts`:

```json
{
  "id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
  "name": "Ana Martin",
  "type": "person",
  "phone": "+33 1 23 45 67 89",
  "email": "ana@example.com",
  "address": "5 Rue X, Paris, 75001, FR",
  "loyalty_tier": null,
  "balance": 0.0,
  "created_at": "2026-10-25T18:06:01",
  "address_parts": {"street": "5 Rue X", "city": "Paris", "region": null, "postcode": "75001", "country": "FR"}
}
```

### `GET /contacts`

Newest first. With no query it lists every contact. To find a customer, add any of these (they combine: a contact must match all of them):

| Query | Matches |
|---|---|
| `phone` | The same phone number, however either side is written. Spaces, dashes, dots, brackets and a leading `+` or `00` are ignored, and a number with a country code matches the same national number stored without it. These are the rules Tekly uses to link calls to contacts. Needs at least 4 digits (`422`). Up to 40 characters. |
| `email` | The email address, exactly, ignoring case. |
| `q` | Text found anywhere in the name, phone or email, ignoring case. At least 2 characters (`422`), up to 100. Digits in `q` also match phone numbers with their formatting removed. |

```bash
curl "$TEKLY_BASE_URL_V2/contacts?phone=%2B1%20(555)%20010-2233" -H "Authorization: Bearer $TEKLY_API_KEY"
```

```json
{
  "data": [
    {"id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31", "name": "Dana Whitfield", "type": "person",
     "phone": "5550102233", "email": "dana@example.com", "address": null, "loyalty_tier": "standard",
     "balance": 0.0, "created_at": "2026-10-25T18:06:01",
     "address_parts": {"street": null, "city": null, "region": null, "postcode": null, "country": null}}
  ],
  "page": 1, "page_size": 25, "total": 1
}
```

A phone search returns **every** match, newest first. Shops sometimes have the same person twice; pick the first, or show the list to whoever is choosing. `+1 (555) 010-2233` finds `5550102233`, `(555) 010-2233` and `+1 555 010 2233`, but not `+44 555 010 2233`. A number sent without a country code also matches the same number stored with one.

Scope: `contacts:read` (or `read`).

### `POST /contacts`

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | 1–200 characters, not blank. Trimmed. |
| `phone` | string? | | up to 40 |
| `email` | string? | | up to 254, must look like an email address |
| `client_type` | string | | `person` (default) or `organization` |
| `address` | string? | | up to 500. The single address line, used when you send no parts. |
| `addr_street` | string? | | up to 200 |
| `addr_city` | string? | | up to 120 |
| `addr_region` | string? | | up to 120 |
| `addr_postcode` | string? | | up to 20. Stored in capitals. |
| `addr_country` | string? | | A 2-letter code, such as `US` or `GB`. |

When you send any `addr_*` part, Tekly rebuilds the single `address` line from the parts (street, city, region and postcode, country), so v1, documents and exports show the same address. For countries with a fixed postcode format (for example US, CA, GB, IE, IN, AU, DE, FR, NL, PL, BR, JP), a postcode that doesn't fit is refused with `422`, as is a country that isn't two letters.

Scope: `contacts:write` (or `write`).

### `PATCH /contacts/{id}`

Changes only the fields you send. Send `null` to clear one.

| Field | Notes |
|---|---|
| `name` | 1–200, not blank. Can't be cleared. |
| `phone` | up to 40 |
| `email` | up to 254, must look like an email address |
| `address` | up to 500 |
| `addr_street`, `addr_city`, `addr_region`, `addr_postcode`, `addr_country` | As on create. Sending any part rebuilds `address` from the parts. |
| `notes` | up to 5000. The internal note on the contact; it isn't returned in the contact object. |

```bash
curl -X PATCH "$TEKLY_BASE_URL_V2/contacts/6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31" \
  -H "Authorization: Bearer $TEKLY_API_KEY" -H "Content-Type: application/json" \
  -d '{"email": "dana.w@example.com", "phone": "+1 555 010 2233"}'
```

Returns the updated contact. Fires [`contact.updated`](06-events.md#contactupdated). Scope: `contacts:write` (or `write`).

---

## Inquiries

An inquiry is a request that isn't a job yet: a web form, a missed call, a walk-in question. Shops see them on the **Inquiries** board.

### The inquiry object

| Field | Type | Description |
|---|---|---|
| `id` | string | |
| `name`, `phone`, `email` | string | The person asking. |
| `source` | string | `phone`, `walk_in`, `whatsapp`, `sms`, `email`, `instagram`, `facebook`, `web`, `api` or `other`. |
| `inquiry` | string? | What they asked for. |
| `status` | string | `new`, `in_progress`, `won` or `lost`. |
| `client_id` | string? | The matching contact. **`null` unless the key can read contacts** (`read`, `write`, `contacts:read` or `contacts:write`), so an inquiry-only key can't learn who is already a customer. |
| `manager_id`, `location_id` | string? | |
| `lead_type_id`, `lead_type` | string? | The inquiry type and its name. |
| `device_brand`, `device_model`, `serial` | string? | The device, if given. `serial` may be a serial number or an IMEI. |
| `custom_data` | object | The shop's own inquiry fields. |
| `call_count`, `last_call_at` | | Missed calls added to this inquiry. |
| `converted_order_id`, `converted_order_number` | string? | Set once it's converted. |
| `sla_due_at`, `sla_overdue` | | The respond-by time and whether it has passed. |
| `created_at`, `updated_at` | string | |

### `GET /inquiries`

Query: `status` (one of the four statuses), `page`, `page_size`.

### `POST /inquiries`

```json
{
  "name": "Jane Doe",
  "phone": "+15551234567",
  "inquiry": "Cracked screen",
  "device_brand": "Apple",
  "device_model": "iPhone 14",
  "serial": "356938035643809",
  "lead_type_id": "<optional>",
  "custom_data": {"budget_0": 200}
}
```

| Field | Notes |
|---|---|
| `name` | required, 1–200 |
| `phone` | up to 40. When there's no `client_id`, Tekly links the inquiry to the contact with this phone number, if there is one. |
| `email` | up to 200 |
| `source` | default `api` |
| `inquiry` | up to 4000 |
| `sla_minutes` | 1–43200, default 60. Sets the respond-by time. Ignored when `lead_type_id` is given: the type's own time is used. |
| `lead_type_id`, `manager_id`, `client_id` | ids from this shop, else `404`. A switched-off type is `422`. `client_id` is ignored for keys that can't read contacts. |
| `device_brand`, `device_model`, `serial` | up to 100 each |
| `custom_data` | at most 100 fields and 20,000 characters. The shop's required and locked fields are enforced (`422`). |

Fires [`inquiry.created`](06-events.md#inquirycreated). Send an `Idempotency-Key` so a retried form submission doesn't make two inquiries.

### `PATCH /inquiries/{id}`

Any of the create fields except `sla_minutes` and `client_id`, plus `status`. Only the fields you send change.

- `status: "won"` is refused (`422`) until the inquiry is converted. Use convert instead.
- A converted inquiry's status can't change (`409`).
- Changing `lead_type_id` moves the respond-by time to the new type's.

Fires [`inquiry.updated`](06-events.md#inquiryupdated).

### `POST /inquiries/{id}/convert`

Turns the inquiry into a work order (or an estimate): it links or creates the contact, attaches the device (reusing that customer's own device with the same serial or IMEI), and opens the job in the shop's default status.

Body (optional): `{"is_estimate": true}` for an estimate. The `?is_estimate=` query parameter still works but is deprecated.

**Scopes:** `inquiries:write`, plus `orders:write` (or `estimates:write` for an estimate) and `contacts:write`, because converting creates the contact and the job. `write` covers all of them.

```json
{"data": {"ok": true, "order_id": "a83d5f2c-…", "order_number": "WO-01042", "client_id": "6f1c2a9d-…", "device_id": "c51a…"}}
```

Returns `200`. Converting twice is `409`; a lost inquiry must be reopened first (`409`). Fires [`inquiry.converted`](06-events.md#inquiryconverted), and [`order.created`](06-events.md#ordercreated) for a work order.

## Inquiry types

### `GET /inquiry-types`

The shop's active types, with their respond-by time: `{"data": [{"id", "name", "sla_minutes"}]}`. Not paged. Scope: `inquiries:read`.

---

## Tags

`{"id", "name", "color", "created_at"}`.

| Route | Body | Notes |
|---|---|---|
| `GET /tags` | | paged |
| `POST /tags` | `name` (1–40, required), `color` (up to 20) | A name already used in the shop, in any case, is `409`. |
| `PATCH /tags/{id}` | `name`, `color` | |
| `DELETE /tags/{id}` | | Removes the tag from everything it was on. |

Fire [`tag.created`, `tag.updated` and `tag.deleted`](06-events.md#tagcreated-tagupdated-tagdeleted).

## Services

`{"id", "name", "description", "category", "category_id", "price", "duration_min", "warranty_days", "barcode", "unit", "is_labor", "bookable", "active"}`, plus `cost_price` with `costs:read`.

| Field | Limits |
|---|---|
| `name` | required on create, 1–300 |
| `description` | up to 2000 |
| `price`, `cost_price` | 0 to 10,000,000 |
| `duration_min` | 1–1440, default 60 |
| `warranty_days` | 0–3650 |
| `barcode` | up to 100 |
| `unit` | 1–24, default `pc` |
| `is_labor`, `bookable`, `active` | booleans |

`PATCH` changes only what you send; send `"barcode": null` to clear the barcode.

## Bundles

A bundle is a kit of services and products sold together.

```json
{
  "id": "…", "name": "Battery swap", "price_mode": "fixed", "price": 79.0, "category_id": null,
  "components": [{"id": "…", "ref_type": "service", "ref_id": "…", "name": "Diagnostics", "qty": 1.0,
                  "std_price": 25.0, "missing": false, "device_variant": false}],
  "sum_price": 104.0, "broken": false, "effective_price": 79.0
}
```

| Field | Notes |
|---|---|
| `name` | required, 1–200 |
| `price_mode` | `sum` (the components' prices added up, default) or `fixed` |
| `price` | 0 to 10,000,000, used when `price_mode` is `fixed` |
| `category_id` | a category in this shop, else `404` |
| `components` | at most 200. Each has `ref_type` (`service` or `product`), `ref_id` (this shop's, else `404`), `qty` (more than 0, up to 10,000) and `device_variant`. A create needs at least one (`400`). |

`PUT /bundles/{id}` changes only the fields you send. Components, when sent, replace the list. `DELETE` switches the bundle off, and it disappears from lists. Fire the [`bundle.*` events](06-events.md#bundlecreated-bundleupdated-bundledeleted).

## Products

The [v1 product](04-resources.md#products), now writable.

| Field | Limits |
|---|---|
| `name` | required on create, 1–300 |
| `sku`, `mpn`, `brand`, `category`, `barcode` | up to 100 |
| `uom` | 1–10, default `pcs` |
| `sell_price`, `cost_price` | 0 to 10,000,000 |
| `active` | boolean |

- An `mpn` already used by another active product in the shop, in any case, is `409`.
- `PATCH` skips `null` for `name`, `uom`, the prices and `active`, which can't be cleared.
- Setting `cost_price` also needs `costs:read`. Without it the request gets `403`.

Fire [`product.created` and `product.updated`](06-events.md#productcreated-productupdated).

---

## Locations

The shop's locations (branches). Use an `id` as `location_id` when you create work orders or appointments.

```json
{"id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48", "name": "Main Street", "address": "12 High St, Springfield",
 "phone": "+1 555 0100", "timezone": "America/New_York", "active": true}
```

`timezone` is the clock the location's [wall-clock times](03-requests-and-errors.md) (`scheduled_for`, `scheduled_to`) are in.

| Route | Notes |
|---|---|
| `GET /locations` | Paged. `?active=true` for open locations only, `false` for switched-off ones. |
| `GET /locations/{id}` | |

Scope: `locations:read` (or `read`).

## Employees

The staff directory, for picking an assignee, a manager or a technician to book.

```json
{"id": "9d0a6b3e-1c2f-4e5d-8a7b-6c5d4e3f2a10", "name": "Sam Ortega", "public_name": "Sam O.",
 "role": "Technician", "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48", "active": true}
```

| Field | Description |
|---|---|
| `name` | The name staff see in the app. |
| `public_name` | The name customers see (booking page, messages), from the shop's **Name on public pages** setting: the full name, the first name, the first name and last initial, or the employee's own public name. Show this one to customers. |
| `role` | The role's name, or `null`. |
| `location_id` | The employee's home location, or `null`. |
| `active` | `false` for someone who has left. |

**Never included:** email, phone, pay, wage or rates, commission, tax ids, or anything to do with signing in, whatever the key's scopes (`costs:read` included).

| Route | Notes |
|---|---|
| `GET /employees` | Paged. Filters: `?location_id=` (home location) and `?active=true|false`. |
| `GET /employees/{id}` | |

Scope: `employees:read` (or `read`).

---

## Work orders

v2 work orders are the jobs on the board. Estimates aren't included: an estimate's id is `404` here (v1 [`GET /estimates`](04-resources.md#estimates) reads them). To create a work order, use v1 [`POST /orders`](04-resources.md#post-orders) or convert an [inquiry](#post-inquiriesidconvert).

### The order object

```json
{
  "id": "a83d5f2c-9b1e-4f7a-8c6d-2e1f0a9b8c7d",
  "number": "WO-01042",
  "status": "in_repair",
  "status_name": "In repair",
  "status_group": "in_progress",
  "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
  "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48",
  "assigned_to_id": "9d0a6b3e-1c2f-4e5d-8a7b-6c5d4e3f2a10",
  "manager_id": null,
  "priority": 0,
  "complaint": "Cracked screen",
  "device": {"id": "c51a7e2d-4b3f-4a1e-9d8c-7b6a5f4e3d2c", "asset_type": "Device", "brand": "Apple",
             "model": "iPhone 14", "serial": "F2LXK0Q1N72J", "imei": "356938035643809", "vin": null, "hin": null,
             "color": "Black"},
  "labour_total": 60.0, "parts_total": 89.0, "discount": 0.0, "tax": 0.0, "total": 149.0, "written_off": 0.0,
  "due_at": "2026-10-29T17:00:00",
  "scheduled_for": "2026-10-28T10:30:00",
  "scheduled_to": "2026-10-28T11:30:00",
  "duration_min": 60,
  "cancel_reason": null,
  "archived": false,
  "tracking_url": "https://shop.example.com/track/Xf3kQ9…",
  "created_at": "2026-10-25T18:06:01",
  "updated_at": "2026-10-26T09:12:44",
  "closed_at": null
}
```

| Field | Description |
|---|---|
| `status`, `status_name`, `status_group` | The status key, the shop's name for it, and its group: `new`, `in_progress`, `pending`, `ready`, `closed` or `dropped`. |
| `priority` | `0` normal, `1` urgent, `2` VIP. |
| `device` | The customer's device on the job, or `null`. Passcodes, photos and prices aren't included. |
| `written_off` | The unpaid balance written off when the job was dropped. |
| `cancel_reason` | Why a dropped job was dropped. |
| `tracking_url` | The customer's tracking page for this job. Anyone with the link can see the job's progress, so send it only to the customer. |
| `due_at`, `created_at`, `updated_at`, `closed_at` | UTC. |
| `scheduled_for`, `scheduled_to` | The shop's wall-clock time at the order's location. |

`GET /orders/{id}` adds `lines`, a summary: `{"count": 2, "by_kind": {"service": 1, "product": 1}, "url": "/api/v2/orders/{id}/lines"}`.

### `GET /orders`

Newest first. Filters (they combine):

| Query | Notes |
|---|---|
| `status` | A status key, for example `ready`. |
| `client_id`, `location_id` | |
| `updated_since` | Orders changed at or after this time: details, status, totals. ISO 8601; an offset (`Z`, `+02:00`) is converted to UTC, without one it's taken as UTC. |
| `created_since` | Orders created at or after this time. Same format. |

To keep a copy in sync, save the time you start each run and pass it as `updated_since` next time. A bad date is `422`.

### `GET /orders/{id}`

The order object plus the `lines` summary.

### `GET /orders/{id}/lines`

Every line on the job, as the app lists them. Not paged.

```json
{
  "data": [
    {"id": "e1c0…", "kind": "service", "line_type": "labour", "description": "Screen replacement", "qty": 1.0,
     "unit_price": 60.0, "discount": 0.0, "discount_type": "pct", "tax_rate": 0.0, "tax_mode": "imposed",
     "tax": 0.0, "total": 60.0, "warranty_days": 90},
    {"id": "e1c1…", "kind": "product", "line_type": "part", "description": "iPhone 14 screen", "qty": 1.0,
     "unit_price": 89.0, "discount": 10.0, "discount_type": "pct", "tax_rate": 8.0, "tax_mode": "imposed",
     "tax": 6.41, "total": 86.51, "warranty_days": 90, "cost_price": 41.0}
  ]
}
```

| Field | Description |
|---|---|
| `kind` | `service`, `product`, `bundle` or `custom`. |
| `discount`, `discount_type` | A percentage (`pct`) or an amount off the line (`fixed`). |
| `tax_rate`, `tax_mode`, `tax` | The rate, whether tax is `included` in the price or `imposed` on top, and the tax amount in the line. |
| `total` | What the customer pays for the line, after discount and with tax. |
| `cost_price` | Only for keys with `costs:read`. |

### `PATCH /orders/{id}`

Changes only the fields you send. `null` clears a field, except `priority` and `location_id`, where it's ignored.

| Field | Notes |
|---|---|
| `complaint` | up to 5000 |
| `priority` | `0`, `1` or `2` |
| `assigned_to_id`, `manager_id` | An active employee of this shop (`404` if not the shop's, `422` if deactivated or not someone the shop lists as an assignee or manager). A new assignee gets the app's "new job" notification. |
| `due_at` | ISO 8601. An offset is converted to UTC; without one it's UTC. |
| `scheduled_for`, `scheduled_to` | ISO 8601, the shop's **wall-clock** time. With an offset it's converted to the location's clock, so `2026-10-28T14:30:00Z` at a New York location is stored as `2026-10-28T10:30:00`. `scheduled_to` can't be before `scheduled_for` (`422`). |
| `location_id` | Another location of this shop (`404` otherwise). Scheduled times you send in the same request are read in the new location's clock. |

```bash
curl -X PATCH "$TEKLY_BASE_URL_V2/orders/a83d5f2c-9b1e-4f7a-8c6d-2e1f0a9b8c7d" \
  -H "Authorization: Bearer $TEKLY_API_KEY" -H "Content-Type: application/json" \
  -d '{"assigned_to_id": "9d0a6b3e-1c2f-4e5d-8a7b-6c5d4e3f2a10", "scheduled_for": "2026-10-28T10:30:00", "priority": 1}'
```

Returns the order (with the `lines` summary). Dates that aren't ISO 8601 are `400`.

- A closed order, or one in a status that locks edits, is `409`. Reopen it with a status change first.
- `status` isn't a field here (`422`): use the status route below.

Fires [`order.updated`](06-events.md#orderupdated). Scope: `orders:write` (or `write`).

### `POST /orders/{id}/status`

Moves the order to another status **through the app's own status change**, so everything the shop has set up applies, exactly as when staff change it on screen.

| Field | Notes |
|---|---|
| `status` | Required. One of the shop's active work-order status keys. Anything else is `422`, and the message lists the keys. |
| `comment` | Optional, up to 5000. Saved with the change in the order's status history. |
| `cancel_reason` | Optional, up to 120. Kept as the job's `cancel_reason` when the status is in the **dropped** group. |

```bash
curl -X POST "$TEKLY_BASE_URL_V2/orders/a83d5f2c-9b1e-4f7a-8c6d-2e1f0a9b8c7d/status" \
  -H "Authorization: Bearer $TEKLY_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: status-a83d5f2c-ready" \
  -d '{"status": "ready", "comment": "Tested and packed"}'
```

```json
{
  "data": {"id": "a83d5f2c-…", "number": "WO-01042", "status": "ready", "status_name": "Ready", "status_group": "ready", "…": "…"},
  "change": {"from": "in_repair", "to": "ready", "is_closing": false}
}
```

Returns `200` with the order after the change. `change.is_closing` is `true` when the new status closes the job (a closed or dropped status).

**What applies**, as in the app:

- **Status transfer rules.** A rule set for all roles applies; rules for one role don't (a key has no role). A move the rules don't allow is `409`.
- **Requirements of the new status**: a comment on the order, every checklist item done, or the order paid in full. Each is `409` with the shop's message, for example `'QC passed' requires all 3 checklist item(s) done (1 left).`
- **Close gates** when the status closes the job: required checklist items, the customer's signature, fields the shop marks "fill in before closing", and products still waiting to be written off from stock. Each is `409`.
- **Dropped jobs** (a status in the dropped group: not collected, cancelled…) skip the "finish the work" close gates, and the unpaid balance is written off, so the job stops showing as owed. Moving the job out of the dropped group puts the balance back.
- **Side effects**: checklists that attach on the status, the auto-drafted invoice, warranty, the customer's ledger, commissions, asset moves, automations, the shop's customer and staff notification rules, the feedback request, and the webhooks [`order.status_changed`](06-events.md#orderstatus_changed) and [`order.closed`](06-events.md#orderclosed).

The change is in the shop's audit log and the order's history, attributed to the key (`via_api_key`), with no employee. Unlike staff, a key isn't limited to its own orders and can reopen a closed order: `orders:write` is the permission.

Send an `Idempotency-Key` to make a retry safe: the same key and body replays the first response, and a change a gate stopped can be retried with the same key. Scope: `orders:write` (or `write`).

---

## Invoices

### The invoice object

The [v1 invoice](04-resources.md#invoices), plus `order_ids`, `display_mode`, `due_at` and `created_at`:

```json
{
  "id": "0b7d3e9a-5c1f-4a2b-8e6d-9f0a1b2c3d4e",
  "number": "INV-00031",
  "order_id": "a83d5f2c-9b1e-4f7a-8c6d-2e1f0a9b8c7d",
  "order_ids": ["a83d5f2c-9b1e-4f7a-8c6d-2e1f0a9b8c7d"],
  "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
  "status": "partial",
  "display_mode": "detailed",
  "subtotal": 149.0, "tax": 0.0, "total": 149.0,
  "amount_paid": 100.0, "credited": 0.0, "balance_due": 49.0,
  "issued_at": "2026-10-26T09:00:00",
  "due_at": "2026-11-09T09:00:00",
  "created_at": "2026-10-26T09:00:00"
}
```

| Field | Description |
|---|---|
| `order_id`, `order_ids` | The first work order it bills, and every work order it bills. |
| `amount_paid` | Payments, net of refunds. |
| `credited` | Issued credit notes. |
| `balance_due` | `total − amount_paid − credited`, never below 0. |
| `display_mode` | How the lines are shown: `detailed`, `simplified` or `one_line`. |

### `GET /invoices`

Newest first. Filters (they combine):

| Query | Notes |
|---|---|
| `status` | `draft`, `issued`, `partial`, `paid`, `cancelled`… |
| `client_id` | |
| `issued_since` | Issued at or after this time. ISO 8601, UTC unless it has an offset. |
| `updated_since` | Created, issued, or with a payment or refund recorded (on the invoice or on a job it bills), at or after this time. Other edits (a changed due date, a cancelled invoice) don't count, so keep a periodic full sweep too. |

### `GET /invoices/{id}`

The invoice plus `lines`, **as the customer sees them**, following `display_mode`: `detailed` lists every line of every job it bills plus lines added on the invoice, `simplified` one line per job, `one_line` a single line.

```json
{
  "data": {
    "id": "0b7d3e9a-…", "number": "INV-00031", "status": "partial", "total": 149.0, "amount_paid": 100.0,
    "credited": 0.0, "balance_due": 49.0, "order_ids": ["a83d5f2c-…"], "…": "…",
    "lines": [
      {"description": "Screen replacement", "qty": 1.0, "unit_price": 60.0, "total": 60.0},
      {"description": "iPhone 14 screen", "qty": 1.0, "unit_price": 89.0, "total": 89.0}
    ]
  }
}
```

Each line has `description`, `qty`, `unit_price` and `total`. On a job with several items, a `detailed` line also has `item_id` and `item`.

### `GET /invoices/{id}/payments`

Money recorded on the invoice, and refunds made on the jobs it bills. Oldest first, not paged.

```json
{
  "data": [
    {"id": "5e2a…", "kind": "payment", "method": "card", "amount": 100.0, "reference": null,
     "paid_at": "2026-10-26T09:05:12", "invoice_id": "0b7d3e9a-…", "order_id": "a83d5f2c-…"},
    {"id": "5e2b…", "kind": "refund", "method": "card", "amount": 20.0, "reference": "Overpayment refund",
     "paid_at": "2026-10-27T11:40:03", "invoice_id": null, "order_id": "a83d5f2c-…"}
  ]
}
```

`kind` is `payment`, `prepayment` or `refund`; `amount` is always positive. `method` is `cash`, `card`, `transfer`, `balance` (the customer's credit) or `online`. Which till or account it went to, who took it, and card-reader charge ids stay in the app.

Scope for every invoice route: `invoices:read` (or `read`).

---

## Webhooks

Manage the shop's webhook endpoints with a key. These routes need **`webhooks:read`** (list) or **`webhooks:write`** (add, delete). `read`, `write` and `*` never include them, so an older key can't point the shop's events at a URL of its choosing.

The rules are the same as in **Settings → Integrations → Webhooks** (see [Webhooks](05-webhooks.md)):

### `GET /webhooks`

```json
{
  "data": [{"id": "…", "url": "https://hooks.example.com/tekly", "description": null, "events": "inquiry.created,order.created",
            "active": true, "secret_hint": "whsec_…Q_Hc", "previous_secret_expires_at": null,
            "failing_since": null, "disabled_reason": null, "created_at": "2026-10-25T18:06:01"}],
  "events": ["order.created", "…"],
  "aliases": {"booking.created": "appointment.created", "lead.created": "inquiry.created"},
  "max_endpoints": 10
}
```

Secrets are never listed, only a hint.

### `POST /webhooks`

| Field | Notes |
|---|---|
| `url` | required, up to 500. A public `https://` address on port 443 or 8443, with no credentials in it (`400` otherwise). |
| `events` | a list or a comma-separated string of [catalogue events](06-events.md) or aliases, or `*` (the default). An unknown name is `422`. |
| `description` | up to 200 |
| `active` | default `true` |

- A shop can have up to **10** endpoints (`409` beyond that).
- The response is the endpoint plus **`secret`**, a `whsec_…` signing secret. **It's shown only here.** If you send an `Idempotency-Key` and retry, the replayed response has no `secret`; if you've lost it, rotate it in the app.

### `DELETE /webhooks/{id}`

Stops deliveries and abandons queued retries. The delivery log is kept. Signing-secret rotation, test events and redelivery are in the app.

---

## Errors you'll see on v2

| Status | When |
|---|---|
| `403` `API key lacks '<resource>:<action>' scope` | The key can't use this resource. |
| `404` | The id, or an id in the body, isn't in this shop. |
| `409` | A duplicate tag name or product MPN, an inquiry already converted, the webhook endpoint limit, a status change the shop's rules stop (with the shop's message), editing a closed or locked order, or an `Idempotency-Key` still in progress. |
| `422` | A field fails validation, an unknown field, an unknown event or status, a bad postcode or date filter, a search term that's too short, an assignee the shop doesn't list, or an `Idempotency-Key` reused with a different body. |
| `429` | The key's rate limit. Wait `Retry-After` seconds. |
