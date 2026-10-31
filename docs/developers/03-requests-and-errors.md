# Requests and errors

## Requests

- Send JSON bodies with `Content-Type: application/json`, encoded as UTF-8.
- Every write is a `POST`. v1 has no `PUT`, `PATCH` or `DELETE`.
- **Unknown fields are rejected.** A misspelt field such as `"complain"` gets a `422` instead of being silently dropped.
- The request body can be at most 16 MB. Larger bodies get `413`.

### Dates and times

| Kind | Fields | How to read and send them |
|---|---|---|
| Timestamps | `created_at`, `issued_at`, `started_at`, `ended_at`, `canceled_at` | ISO 8601 in **UTC**, written without an offset, for example `2026-10-25T18:06:01.474163`. |
| Scheduled times | `scheduled_for` on appointments and work orders | The **shop's wall-clock time** at that location, written without an offset, for example `2026-10-28T10:30:00` means 10:30 on the shop's own clock. |

When you **send** `scheduled_for` with an offset (`2026-10-28T15:30:00Z` or `…+01:00`), Tekly converts it to the location's clock. Without an offset, it's taken as wall-clock time. `started_at` on a time entry with an offset is converted to UTC; without one, it's taken as UTC.

### Money

Amounts are JSON numbers in the shop's currency, for example `129.0`. They aren't in minor units. Whole amounts can come back without a decimal point (`9400`), so parse them as decimals, not integers.

## The response envelope

A single record:

```json
{ "data": { "id": "6f1c2a9d-3e4b-4c8a-9f17-5b2d8e0a6c31", "name": "Dana Whitfield" } }
```

A list:

```json
{
  "data": [ { "id": "…" }, { "id": "…" } ],
  "page": 1,
  "page_size": 25,
  "total": 137
}
```

An error:

```json
{ "detail": "Contact not found" }
```

## Pagination

Every list endpoint takes the same two query parameters.

| Parameter | Default | Range |
|---|---|---|
| `page` | `1` | 1–10000 |
| `page_size` | `25` | 1–100 |

`total` is the number of matching records, so the number of pages is `ceil(total / page_size)`. Lists come newest first and have no sort parameter:

| List | Order |
|---|---|
| Contacts, orders, estimates, products, invoices, credit notes | `created_at`, newest first |
| Appointments | `scheduled_for`, latest first |
| Time entries | `started_at`, latest first |

Pagination is by offset. If new records arrive while you're paging, a record can move to the next page and appear twice. De-duplicate on `id`.

```python
def all_pages(session, url, **params):
    page = 1
    while True:
        resp = session.get(url, params={**params, "page": page, "page_size": 100}, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        yield from body["data"]
        if page * body["page_size"] >= body["total"]:
            return
        page += 1
```

## Errors

Tekly uses standard HTTP status codes. Errors always carry `detail`.

| Status | Meaning | What to do |
|---|---|---|
| `200` | OK | |
| `201` | Created. Also the status of a replayed idempotent create. | |
| `400` | A date or time couldn't be parsed. | Send ISO 8601, for example `2026-10-28T10:30:00`. |
| `401` | Missing, invalid, expired or revoked key. | Check the key. Don't retry. |
| `403` | The key lacks the scope, the shop's account isn't active, or the call wasn't over HTTPS. | Fix the scope or URL. Don't retry. |
| `404` | The record, or an id you referenced in the body, doesn't exist in this shop. | Check the id. |
| `409` | A conflict: a double-booked employee or slot, or the same `Idempotency-Key` still being processed. | For idempotency, retry after a short wait. For a scheduling clash, pick another time. |
| `413` | Body larger than 16 MB. | |
| `422` | Validation failed, or an `Idempotency-Key` was reused with a different body. | Fix the request. Don't retry it unchanged. |
| `429` | Rate limit exceeded. | Wait `Retry-After` seconds, then retry. |
| `5xx` | Something went wrong on our side. | Retry with backoff, and always send an `Idempotency-Key` on POSTs. |

### Error shapes

Most errors have a string `detail`:

```json
{ "detail": "Location not found" }
```

A `422` from validation has a **list** of field errors. `loc` is the path to the field:

```json
{
  "detail": [
    {
      "type": "extra_forbidden",
      "loc": ["body", "complain"],
      "msg": "Extra inputs are not permitted",
      "input": "Cracked screen"
    }
  ]
}
```

