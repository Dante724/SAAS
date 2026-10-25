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

Use one key per integration, named after it (`Zapier`, `Website`, `Xero sync`). Then you can rotate or revoke one integration without touching the others, and the key list tells you which one was **Last used** when.

## Scopes

| Scope | Shown in the app as | Allows |
|---|---|---|
| `read` | Read only | Every `GET` on `/api/v1`. |
| `write` | Read and write | Every `POST` on `/api/v1` (create contacts, work orders, appointments and time entries). **Includes `read`.** |
| `costs:read` | Include cost prices | Adds cost fields to responses: product `cost_price`, and time-entry `cost_rate` and `billable_rate`. Without it those fields are **left out**, not set to `null`. It doesn't allow reading on its own, so combine it with `read` or `write`. |

- A request without the scope it needs gets `403` with `{"detail": "API key lacks 'write' scope"}`.
- Only someone whose role can **View the purchase price of products** can create a key with `costs:read`.
- Keys made before 2026-10-25 with the old `*` scope still work, as `read` plus `write`. They never get `costs:read`.

Give each key the least it needs. A dashboard needs `read`. A contact form needs `write`. Only an accounting or payroll export needs `costs:read`.

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
| `403` | `API key lacks '<scope>' scope` | The key doesn't have the scope this route needs. |
| `403` | `The Tekly API is only available over HTTPS.` | The request came over plain HTTP. |
| `403` | `This account is suspended. Please contact support.` or `Your subscription has ended. Please contact support to renew.` | The shop's account isn't active. Its keys stop working until it is. |

`401` responses carry `WWW-Authenticate: Bearer`.

## Server-side only

The API is meant to be called **server to server**. `/api/v1` sends no CORS headers, so a browser on another site can't call it, and a key pasted into a web page or a mobile app wouldn't work there anyway. That's deliberate: anyone who can see a key can use it.

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
