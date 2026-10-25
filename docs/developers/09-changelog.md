# Changelog

## 2026-10-25: Hardening release

This release tightens security across the public API and webhooks, and adds the features integrations need to run safely in production: scopes, key expiry and rotation, idempotency, rate-limit headers, signed and retried webhooks, and a public OpenAPI document.

**If you built an integration before 2026-10-25, read [Breaking changes](#breaking-changes-for-api-integrators) and [Breaking changes for webhook receivers](#breaking-changes-for-webhook-receivers).**

### New

**API**

- **Scopes** `read`, `write` and `costs:read`, now enforced on every route. See [Authentication](02-authentication.md#scopes).
- **Key expiry**: 30 days, 90 days, 1 year (the default) or never.
- **Key rotation** with a 24-hour overlap, so you can rotate without downtime. Revoking a key also ends its overlap.
- **`Idempotency-Key`** on every `POST`, kept for 24 hours. Replays carry `Idempotent-Replayed: true`. See [Idempotency](03-requests-and-errors.md#idempotency).
- **Rate-limit headers**: `X-RateLimit-Limit` and `X-RateLimit-Remaining` on every response, and `Retry-After` on `429`. The budget is 600 requests a minute per key.
- **OpenAPI 3.1** at `GET /api/v1/openapi.json`. It's public, served in production, covers only v1, and has readable `operationId`s. `/api/v1/docs` now redirects to it, and the discovery endpoint links to it.
- **Referenced ids are checked.** A `location_id`, `client_id`, `assigned_to_id`, `service_id`, `employee_id` or `order_id` that isn't in your shop now gives `404`, instead of being stored as given.
- **Appointment conflicts.** Double-booking an employee gives `409`. A `scheduled_for` with an offset is converted to the location's clock.
- **Auditing.** Every API write is in the shop's audit log, attributed to the key.
- A suspended shop's keys stop working.

**Webhooks**

- **New signature scheme.** `Tekly-Signature: t=<unix>,v1=<hmac>` signs the timestamp and the body, which prevents replays. See [Verify signatures](05-webhooks.md#verify-signatures).
- **New headers**: `Tekly-Webhook-Id` (stable across retries, for de-duplication) and `Tekly-Event`.
- **Versioned envelope** with `id`, `event`, `api_version`, `org_id`, `created_at` and `data`.
- **New events:** `order.updated`, `contact.created`, `lead.created`, `invoice.created` and `invoice.paid`. `appointment.created` now fires for appointments from every source, not just the API. `order.created` has the same full payload from the app and the API. See [Events](06-events.md).
- **Retries with backoff**: 1 minute, 5 minutes, 30 minutes, 2 hours, 8 hours and 24 hours, for 7 attempts in all.
- **Automatic disabling** after 24 hours of continuous failure, with an in-app notification to admins.
- **Signing secrets** start with `whsec_`, are encrypted at rest, are shown only once, and can be rotated with a 24-hour overlap.
- **The webhooks screen** in **Settings → Integrations → Webhooks**: edit endpoints, choose several events, turn endpoints on and off, browse the delivery log with payloads, **Redeliver**, **Send test event** and **New secret**.

### Breaking changes for API integrators

| Change | What to do |
|---|---|
| **New keys are read-only by default.** Keys made in the app used to get read and write. | Choose **Read and write** when you create a key that writes. |
| **`read` is now required for reads.** Keys whose scopes are empty, or hold none of `read`, `write` or `*`, can no longer read. Any key used to be able to read. | Use a key with `read` or `write`. |
| **Cost fields need `costs:read`.** `cost_price` on products, and `cost_rate` and `billable_rate` on time entries, are now **left out** for other keys. | Create a key with **Include cost prices** if you need them. |
| **Stricter validation (`422`).** Unknown fields are rejected, lengths are capped, `client_type` must be `person` or `organization`, `email` must be valid, durations are bounded, `page` must be 1–10000, and `page_size` 1–100 on every list, `/credit-notes` included. | Fix any request that now gets `422`. Check field spelling first. |
| **HTTPS only.** Plain-HTTP calls get `403`. | Use `https://`. If you used HTTP, rotate the key. |
| **No CORS.** `/api/v1` no longer answers browsers from other sites. | Call the API from your server. |
| **Listing keys needs Manage company settings.** Any signed-in staff member used to be able to list keys. | Only affects staff tools that listed keys. |
| **API-created orders start in the shop's default status**, not always `new`. | Read `status` from the response. Don't assume `new`. |
| **API-created orders send no customer notifications.** | Send your own confirmation if you need one. |
| **The `401` message changed** to `Invalid, expired or revoked API key`. | Match on the status code, not the message. |

### Breaking changes for webhook receivers

| Change | What to do |
|---|---|
| **The body is now the envelope.** `event`, `data` and `at` are still at the top level, so most receivers keep working, but `data` has more fields. | Read `id`, `created_at` and `org_id` as well. |
| **`order.created` from the API has the full payload** instead of `{id, number}`. | Read `order_number`. `number` is still sent, but deprecated. |
| **`http://` endpoints stop receiving** once deployed. Only `https://` on ports 443 or 8443, to public addresses, is allowed. | Move your receiver to HTTPS. |
| **Automation rule webhooks are queued, signed and retried.** They used to be sent once, unsigned. | Verify `Tekly-Signature` with the secret shown when you save the rule. |
| **`GET /api/webhooks` no longer returns `secret`**, only a `secret_hint`. Secrets are shown only on create and rotate. | Store the secret when it's shown. Rotate if you've lost it. |
| **Event names are validated.** An unknown event name in a subscription is refused. | Use names from the [catalogue](06-events.md), or `*`. |
| **Up to 10 endpoints** per shop. | |
| **Deleting an endpoint** keeps its delivery log and abandons pending retries. | |

Existing signing secrets keep their values. Your verifier keeps working while you move to `Tekly-Signature`.

### Deprecations

These still work, and will be removed after one release cycle. Move off them now.

| Deprecated | Replacement | Notes |
|---|---|---|
| Headers `X-Tekly-Signature` (`sha256=<hmac of body>`), `X-Tekly-Event`, `X-RepairOS-Signature`, `X-RepairOS-Event` | `Tekly-Signature`, `Tekly-Event` | Deliveries carry `Deprecation: true` and `Tekly-Deprecated-Headers`, naming them. The old signature has no timestamp, so it can't stop replays. |
| `number` in `order.created` and `order.updated` payloads | `order_number` | Same value. |
| `at` in the webhook envelope | `created_at` | `at` has no `Z` and has microseconds. |
| `booking.created` event name | `appointment.created` | Still delivered under the old name to old subscriptions. |
| Flat `rule`, `order_id`, `order_number`, `status` at the top of automation webhook bodies | `data` | |
| `/api/v1/bookings` | `/api/v1/appointments` | Not in the OpenAPI document. |
| Scope `*` on old keys | `read` + `write` | Still treated as read and write. Never includes `costs:read`. |
