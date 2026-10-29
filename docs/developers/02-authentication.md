# Authentication

The Tekly API is authenticated with **API keys**. Each key belongs to one shop, carries a fixed set of scopes, and is sent as a bearer token:

```http
GET /api/v1/orders HTTP/1.1
Host: <your-shop-domain>
Authorization: Bearer rok_5f2c9a1e_...
```

## API keys

| | |
|---|---|
| Format | `rok_<8 hex characters>_<random secret>`. The first part, for example `rok_5f2c9a1e`, is the **prefix**. It's shown in the app so you can tell keys apart. |
| Where to create one | **Settings → Integrations → API keys**. You need the **Manage company settings** permission. |
| Shown | **Once**, when it's created or rotated. Tekly stores only a SHA-256 hash, so a lost key can't be recovered. Rotate it instead. |
| Belongs to | One shop. A key can never see another shop's data. |
| Default access | Read only. |
| Default expiry | 1 year. You can pick 30 days, 90 days, 1 year or never. |
| Limit | 25 active keys per shop. Revoke one to make room for another. |
| Requests a minute | 120 by default. You can set 1 to 6,000 per key when you create it. See [Rate limits](03-requests-and-errors.md#rate-limits). |
| Works on | `/api/v1` and `/api/v2`, depending on its scopes. |

Use one key per integration, named after it (`Zapier`, `Website`, `Xero sync`). Then you can rotate or revoke one integration without touching the others, and the key list tells you which one was **Last used** when.

## Scopes

A key's scopes are fixed when it's created. There are **broad** scopes, which cover a whole API version, and **resource** scopes, which narrow a key to one kind of record on [API v2](10-api-v2.md).

### Broad scopes

| Scope | Shown in the app as | Allows |
|---|---|---|
| `read` | Read only | Every `GET` on `/api/v1`, and every `GET` on `/api/v2` except webhooks. |
| `write` | Read and write | Everything `read` allows, plus every write on `/api/v1` and on `/api/v2` except webhooks. **Includes `read`.** |
| `costs:read` | Include cost prices | Adds cost fields to responses: product and service `cost_price`, and time-entry `cost_rate` and `billable_rate`. Without it those fields are **left out**, not set to `null`. It doesn't allow reading on its own, so combine it with other scopes. |

### Resource scopes (API v2)

`<resource>:read` and `<resource>:write`, shown in the app as **Only some things**. `<resource>:write` includes `<resource>:read`. The resources are:

`contacts`, `orders`, `estimates`, `products`, `appointments`, `invoices`, `credit_notes`, `time_entries`, `inquiries`, `tags`, `services`, `bundles`, `webhooks`.

- Resource scopes work on **`/api/v2` only**. A key that holds only resource scopes gets `403` on every `/api/v1` route.
- `orders`, `estimates` and `contacts` are also checked when an inquiry is [converted](10-api-v2.md#post-inquiriesidconvert) on v2.
- **`webhooks` is never covered by a broad scope.** `read`, `write` and `*` can't list or add webhook endpoints: a key needs `webhooks:read` or `webhooks:write` for that, picked on its own.
- A key can mix both kinds, for example `read,inquiries:write`: read everything, and also create and change inquiries.

### Rules for every key

- A request without the scope it needs gets `403`, for example `{"detail": "API key lacks 'write' scope"}` or `{"detail": "API key lacks 'inquiries:write' scope"}`.
- New keys get `read` only unless you choose otherwise.
- Only someone whose role can **View the purchase price of products** can create a key with `costs:read`.
- An unknown scope, or none at all, is refused with `422` when the key is created.
- Keys made before 2026-10-25 with the old `*` scope still work, as `read` plus `write`. A new key asked for with `*` is stored as `read,write`. Neither ever gets `costs:read`.
- Expiry, rotation, revocation and the 25-key limit apply to every key, whatever its scopes.

Give each key the least it needs. A dashboard needs `read`. A website form that files inquiries needs only `inquiries:write`. Only an accounting or payroll export needs `costs:read`.

## Expiry

A key stops working at its expiry time and then returns `401`. The key list shows **Expires …** or **Never expires**, and **Expired** once it has passed. You can't extend a key: create a new one, or rotate it before it expires (rotation keeps the original expiry date).

## Rotation

Rotate a key when someone who had it leaves, when it may have been exposed, or on a regular schedule.

1. In **Settings → Integrations → API keys**, choose **Rotate** on the key.
2. Copy the new key. It's shown once.
3. Deploy it to your integration.

**The old key keeps working for 24 hours after a rotation**, so you can swap keys without downtime. During that window both work, the list shows **The previous key still works until …**, and both share one rate-limit budget. Name, scopes and expiry don't change. Only an active key can be rotated.

## Revocation

Choose **Revoke** to turn a key off at once. A revoked key, and any previous key still in its rotation window, returns `401` on its next request. Revoking can't be undone.

## Authentication errors

| Status | `detail` | Why |
|---|---|---|
| `401` | `Missing API key (Authorization: Bearer rok_...)` | No `Authorization` header, or it isn't `Bearer …`. |
| `401` | `Not a public API key` | The token doesn't start with `rok_`. Staff sign-in tokens don't work here. |
| `401` | `Invalid, expired or revoked API key` | Unknown, expired or revoked. The message is the same in every case, on purpose. |
| `403` | `API key lacks '<scope>' scope` | The key doesn't have the scope this route needs. On v2 the scope is named with its resource, such as `tags:write`. |
| `403` | `The Tekly API is only available over HTTPS.` | The request came over plain HTTP. |
| `403` | `This account is suspended. Please contact support.` or `Your subscription has ended. Please contact support to renew.` | The shop's account isn't active. Its keys stop working until it is. |

`401` responses carry `WWW-Authenticate: Bearer`.

## Server-side only

The API is meant to be called **server to server**. `/api/v1` and `/api/v2` send no CORS headers, so a browser on another site can't call it, and a key pasted into a web page or a mobile app wouldn't work there anyway. That's deliberate: anyone who can see a key can use it.

If a browser or app needs Tekly data, have it call your own backend, and let the backend call Tekly.

## HTTPS

The API only answers over HTTPS. A request over plain HTTP is refused with `403` rather than quietly served, so a key sent in clear text shows up as an error straight away. If you see that `403`, fix the URL and then **rotate the key**, because it has already crossed the network unencrypted.

## Best practice

- **Keep keys in a secret store** (your cloud provider's secret manager, or environment variables on the server). Never commit them, log them, or put them in URLs.
- **Use one key per integration**, with the fewest scopes it needs and an expiry.
- **Rotate** at least once a year, and straight away when someone with access leaves. The key list flags keys made by someone who has since been deactivated, so you know which to review.
- **Revoke** keys you no longer use. **Last used** shows which ones are idle.
- **Watch for `401`s** in your integration's logs. They usually mean an expired or revoked key.
- Every write made with a key is in the shop's audit log, attributed to that key.

## Managing keys from code

Keys are managed in the app. The same actions are at `/api/api-keys` for a **signed-in staff session** with **Manage company settings**; API keys can't call them.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/api-keys` | The shop's keys, without secrets. Also returns `available_scopes` (the broad scopes), `resources`, `resource_scopes`, `explicit_only` (`["webhooks"]`), `default_rate_limit_per_min`, `max_rate_limit_per_min` and `max_active`. Each key has `scopes`, `scope_list`, `rate_limit_per_min` (what was set, or `null`) and `effective_rate_limit_per_min`. |
| `POST` | `/api/api-keys` | Body `{name, scopes, expires_in_days, rate_limit_per_min}`. `scopes` is a list (or the older comma-separated string), default `["read"]`. `expires_in_days` is 1–3650, default 365, `null` for never. `rate_limit_per_min` is 1–6000, or left out for the default. Returns `key` once. |
| `POST` | `/api/api-keys/{id}/rotate` | A new secret; the old one works for 24 hours. |
| `DELETE` | `/api/api-keys/{id}` | Revoke. |
