# Events

Every event you can subscribe to. Each delivery wraps the event's payload in the [envelope](05-webhooks.md#the-envelope): what's shown below is the `data` object.

| Event | Fires when |
|---|---|
| [`order.created`](#ordercreated) | A work order is created in the app or through the API. |
| [`order.updated`](#orderupdated) | A work order's details change. The event lists which fields. |
| [`order.status_changed`](#orderstatus_changed) | A work order or estimate moves to another status. |
| [`order.closed`](#orderclosed) | A work order is closed or dropped. |
| [`estimate.accepted`](#estimateaccepted) | A customer accepts an estimate. |
| [`payment.recorded`](#paymentrecorded) | An online payment succeeds. |
| [`appointment.created`](#appointmentcreated) | An appointment is booked online, by staff or through the API. |
| [`appointment.updated`](#appointmentupdated) | An appointment is rescheduled, or its note changes, through API v2. The event lists which fields. |
| [`appointment.cancelled`](#appointmentcancelled) | An appointment is cancelled through API v2. |
| [`contact.created`](#contactcreated) | A customer or company contact is added. |
| [`inquiry.created`](#inquirycreated) | A new inquiry is added, from any source. |
| [`inquiry.updated`](#inquiryupdated) | An inquiry's details or status change. |
| [`inquiry.converted`](#inquiryconverted) | An inquiry is turned into a work order or estimate. |
| [`invoice.created`](#invoicecreated) | An invoice is created. |
| [`invoice.paid`](#invoicepaid) | An invoice is paid in full. |
| [`time_entry.stopped`](#time_entrystopped) | A timer on a job is stopped. |
| [`tag.created`](#tagcreated-tagupdated-tagdeleted) | A tag is added. |
| [`tag.updated`](#tagcreated-tagupdated-tagdeleted) | A tag is renamed or recoloured. |
| [`tag.deleted`](#tagcreated-tagupdated-tagdeleted) | A tag is deleted. |
| [`bundle.created`](#bundlecreated-bundleupdated-bundledeleted) | A bundle (a kit of services and products) is added. |
| [`bundle.updated`](#bundlecreated-bundleupdated-bundledeleted) | A bundle changes. |
| [`bundle.deleted`](#bundlecreated-bundleupdated-bundledeleted) | A bundle is deleted. |
| [`product.created`](#productcreated-productupdated) | A product is added through API v2. |
| [`product.updated`](#productcreated-productupdated) | A product is changed through API v2. |

Subscribe to `*` (**All events** in the app) to get every event, including ones added later. [`webhook.test`](05-webhooks.md#send-a-test-event) is sent only when you ask for it, and isn't subscribable.

### Aliases

| Old name | Current name | Behaviour |
|---|---|---|
| `booking.created` | `appointment.created` | An endpoint subscribed to `booking.created` still gets the event, **labelled `booking.created`** in `event` and `Tekly-Event`. New subscriptions should use `appointment.created`. |
| `lead.created` | `inquiry.created` | Inquiries used to be called leads. An endpoint subscribed to `lead.created` gets each new inquiry **once**, labelled `lead.created`, with the `inquiry.created` payload. An endpoint subscribed to both names, or to `*`, still gets it once. New subscriptions should use `inquiry.created`. |

### Deprecated fields

| Field | Where | Use instead |
|---|---|---|
| `number` | `data` of `order.created` and `order.updated` | `order_number`. It holds the same value. |
| `at` | Every envelope | `created_at` |

### General notes

- Timestamps in record payloads are UTC, ISO 8601 with `Z` and no fractions, for example `2026-10-25T18:06:01Z`. Scheduled times (`scheduled_for`) are the exception: they are shop wall-clock time with no `Z`.
- Money is a JSON number in the shop's currency.
- Ids match the [API](04-resources.md), so you can fetch the full record, for example `GET /orders/{data.id}`.
- A field shown as `null` below can also hold a value, and the other way round.

---

## `order.created`

A work order was created, in the app or through `POST /orders`. Estimates don't fire it.

**Sources:**

- **New order** in the app.
- [`POST /orders`](04-resources.md#post-orders).
- Converting an inquiry to a work order, in the app, in Tekly Go or with [`POST /api/v2/inquiries/{id}/convert`](10-api-v2.md#post-inquiriesidconvert).

Orders made in other ways don't fire it yet: by converting an appointment or a chat, by duplicating or splitting an order, or by an e-commerce order sync. Payloads are the same whichever source fires them.

```json
{
  "id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
  "order_number": "WO-01042",
  "number": "WO-01042",
  "status": "new",
  "total": 0.0,
  "is_estimate": false,
  "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
  "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48",
  "assigned_to_id": null,
  "priority": 0,
  "due_at": null,
  "created_at": "2026-10-25T18:06:01Z"
}
```

`number` is a **deprecated** alias of `order_number`.

## `order.updated`

A work order's details changed. One event is sent per saved change, however many fields it touched. `changed_fields` lists them. It isn't sent for the save that created the order.

**Sources:** any change, from any screen, to one of these fields: `status`, `priority`, `assigned_to_id`, `manager_id`, `location_id`, `client_id`, `device_id`, `complaint`, `diagnosis`, `conclusion`, `due_at`, `scheduled_for`, `scheduled_to`, `total`, `discount`, `type_id`, `warranty_until`, `is_estimate`, `archived`.

```json
{
  "id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
  "order_number": "WO-01042",
  "number": "WO-01042",
  "status": "in_repair",
  "total": 129.0,
  "is_estimate": false,
  "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
  "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48",
  "assigned_to_id": "9d0a6b3e-2c8f-4e1d-b75a-3f9c1e8d4a27",
  "priority": 0,
  "due_at": null,
  "created_at": "2026-10-25T18:06:01Z",
  "changed_fields": ["status", "assigned_to_id"]
}
```

The rest of the payload shows the order **after** the change. `changed_fields` can name fields that aren't in the payload, such as `diagnosis`. Read them from the API if you need them. A status change sends `order.updated` *and* `order.status_changed`.

`number` is a **deprecated** alias of `order_number`.

## `order.status_changed`

A work order or an estimate moved to another status.

**Sources:** a status change from the app (the order screen, the board or the technician app).

```json
{
  "id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
  "order_number": "WO-01042",
  "from": "in_repair",
  "to": "ready"
}
```

`from` and `to` are the shop's own status keys.

## `order.closed`

A work order moved from an open status to a closed or dropped one. Moving between two closed statuses doesn't fire it again. Estimates don't fire it.

**Sources:** a status change into a closed or dropped status.

```json
{
  "id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
  "order_number": "WO-01042",
  "status": "done",
  "total": 129.0,
  "closed_at": "2026-10-25T18:06:54Z"
}
```

`closed_at` is UTC, in the same ISO 8601 format as the other timestamps.

## `estimate.accepted`

A customer accepted an estimate.

**Sources:**

- The customer's tracking page.
- The customer portal.

```json
{
  "id": "b4f0e2a7-6c1d-4e8b-9a35-8d2f7c0e1b96",
  "number": "E01043",
  "total": 245.0
}
```

`number` is the estimate's number. This payload has no `order_number`.

## `payment.recorded`

An online payment succeeded.

**Sources:** a successful online payment through one of the shop's connected payment providers, for example when a customer pays from a payment link. Cash, card-reader and manually recorded payments don't fire it.

```json
{
  "intent_id": "pi_7c3e9a1f2b8d4e6a",
  "order_id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
  "amount": 129.0,
  "provider": "stripe",
  "payment_id": "d1e8b4a2-5f3c-4a7e-9b06-2c8f1e7d3a59"
}
```

`provider` is `stripe`, `square` or `sumup`. `intent_id` and `payment_id` are Tekly ids. `order_id` can be `null` for a payment not tied to a work order.

## `appointment.created`

An appointment was booked.

**Sources:**

- The shop's online booking page.
- Staff, on the calendar.
- [`POST /appointments`](04-resources.md#post-appointments).

```json
{
  "id": "e02f7c4a-9b1d-4e6f-a83c-5d2b0f9e7c16",
  "scheduled_for": "2026-10-28T10:30:00",
  "duration_min": 60,
  "status": "confirmed",
  "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
  "client_name": "Dana Whitfield",
  "service_id": null,
  "employee_id": "9d0a6b3e-2c8f-4e1d-b75a-3f9c1e8d4a27",
  "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48",
  "order_id": null,
  "created_at": "2026-10-25T18:06:36Z"
}
```

**Note:** `scheduled_for` is the shop's **wall-clock** time, as in the API, so it has no `Z` or offset. `2026-10-28T10:30:00` means 10:30 on the shop's clock.

Subscribed as `booking.created`? You get this same payload, labelled `booking.created`.

## `appointment.updated`

An appointment was rescheduled, moved to another employee, or its note changed, through [`PATCH /api/v2/appointments/{id}`](11-booking-api.md#reschedule). `changed_fields` lists what changed: any of `scheduled_for`, `duration_min`, `employee_id` and `note`. The rest is the `appointment.created` payload, showing the appointment after the change.

```json
{
  "id": "e02f7c4a-9b1d-4e6f-a83c-5d2b0f9e7c16",
  "scheduled_for": "2026-10-29T14:00:00",
  "duration_min": 60,
  "status": "booked",
  "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
  "client_name": "Dana Whitfield",
  "service_id": "5c7d1e2f-8a9b-4c3d-b6e5-0f1a2b3c4d5e",
  "employee_id": "41be7d2c-6a5f-4b3e-9d81-2c0f7e6a5b49",
  "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48",
  "order_id": null,
  "created_at": "2026-10-25T18:06:36Z",
  "changed_fields": ["scheduled_for", "employee_id"]
}
```

**Not yet:** changes made by staff in the app don't fire this event yet. Poll [`GET /api/v2/appointments?updated_since=`](11-booking-api.md#keeping-in-sync) to catch those.

## `appointment.cancelled`

An appointment was cancelled through [`POST /api/v2/appointments/{id}/cancel`](11-booking-api.md#cancel). It fires once: cancelling again doesn't fire it again. `reason` is what the caller sent, or `null`.

```json
{
  "id": "e02f7c4a-9b1d-4e6f-a83c-5d2b0f9e7c16",
  "scheduled_for": "2026-10-28T10:30:00",
  "duration_min": 60,
  "status": "canceled",
  "client_id": null,
  "client_name": "Dana Whitfield",
  "service_id": "5c7d1e2f-8a9b-4c3d-b6e5-0f1a2b3c4d5e",
  "employee_id": "9d0a6b3e-2c8f-4e1d-b75a-3f9c1e8d4a27",
  "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48",
  "order_id": null,
  "created_at": "2026-10-25T18:06:36Z",
  "reason": "Customer called to cancel"
}
```

The event is spelled `cancelled`; the appointment's `status` value is `canceled`, as everywhere in the app and the API.

**Not yet:** cancellations made by staff in the app don't fire this event yet. Poll `GET /api/v2/appointments?status=canceled&updated_since=` to catch those.

## `contact.created`

A contact was added.

**Sources:** every way a contact is created: on any screen of the app, by any feature that adds a customer, and through [`POST /contacts`](04-resources.md#post-contacts). It also fires for **suppliers**; check `is_supplier`.

```json
{
  "id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
  "name": "Dana Whitfield",
  "type": "person",
  "phone": "+1 555 0100",
  "email": "dana@example.com",
  "is_supplier": false,
  "source": null,
  "created_at": "2026-10-25T18:06:01Z"
}
```

`type` is `person` or `organization`.

## `inquiry.created`

A new inquiry was added to **Inquiries**. It used to be called `lead.created`, which still works as an [alias](#aliases).

**Sources:** every way an inquiry is made:

- Staff, in the app, and Tekly Go.
- [`POST /api/v2/inquiries`](10-api-v2.md#post-inquiries).
- A missed call (Twilio, Aircall or the generic call webhook), when the shop has **Missed calls become inquiries** on. A repeat call added to an open inquiry doesn't fire it again.
- A chat turned into an inquiry.
- A request left on the online booking page when no slot fits.
- A new request from a customer's tracking page.

```json
{
  "id": "0b7d3e9f-4a2c-4f1e-8d65-3c9a0e7f2b14",
  "name": "Jordan Lee",
  "phone": "+1 555 0177",
  "email": "jordan@example.com",
  "source": "web",
  "status": "new",
  "inquiry": "Service: Battery replacement\nPixel 7 battery drains by lunchtime",
  "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48",
  "client_id": null,
  "lead_type_id": null,
  "device_brand": "Google",
  "device_model": "Pixel 7",
  "serial": null,
  "call_count": 0,
  "converted_order_id": null,
  "created_at": "2026-10-25T16:41:12Z",
  "updated_at": null
}
```

`source` is one of `phone`, `walk_in`, `whatsapp`, `sms`, `email`, `instagram`, `facebook`, `web`, `api` or `other`. `status` is one of `new`, `in_progress`, `won` or `lost`. `call_count` counts missed calls added to the inquiry.

## `inquiry.updated`

An inquiry's details or status changed.

**Sources:** an edit in the app or Tekly Go, or [`PATCH /api/v2/inquiries/{id}`](10-api-v2.md#patch-inquiriesid). A missed call added to an open inquiry doesn't fire it.

The payload has the same shape as [`inquiry.created`](#inquirycreated), showing the inquiry after the change.

## `inquiry.converted`

An inquiry became a work order or an estimate. Its `status` is now `won` and `converted_order_id` names the new record.

**Sources:** **Convert** in the app or Tekly Go, or [`POST /api/v2/inquiries/{id}/convert`](10-api-v2.md#post-inquiriesidconvert).

The payload has the same shape as [`inquiry.created`](#inquirycreated). Converting to a work order also sends [`order.created`](#ordercreated); converting to an estimate doesn't.

## `invoice.created`

An invoice was created.

**Sources:** every way an invoice is created. That includes by hand, and automatically when an order reaches a status set to create an invoice.

```json
{
  "id": "c7e19a4d-6f2b-4d8e-9a05-1b3c7e2f8d64",
  "invoice_number": "INV-0318",
  "status": "draft",
  "order_id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
  "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
  "total": 129.0,
  "amount_paid": 0.0,
  "issued_at": "2026-10-25T18:06:54Z",
  "due_at": "2026-11-08T00:00:00Z",
  "created_at": "2026-10-25T18:06:54Z"
}
```

An invoice is often created as a `draft` and edited afterwards. There's no `invoice.updated` event, so read the invoice from the API, `GET /invoices/{id}`, when you need its final amounts.

## `invoice.paid`

An invoice became fully paid: its status changed to `paid`, or it was created already paid.

**Sources:** any payment that settles the invoice, whatever the method.

The payload has the same shape as [`invoice.created`](#invoicecreated), with `"status": "paid"` and the final `amount_paid`.

```json
{
  "id": "c7e19a4d-6f2b-4d8e-9a05-1b3c7e2f8d64",
  "invoice_number": "INV-0318",
  "status": "paid",
  "order_id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
  "client_id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31",
  "total": 129.0,
  "amount_paid": 129.0,
  "issued_at": "2026-10-25T18:06:54Z",
  "due_at": "2026-11-08T00:00:00Z",
  "created_at": "2026-10-25T18:06:54Z"
}
```

## `time_entry.stopped`

A running timer was stopped.

**Sources:** the time clock: **Stop**, stopping a specific entry, or switching to another job (which stops the current timer). Time logged through [`POST /time-entries`](04-resources.md#post-time-entries) doesn't fire it.

```json
{
  "id": "4b8e2d1f-7a3c-4e9b-a065-1d9c3f7e2b48",
  "employee_id": "9d0a6b3e-2c8f-4e1d-b75a-3f9c1e8d4a27",
  "order_id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
  "duration_sec": 2700,
  "billable": true
}
```

`order_id` is `null` for time that wasn't on a job.

## `tag.created`, `tag.updated`, `tag.deleted`

A tag was added, renamed or recoloured, or deleted.

**Sources:** the tags screen in the app and the [v2 tag routes](10-api-v2.md#tags).

```json
{
  "id": "5a1e7c3b-2d9f-4b6a-8e04-7c3f1a9d2b65",
  "name": "VIP",
  "color": "#64748b"
}
```

`tag.deleted` carries the tag as it was just before it was deleted.

## `bundle.created`, `bundle.updated`, `bundle.deleted`

A bundle was added, changed or deleted.

**Sources:** the bundles screen in the app and the [v2 bundle routes](10-api-v2.md#bundles).

```json
{
  "id": "8c2f4e1a-6b3d-4a9e-b175-2e8d0c4f7a93",
  "name": "Battery swap",
  "active": true
}
```

`bundle.deleted` has `"active": false`. Read the components with `GET /api/v2/bundles/{id}`.

## `product.created`, `product.updated`

A product was added or changed **through API v2**. Products added or edited in the app don't fire these yet.

**Sources:** [`POST /api/v2/products` and `PATCH /api/v2/products/{id}`](10-api-v2.md#products).

```json
{
  "id": "c51a9e3d-7f2b-4c8e-a046-3b9d1f7e2c58",
  "sku": "SCR-14",
  "name": "Screen iPhone 14"
}
```

The payload never has cost fields. Read the product with `GET /api/v2/products/{id}` for the rest.
