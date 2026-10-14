# Phase 2/3 integrations — operator guide

Every external integration in Phase 2/3 is a **config-gated seam**: it runs in a
simulated/stub mode out of the box (so it is testable and the UI works in every
environment) and switches to live behavior the moment the relevant credentials
are present in `backend/config.py` (or the `.env` file / environment variables).
No code change is needed to go live.

## 2A — Payments (Stripe)

| Setting | Purpose |
|---------|---------|
| `STRIPE_SECRET_KEY` | When set, real Stripe Checkout Sessions are created |
| `STRIPE_WEBHOOK_SECRET` | When set, the webhook verifies Stripe's signature |
| `STRIPE_SUCCESS_URL` / `STRIPE_CANCEL_URL` | Redirect targets after checkout |

Flow: `POST /api/payments/checkout {order_id, amount}` creates a `PaymentIntent`
and returns a `checkout_url`. On success the Stripe webhook
(`POST /api/payments/stripe/webhook`) reconciles the payment into the existing
cash system — a transaction is posted to the target cashbox (Account) and a
`Payment` row is written against the invoice, exactly like a manual payment. A
`payment.recorded` webhook fires.

Without a key, the **simulated** gateway returns a local confirm URL
(`/api/payments/intents/{id}/simulate-pay`) so the full reconcile path is
testable. Reconciliation is idempotent.

Roadmap: Terminal / QR (Payment Links) plug into the same `PaymentGateway`
abstraction in `backend/services/payments.py`.

## 2B — Public REST API v1

Self-contained; no external keys. See `public-api-v1.md`. Mint keys at
`POST /api/api-keys`.

## 2C — QuickBooks Online

| Setting | Purpose |
|---------|---------|
| `QBO_CLIENT_ID` / `QBO_CLIENT_SECRET` | Intuit OAuth2 app credentials |
| `QBO_REDIRECT_URI` | Must match the Intuit app's redirect |
| `QBO_ENVIRONMENT` | `sandbox` or `production` |

Flow: `GET /api/integrations/qbo/connect` returns the Intuit authorize URL (with
a CSRF state nonce). After the user authorizes, Intuit redirects to
`/api/integrations/qbo/callback` which exchanges the code for tokens and marks the
connection live. Push a record with
`POST /api/integrations/qbo/sync/{invoice|payment|contact}/{local_id}`; mappings
and a full audit trail are at `/api/integrations/qbo/mappings` and `/logs`.

Without credentials, `connect` returns `400` and sync attempts are logged
`skipped` (never lost). Provider/network failures are logged `failed`, never
raised into the request.

## 3A — Reporting / export

CSV, XLSX and a JSON read API are available for every registered report (see
`public-api-v1.md`, Reporting section). New labor analytics from the time clock:

- `labor_utilization` — per-tech clocked vs billable hours, utilization %, cost
- `labor_by_job` — scheduled vs tracked vs billed labor per work order
- `labor_cost` — org totals incl. daily overtime

## 3B — Workflow AI

| Setting | Purpose |
|---------|---------|
| `ANTHROPIC_API_KEY` | When set, real model calls; otherwise deterministic stub |
| `AI_MODEL` | Model id (default `claude-sonnet-4-6`) |

Endpoints: `POST /api/ai/orders/{id}/summary`, `POST /api/ai/estimate/draft`,
`POST /api/ai/chat/{thread}/suggest`. Each response includes
`{"provider": "anthropic" | "stub"}`. Provider failures fall back to the stub.

## 3C — Chat channels (WhatsApp / SMS via Twilio)

| Setting | Purpose |
|---------|---------|
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | Twilio credentials |
| `TWILIO_FROM` | SMS sender number |
| `TWILIO_WHATSAPP_FROM` | WhatsApp sender (`whatsapp:+...`) |

Inbound: point your Twilio number's webhook at
`POST /api/chats/webhook/twilio?org_id=<org>`. Inbound messages create/link a
thread (CRM client matched by phone) and store the message. Outbound replies via
`POST /api/chats/threads/{id}/send` transmit through Twilio when keys are set,
and fall back to the simulated (logged) transport otherwise — the response
includes `"simulated": true|false`.
