# Booking API

Build your own booking site on Tekly: show a shop's free times, book the visitor in, and let them move or cancel. The free times are computed by the same code as the shop's own **Online booking** page (**Settings → Public pages**), so your site and that page always agree.

Paths below are relative to `{BASE_URL_V2}`, which is `https://<your-shop-domain>/api/v2`. Everything on [API v2](10-api-v2.md#whats-the-same-as-v1) applies: HTTPS only, no CORS (call it from your server, never the browser), the `{"data": ...}` envelope, strict input (`422` for unknown fields), `404` for ids from another shop, `Idempotency-Key` on every `POST`, and every write audited against the key.

- [The flow](#the-flow)
- [Keys and scopes](#keys-and-scopes)
- [Time: shop wall-clock](#time-shop-wall-clock)
- [1. Pick a service, location and employee](#1-pick-a-service-location-and-employee)
- [2. Show free slots](#2-show-free-slots)
- [3. Book](#3-book)
- [Reschedule](#reschedule)
- [Cancel](#cancel)
- [Read appointments](#read-appointments)
- [Keeping in sync](#keeping-in-sync)
- [Webhooks to listen for](#webhooks-to-listen-for)
- [Errors](#errors)
- [Examples: curl and JavaScript](#examples)

## The flow

1. Your server lists the shop's bookable services (and, if you let visitors choose, its locations and staff).
2. The visitor picks a day. You call [`GET /appointments/slots`](#2-show-free-slots) and show the free times.
3. The visitor picks a time and enters their details. You call [`POST /appointments`](#3-book) with an `Idempotency-Key`. Tekly re-checks the slot and books it, or answers `409` if someone else just took it.
4. Later the visitor can [reschedule](#reschedule) or [cancel](#cancel) from your site, and you hear about the shop's own changes through [webhooks](#webhooks-to-listen-for) and [`updated_since`](#keeping-in-sync).

## Keys and scopes

Create a key in **Settings → Integrations → API keys**. For a booking site, limit it to what the site needs:

| Scope | For |
|---|---|
| `appointments:write` | Slots, booking, rescheduling, cancelling, reading appointments. It includes `appointments:read`. |
| `services:read` | Listing services. |
| `locations:read`, `employees:read` | Listing locations and staff, if visitors choose them. |
| `contacts:read` | Only if you link bookings to existing Tekly contacts by `client_id`. Without it, `client_id` is `null` in responses and can't be sent or filtered on (`403`). |

A key with only `appointments:read` gets `403` on every write. The broad `read` / `write` scopes cover all of these.

## Time: shop wall-clock

Every scheduled time in this API, and in the appointment webhooks, is the **shop's wall-clock time** at the location: ISO 8601 **without** an offset, such as `2026-10-28T10:30:00`, meaning 10:30 on the shop's clock. The slots response tells you that clock's `timezone` (an IANA name such as `America/New_York`).

- **Send times without an offset** (recommended): `"scheduled_for": "2026-10-28T10:30:00"` is stored as 10:30 shop time, exactly what the visitor picked.
- **Or send an instant with an offset** (`Z`, `+05:30`): Tekly converts it to the location's clock first. `2026-10-28T14:30:00Z` at a New York location is stored as `2026-10-28T10:30:00`.
- **"Today"**, the lead time and the booking window are worked out on the location's clock too, not UTC and not your server's.
- `created_at` and `updated_at` are different: they are UTC instants.

Don't convert slot times to the visitor's timezone unless you show both: a booking is for a time at the shop.

## 1. Pick a service, location and employee

**Services.** `GET /services` (`services:read`). Offer only services with `"active": true` and `"bookable": true`; the shop controls `bookable` in **Settings → Services**. A service's `duration_min` is the length of its slots. Asking for slots or booking with a service that isn't bookable is a `422`.

**Locations.** `GET /locations` (`locations:read`), if the shop has more than one. Leave `location_id` out everywhere to use the shop's first active location, which is what the shop's own booking page does.

**Employees.** Optional. There are two ways to offer staff:

- **Let the slots tell you.** Leave `employee_id` out of the slots call: each slot then lists the `employees` free for the whole slot who are allowed to perform the service. Show those as choices for that time. Names are the ones the shop shows customers (full, first name, or first name and initial, per **Settings → Public pages**).
- **Ask first.** List staff with `GET /employees` (`employees:read`), then pass `employee_id` to the slots call to get only that person's free times. If the service is limited to certain staff and this employee isn't one of them, the call is a `422`.

## 2. Show free slots

```
GET /appointments/slots?date=2026-10-28&service_id=…&employee_id=…&location_id=…&duration_min=…
```

| Parameter | |
|---|---|
| `date` | Required. `YYYY-MM-DD` on the location's clock. From today up to the shop's booking window (30 days unless the shop changed it), else `422` with the last bookable day in the message. |
| `service_id` | Optional. Its duration sets the slot length. Must be bookable (`422` otherwise). |
| `employee_id` | Optional. Only this employee's free times. |
| `location_id` | Optional. Default: the shop's first active location. |
| `duration_min` | Optional, 5–1440. Overrides the length. |

An unknown `service_id`, `employee_id` or `location_id`, or one from another shop, is a `404`.

The answer applies the same rules as the shop's booking page: that weekday's working hours, the shop's slot size (`step_min`), the minimum notice (lead time), existing `booked` and `confirmed` appointments, scheduled work orders if the shop counts them, and whether the shop takes one appointment per slot or several.

```json
{
  "data": {
    "date": "2026-10-28",
    "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48",
    "timezone": "America/New_York",
    "service_id": "5c7d1e2f-8a9b-4c3d-b6e5-0f1a2b3c4d5e",
    "employee_id": null,
    "duration_min": 60,
    "step_min": 30,
    "reason": null,
    "slots": [
      {"start": "2026-10-28T09:00:00", "end": "2026-10-28T10:00:00", "time": "09:00",
       "employees": [{"id": "9d0a6b3e-2c8f-4e1d-b75a-3f9c1e8d4a27", "name": "Sam K."}]},
      {"start": "2026-10-28T09:30:00", "end": "2026-10-28T10:30:00", "time": "09:30",
       "employees": [{"id": "9d0a6b3e-2c8f-4e1d-b75a-3f9c1e8d4a27", "name": "Sam K."},
                     {"id": "41be7d2c-6a5f-4b3e-9d81-2c0f7e6a5b49", "name": "Ola N."}]}
    ]
  }
}
```

- `reason` is `"closed"`, with no slots, when the location is closed that day. A day with no free time left has `"reason": null` and an empty `slots`.
- `employees` is `null` when you passed `employee_id`. When it is an empty list, the time is open at the shop but no suitable employee is free: book it unassigned, or don't offer it.
- Slots are a snapshot. Don't cache them for more than a minute or so, and expect the occasional `409` on booking.

## 3. Book

```
POST /appointments
Idempotency-Key: booking-<your booking id>
```

| Field | |
|---|---|
| `scheduled_for` | Required. A slot's `start`, or any ISO 8601 time; see [Time](#time-shop-wall-clock). |
| `client_name` | Required unless you send `client_id`. Up to 200 characters. |
| `client_phone` | Optional, up to 40. Shown to the shop on the appointment. |
| `client_id` | Optional. An existing contact. Needs `contacts:read`. |
| `service_id` | Optional. Must be bookable. |
| `employee_id` | Optional. Must be allowed to perform the service (`422`). Leave it out for an unassigned appointment the shop assigns later. |
| `location_id` | Optional. Default: the shop's first active location. |
| `duration_min` | Optional, 5–1440. Default: the service's duration, else the shop's slot size. |
| `note` | Optional, up to 5000. The visitor's message, the device, anything the shop should know. |
| `check_slot` | Default `true`: the time must be one of the free starts `GET /appointments/slots` gives for the same inputs, else `409`. That covers working hours, lead time, the booking window and other bookings. Send `false` only to import bookings already agreed elsewhere. |
| `notify_client` | Default `true`: runs the shop's appointment confirmation rules (the email or SMS the shop set up in **Settings → Notifications**), as a booking from the shop's own page does. Send `false` if your site sends its own confirmation. |

Whatever `check_slot` says, an employee is never double-booked: an overlapping `booked` or `confirmed` appointment for the same employee is a `409`.

The new appointment's status is `booked`. The response is `201` with [the appointment](#the-appointment-object), and [`appointment.created`](06-events.md#appointmentcreated) fires once.

**Always send an `Idempotency-Key`**, derived from your own booking id. If the network drops after Tekly booked but before you got the answer, retrying with the same key and body returns the original appointment (with `Idempotent-Replayed: true`) instead of booking twice. The same key with a different body is a `422`.

**On `409`**, the time was taken between showing it and booking it. Fetch slots again and ask the visitor to pick another time.

## Reschedule

```
PATCH /appointments/{id}
```

Send only what changes: `scheduled_for`, `duration_min`, `employee_id`, `note`. A field left out or `null` keeps its value, except `note`, which `null` clears. An empty body is a `422`. `employee_id` can't be cleared here.

- Only a `booked` or `confirmed` appointment can be changed. One that is `done`, `canceled` or `no_show` is a `409`.
- The same clash rule as the shop's calendar: the employee can't already hold an overlapping appointment (`409`).
- A new employee must be allowed to perform the appointment's service (`422`).
- Rescheduling does not re-check working hours or lead time: call `GET /appointments/slots` first and offer only free times.

The response is the updated appointment. [`appointment.updated`](06-events.md#appointmentupdated) fires with `changed_fields`; a request that changes nothing fires nothing. When the time, length or employee changed, the shop's "appointment changed" notification rules run, as when staff move an appointment in the app.

## Cancel

```
POST /appointments/{id}/cancel
Idempotency-Key: cancel-<your booking id>

{"reason": "Customer called to cancel"}
```

The body is optional; `reason` is up to 500 characters and is added to the appointment's note. The status becomes `canceled` and the slot is free again at once.

- Cancelling an appointment that is already cancelled returns `200` with the same record and fires nothing, so retries are safe.
- A `done` or `no_show` appointment can't be cancelled (`409`).
- [`appointment.cancelled`](06-events.md#appointmentcancelled) fires once, the shop's cancellation notification rules run, and the booked employee gets an in-app notice, as when staff cancel in the app.

## Read appointments

`GET /appointments/{id}` returns one appointment. `GET /appointments` lists them, **soonest first**, with these filters:

| Parameter | |
|---|---|
| `date_from`, `date_to` | `YYYY-MM-DD` shop days, both inclusive. |
| `employee_id`, `location_id` | Only these. `404` for ids that aren't the shop's. |
| `client_id` | Only this contact's. Needs `contacts:read`. |
| `status` | `booked`, `confirmed`, `done`, `canceled` or `no_show`. |
| `updated_since` | An ISO 8601 instant; without an offset it is read as UTC. Appointments created or changed since then. |
| `page`, `page_size` | As everywhere: 1–10000 and 1–100. |

### The appointment object

```json
{
  "id": "e02f7c4a-9b1d-4e6f-a83c-5d2b0f9e7c16",
  "status": "booked",
  "scheduled_for": "2026-10-28T10:30:00",
  "ends_at": "2026-10-28T11:30:00",
  "duration_min": 60,
  "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48",
  "location_name": "High Street",
  "service_id": "5c7d1e2f-8a9b-4c3d-b6e5-0f1a2b3c4d5e",
  "service_name": "Screen repair",
  "employee_id": "9d0a6b3e-2c8f-4e1d-b75a-3f9c1e8d4a27",
  "employee_name": "Sam Kerr",
  "client_id": null,
  "client_name": "Dana Whitfield",
  "client_phone": "+1 555 0100",
  "note": "Cracked screen",
  "order_id": null,
  "created_at": "2026-10-25T18:06:36",
  "updated_at": "2026-10-25T18:06:36"
}
```

| Status | Meaning |
|---|---|
| `booked` | Booked and holding its slot. What this API, the app and the shop's booking page create. |
| `confirmed` | Also holding its slot. API v1 creates appointments with this status. |
| `done` | The customer came in; often it has become a work order (`order_id`). |
| `canceled` | Cancelled. Its slot is free. |
| `no_show` | The customer didn't come. |

`employee_name` here is the employee's full name, for your own systems; slots show the customer-facing name.

## Keeping in sync

Staff also book, move and cancel appointments in the app. To mirror the shop's calendar on your side:

1. Subscribe to the [webhooks](#webhooks-to-listen-for) below for the changes made through the API and new bookings from anywhere.
2. Every few minutes, call `GET /appointments?updated_since=<the time of your last sync>` and upsert what comes back. Keep the time **from before** you started the call, so nothing falls in the gap.

Changes staff make in the app don't fire `appointment.updated` or `appointment.cancelled` yet, so the `updated_since` poll is what catches them.

## Webhooks to listen for

Set them up in **Settings → Integrations → Webhooks**, or with [`POST /webhooks`](10-api-v2.md#post-webhooks). See [Webhooks](05-webhooks.md) for verifying signatures.

| Event | Fires when |
|---|---|
| [`appointment.created`](06-events.md#appointmentcreated) | Any new appointment: your site, the shop's booking page, staff, or API v1. Once per appointment. |
| [`appointment.updated`](06-events.md#appointmentupdated) | An appointment is rescheduled or its note changes through API v2. `changed_fields` lists what changed. |
| [`appointment.cancelled`](06-events.md#appointmentcancelled) | An appointment is cancelled through API v2. Once, even if cancel is called again. |

Webhook payloads use the same wall-clock `scheduled_for` as the API. Use the slots endpoint, not the webhooks, to decide what's free: it applies rules (working hours, lead time) that the events don't carry.

## Errors

| Status | When |
|---|---|
| `403` | The key lacks `appointments:read` / `appointments:write`, or `contacts:read` for a `client_id`. |
| `404` | The appointment, service, employee, location or contact isn't in this shop. |
| `409` | The time isn't a free slot, the employee already has an overlapping appointment, the slot was just taken, the appointment is `done` / `canceled` / `no_show` and can't be changed, or a request with the same `Idempotency-Key` is still running. |
| `422` | Invalid input or an unknown field; a service that isn't bookable; an employee not allowed for the service; a `date` before today or beyond the booking window; neither `client_name` nor `client_id`; an `Idempotency-Key` reused with a different body. |

Errors are `{"detail": "..."}`, with a message you can show staff; for field errors `detail` is a list.

## Examples

### curl

```bash
# Free times for a screen repair on 28 October, any employee
curl -sS "$TEKLY_BASE_URL_V2/appointments/slots?date=2026-10-28&service_id=5c7d1e2f-8a9b-4c3d-b6e5-0f1a2b3c4d5e" \
  -H "Authorization: Bearer $TEKLY_API_KEY"

# Book 09:30 with Ola, safely retryable
curl -sS -X POST "$TEKLY_BASE_URL_V2/appointments" \
  -H "Authorization: Bearer $TEKLY_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: booking-8841" \
  -d '{"scheduled_for": "2026-10-28T09:30:00",
       "service_id": "5c7d1e2f-8a9b-4c3d-b6e5-0f1a2b3c4d5e",
       "employee_id": "41be7d2c-6a5f-4b3e-9d81-2c0f7e6a5b49",
       "client_name": "Dana Whitfield", "client_phone": "+1 555 0100",
       "note": "iPhone 13, cracked screen"}'

# Move it to 14:00 the next day
curl -sS -X PATCH "$TEKLY_BASE_URL_V2/appointments/e02f7c4a-9b1d-4e6f-a83c-5d2b0f9e7c16" \
  -H "Authorization: Bearer $TEKLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"scheduled_for": "2026-10-29T14:00:00"}'

# Cancel it
curl -sS -X POST "$TEKLY_BASE_URL_V2/appointments/e02f7c4a-9b1d-4e6f-a83c-5d2b0f9e7c16/cancel" \
  -H "Authorization: Bearer $TEKLY_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: cancel-8841" \
  -d '{"reason": "Customer called to cancel"}'
```

### JavaScript (Node 18+, on your server)

```js
const BASE = process.env.TEKLY_BASE_URL_V2;          // https://<your-shop-domain>/api/v2
const headers = {
  Authorization: `Bearer ${process.env.TEKLY_API_KEY}`,
  "Content-Type": "application/json",
};

async function tekly(path, { method = "GET", body, idempotencyKey } = {}) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: idempotencyKey ? { ...headers, "Idempotency-Key": idempotencyKey } : headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const json = await res.json();
  if (!res.ok) {
    const err = new Error(typeof json.detail === "string" ? json.detail : JSON.stringify(json.detail));
    err.status = res.status;
    throw err;
  }
  return json.data;
}

// Step 2: free times for a day. Each slot.start is shop wall-clock: show it as-is.
export async function freeSlots({ date, serviceId, employeeId }) {
  const qs = new URLSearchParams({ date });
  if (serviceId) qs.set("service_id", serviceId);
  if (employeeId) qs.set("employee_id", employeeId);
  const day = await tekly(`/appointments/slots?${qs}`);
  return day.slots.map((s) => ({ start: s.start, label: s.time, staff: s.employees }));
}

// Step 3: book. The key comes from your own booking id, so a retry never books twice.
export async function book({ bookingId, start, serviceId, employeeId, name, phone, note }) {
  try {
    return await tekly("/appointments", {
      method: "POST",
      idempotencyKey: `booking-${bookingId}`,
      body: { scheduled_for: start, service_id: serviceId, employee_id: employeeId,
              client_name: name, client_phone: phone, note },
    });
  } catch (err) {
    if (err.status === 409) return { taken: true };   // fetch slots again, ask for another time
    throw err;
  }
}

export const reschedule = (id, start) =>
  tekly(`/appointments/${id}`, { method: "PATCH", body: { scheduled_for: start } });

export const cancel = (id, reason, bookingId) =>
  tekly(`/appointments/${id}/cancel`, { method: "POST", body: { reason }, idempotencyKey: `cancel-${bookingId}` });
```
