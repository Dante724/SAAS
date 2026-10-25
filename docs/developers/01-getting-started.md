# Getting started

This page takes you from nothing to a working API call in about five minutes.

## Base URL

Every shop is served from its own Tekly address. Its API lives under `/api/v1`:

```
{BASE_URL} = https://<your-shop-domain>/api/v1
```

> **The production hostname isn't final yet.** Tekly's public domain isn't live, so these docs never hard-code a host. Use the address your shop signs in at, with `/api/v1` on the end, and keep it in configuration (the samples use `TEKLY_BASE_URL`) so changing it later is a one-line change.

The API only answers over HTTPS. A plain `http://` request gets `403` (see [Security](07-security.md)).

## 1. Create an API key

1. Sign in to Tekly as someone whose role has the **Manage company settings** permission.
2. Go to **Settings → Integrations → API keys** and choose **Create key**.
3. Give the key a name that says where it will be used, for example `Website contact form`.
4. Choose the **Access**:
   - **Read only** (the default): see customers, jobs, estimates, products, appointments, invoices and time.
   - **Read and write**: also add customers, jobs, appointments and time entries.
   - **Include cost prices**: adds product costs and staff pay rates (the `costs:read` scope). Leave this off unless the other system needs them. Only someone whose role can **View the purchase price of products** can turn it on.
5. Choose when the key expires: 30 days, 90 days, 1 year (the default) or never.
6. Copy the key. It looks like `rok_5f2c9a1e_…` and is **shown only once**.

Store the key in your server's secret store or environment, never in a browser, a mobile app or source control. See [Authentication](02-authentication.md) for scopes, rotation and revocation.

```bash
export TEKLY_BASE_URL="https://<your-shop-domain>/api/v1"
export TEKLY_API_KEY="rok_5f2c9a1e_..."   # paste your real key here, not this placeholder
```

## 2. Make your first request

`GET {BASE_URL}` is the discovery endpoint. It tells you which shop the key belongs to and what it can do, so it's a good first call.

### curl

```bash
curl "$TEKLY_BASE_URL" \
  -H "Authorization: Bearer $TEKLY_API_KEY"
```

### Python

Uses [`requests`](https://pypi.org/project/requests/).

```python
import os

import requests

BASE_URL = os.environ["TEKLY_BASE_URL"]
API_KEY = os.environ["TEKLY_API_KEY"]

session = requests.Session()
session.headers["Authorization"] = f"Bearer {API_KEY}"

me = session.get(BASE_URL, timeout=10)
me.raise_for_status()
print(me.json()["data"]["scopes"])

# The 10 newest work orders
orders = session.get(f"{BASE_URL}/orders", params={"page_size": 10}, timeout=10)
orders.raise_for_status()
for order in orders.json()["data"]:
    print(order["number"], order["status"], order["total"])
```

### Node.js

Node 18 or later (built-in `fetch`). Save it as `first.mjs`.

```js
const BASE_URL = process.env.TEKLY_BASE_URL;
const API_KEY = process.env.TEKLY_API_KEY;

const res = await fetch(`${BASE_URL}/orders?page_size=10`, {
  headers: { Authorization: `Bearer ${API_KEY}` },
});
if (!res.ok) throw new Error(`Tekly API ${res.status}: ${await res.text()}`);

const { data, total } = await res.json();
console.log(`${data.length} of ${total} work orders`);
for (const order of data) console.log(order.number, order.status, order.total);
```

### PHP

Uses the cURL extension.

```php
<?php
$baseUrl = getenv('TEKLY_BASE_URL');
$apiKey  = getenv('TEKLY_API_KEY');

$ch = curl_init($baseUrl . '/orders?page_size=10');
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER     => ['Authorization: Bearer ' . $apiKey],
    CURLOPT_TIMEOUT        => 10,
]);
$body   = curl_exec($ch);
$status = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
curl_close($ch);

if ($status !== 200) {
    throw new RuntimeException("Tekly API $status: $body");
}
$result = json_decode($body, true);
foreach ($result['data'] as $order) {
    echo $order['number'], ' ', $order['status'], ' ', $order['total'], PHP_EOL;
}
```

### What you get back

```json
{
  "data": {
    "version": "1.0",
    "org_id": "3c2b8f1e-7a45-4d0b-9e61-2f8a5c7d9b10",
    "scopes": ["read"],
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

`scopes` lists the scopes the key was created with. `write` also allows reading, so a key that shows `["write"]` can read as well.

## 3. Make your first write

Writes need a **Read and write** key. Send an `Idempotency-Key` so that a retry after a network error can't create a duplicate:

```bash
curl -X POST "$TEKLY_BASE_URL/contacts" \
  -H "Authorization: Bearer $TEKLY_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 4f9a3c1e-2b7d-4e8f-9a61-0c5d2e7b8f13" \
  -d '{"name": "Sam Patel", "phone": "+1 555 0142"}'
```

```json
{
  "data": {
    "id": "9e9629c7-f8a4-4aef-bb49-a1a961ce1297",
    "name": "Sam Patel",
    "type": "person",
    "phone": "+1 555 0142",
    "email": null,
    "address": null,
    "loyalty_tier": "standard",
    "balance": 0,
    "created_at": "2026-10-25T18:08:17.424271"
  }
}
```

Send the same request again with the same `Idempotency-Key` and you get the same contact back, with an `Idempotent-Replayed: true` header, instead of a second Sam Patel.

## The OpenAPI document

The full contract is published as OpenAPI 3.1:

```
GET {BASE_URL}/openapi.json
```

- It's public: no key is needed, and it's cached for 5 minutes.
- It covers only the public v1 routes. Every operation has a readable `operationId`, such as `v1_list_orders` or `v1_create_contact`.
- `{BASE_URL}/docs` redirects to it.

Load it into Postman, Insomnia or Bruno to try the API, or into a generator such as `openapi-generator` to build a typed client.

## Next steps

- [Authentication](02-authentication.md): scopes, rotation and key hygiene.
- [Requests and errors](03-requests-and-errors.md): pagination, error handling, rate limits and retries.
- [Webhooks](05-webhooks.md): get told when things happen, instead of polling.
- [Recipes](08-recipes.md): end-to-end guides for common shop integrations.
