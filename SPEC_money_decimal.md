# H2 — Money Decimal-compute: scope & staged rollout

## Why this is staged, not one commit

Money is already **stored** exactly — every money column is `Numeric(12,2, asdecimal=False)`
(build 144aq), so the database never holds binary-float drift. The remaining exposure is purely in
**computation**: ~338 `round(...)` call sites across the routers/services compute money with
Python's built-in `round()`, which uses banker's rounding (round-half-to-even) on top of float
representation error. That produces the occasional wrong penny on a receipt (`round(2.675,2)==2.67`,
`1.005→1.00`) and non-exact sums (`0.1+0.2==0.30000000000000004`).

Rewriting all 338 sites at once would be the single riskiest possible diff in the codebase — every
invoice, payment, payroll, tax, refund and ledger path in one swing. So it is staged, each stage
independently shippable and test-gated.

## Phase 1 — SHIPPED (build 144aw)

- `backend/services/money.py`: the one place money quantizes. `q2`/`q4` (round-half-up, returns
  float for drop-in use), `d2` (exact `Decimal`), `msum` (exact summation). Converts through
  `Decimal(str(x))` so the float's decimal text rounds, not its binary noise. Self-check + pytest
  (`test_money_helper.py`).
- Applied at the first high-value boundary: `finance.create_invoice` subtotal + discount/tax total.

## Phase 2 — highest-value boundaries (next)

Convert the money math a customer or the books actually see, grouped so each PR is one concern with
its own regression test. Priority order by blast radius:

1. **Invoice & order totals** — `services/invoicing.py` (`_order_amount`, line roll-ups),
   `routers/orders.py` line/total recompute, `routers/finance.py` remaining totals.
2. **Tax & discount proration** — anywhere a percentage or a split is applied then rounded
   (`tax_rate`, `discount`, multi-order invoice apportionment). Rounding per-line vs per-total must
   be decided once and documented (penny-allocation rule: round lines, carry the remainder to the
   last line).
3. **Payments, refunds, change** — `routers/cash.py`, `routers/returns.py`, `services/ledger.py`
   (`order_charge_net`, contact-ledger deltas) and the archive reversal.
4. **Payroll & commissions** — `services/payroll.py` already uses `Decimal`; standardise it on
   `money.d2` so one rounding rule governs everywhere.

Each item: swap `round(x, 2)` → `q2(x)` and `sum([...])` → `msum([...])`, add a targeted test that
pins the previously-wrong penny, run the money regression suite.

## Phase 3 — Decimal end-to-end on the hot paths (optional, measured)

Only if Phase 2 leaves residual drift worth chasing. Flip the hottest compute chains (invoice build,
payment posting) to carry `Decimal` unconverted from read to write, quantizing only at the
API/serialization boundary. This needs `asdecimal=True` on those columns' reads (or local
`Decimal(str(...))` lifts) and a pass over the Pydantic response models. Defer until a real
reconciliation discrepancy justifies the churn — storage is already exact, so this is a
belt-and-suspenders step, not a correctness fix.

## Invariants that must hold throughout

- Round-half-up is the money convention; `round()` (banker's) is never used on money again. A lint
  rule / review check can enforce "no bare `round(` in money modules once Phase 2 lands".
- Quantize once, at the boundary — never round intermediate sub-expressions, which re-introduces
  drift.
- `asdecimal=False` stays until Phase 3 deliberately changes specific columns, so Phase 1/2 carry
  zero type-ripple.
