# Recipes

End-to-end guides for the integrations repair shops ask for most. Each uses only what the API and webhooks do today, and says where you'll hit a limit.

- [Website contact form → contact and work order](#website-contact-form--contact-and-work-order)
- [Booking site → appointments](#booking-site--appointments)
- [Zapier and Make](#zapier-and-make)
- [Sync invoices to accounting](#sync-invoices-to-accounting)

---

## Website contact form → contact and work order

A visitor fills in "What's wrong with your device?" on the shop's website. You want a customer and a job in Tekly, ready for the front desk.

**You need:** a key with **Read and write** access, and the `location_id` of the shop that should get the job.

The API can't create **inquiries** (the Inquiries board) yet, only contacts and work orders. If you'd rather web requests land as inquiries, use Tekly's own **Online booking** page (**Settings → Public pages**). Its request form creates an inquiry, and fires [`lead.created`](06-events.md#leadcreated).

### 1. Find your `location_id` (once)

There's no locations endpoint yet. Read the id from any existing work order, and keep it in your configuration:

```bash
curl "$TEKLY_BASE_URL/orders?page_size=1" -H "Authorization: Bearer $TEKLY_API_KEY"
# → "location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48"
```

### 2. On each form submission, on your server

```python
import os

import requests

BASE_URL = os.environ["TEKLY_BASE_URL"]
LOCATION_ID = os.environ["TEKLY_LOCATION_ID"]
session = requests.Session()
session.headers["Authorization"] = f"Bearer {os.environ['TEKLY_API_KEY']}"


def submit_repair_request(form: dict, submission_id: str) -> str:
    # 1. The customer. The Idempotency-Key ties it to this submission, so a retry or a
    #    double-click doesn't create a second contact.
    contact = session.post(
        f"{BASE_URL}/contacts",
        headers={"Idempotency-Key": f"webform-{submission_id}-contact"},
        json={
            "name": form["name"],
            "phone": form.get("phone") or None,
            "email": form.get("email") or None,
        },
        timeout=10,
    )
    contact.raise_for_status()
    client_id = contact.json()["data"]["id"]

    # 2. The job. Put the device and the problem in the complaint: v1 has no device fields.
    complaint = f"{form['device']}: {form['problem']}"[:5000]
    order = session.post(
        f"{BASE_URL}/orders",
        headers={"Idempotency-Key": f"webform-{submission_id}-order"},
        json={"location_id": LOCATION_ID, "client_id": client_id, "complaint": complaint},
        timeout=10,
    )
    order.raise_for_status()
    return order.json()["data"]["number"]     # e.g. "WO-01042", to show on your thank-you page
```

### What to know

- **Duplicates.** You can't search contacts by email or phone yet, so a returning customer gets a new contact each time. Staff can merge them in Tekly. The idempotency keys stop duplicates from retries, not from repeat visitors.
- **No customer message.** API-created orders don't trigger the shop's notification rules, so send your own confirmation email from the website if you want one.
- **Validation.** Check your form against the [limits](03-requests-and-errors.md#validation-limits) (name up to 200 characters, a valid email), or show the `422` field errors to the visitor.
- **Never call Tekly from the browser.** The form posts to your server, and your server calls Tekly. The key must not reach the page.
- **Spam.** Put a CAPTCHA or rate limit on the form itself. Every call creates real records in the shop.

---

## Booking site → appointments

Your own booking site, or a booking platform, puts appointments into the Tekly calendar.

**You need:** a key with **Read and write** access, the `location_id`, and the `employee_id` of each bookable technician, if you book per technician.

### Create the appointment

```js
const BASE_URL = process.env.TEKLY_BASE_URL;
const headers = {
  Authorization: `Bearer ${process.env.TEKLY_API_KEY}`,
  "Content-Type": "application/json",
};

export async function book({ bookingId, startLocal, minutes, customer, technicianId, note }) {
  const res = await fetch(`${BASE_URL}/appointments`, {
    method: "POST",
    headers: { ...headers, "Idempotency-Key": `booking-${bookingId}` },
    body: JSON.stringify({
      location_id: process.env.TEKLY_LOCATION_ID,
      scheduled_for: startLocal,          // "2026-10-28T10:30:00", the shop's own clock
      duration_min: minutes,              // 5–1440
      client_name: customer.name,
      employee_id: technicianId,          // optional; turns on the double-booking check
      note,
    }),
  });

  if (res.status === 409) {
    // That technician already has an appointment then, or the slot was just taken.
    const { detail } = await res.json();
    return { ok: false, reason: detail };
  }
  if (!res.ok) throw new Error(`Tekly ${res.status}: ${await res.text()}`);
  return { ok: true, appointment: (await res.json()).data };
}
```

### What to know

- **Time zones.** Send `scheduled_for` as the shop's local time, without an offset. If your system works in UTC, send it with `Z` and Tekly converts it to the location's clock.
- **Double-booking.** With an `employee_id`, Tekly refuses an overlapping `booked` or `confirmed` appointment for that employee with `409`. Show the visitor another time. Without an `employee_id`, nothing is checked.
- **Linking the customer.** To link the appointment to a contact, create the contact first (`POST /contacts`) and pass its `client_id`. Otherwise `client_name` is enough for the calendar.
- **Status.** API appointments are created as `confirmed`.
- **Hearing about other bookings.** Subscribe to [`appointment.created`](06-events.md#appointmentcreated) to learn about appointments booked in Tekly itself, for example to block those times on your site. Its `scheduled_for` is wall-clock time, despite the `Z`.

**Not possible yet:**

- Reading free slots: there's no availability endpoint.
- Rescheduling or cancelling through the API.
- Fetching one appointment by id, or filtering appointments by date.
- Listing services or employees.

If you need those, consider Tekly's own **Online booking** page, which handles slots, working hours and services for you.

---

## Zapier and Make

No-code tools can use both halves of the platform: **webhooks as triggers** and **the API as actions**.

### Trigger: something happened in Tekly

1. In Zapier, add a **Webhooks by Zapier** trigger, and choose **Catch Raw Hook** so you get the exact body and headers. In Make, add a **Webhooks → Custom webhook** module.
2. Copy the URL it gives you. It's a public `https://` address, so Tekly accepts it.
3. In Tekly, **Settings → Integrations → Webhooks → Add endpoint**: paste the URL, and choose just the events you need, for example `invoice.paid`.
4. Choose **Send test event**. You'll get a `webhook.test` delivery, which you can use to map fields in the tool. Then make a real change in Tekly to get a sample of the real event.
5. Map fields from the envelope's `data`, for example `data.invoice_number` and `data.total`. Filter on `event` if one endpoint receives several events.

**Verify the signature.** Anyone who learns the URL can post to it. If your workflow does anything that matters, check `Tekly-Signature` before acting. In Zapier, a **Code by Zapier** step can run the [Python or JavaScript verifier](05-webhooks.md#verify-signatures) on the raw body and headers. If your tool can't compute an HMAC, put a small verifying relay in front of it, for example a serverless function.

Zapier and Make answer webhooks immediately with a `2xx`, so Tekly will mark deliveries as sent even if a later step in your workflow fails. Check the tool's own run history for errors.

### Action: do something in Tekly

There's no native Tekly app in Zapier or Make yet. Use their generic HTTP steps:

| Setting | Value |
|---|---|
| Step | Zapier **Webhooks by Zapier → Custom Request**, or Make **HTTP → Make a request** |
| Method | `POST` (or `GET` to read) |
| URL | `{BASE_URL}/contacts`, `{BASE_URL}/orders`, `{BASE_URL}/appointments` or `{BASE_URL}/time-entries` |
| Headers | `Authorization: Bearer rok_…`, `Content-Type: application/json`, and `Idempotency-Key:` set to a unique value from the trigger, such as its id |
| Body | JSON, as in [Resources](04-resources.md) |

Use a dedicated key named after the tool (`Zapier`), with **Read only** unless the workflow writes.

### Example: new paid invoice → a row in a spreadsheet

1. Trigger: catch hook, with the Tekly endpoint subscribed to `invoice.paid`.
2. Optional: a custom request `GET {BASE_URL}/invoices/{data.id}` for `subtotal`, `tax` and `balance_due`.
3. Action: add a spreadsheet row with `invoice_number`, `total`, `amount_paid` and `created_at`.

---

## Sync invoices to accounting

Push Tekly invoices into an accounting system: the books, a data warehouse, or an ERP.

> If you use **QuickBooks Online** or **Xero**, look in **Settings → Integrations** first. Tekly has built-in connections for those. This recipe is for everything else.

**You need:** a **Read only** key, and a webhook endpoint subscribed to `invoice.created` and `invoice.paid`.

### The approach

Use webhooks for speed and a scheduled sweep for completeness.

1. **On `invoice.created` or `invoice.paid`:** fetch the invoice with `GET /invoices/{data.id}` and upsert it in your system, keyed on Tekly's invoice `id`. Re-fetching matters: invoices are often created as `draft` and edited afterwards, and there's no `invoice.updated` event.
2. **Every night:** page through `GET /invoices` (newest first) and upsert each one, to catch edits and anything a webhook missed. Stop once you reach invoices older than your look-back window, for example 60 days.
3. **Credit notes:** page through `GET /credit-notes` on the same schedule. There's no credit-note webhook.

```python
from datetime import datetime, timedelta, timezone

LOOKBACK = timedelta(days=60)


def nightly_invoice_sweep(session, base_url, upsert):
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - LOOKBACK   # Tekly timestamps are naive UTC
    page = 1
    while True:
        resp = session.get(f"{base_url}/invoices", params={"page": page, "page_size": 100}, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        for invoice in body["data"]:
            upsert(invoice)                        # idempotent on invoice["id"]
        oldest = body["data"][-1]["issued_at"] if body["data"] else None
        done = page * body["page_size"] >= body["total"]
        if done or (oldest and datetime.fromisoformat(oldest) < cutoff):
            return
        page += 1
```

The sweep stops on `issued_at`, which can be empty on some invoices. If your shop has many of those, page all the way through instead.

### Mapping

| Accounting field | Tekly field |
|---|---|
| Invoice number | `number` |
| Customer | `client_id`, read from `GET /contacts/{id}` |
| Net, tax, gross | `subtotal`, `tax`, `total` |
| Paid / outstanding | `amount_paid`, `balance_due` |
| Credited | `credited` (details in `/credit-notes`) |
| Date | `issued_at` |
| Due date | `due_at`, from the webhook payload only |
| Status | `status` (`draft`, `issued`, `partial`, `paid`, `cancelled`) |

### What to know

- **No invoice lines.** v1 returns invoice totals, not line items or per-line tax. Post each invoice as one summary line, or wait for line support.
- **No payments endpoint.** You can see `amount_paid` and `balance_due`, and [`payment.recorded`](06-events.md#paymentrecorded) for online payments, but you can't list individual payments, methods or refunds.
- **No `updated_since` filter.** The nightly sweep re-reads a window of invoices rather than only changed ones. At 100 per page and 600 requests a minute, that's quick even for busy shops.
- **Skip drafts** if your books shouldn't contain them: post on `invoice.paid`, or once `status` is past `draft`.