```json
{
  "detail": [
    {
      "type": "less_than_equal",
      "loc": ["query", "page_size"],
      "msg": "Input should be less than or equal to 100",
      "input": "500",
      "ctx": { "le": 100 }
    }
  ]
}
```

Handle both: check whether `detail` is a string or a list. Don't match on message text, which can change. Use the status code and, for 422, `loc` and `type`.

An id in the path or body that belongs to **another shop** is treated exactly like one that doesn't exist: `404`.

## Validation limits

| Field | Limit |
|---|---|
| `name` (contact), `client_name` | 1–200 characters. A blank or whitespace-only `name` is refused. |
| `phone` | up to 40 |
| `email` | up to 254, and must look like an email address |
| `address` | up to 500 |
| `complaint`, `note` | up to 5000 |
| Any id (`location_id`, `client_id`, …) | 1–64 characters |
| `client_type` | `person` or `organization` |
| `duration_min` on work orders and appointments | whole minutes, 5–1440 |
| `duration_min` on time entries | more than 0, at most 1440 (decimals allowed) |
| `scheduled_for`, `started_at` | ISO 8601, up to 64 characters |
| `status` filter on `GET /orders` | up to 40 |
| `Idempotency-Key` header | 1–255 characters |

## Rate limits

Each API key has its own limit: **120 requests a minute** unless it was given another one (1 to 6,000) when it was created. The key list in the app shows each key's limit. The limit counts every call with that key, on v1 and v2 together, and a rotated key's old and new secrets share it.

On top of that, every key also counts towards a shop-wide ceiling of 600 requests a minute per key, the same one that limits Tekly's own screens. It only matters for keys set above 600. Requests with an invalid key count against the caller's IP address instead.

Every response carries:

| Header | Meaning |
|---|---|
| `X-RateLimit-Limit` | Requests allowed per minute, for example `120`. When both limits apply, it's the one closer to running out. |
| `X-RateLimit-Remaining` | Requests left in the current minute, under that same limit. |

Over the limit you get:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 42
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 0

{"detail": "Rate limit exceeded for this API key"}
```

The `detail` is `Rate limit exceeded` when the shop-wide ceiling is the one reached. Match on the status code, not the text.

`Retry-After` is in seconds. Wait at least that long before retrying. If you sync in bulk, slow down when `X-RateLimit-Remaining` gets low, rather than waiting for the 429.

```python
import time

def request_with_retry(session, method, url, attempts=5, **kwargs):
    for attempt in range(attempts):
        resp = session.request(method, url, timeout=10, **kwargs)
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", "60")))
            continue
        if resp.status_code >= 500 and attempt < attempts - 1:
            time.sleep(2 ** attempt)
            continue
        return resp
    return resp
```

## Idempotency

Networks fail. If a `POST` times out, you can't tell whether Tekly created the record. An **`Idempotency-Key`** makes the retry safe.

Send a unique value, such as a UUID or your own form-submission id, in the `Idempotency-Key` header on any `POST`, on v1 or [v2](10-api-v2.md):

```bash
curl -X POST "$TEKLY_BASE_URL/orders" \
  -H "Authorization: Bearer $TEKLY_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: webform-20261025-000187" \
  -d '{"location_id": "2b9e4c71-5d3a-4f8e-a1b6-7c0d9e2f3a48", "complaint": "Cracked screen"}'
```

How Tekly treats a key you've used before:

| You send | You get |
|---|---|
| The same key and **the same body** | The original response, with the same status and body. Nothing new is created. The response has `Idempotent-Replayed: true`. |
| The same key and **a different body** | `422`, `This Idempotency-Key was already used with a different request`. |
| The same key while the first request **is still running** | `409`, `A request with this Idempotency-Key is still being processed`. Wait a moment, then retry. |
| The same key after the first request **failed** (any error) | It runs again as new. A failed request doesn't keep its key. |

Responses to requests that sent a key echo it back in an `Idempotency-Key` header.

- Keys are kept for **24 hours**. After that, the same key is treated as new.
- Keys belong to one API key and one shop. Two integrations can't collide.
- "The same body" means the same fields and values. Whitespace and key order don't matter.

```python
import uuid

idem_key = str(uuid.uuid4())     # make it once, before the first attempt, and reuse it on retries
resp = request_with_retry(
    session, "POST", f"{BASE_URL}/contacts",
    headers={"Idempotency-Key": idem_key},
    json={"name": "Dana Whitfield", "email": "dana@example.com"},
)
```

The key must stay the same across retries of one logical operation. Generate it **before** the first attempt, not inside the retry loop.
