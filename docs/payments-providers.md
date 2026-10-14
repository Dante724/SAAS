# Payments Breadth (A1) — Stripe · Square · SumUp · Terminal

Per-org gateway selection on the existing `PaymentGateway` seam. Absent keys ⇒
that provider isn't selectable; with no live provider everything falls back to
the simulated gateway, so the full flow stays testable.

## Provider selection
`Organisation.payment_provider` ∈ `auto | stripe | square | sumup | simulated`
(Settings → Integrations, or `PUT /api/staff/company`; 422 on unknown values).
`auto` picks the first configured live gateway (stripe → square → sumup), else
simulated. `GET /api/payments/config` returns the org's choice, the resolved
live gateway, and which providers have keys. Choosing an unconfigured provider
fails cleanly (502 with "not configured") rather than silently downgrading.

## Gateways
| Provider | Checkout | Confirmation | Keys |
|---|---|---|---|
| **Stripe** | Checkout Session (existing) | Signed webhook (existing) | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` |
| **Square** | Payment Links (quick pay). `provider_ref` stores the Square **order id** so the webhook can match. Sandbox/production via `SQUARE_ENV`. | `POST /api/payments/square/webhook` — HMAC-SHA256 over `notification_url + body`, base64, `x-square-hmacsha256-signature`; fail-closed when `SQUARE_WEBHOOK_SIGNATURE_KEY` is set (`PUBLIC_BASE_URL` forms the URL). Also pollable via order state. | `SQUARE_ACCESS_TOKEN`, `SQUARE_LOCATION_ID`, `SQUARE_WEBHOOK_SIGNATURE_KEY` |
| **SumUp** | Hosted checkout (`hosted_checkout.enabled`); `provider_ref` = checkout id | **Poll**: `POST /api/payments/intents/{id}/refresh` checks `status == PAID` (no signed webhook); the public pay page auto-polls once on load | `SUMUP_API_KEY`, `SUMUP_MERCHANT_CODE` |
| **Simulated** | Local confirm URL | `simulate-pay` | — |

All confirmations converge on the same idempotent `mark_succeeded` reconciliation
(cash transaction + Payment row + invoice status + `payment.recorded` webhook +
technician push).

## In-person: Stripe Terminal (slice)
`POST /api/payments/terminal/connection-token` (reader SDK token) and
`POST /api/payments/terminal/create-intent` (`card_present` PaymentIntent for an
existing RepairOS intent). Both `can_view_finance`, both **503** without Stripe
keys. Reader pairing/UX is the client-side SDK's job; this is the server half.

## Caveats (honest)
- Verified against provider docs + unit tests with a stubbed transport — **not
  live-fired** (no provider keys in the sandbox). First live run per provider
  should be a sandbox-key smoke test.
- SumUp's hosted-checkout URL field is parsed defensively (`hosted_checkout_url`
  or nested `hosted_checkout.url`); confirm on first live run.
- Square Terminal (as opposed to Stripe Terminal) and gift receipts remain on
  the roadmap (A1 tail / A2).

Tests: `backend/tests/test_payments_providers.py` (6) — Square request/parse,
SumUp parse, org setting + unconfigured 502 + auto fallback, signed-webhook
verify + reconciliation, SumUp poll-to-paid, terminal 503 gating.
