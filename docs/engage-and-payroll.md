# Gift Cards, Coupons, Loyalty (A2) + Multi-Technician Work Orders & Payroll

## How Orderry connects roles → work → payroll (research summary)
From Orderry's payroll documentation: each employee has **Payroll Calculation
Rules** — a base salary (hourly/daily/monthly) plus **commission rules keyed to
events**: *for creating an order*, *for closing an order*, *to the technician
for the labor/service they added* ("if several employees add labors to the work
order… each employee will receive a commission for their labors"), *to the
assigned technician* (order-level and per line/product), and seller events for
sales. Commissions are percent or fixed, can wait for a specific status, and
support refund cancellation/deductions. **Exceptional commissions** on a
product/service/category override profile rules (priority: product → category →
profile) and are multiplied by per-employee **coefficients** (seniority).
Calculations aggregate on the Payroll page into one monthly **accrual** per
employee onto their balance, then payouts.

## What Tekly already had (verified, not rebuilt)
`CommissionRule(event, calc_type percent|flat, percent_basis total|margin,
coefficient, category, employee-specific > org-wide)`, an idempotent
`EmployeeBalanceLedger` (exactly-once via idempotency keys), `PayrollRun`,
per-line events (`labor_performed`, `product_sold`) attributed to
**`OrderLine.technician_id`** with fallback to the assigned tech, margin-basis
using MAC, company-paid-discount handling, and clawbacks on reopen/refund —
i.e., the Orderry model, already implemented.

## What was missing → shipped this pass
- **Multi-technician UI**: the backend supported per-line technicians but the
  UI never exposed it. The order/estimate line table now has a **Tech** column;
  click to assign any employee per line ("performs this task — payroll
  commission goes to them"), and lines are PATCHable (`technician_id`). Two
  techs on one work order each earn on their own lines — test-proven.
- **`order_created` commissions**: the event existed in the rule registry but
  never fired; it now accrues to the order's creator on create (idempotent).
  Two wiring bugs found & fixed en route (engine constructor missing `org_id`;
  loyalty hook's `Client` import).

## A2 — Gift cards · Coupons · Loyalty
Redemptions post a **negative fee line** on the order (totals/invoices/payroll
discount handling stay consistent) plus a ledger row; balances are derivable.

| Endpoint | Behaviour |
|---|---|
| `POST /api/giftcards` `{value, client_id?, code?, expires_at?}` | Issue (finance perm); code auto `GC-XXXXXX`; ledger `+load`. 409 on duplicate code. |
| `GET /api/giftcards` · `GET /api/giftcards/{code}` | List / balance check. |
| `POST /api/orders/{id}/redeem-giftcard` `{code, amount?}` | Default amount = min(card balance, order balance); over-redeem/expired/inactive → 400. |
| `POST /api/coupons` `{code, kind pct\|fixed, value, min_total?, starts_at?, ends_at?, max_uses?}` | Create (settings perm). |
| `POST /api/orders/{id}/apply-coupon` `{code}` | Validates window/min-total/usage-cap/one-per-order; bumps `uses`. |
| `GET /api/clients/{id}/loyalty` | `{points, lifetime_points, tier, earn_rate, point_value, enabled}`. |
| `POST /api/orders/{id}/redeem-loyalty` `{points}` | Burns points at `loyalty_point_value`; insufficient → 400. |

**Earning** is automatic in payment reconciliation (`mark_succeeded`): when
`Organisation.loyalty_earn_rate > 0`, the paying client earns
`amount × earn_rate` points, and their **tier auto-upgrades** from lifetime
earned points (standard < 500 ≤ silver < 2000 ≤ gold), replacing the static
label. Order UI: **Coupon / Gift card / Points** buttons beside "Take
prepayment".

Config lives on the org (`loyalty_earn_rate`, `loyalty_point_value`; 0 = off).
Migration adds 4 tables + 2 columns (schema: **86 tables**).

## Tests
`backend/tests/test_engage_multitech.py` (6): two-tech commission split
(10% rule → 10.00 + 5.00 to the right people), line-tech PATCH, order_created
accrual, gift-card lifecycle (issue→redeem→over-redeem 400, order total drops),
coupon rules (case-insensitive apply, duplicate 400, max_uses 400), loyalty
earn-on-payment + auto-tier + redeem + insufficient 400.

Tail (documented, not built): gift-card sale as a POS product, campaign UI for
coupons, settings UI for loyalty rates (API-first today), tier thresholds
per-org.
