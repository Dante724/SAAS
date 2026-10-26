# Resources

Every endpoint in v1. Paths are relative to `{BASE_URL}` (`https://<your-shop-domain>/api/v1`). Every endpoint needs an API key with a broad scope, `read` or `write` (see [Authentication](02-authentication.md)); keys limited to some things only work on v2. The [envelope, pagination and errors](03-requests-and-errors.md) work the same everywhere.

Inquiries, tags, services, bundles, writable products, structured contact addresses and webhook subscriptions are in [API v2](10-api-v2.md).

| Resource | Endpoints | Scope |
|---|---|---|
| [Discovery](#discovery) | `GET /` | `read` |
| [Contacts](#contacts) | `GET /contacts`, `GET /contacts/{id}`, `POST /contacts` | `read`, `write` |
| [Work orders](#work-orders) | `GET /orders`, `GET /orders/{id}`, `POST /orders` | `read`, `write` |
| [Estimates](#estimates) | `GET /estimates`, `GET /estimates/{id}` | `read` |
| [Products](#products) | `GET /products`, `GET /products/{id}` | `read` (+ `costs:read`) |
| [Appointments](#appointments) | `GET /appointments`, `POST /appointments` | `read`, `write` |
| [Invoices](#invoices) | `GET /invoices`, `GET /invoices/{id}` | `read` |
| [Credit notes](#credit-notes) | `GET /credit-notes`, `GET /credit-notes/{id}` | `read` |
| [Time entries](#time-entries) | `GET /time-entries`, `POST /time-entries` | `read`, `write` (+ `costs:read`) |

All `POST`s accept an optional [`Idempotency-Key`](03-requests-and-errors.md#idempotency) header and return `201 Created`.

---

## Discovery

### `GET /`

Scope: `read`. Returns which shop the key belongs to, the scopes it was created with, and where each resource lives.

```json
{
  "data": {
    "version": "1.0",
    "org_id": "3c2b8f1e-7a45-4d0b-9e61-2f8a5c7d9b10",
    "scopes": ["costs:read", "write"],
    "resources": {
      "contacts": "/api/v1/contacts",
      "orders": "/api/v1/orders",
      "estimates": "/api/v1/estimates",
      "products": "/api/v1/products",
      "appointments": "/api/v1/appointments",
      "invoices": "/api/v1/invoices",
      "credit_notes": "/api/v1/credit-notes",
      "time_entries": "/api/v1/time-entries"
    },
    "docs": "/api/v1/openapi.json",
    "openapi": "/api/v1/openapi.json"
  }
}
```

`GET /openapi.json` is the OpenAPI 3.1 document. It's public and needs no key. `GET /docs` redirects to it.

---

## Contacts

A contact is a customer: a person or an organization.

### The contact object

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique id. |
| `name` | string | Full name or company name. |
| `type` | string | `person` or `organization`. |
| `phone` | string? | |
| `email` | string? | |
| `address` | string? | |
| `loyalty_tier` | string? | For example `standard`, `silver` or `gold`. |
| `balance` | number? | What the contact owes (positive), or holds in credit. |
| `created_at` | string | UTC timestamp. |

### `GET /contacts`

Scope: `read`. Lists contacts, newest first.

| Query | Type | Description |
|---|---|---|
| `page` | integer | 1–10000, default 1. |
| `page_size` | integer | 1–100, default 25. |

There's no search or filter yet: you can't look a contact up by email or phone.

```bash
curl "$TEKLY_BASE_URL/contacts?page_size=1" -H "Authorization: Bearer $TEKLY_API_KEY"
```

```json
{
  "data": [
    {
      "id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
      "name": "Dana Whitfield",
      "type": "person",
      "phone": "+1 555 0100",
      "email": "dana@example.com",
      "address": "12 High St",
      "loyalty_tier": "standard",
      "balance": 0,
      "created_at": "2026-10-25T18:06:01.474163"
    }
  ],
  "page": 1,
  "page_size": 1,
  "total": 212
}
```

### `GET /contacts/{id}`

Scope: `read`. Returns one contact, or `404` `Contact not found`.

### `POST /contacts`

Scope: `write`. Creates a contact.

| Field | Type | Required | Rules |
|---|---|---|---|
| `name` | string | yes | 1–200 characters, not blank. Leading and trailing spaces are trimmed. |
| `phone` | string | no | Up to 40 characters. |
| `email` | string | no | Up to 254 characters, and must be a valid address. An empty string counts as no email. |
| `address` | string | no | Up to 500 characters. |
| `client_type` | string | no | `person` (default) or `organization`. |

```bash
curl -X POST "$TEKLY_BASE_URL/contacts" \
  -H "Authorization: Bearer $TEKLY_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 0c6a2f4e-8d1b-4b7a-9e35-71f0c2d8a944" \
  -d '{
    "name": "Dana Whitfield",
    "phone": "+1 555 0100",
    "email": "dana@example.com",
    "address": "12 High St",
    "client_type": "person"
  }'
```

Returns `201` with the [contact object](#the-contact-object).

**Events:** [`contact.created`](06-events.md#contactcreated).

Tekly doesn't de-duplicate contacts, so calling this twice for the same person creates two contacts. An `Idempotency-Key` only protects against retries of the same request.

---

## Work orders

A work order is a repair job. Estimates share the same object (see [Estimates](#estimates)).

### The order object

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique id. |
| `number` | string | The shop's order number, for example `WO-01042`. |
| `status` | string | The shop's own status key, for example `new`, `in_repair` or `ready`. Each shop sets up its own statuses. |
| `is_estimate` | boolean | `true` for an estimate. |
| `client_id` | string? | The contact. |
| `location_id` | string? | The shop location. |
| `assigned_to_id` | string? | The assigned employee. |
| `complaint` | string? | What the customer reported. |
| `labour_total` | number | |
| `parts_total` | number | |
| `total` | number | |
| `due_at` | string? | |
| `scheduled_for` | string? | Shop wall-clock time. |
| `created_at` | string | UTC timestamp. |

Line items, devices and payments aren't part of the v1 object. [API v2](10-api-v2.md#work-orders) has them, and can update an order and change its status.

### `GET /orders`

Scope: `read`. Lists **work orders only** (no estimates), newest first.

| Query | Type | Description |
|---|---|---|
| `status` | string | Optional. An exact status key, up to 40 characters, for example `ready`. |
| `updated_since` | string | Optional. Only orders changed at or after this time. ISO 8601; an offset (`Z`, `+02:00`) is converted to UTC, without one it's taken as UTC. A bad value is `422`. |
| `page` | integer | 1–10000, default 1. |
| `page_size` | integer | 1–100, default 25. |

```bash
curl "$TEKLY_BASE_URL/orders?status=ready&page_size=10" -H "Authorization: Bearer $TEKLY_API_KEY"
```

```json
{
  "data": [
    {
      "id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
      "number": "WO-01042",
      "status": "ready",
      "is_estimate": false,
      "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
      "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48",
      "assigned_to_id": "9d0a6b3e-2c8f-4e1d-b75a-3f9c1e8d4a27",
      "complaint": "Cracked screen, iPhone 13",
      "labour_total": 40.0,
      "parts_total": 89.0,
      "total": 129.0,
      "due_at": null,
      "scheduled_for": "2026-10-28T10:00:00",
      "created_at": "2026-10-25T18:06:01.518836"
    }
  ],
  "page": 1,
  "page_size": 10,
  "total": 3
}
```

### `GET /orders/{id}`

Scope: `read`. Returns one order, or `404` `Order not found`. This also returns an **estimate** if you pass an estimate's id; check `is_estimate`.

### `POST /orders`

Scope: `write`. Creates a work order.

| Field | Type | Required | Rules |
|---|---|---|---|
| `location_id` | string | yes | A location in this shop. Up to 64 characters. |
| `client_id` | string | no | A contact in this shop. |
| `complaint` | string | no | Up to 5000 characters. |
| `assigned_to_id` | string | no | An employee in this shop. |
| `duration_min` | integer | no | Planned minutes, 5–1440. Default 60. |

```bash
curl -X POST "$TEKLY_BASE_URL/orders" \
  -H "Authorization: Bearer $TEKLY_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 7d3e9b1a-4c2f-4a8e-b6d0-2e9f5c1a7b38" \
  -d '{
    "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48",
    "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
    "complaint": "Cracked screen, iPhone 13",
    "duration_min": 60
  }'
```

```json
{
  "data": {
    "id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
    "number": "WO-01042",
    "status": "new",
    "is_estimate": false,
    "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
    "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48",
    "assigned_to_id": null,
    "complaint": "Cracked screen, iPhone 13",
    "labour_total": 0,
    "parts_total": 0,
    "total": 0,
    "due_at": null,
    "scheduled_for": null,
    "created_at": "2026-10-25T18:06:01.518836"
  }
}
```

How an API-created order behaves:

- It gets the shop's next order number, and starts in the shop's **default work-order status**, the same as an order made in the app.
- Checklists set to attach on that status are attached.
- It's logged on the order's timeline as *Work order created through the API*, and in the audit log against your key.
- **It doesn't send the customer any message.** Notification rules aren't run for API-created orders.
- An id that doesn't exist in this shop gives `404` `Location not found`, `Contact not found` or `Employee not found`.

**Events:** [`order.created`](06-events.md#ordercreated).

---

## Estimates

Estimates are orders with `is_estimate: true`. They use the [order object](#the-order-object). v1 can read estimates but not create them.

### `GET /estimates`

Scope: `read`. Lists estimates, newest first. Takes `page` and `page_size`. There's no status filter.

### `GET /estimates/{id}`

Scope: `read`. Returns one estimate, or `404` `Estimate not found` (also when the id is a work order).

---

## Products

Inventory products and parts.

### The product object

| Field | Type | Description |
|---|---|---|
| `id` | string | |
| `sku` | string? | |
| `mpn` | string? | Manufacturer part number. |
| `name` | string | |
| `brand` | string? | |
| `category` | string? | |
| `sell_price` | number? | |
| `cost_price` | number? | **Only with `costs:read`.** Otherwise the field is left out. |
| `uom` | string? | Unit of measure, for example `pcs`. |
| `barcode` | string? | |
| `active` | boolean? | |

Stock levels aren't part of the v1 object.

### `GET /products`

Scope: `read`. Lists products, newest first. Takes `page` and `page_size`.

```json
{
  "data": [
    {
      "id": "c51a8e3f-2d9b-4f6a-8c14-7e0b3d5a9f62",
      "sku": "SCR-IP13",
      "mpn": null,
      "name": "iPhone 13 screen",
      "brand": "Apple",
      "category": "Screens",
      "sell_price": 129.0,
      "uom": "pcs",
      "barcode": null,
      "active": true,
      "cost_price": 54.0
    }
  ],
  "page": 1,
  "page_size": 25,
  "total": 1
}
```

(`cost_price` shown as returned to a key with `costs:read`.)

### `GET /products/{id}`

Scope: `read`. Returns one product, or `404` `Product not found`.

---

## Appointments

### The appointment object

| Field | Type | Description |
|---|---|---|
| `id` | string | |
| `client_id` | string? | The contact, if linked. |
| `client_name` | string? | The name shown on the calendar. |
| `service_id` | string? | |
| `employee_id` | string? | |
| `status` | string | For example `booked` or `confirmed`. |
| `scheduled_for` | string | Shop wall-clock time. |
| `duration_min` | integer? | |
| `note` | string? | |

### `GET /appointments`

Scope: `read`. Lists appointments, latest `scheduled_for` first. Takes `page` and `page_size`. There's no date filter and no `GET /appointments/{id}` yet.

### `POST /appointments`

Scope: `write`. Books an appointment.

| Field | Type | Required | Rules |
|---|---|---|---|
| `location_id` | string | yes | A location in this shop. |
| `scheduled_for` | string | yes | ISO 8601. Without an offset it's the location's wall-clock time; with an offset (`Z`, `+01:00`) it's converted to the location's clock. Up to 64 characters. |
| `client_name` | string | no | Up to 200 characters. |
| `client_id` | string | no | A contact in this shop. |
| `service_id` | string | no | A service in this shop. |
| `employee_id` | string | no | An employee in this shop. |
| `duration_min` | integer | no | 5–1440. Default 60. |
| `note` | string | no | Up to 5000 characters. |

```bash
curl -X POST "$TEKLY_BASE_URL/appointments" \
  -H "Authorization: Bearer $TEKLY_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: booking-88412" \
  -d '{
    "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48",
    "scheduled_for": "2026-10-28T10:30:00",
    "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
    "client_name": "Dana Whitfield",
    "employee_id": "9d0a6b3e-2c8f-4e1d-b75a-3f9c1e8d4a27",
    "duration_min": 60,
    "note": "Drop-off"
  }'
```

```json
{
  "data": {
    "id": "e02f7c4a-9b1d-4e6f-a83c-5d2b0f9e7c16",
    "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
    "client_name": "Dana Whitfield",
    "service_id": null,
    "employee_id": "9d0a6b3e-2c8f-4e1d-b75a-3f9c1e8d4a27",
    "status": "confirmed",
    "scheduled_for": "2026-10-28T10:30:00",
    "duration_min": 60,
    "note": "Drop-off"
  }
}
```

- API appointments are created as `confirmed`.
- With an `employee_id`, Tekly refuses a time that overlaps another `booked` or `confirmed` appointment for that employee: `409` `That employee already has an appointment 10:30-11:30`. A slot taken at the same moment by someone else gives `409` `That time slot was just taken — please pick another`.
- Without an `employee_id`, there's no overlap check.
- A `scheduled_for` that isn't ISO 8601 gives `400` `Invalid scheduled_for: expected an ISO datetime, got 'next tuesday'`.
- An unknown id gives `404` `Location not found`, `Contact not found`, `Service not found` or `Employee not found`.

**Events:** [`appointment.created`](06-events.md#appointmentcreated).

`/bookings` is an old alias for `/appointments` (`GET` and `POST`). It still works, but isn't in the OpenAPI document. Use `/appointments`.

---

## Invoices

### The invoice object

| Field | Type | Description |
|---|---|---|
| `id` | string | |
| `number` | string? | For example `INV-0318`. |
| `order_id` | string? | The work order it bills. |
| `client_id` | string? | |
| `status` | string | For example `draft`, `issued`, `partial`, `paid` or `cancelled`. |
| `subtotal` | number | |
| `tax` | number | |
| `total` | number | |
| `amount_paid` | number | |
| `credited` | number | Total of credit notes against it. |
| `balance_due` | number | What's still owed. |
| `issued_at` | string? | UTC timestamp. |

Invoice lines and the due date aren't part of the v1 object. The due date is in the [invoice webhook payload](06-events.md#invoicecreated).

### `GET /invoices`

Scope: `read`. Lists invoices, newest first. Takes `page` and `page_size`.

```json
{
  "data": [
    {
      "id": "c7e19a4d-6f2b-4d8e-9a05-1b3c7e2f8d64",
      "number": "INV-0318",
      "order_id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
      "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
      "status": "paid",
      "subtotal": 129.0,
      "tax": 0,
      "total": 129.0,
      "amount_paid": 129.0,
      "credited": 0.0,
      "balance_due": 0.0,
      "issued_at": "2026-10-24T18:02:52.259935"
    }
  ],
  "page": 1,
  "page_size": 25,
  "total": 1
}
```

### `GET /invoices/{id}`

Scope: `read`. Returns one invoice, or `404` `Invoice not found`.

---

## Credit notes

### The credit note object

| Field | Type | Description |
|---|---|---|
| `id` | string | |
| `number` | string? | |
| `status` | string | `draft`, `issued` or `canceled`. |
| `client_id` | string? | |
| `invoice_id` | string? | The invoice it credits. |
| `reason` | string? | |
| `subtotal` | number | |
| `tax` | number | |
| `total` | number | |
| `issued_at` | string? | |
| `canceled_at` | string? | |
| `lines` | array | Each line has `description`, `qty`, `unit_price`, `tax_rate`, `net`, `tax` and `total`. |

### `GET /credit-notes`

Scope: `read`. Lists credit notes with their lines, newest first. Takes `page` and `page_size`.

```json
{
  "data": [
    {
      "id": "5a2d9f0b-7e3c-4b1a-8d64-2f9e0c7b1a53",
      "number": "CN-0012",
      "status": "issued",
      "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
      "invoice_id": "c7e19a4d-6f2b-4d8e-9a05-1b3c7e2f8d64",
      "reason": "Screen protector returned",
      "subtotal": 15.0,
      "tax": 0.0,
      "total": 15.0,
      "issued_at": "2026-10-26T09:14:30",
      "canceled_at": null,
      "lines": [
        { "description": "Screen protector", "qty": 1, "unit_price": 15.0, "tax_rate": 0, "net": 15.0, "tax": 0.0, "total": 15.0 }
      ]
    }
  ],
  "page": 1,
  "page_size": 25,
  "total": 1
}
```

### `GET /credit-notes/{id}`

Scope: `read`. Returns one credit note, or `404` `Credit note not found`.

---

## Time entries

Time logged by staff, on a job or otherwise.

### The time entry object

| Field | Type | Description |
|---|---|---|
| `id` | string | |
| `employee_id` | string | |
| `order_id` | string? | The work order, if the time was on a job. |
| `category` | string? | For example `job` or `other`. |
| `started_at` | string? | UTC timestamp. |
| `ended_at` | string? | UTC timestamp. |
| `duration_sec` | integer? | |
| `billable` | boolean? | |
| `state` | string? | For example `running` or `stopped`. |
| `cost_rate` | number? | The employee's cost per hour. **Only with `costs:read`.** |
| `billable_rate` | number? | The rate billed per hour. **Only with `costs:read`.** |

### `GET /time-entries`

Scope: `read`. Lists time entries, latest `started_at` first. Voided entries are left out.

| Query | Type | Description |
|---|---|---|
| `order_id` | string | Optional. Only entries on this work order. |
| `employee_id` | string | Optional. Only this employee's entries. |
| `page` | integer | 1–10000, default 1. |
| `page_size` | integer | 1–100, default 25. |

### `POST /time-entries`

Scope: `write`. Logs a finished block of time.

| Field | Type | Required | Rules |
|---|---|---|---|
| `employee_id` | string | yes | An employee in this shop. |
| `started_at` | string | yes | ISO 8601. An offset is converted to UTC; without one it's taken as UTC. |
| `duration_min` | number | yes | More than 0, at most 1440. Decimals allowed. |
| `order_id` | string | no | A work order in this shop. |
| `billable` | boolean | no | Defaults to `true` when `order_id` is set, otherwise `false`. |
| `note` | string | no | Up to 5000 characters. Stored, but not returned by v1. |

```bash
curl -X POST "$TEKLY_BASE_URL/time-entries" \
  -H "Authorization: Bearer $TEKLY_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 2c9f4e7a-1d3b-4a6e-8f05-9b2c7d1e4a80" \
  -d '{
    "employee_id": "9d0a6b3e-2c8f-4e1d-b75a-3f9c1e8d4a27",
    "order_id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
    "started_at": "2026-10-25T09:00:00Z",
    "duration_min": 45
  }'
```

```json
{
  "data": {
    "id": "10e08c0d-1c29-4571-9be1-023f1871daa2",
    "employee_id": "9d0a6b3e-2c8f-4e1d-b75a-3f9c1e8d4a27",
    "order_id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
    "category": "job",
    "started_at": "2026-10-25T09:00:00",
    "ended_at": "2026-10-25T09:45:00",
    "duration_sec": 2700,
    "billable": true,
    "state": "stopped"
  }
}
```

- The entry is stored as `stopped`, with `category` `job` when it has an `order_id` and `other` when it doesn't. The cost and billable rates come from the employee's settings.
- An unknown id gives `404` `Employee not found` or `Order not found`.

**Events:** none. [`time_entry.stopped`](06-events.md#time_entrystopped) fires only when a running timer is stopped in Tekly.

---

## Finding ids

Look ids up with [API v2](10-api-v2.md). The same key works there if it has `read`:

| You need | Look it up with |
|---|---|
| `location_id` | [`GET /api/v2/locations`](10-api-v2.md#locations) |
| `employee_id`, `assigned_to_id` | [`GET /api/v2/employees`](10-api-v2.md#employees), filtered by `?location_id=` and `?active=true` |
| `service_id` | [`GET /api/v2/services`](10-api-v2.md#services) |
| `client_id` | [`GET /api/v2/contacts?phone=…`](10-api-v2.md#get-contacts) or `?email=…` |

Locations, employees and services rarely change: store their ids in your integration's configuration. Look contacts up each time.
