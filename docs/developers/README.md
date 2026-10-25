# Tekly developer docs

Tekly is software for running a repair shop. The Tekly API and webhooks let your own systems work with a shop's customers, jobs, appointments, invoices and time: a website contact form, a booking site, an accounting sync, or a Zapier or Make workflow.

| Page | What's in it |
|---|---|
| [Getting started](01-getting-started.md) | Base URL, your first key, a first request in curl, Python, Node and PHP |
| [Authentication](02-authentication.md) | API keys, scopes, expiry, rotation, revocation |
| [Requests and errors](03-requests-and-errors.md) | Envelope, pagination, errors, limits, rate limits, idempotency |
| [Resources](04-resources.md) | Every v1 endpoint, with parameters, bodies, responses and the events it fires |
| [Webhooks](05-webhooks.md) | Setup, envelope, signature verification, retries, redelivery |
| [Events](06-events.md) | The full event catalogue, with a payload for each event |
| [Security](07-security.md) | How Tekly protects data, an integrator checklist, reporting a vulnerability |
| [Recipes](08-recipes.md) | Contact forms, booking sites, Zapier and Make, accounting sync |
| [Changelog](09-changelog.md) | The 2026-10-25 releases, breaking changes and deprecations |
| [API v2](10-api-v2.md) | Inquiries, tags, services, bundles, writable products, addresses, webhooks by API, resource scopes |

## At a glance

- **Base URL:** `{BASE_URL}` is `https://<your-shop-domain>/api/v1`; v2 is at `https://<your-shop-domain>/api/v2`. See [Getting started](01-getting-started.md#base-url).
- **Auth:** `Authorization: Bearer rok_…`, server-side only. Broad scopes are `read`, `write` and `costs:read`; on v2 a key can instead be limited to `<resource>:read` or `<resource>:write`. Webhook management always needs `webhooks:read` / `webhooks:write`.
- **Rate limit:** 120 requests a minute per key by default (1–6,000 per key), reported in `X-RateLimit-*`.
- **Format:** JSON in and out. Lists are `{"data": [...], "page", "page_size", "total"}`; single records are `{"data": {...}}`; errors are `{"detail": ...}`.
- **Machine-readable contract:** `GET /api/v1/openapi.json` and `GET /api/v2/openapi.json` (OpenAPI 3.1, public, no key needed).
- **Webhooks:** signed with HMAC-SHA256 in `Tekly-Signature`, retried with backoff for up to 7 attempts, and de-duplicated on `Tekly-Webhook-Id`.
- **API versions:** `1.0` and `2.0`. **Webhook payload version:** `2026-10-25`.

## Conventions in these docs

- `{BASE_URL}` stands for your shop's API base URL. The code samples read it from `TEKLY_BASE_URL`, and the key from `TEKLY_API_KEY`.
- Every id, key, secret, name and number in the examples is made up.
- A field shown as `string?` can be `null` or left out.
