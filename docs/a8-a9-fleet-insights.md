# A8 Fleet/B2B + A9 Owner Analytics

## Status correction
Much of A8/A9 was **already built** in `backend/routers/analytics.py`:
technician-productivity, location-comparison, trends (A9) and fleet overview +
fleet detail (A8), plus `Client.client_type` (person|company/organization). This
pass fixes a latent bug and adds the genuinely-missing owner-summary + B2B
statement.

## Fixed
- **client_type mismatch:** fleet overview queried `== "company"` but the model
  comment/schema said `organization`. Now matches `in ("company","organization")`
  so fleet accounts appear regardless of which value was used. (Regression test.)

## Added
### A9 — Business Insights (`GET /api/analytics/business-insights?days=`)
Owner KPI roll-up over a window: orders created/closed, close-rate %, revenue,
avg ticket value, avg turnaround (hours), top 5 technicians by closed-order
revenue, top 5 services by frequency.

### A8 — Fleet statement (`GET /api/analytics/fleet/{client_id}/statement?days=`)
One consolidated billable statement across a B2B account's vehicles: each closed
order with its vehicle + amount, and a rolled-up total — what a fleet account
manager invoices monthly.

## Tests
`backend/tests/test_a8_a9.py` (3): both-type fleet regression, fleet statement
(2 vehicles → $200 rollup), business insights (revenue, close-rate, top
tech/service). Suite 104. No schema change.

## Remaining Track A (this is pass 1 of the A4–A9 sweep)
- **A4 Xero, A6 Gmail/Outlook, A7 VoIP/Zapier** — external OAuth; will be built as
  gated adapter seams (Square/SumUp/QBO pattern), tested at the seam, not
  live-fired without credentials.
- **A5 AI depth** — text tasks callable via the API; image tasks scaffolded.
