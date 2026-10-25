# Webhooks

Webhooks tell your system when something happens in a shop: a job is created, an invoice is paid, an appointment is booked. Tekly sends an HTTPS `POST` with a signed JSON body to a URL you choose. It's faster and cheaper than polling the API.

## Set up an endpoint

1. In Tekly, go to **Settings → Integrations → Webhooks** and choose **Add endpoint**. You need the **Manage company settings** permission.
2. Enter the **Endpoint URL**, a public `https://` address (see [URL rules](#url-rules)), and an optional **Description**, for example `Accounting sync`.
3. Under **Events**, keep **All events** or **Choose events** to pick some. The list comes from the [event catalogue](06-events.md).
4. Save, then copy the **Signing secret**. It starts with `whsec_` and is **shown only once**. Keep it with your receiver.
5. Choose **Send test event** to check that your endpoint answers.

From the same screen you can edit an endpoint, turn it off and on, **Delete** it, make a **New secret**, and open **Recent deliveries** to see each delivery's status, response code, attempts and payload, and **Redeliver** it.

A shop can have up to **10 endpoints**.

## What Tekly sends

```http
POST /tekly HTTP/1.1
Host: hooks.example.com
Content-Type: application/json
User-Agent: Tekly-Webhooks/1.0
Tekly-Signature: t=1792951681,v1=5d1c0b7e…
Tekly-Webhook-Id: 3f6a1d2e-8b4c-4e9a-a7f1-0d2c5b8e9f34
Tekly-Event: order.created

{"id": "evt_6ae454c7d6894392a34e6bcf63f14bab", "event": "order.created", ...}
```

### Headers

| Header | Description |
|---|---|
| `Tekly-Signature` | `t=<unix seconds>,v1=<hex signature>`, with one `v1` for each active secret. See [Verify signatures](#verify-signatures). |
| `Tekly-Webhook-Id` | The delivery id. **It stays the same on every retry and redelivery of this delivery**, so de-duplicate on it. |
| `Tekly-Event` | The event name, for example `invoice.paid`. |
| `Content-Type` | `application/json` |
| `User-Agent` | `Tekly-Webhooks/1.0` |

Deliveries also carry `X-Tekly-Event`, `X-Tekly-Signature`, `X-RepairOS-Event` and `X-RepairOS-Signature`, plus `Deprecation: true` and a `Tekly-Deprecated-Headers` list naming them. They're **deprecated** and kept for one release cycle so older receivers keep working. Don't build on them. See the [changelog](09-changelog.md).

### The envelope

Every body has the same envelope:

```json
{
  "id": "evt_6ae454c7d6894392a34e6bcf63f14bab",
  "event": "order.created",
  "api_version": "2026-10-25",
  "org_id": "3c2b8f1e-7a45-4d0b-9e61-2f8a5c7d9b10",
  "created_at": "2026-10-25T18:06:01Z",
  "data": {
    "id": "a83d5f2c-1b7e-4a9d-8c63-0e4f9b2d7a15",
    "order_number": "WO-01042",
    "status": "new",
    "total": 0.0
  },
  "at": "2026-10-25T18:06:01.526939"
}
```

| Field | Description |
|---|---|
| `id` | The event id (`evt_…`). One event sent to several endpoints has the same `id` at each of them. |
| `event` | The event name. For an alias it's the name you subscribed to (see [Events](06-events.md#aliases)). |
| `api_version` | The payload version, currently `2026-10-25`. |
| `org_id` | The shop the event belongs to. If you serve several shops, use it to pick the right secret and API key. |
| `created_at` | When the event happened, UTC, ISO 8601 with `Z`. |
| `data` | The event's payload. See the [event catalogue](06-events.md). |
| `at` | **Deprecated.** The same moment as `created_at`, without the `Z` and with microseconds. Use `created_at`. |

## Verify signatures

Anyone who learns your URL can POST to it. Check the signature on **every** delivery before you trust it.

### How the signature works

1. Tekly takes the current Unix time `t` and the **exact raw body** it is about to send.
2. It builds the signed payload: `t`, then a `.`, then the body. For example `1792951681.{"id":"evt_…",…}`.
3. It computes **HMAC-SHA256** of that payload, keyed with your signing secret (the whole string, `whsec_` included), and hex-encodes it, lowercase.
4. It sends `Tekly-Signature: t=<t>,v1=<hex>`. While a [secret rotation](#rotate-the-signing-secret) is under way, there's one `v1` per active secret: `t=…,v1=<new>,v1=<old>`.

To verify:

1. Read the **raw request body** as bytes, *before* any JSON parsing. A parsed and re-serialized body won't match.
2. Split the header on `,`. Take `t`, and every `v1` value. Ignore keys you don't recognize.
3. **Reject** the request if `t` is more than **300 seconds** away from your clock, in either direction. This stops a captured request being replayed later. Keep your server's clock in sync (NTP).
4. Compute `HMAC-SHA256(secret, t + "." + raw_body)` for each secret you hold.
5. Accept if **any** `v1` matches **any** of your secrets, using a constant-time comparison.
6. Otherwise respond `400` or `401`, and don't process the event.

### Python

Standard library only.

```python
import hashlib
import hmac
import time

TOLERANCE_SECONDS = 300


def verify_tekly_signature(raw_body: bytes, signature_header: str, secrets, now=None) -> bool:
    """Return True if the Tekly-Signature header is valid for raw_body.

    raw_body          the exact bytes of the request body, before any JSON parsing
    signature_header  the value of the Tekly-Signature header
    secrets           your signing secret, or a list of secrets while you rotate
    """
    if isinstance(secrets, str):
        secrets = [secrets]

    timestamp, signatures = None, []
    for part in (signature_header or "").split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return False
        elif key == "v1" and value:
            signatures.append(value)
    if timestamp is None or not signatures:
        return False

    # Reject old (or future) timestamps so a captured request can't be replayed.
    current = time.time() if now is None else now
    if abs(current - timestamp) > TOLERANCE_SECONDS:
        return False

    signed_payload = str(timestamp).encode() + b"." + raw_body
    for secret in secrets:
        expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        # During a rotation the header carries one v1 per active secret: accept any match.
        if any(hmac.compare_digest(expected, sig) for sig in signatures):
            return True
    return False
```

In Flask:

```python
import json
import os

from flask import Flask, abort, request

app = Flask(__name__)
SECRETS = [s for s in os.environ["TEKLY_WEBHOOK_SECRETS"].split(",") if s]  # new,old while rotating


@app.post("/tekly")
def tekly_webhook():
    raw = request.get_data()                      # raw bytes: verify before parsing
    if not verify_tekly_signature(raw, request.headers.get("Tekly-Signature", ""), SECRETS):
        abort(400)
    event = json.loads(raw)
    # De-duplicate on request.headers["Tekly-Webhook-Id"], queue the work, answer fast.
    return "", 204
```

### Node.js

Built-in `crypto` only.

```js
const crypto = require("crypto");

const TOLERANCE_SECONDS = 300;

/**
 * Returns true if the Tekly-Signature header is valid for rawBody.
 * rawBody:         the exact request body as a Buffer (or string), before JSON parsing
 * signatureHeader: the value of the Tekly-Signature header
 * secrets:         your signing secret, or an array of secrets while you rotate
 */
function verifyTeklySignature(rawBody, signatureHeader, secrets, now = Date.now() / 1000) {
  const secretList = Array.isArray(secrets) ? secrets : [secrets];

  let timestamp = null;
  const signatures = [];
  for (const part of String(signatureHeader || "").split(",")) {
    const i = part.indexOf("=");
    if (i === -1) continue;
    const key = part.slice(0, i).trim();
    const value = part.slice(i + 1).trim();
    if (key === "t") {
      if (!/^\d+$/.test(value)) return false;
      timestamp = Number(value);
    } else if (key === "v1" && value) {
      signatures.push(value);
    }
  }
  if (timestamp === null || signatures.length === 0) return false;

  // Reject old (or future) timestamps so a captured request can't be replayed.
  if (Math.abs(now - timestamp) > TOLERANCE_SECONDS) return false;

  const body = Buffer.isBuffer(rawBody) ? rawBody : Buffer.from(rawBody, "utf8");
  const signedPayload = Buffer.concat([Buffer.from(`${timestamp}.`, "utf8"), body]);

  for (const secret of secretList) {
    const expected = crypto.createHmac("sha256", secret).update(signedPayload).digest();
    // During a rotation the header carries one v1 per active secret: accept any match.
    for (const sig of signatures) {
      if (!/^[0-9a-f]{64}$/i.test(sig)) continue;
      const given = Buffer.from(sig, "hex");
      if (given.length === expected.length && crypto.timingSafeEqual(expected, given)) return true;
    }
  }
  return false;
}

module.exports = { verifyTeklySignature };
```

In Express, use `express.raw()` on the webhook route so you get the body as a `Buffer`. `express.json()` would parse it and lose the exact bytes.

```js
const express = require("express");
const { verifyTeklySignature } = require("./verifyTekly");

const app = express();
const SECRETS = process.env.TEKLY_WEBHOOK_SECRETS.split(",").filter(Boolean); // new,old while rotating

app.post("/tekly", express.raw({ type: "application/json" }), (req, res) => {
  if (!verifyTeklySignature(req.body, req.get("Tekly-Signature"), SECRETS)) {
    return res.sendStatus(400);
  }
  const event = JSON.parse(req.body.toString("utf8"));
  // De-duplicate on req.get("Tekly-Webhook-Id"), queue the work, answer fast.
  res.sendStatus(204);
});
```

### PHP

Uses `hash_hmac` and `hash_equals`.

```php
<?php
const TEKLY_TOLERANCE_SECONDS = 300;

/**
 * Returns true if the Tekly-Signature header is valid for $rawBody.
 * $rawBody:         the exact request body, before json_decode
 * $signatureHeader: the value of the Tekly-Signature header
 * $secrets:         your signing secret, or an array of secrets while you rotate
 */
function tekly_verify_signature(string $rawBody, string $signatureHeader, $secrets, ?int $now = null): bool
{
    $secrets = is_array($secrets) ? $secrets : [$secrets];

    $timestamp = null;
    $signatures = [];
    foreach (explode(',', $signatureHeader) as $part) {
        $pair = explode('=', trim($part), 2);
        if (count($pair) !== 2) {
            continue;
        }
        [$key, $value] = $pair;
        if ($key === 't') {
            if (!ctype_digit($value)) {
                return false;
            }
            $timestamp = (int) $value;
        } elseif ($key === 'v1' && $value !== '') {
            $signatures[] = $value;
        }
    }
    if ($timestamp === null || count($signatures) === 0) {
        return false;
    }

    // Reject old (or future) timestamps so a captured request can't be replayed.
    $now = $now ?? time();
    if (abs($now - $timestamp) > TEKLY_TOLERANCE_SECONDS) {
        return false;
    }

    $signedPayload = $timestamp . '.' . $rawBody;
    foreach ($secrets as $secret) {
        $expected = hash_hmac('sha256', $signedPayload, $secret);
        // During a rotation the header carries one v1 per active secret: accept any match.
        foreach ($signatures as $signature) {
            if (hash_equals($expected, $signature)) {
                return true;
            }
        }
    }
    return false;
}

// A plain PHP endpoint
$rawBody = file_get_contents('php://input');
$header  = $_SERVER['HTTP_TEKLY_SIGNATURE'] ?? '';
$secrets = array_filter(explode(',', getenv('TEKLY_WEBHOOK_SECRETS'))); // new,old while rotating

if (!tekly_verify_signature($rawBody, $header, $secrets)) {
    http_response_code(400);
    exit;
}
$event = json_decode($rawBody, true);
// De-duplicate on $_SERVER['HTTP_TEKLY_WEBHOOK_ID'], queue the work, answer fast.
http_response_code(204);
```

### Test vector

Use this to check your implementation. With these inputs, your HMAC must produce exactly this `v1`:

| Input | Value |
|---|---|
| Secret | `whsec_test_5WbX3nC1p9QeK2tLmZ8rYvA4` |
| Timestamp `t` | `1767225600` |
| Raw body | `{"id":"evt_test","event":"order.created","data":{"id":"ord_1"}}` |
| Expected `v1` | `d83670797014725e0bb393ba9d8969615956b6cb14b92d18bcdb6d926654023b` |
| Header | `t=1767225600,v1=d83670797014725e0bb393ba9d8969615956b6cb14b92d18bcdb6d926654023b` |

The timestamp is in the past, so pass `now` explicitly when you test:

```python
body = b'{"id":"evt_test","event":"order.created","data":{"id":"ord_1"}}'
header = "t=1767225600,v1=d83670797014725e0bb393ba9d8969615956b6cb14b92d18bcdb6d926654023b"
assert verify_tekly_signature(body, header, "whsec_test_5WbX3nC1p9QeK2tLmZ8rYvA4", now=1767225600)
assert not verify_tekly_signature(body, header, "whsec_test_5WbX3nC1p9QeK2tLmZ8rYvA4", now=1767225901)
```

```js
const body = '{"id":"evt_test","event":"order.created","data":{"id":"ord_1"}}';
const header = "t=1767225600,v1=d83670797014725e0bb393ba9d8969615956b6cb14b92d18bcdb6d926654023b";
console.assert(verifyTeklySignature(body, header, "whsec_test_5WbX3nC1p9QeK2tLmZ8rYvA4", 1767225600));
console.assert(!verifyTeklySignature(body, header, "whsec_test_5WbX3nC1p9QeK2tLmZ8rYvA4", 1767225901));
```

```php
$body = '{"id":"evt_test","event":"order.created","data":{"id":"ord_1"}}';
$header = 't=1767225600,v1=d83670797014725e0bb393ba9d8969615956b6cb14b92d18bcdb6d926654023b';
assert(tekly_verify_signature($body, $header, 'whsec_test_5WbX3nC1p9QeK2tLmZ8rYvA4', 1767225600));
assert(!tekly_verify_signature($body, $header, 'whsec_test_5WbX3nC1p9QeK2tLmZ8rYvA4', 1767225901));
```

## Respond quickly with a 2xx

- **Any `2xx`** counts as delivered. Everything else counts as a failure and is retried: `3xx`, `4xx`, `5xx`, a timeout, or a connection error.
- **Redirects aren't followed.** A `301` or `302` is a failure, so give Tekly the final URL.
- Each attempt has **10 seconds** in total, with 5 seconds for each network step. Answer first, then do the work: store the event, return `2xx`, and process it from a queue.
- Tekly reads at most the first 4 KB of your response and doesn't store it. The body you return doesn't matter.

## Retries and backoff

A failed delivery is retried on this schedule, measured from the previous attempt:

| Attempt | Delay |
|---|---|
| 1 | Straight away, once the change is saved |
| 2 | 1 minute |
| 3 | 5 minutes |
| 4 | 30 minutes |
| 5 | 2 hours |
| 6 | 8 hours |
| 7 | 24 hours |

After the 7th attempt, the delivery stays **Failed**. You can still [redeliver](#redeliver) it by hand. Retries run on a one-minute sweep, so allow up to about a minute of extra delay.

Events are only sent for changes that were actually saved. If the change in Tekly fails and is rolled back, no webhook goes out.

## Automatic disabling

If an endpoint has failed **continuously for 24 hours**, Tekly turns it off and tells everyone in the shop with **Manage company settings** in the app: *Webhook turned off*. The endpoint shows **Turned off** with the reason. While it's off, nothing is sent and its queued deliveries are dropped.

Fix the receiver, then turn the endpoint back on in **Settings → Integrations → Webhooks**. That restarts the 24-hour clock. Redeliver anything important from **Recent deliveries**.

One successful delivery resets the clock, so an endpoint that is only sometimes down stays on.

## Send a test event

**Send test event** sends a signed `webhook.test` event to the endpoint straight away, whatever events it's subscribed to, and shows what your endpoint answered. Tests aren't retried.

```json
{
  "id": "evt_f5e969871e18423d922a8b7b544d7429",
  "event": "webhook.test",
  "api_version": "2026-10-25",
  "org_id": "3c2b8f1e-7a45-4d0b-9e61-2f8a5c7d9b10",
  "created_at": "2026-10-25T18:06:57Z",
  "data": {
    "message": "Test event from Tekly",
    "endpoint_id": "fe945c77-0c39-47f5-be62-22dceb6ce509"
  },
  "at": "2026-10-25T18:06:57.530915"
}
```

Answer `webhook.test` with a `2xx` like any other event, even if you don't act on it.

## Redeliver

In **Recent deliveries**, choose **Redeliver** to send a delivery again now. It's sent with **the same body and the same `Tekly-Webhook-Id`**, and a fresh `t` and signature. If it fails, it goes through the retry schedule again.

You can't redeliver a delivery that's still being sent, or one whose endpoint is off or deleted. Turn the endpoint on first.

## De-duplication

Tekly delivers **at least once**. Network problems, retries and redeliveries mean you can get the same delivery more than once. Make your handler idempotent:

- Store each `Tekly-Webhook-Id` you've processed, for at least a few days, and skip ones you've seen.
- Or key your writes on the event `id` (`evt_…`), or on the record id in `data`, so applying an event twice changes nothing.

## Ordering

Deliveries to one endpoint go out one at a time, but **order isn't guaranteed**. A retried delivery can arrive after a newer one: for example, an `order.status_changed` to `ready` can arrive after the one to `delivered`. So:

- Compare `created_at` before you overwrite newer data with older data.
- When the latest state matters, treat the event as a prompt and read the record from the API, for example `GET /orders/{id}`.

## URL rules

| Rule | Detail |
|---|---|
| Scheme | `https://` only. |
| Port | `443` (the default) or `8443`. |
| Host | Must resolve **only** to public internet addresses. Private networks (10/8, 172.16/12, 192.168/16), loopback, link-local and cloud metadata addresses (169.254.169.254), carrier-grade NAT (100.64/10), and other reserved ranges are refused. |
| Credentials | No `user:password@` in the URL. |
| Length | Up to 500 characters. |

The URL is checked when you save it, and again on every attempt. Tekly connects to the exact address that passed the check, so a DNS change can't point a delivery at an internal service. A URL that fails on save gets `URL must be a public https:// address`. One that fails at delivery time is logged as `Blocked: URL is not an allowed public https address`.

To test a receiver on your own machine, expose it through an HTTPS tunnel that gives you a public hostname. `localhost` and private addresses are always refused.

## Rotate the signing secret

1. In **Settings → Integrations → Webhooks**, choose **New secret** on the endpoint, and copy the new secret. It's shown once.
2. **For 24 hours, every delivery is signed with both the new and the old secret**: the header has two `v1` values.
3. Add the new secret to your receiver alongside the old one. All the verifiers above accept a list.
4. Within 24 hours, remove the old secret from your receiver.

Rotate when someone who had the secret leaves, if it may have leaked, or on a regular schedule.

## Managing endpoints from code

Endpoints are managed in the app. The same actions are available at `/api/webhooks` to a **signed-in staff session** with **Manage company settings**. API keys can't call them. They exist for the Tekly app itself and aren't part of the public, versioned API. Prefer the UI.

## Automation rule webhooks

A rule in **Settings → Notifications → Automations** can also have a webhook action. Those deliveries use the same envelope, signing, retries and auto-disabling, with a secret shown once when you save the rule. For one release cycle their body also repeats the old flat keys `rule`, `order_id`, `order_number` and `status` at the top level. Their event names come from the automation rule, not from the [event catalogue](06-events.md).
