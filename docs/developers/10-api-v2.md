# API v2

API v2 adds inquiries, inquiry types, tags, services, bundles, writable products, contacts with structured addresses, and webhook subscriptions managed with a key. [v1](04-resources.md) is unchanged and keeps working; use whichever version has the resource you need. The same keys work on both.

Paths below are relative to `{BASE_URL_V2}`, which is `https://<your-shop-domain>/api/v2`.

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
- **Cost fields** (`cost_price` on services and products) are read and written only by keys with `costs:read`. Without it they are left out of responses (not set to `null`), and setting them returns `403`.

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
| [Contacts](#contacts) | `GET /contacts`, `GET /contacts/{id}` | `POST /contacts` | `contacts` |
| [Inquiries](#inquiries) | `GET /inquiries`, `GET /inquiries/{id}` | `POST /inquiries`, `PATCH /inquiries/{id}`, `POST /inquiries/{id}/convert` | `inquiries` |
| [Inquiry types](#inquiry-types) | `GET /inquiry-types` | | `inquiries` |
| [Tags](#tags) | `GET /tags` | `POST /tags`, `PATCH /tags/{id}`, `DELETE /tags/{id}` | `tags` |
| [Services](#services) | `GET /services`, `GET /services/{id}` | `POST /services`, `PATCH /services/{id}` | `services` |
| [Bundles](#bundles) | `GET /bundles`, `GET /bundles/{id}` | `POST /bundles`, `PUT /bundles/{id}`, `DELETE /bundles/{id}` | `bundles` |
| [Products](#products) | `GET /products`, `GET /products/{id}` | `POST /products`, `PATCH /products/{id}` | `products` |
| [Webhooks](#webhooks) | `GET /webhooks` | `POST /webhooks`, `DELETE /webhooks/{id}` | `webhooks` (explicit only) |

`POST` creates return `201`, except convert, which returns `200`. `PATCH`, `PUT` and `DELETE` return `200`.

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
      "webhooks": "/api/v2/webhooks"
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
| `409` | A duplicate tag name or product MPN, an inquiry already converted, the webhook endpoint limit, or an `Idempotency-Key` still in progress. |
| `422` | A field fails validation, an unknown field, an unknown event, a bad postcode, or an `Idempotency-Key` reused with a different body. |
| `429` | The key's rate limit. Wait `Retry-After` seconds. |
