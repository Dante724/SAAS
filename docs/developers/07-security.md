# Security

A repair shop's data includes its customers' names, phone numbers, addresses and payments. This page covers how Tekly protects that data across the API and webhooks, and what we ask of you as an integrator.

## How Tekly protects data

| Area | What Tekly does |
|---|---|
| **Hashed API keys** | Keys are random and shown once. Tekly stores only a SHA-256 hash, so nobody, including Tekly staff, can read a key back out of the database. |
| **Least privilege** | Every key has scopes. New keys are read-only by default. Cost fields need a separate `costs:read` scope, which only people allowed to see purchase prices can grant. |
| **Expiry, rotation, revocation** | Keys expire (1 year by default). Rotation keeps the old key valid for only 24 hours. Revocation is immediate. A suspended shop's keys stop working. |
| **Tenant isolation** | A key belongs to one shop. Every query is limited to that shop, and an id from another shop gets the same `404` as an id that doesn't exist, so ids can't be probed across shops. |
| **HTTPS only** | The API refuses plain HTTP with `403`. Webhooks are only delivered to `https://` URLs. |
| **No browser access** | `/api/v1` sends no CORS headers, so a key can't be used from a web page on another site. |
| **Strict input** | Unknown fields are rejected, and every field has a type and a length limit. Bodies are capped at 16 MB. |
| **Rate limits** | 600 requests a minute per key. Invalid keys are limited by IP address, so made-up keys can't get a fresh budget. |
| **SSRF protection** | Webhook URLs must resolve only to public addresses, on port 443 or 8443. Private, loopback, link-local, cloud-metadata and carrier-grade NAT ranges are blocked. The check runs again on every delivery, and Tekly connects to the exact IP it checked, so DNS rebinding can't redirect a delivery inward. Redirects aren't followed. |
| **Signed webhooks** | Every delivery is signed with HMAC-SHA256 over a timestamp and the full body, so receivers can detect tampering and replays. |
| **Encrypted secrets** | Webhook signing secrets are encrypted at rest and shown only when created or rotated. The endpoint list shows only a short hint, such as `whsec_…Q_Hc`. |
| **Generic delivery errors** | The delivery log records outcomes like `Timed out` or `HTTP 503`, never raw network errors, so it can't be used to map someone's network. |
| **Auditing** | Every write through the API is in the shop's audit log, attributed to the key that made it. Creating, rotating and revoking keys is audited too, along with who did it. |
| **Access to settings** | Only people with **Manage company settings** can create keys, manage webhooks or see delivery payloads. |

## Integrator checklist

**Keys**

- [ ] Keys live in a secret store or server environment, never in code, front-end bundles, mobile apps, logs or URLs.
- [ ] One key per integration, with the fewest scopes it needs. No `costs:read` unless the job needs it.
- [ ] Every key has an expiry, and someone owns rotating it.
- [ ] Unused keys are revoked. You've checked **Last used** for idle ones.
- [ ] You rotate straight away if a key may have leaked, or if it was ever sent over plain HTTP.

**Requests**

- [ ] All calls go to an `https://` base URL that's configurable, not hard-coded.
- [ ] Every `POST` sends an `Idempotency-Key` that stays the same across retries.
- [ ] You back off on `429` (respect `Retry-After`) and on `5xx`.
- [ ] You don't log full responses that hold customer data, or you redact them.

**Webhooks**

- [ ] You verify `Tekly-Signature` on every delivery, using the raw body and a constant-time comparison.
- [ ] You reject timestamps more than 300 seconds old, and keep your clock in sync.
- [ ] You de-duplicate on `Tekly-Webhook-Id`, and handle events arriving out of order.
- [ ] You answer `2xx` within a few seconds and do slow work in a queue.
- [ ] The signing secret is stored like a password, and you know how to [rotate it](05-webhooks.md#rotate-the-signing-secret).
- [ ] You have moved off the deprecated `X-Tekly-*` and `X-RepairOS-*` headers.

**Data**

- [ ] You keep only the customer data you need, and delete it when you no longer do.
- [ ] Your own storage of Tekly data is access-controlled and encrypted.

## Reporting a vulnerability

If you think you've found a security problem in Tekly, please tell us privately first.

- **Email:** `security@<your-domain>`
- Include what you found, the steps to reproduce it, and what an attacker could do with it.
- Please don't access other shops' data, degrade the service, or share the issue publicly until we've fixed it.

We'll acknowledge your report, keep you updated while we fix it, and credit you if you'd like.

<!-- TODO(owner): replace security@<your-domain> with the real security inbox before publishing, and confirm the response-time commitment. -->
