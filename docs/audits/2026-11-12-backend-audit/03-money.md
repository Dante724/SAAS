# 03 — Money: finance, cash box, discounts, tax, refunds (prefix FIN)

Source: `audit-src` snapshot of `roadmap/orderry-parity` @ d353c67b. Read-only. Probes in
`scratchpad/probes_fin/test_fin_probes.py`, run with
`venv/bin/python -m pytest -p backend.tests.conftest --rootdir=. -c pytest.ini ../probes_fin/test_fin_probes.py -s`
(throwaway SQLite, 6/6 passed, i.e. every bug the probes target reproduced).

## Summary

| Severity | Count |
|---|---|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 8 |
| LOW | 7 |

Overall, the money core is in much better shape than at the 29 Oct audit:
- one invoice-money service (`services/invoice_money.py`);
- one "what's paid" definition (`services/order_money.py`);
- atomic `UPDATE col = col + x` for running balances (`money.add_to_column`);
- conditional-UPDATE claims for void, delete and restore (`money.claim_status`);
- idempotency keys on most money-in endpoints;
- a nightly ledger-vs-balance sweep (`services/reconcile.py`).

The remaining problems sit where a document's **cash leg and its other legs can be separated**, or where an endpoint was left out of the idempotency and lock patterns.

## Prior items

| ID | Status | Evidence |
|---|---|---|
| H5 (total below 0, redeem races) | **PARTIAL** | **Floor: FIXED.** `orders.py:4357` `order.total = max(total, 0)`; `invoice_money.retotal` floors at 0; `order_money.overpaid` uses `max(order.total, 0)`.<br>**Redeem lock: FIXED.** Redeem endpoints lock the order: `engage._order_or_404(lock=True)` uses `with_for_update()`; coupons also call `lock_order`.<br>**Caps: FIXED.** Gift-card, coupon and loyalty amounts are capped at `balance_due`.<br>**Re-cap when lines change: STILL OPEN.** Deleting or editing a discount line doesn't give back the card balance, coupon use or points (FIN-03, test-verified). |
| M3 (restore of voided sale/refund leg) | **FIXED** for document legs | `cash.py:421-460` refuses `refund`, `client_return`, `purchase_return`, `order`, `invoice`, `sale` and `payroll_run`. Existing test `test_restore_refuses_document_legs` passes. **Gap:** `client` and `supplier_payment` legs are deletable and restorable on their own (FIN-02). |
| M4 (double delete/restore) | **FIXED** for cash | `delete_transaction` and `restore_transaction` claim via `claim_status` (`cash.py:409,449`). `test_concurrent_delete_reverses_once` and `test_concurrent_restore_applies_once` pass (run here). Transfer and stock-doc parts were not re-checked (another reviewer's scope); their tests in the same file pass. |
| R4 (money as float) | **PARTIAL** | Metadata introspection:<br>• 41 `Numeric` columns are Decimal (cash shifts, drawer movements, expenses, gift cards, client ledger, payroll, postings);<br>• **95 are still `Numeric(asdecimal=False)`** (float in Python): `payments.amount`, `transactions.amount`, `accounts.balance`, `invoices.*`, `orders.*`, `sales.*`, `refunds.amount`, `coupons.value`, `credit_notes.*`;<br>• 63 are `Float`, including `transactions.fx_rate`, `orders.fx_rate`, `currencies.rate`, `order_lines.tax_rate`, `taxes.rate`, `accounts.fee_pct`, `loyalty_ledger.points`, `employees.commission_*`.<br>Storage is exact (2 dp). Compute uses `q2`/`msum`/`d2` (half-up) on most paths: there are no bare `round(` calls in `finance`, `invoice_money`, `order_money`, `ledger`, `payroll`, `payments` or `revenue`. A few remain: `returns.py:215` `round(data.amount - …, 2)` and `orders.py:5036` (cost). |
| Estimate→invoice audit G4, F2–F5 | **FIXED (code-read)** | `invoice_money` header and functions:<br>• `apply_order_money`/`apply_deposits` (G4);<br>• `room_for_job` caps each invoice at its own balance (F2);<br>• `apply_payment_to_invoice` splits money across jobs (F3);<br>• `sync_order_invoices` makes unlocked invoices follow the job and refuses edits to locked lines (F4);<br>• discount and tax are stored as amounts via `pct_amounts` (F5). |

---

## Findings

### FIN-01 [HIGH] Refund endpoints have no idempotency or double-click guard: the same refund books twice
- **Module:** refunds (work orders)
- **Tables/columns:** `refunds`, `payments(kind='refund')`, `transactions(txn_type='refund')`, `accounts.balance`, `client_ledger_entries`
- **Functions/APIs:**
  - `backend/routers/returns.py:170-242`, `create_refund` (POST `/api/returns/refunds`);
  - `backend/routers/cash.py:863-908`, `refund_overpayment` (POST `/api/cash/orders/{id}/refund`);
  - both call `book_refund`, `returns.py:265`.
- **Execution flow:**
  1. The order is locked.
  2. The cap is `net_paid` (create_refund) or `max(overpaid, deposit held)`.
  3. `book_refund` writes the Refund, the refund Payments, the till leg and the ledger.
  4. Commit.
  - Neither endpoint calls `idem_begin`/`run_once` or `_refuse_double_click`, though `pay_order`, `prepay_order`, `sales.return` and `returns.create` all have them. Compare the list from grep: `cash.pay_order cash.prepay_order … returns.create sales.return` (no `returns.refund`).
- **Evidence:**
  - Probe `test_probe_duplicate_refund`:
    - pay 100;
    - POST the refund of 30 twice, with the **same** `Idempotency-Key`;
    - result: `refund1 201 …` `refund2 201 …` (two different ids), till `40`.
  - Probe `test_probe_duplicate_deposit_refund`:
    - prepay 80;
    - refund 40 twice with the same key;
    - result: both 201, till `0.0`.
- **Verification level:** test-verified.
- **Why it's a problem:** The lock only stops a refund above the cap. A repeated partial refund under the cap goes through. The Idempotency-Key the client sends is ignored.
- **Real-world failure scenario:**
  - A cashier double-taps "Refund 30" on a phone, or the network retries the request.
  - 60 leaves the drawer, the customer is "refunded" twice, and commission is clawed back twice (each refund id is its own clawback key).
- **Recommended fix:**
  - Wrap both endpoints in `idem_begin` with `body=`.
  - Add the same `_refuse_double_click` window that `_pay_order` uses (same order, account and amount within 10 s) when no key is sent.
- **Fix dependencies/risks:** The frontend must send a key per dialog open. Low risk.

### FIN-02 [HIGH] The cash leg of a client advance, payout or supplier payment can be deleted or restored alone: the till and the customer's credit or supplier AP desync, and the integrity sweep can't see it
- **Module:** cash box / contact ledger / supplier AP
- **Tables/columns:**
  - `transactions(ref_type IN ('client','supplier_payment'), status)`;
  - `accounts.balance`;
  - `clients.balance`;
  - `client_ledger_entries`;
  - `suppliers.balance`.
- **Functions/APIs:**
  - `backend/routers/cash.py:378-418`, `delete_transaction`: guards only `refund|client_return|purchase_return` and the `_DOCUMENT_LEGS` set `{order, invoice, sale, payroll_run}`;
  - `cash.py:421-460`, `restore_transaction`: same lists;
  - writers:
    - `clients.py:1088-1134` `move_balance` (`ref_type="client"`);
    - `finance.py:797` (overpayment kept as balance, `ref_type="client"`);
    - `suppliers.py:111-155` `pay_supplier` (`ref_type="supplier_payment"`).
- **Execution flow:**
  1. An advance of 100 adds +100 to the till and -100 to the client ledger (credit).
  2. DELETE `/api/cash/transactions/{id}` takes the till back to 0.
  3. The client keeps -100 of credit.
  4. Pay a job with `method=balance` → `available_credit` allows 100.
- **Evidence:** Probe `test_probe_delete_client_advance_leg`:
  - `delete client leg 200 {"ok":true,"voided":1}`;
  - `client balance after delete {'balance': -100.0 …}`;
  - `pay from credit 200 {"ok":true,"method":"balance","amount":100.0}`;
  - `mismatches []` (from `reconcile.find_mismatches`).

  The supplier probe: delete of the `supplier_payment` leg returns 200 and the till goes back to 500. `delete_transaction` has no supplier-side reversal (code-read; `suppliers.balance` stays decremented). The code comment at `suppliers.py:118` even says these are "reversible via the cash transaction's own correction tooling".
- **Verification level:** test-verified (client); test-verified (till) plus code-read (supplier side).
- **Why it's a problem:**
  - Money that never stayed in the till becomes spendable customer credit, or supplier debt silently disappears.
  - Each side still matches its own ledger, so the nightly sweep (account vs txns, client vs ledger) reports nothing.
- **Real-world failure scenario:** A staff member with `can_delete_transactions` deletes an advance's cash row (pocketing the cash) and the customer still spends the credit. Or a supplier payment is "undone" in Cash while AP still shows it as paid.
- **Recommended fix:**
  - Add `client` and `supplier_payment` to the guarded set: a void must go through a document-level reversal that posts the contra ledger row.
  - Or have `delete_transaction`/`restore_transaction` post the mirrored `ClientLedgerEntry`/`adjust_supplier_ap`.
  - Add a sweep invariant: Σ cash legs by `(ref_type, ref_id)` vs the ledger rows that reference them.
- **Fix dependencies/risks:** Existing data may already be desynced. Write a one-off report query.

### FIN-03 [MEDIUM] Work-order discount lines from a gift card, coupon or loyalty points can be deleted or edited without giving back what they consumed (H5 remainder)
- **Module:** engage (gift cards, coupons, loyalty) / order lines
- **Tables/columns:** `order_lines(kind='custom', line_type='fee', description 'Gift card …'/'Coupon …'/'Loyalty points …')`, `gift_cards.balance`, `gift_card_ledger`, `coupons.uses`, `loyalty_ledger`
- **Functions/APIs:**
  - `engage.py:60-66` `_add_discount_line`, `:149-187` `redeem_giftcard`, `:308-370` `apply_coupon`, `:395-435` `redeem_loyalty`;
  - `orders.py:5390-5440` `delete_line` and `:5271` `update_line`: they guard `kind in ('refund','membership')` only.
- **Execution flow:**
  1. Redeem a card worth 60 on a 100 job. A -60 fee line is added and the card goes to 0.
  2. DELETE the line. The job total goes back to 100.
  3. Nothing restores the card, and no `GiftCardLedger` row is written.
  4. The same applies to the coupon `uses` count and to points.
  - PATCH can also change the line's `unit_price` to any value, so the discount no longer matches the ledger.
- **Evidence:** Probe `test_probe_giftcard_line_delete` printed `delete gift line 200 {"ok":true,"order_total":100.0}` and `card balance 0.0 order total 100`. Compare the POS void, which restores all three (`sales.py:449-468`).
- **Verification level:** test-verified (gift card); code-read (coupon, loyalty, PATCH).
- **Why it's a problem:** The customer loses stored value, or staff can re-price a promo line freely. The gift-card ledger stays self-consistent, so nothing flags it.
- **Real-world failure scenario:** A tech removes the "Gift card" line to re-quote a job. The customer's 60 is gone, and the support ticket has no audit trail of where it went.
- **Recommended fix:**
  - Give discount lines a dedicated `kind` (`giftcard`/`coupon`/`loyalty`) with `ref_id` set to the ledger row.
  - Refuse PATCH on these lines.
  - On DELETE, post the reversing ledger row (as `delete_sale` does).
  - Also re-cap discount lines when the lines they paid for are removed.
- **Fix dependencies/risks:** Existing lines are identified only by description text. A migration has to backfill `kind`.

### FIN-04 [MEDIUM] Invoice "Record payment" accepts a cash payment with no account (no till leg) and accepts a closed drawer shift
- **Module:** invoices / cash box
- **Tables/columns:** `payments.account_id`, `payments.shift_id`, `transactions`, `cash_shifts.closed_at`, `cash_shifts.expected_cash`
- **Functions/APIs:** `backend/routers/finance.py:667-832` `_record_payment`:
  - `moves_cash = acc is not None and data.method != "balance"`;
  - `if shift_id: await _shift_or_404(...)`, which only checks the org, not `closed_at`.
- **Execution flow:**
  - **Path A:** `method="cash"`, no `account_id`:
    1. A Payment with `account_id NULL, shift_id NULL` is written.
    2. A contact-ledger income row is written.
    3. The invoice is marked paid.
    4. No Transaction is posted.
  - **Path B:** `shift_id` of a closed shift:
    1. The Payment is attached to that shift.
    2. The shift's `expected_cash` was frozen at close.
    3. The current drawer doesn't count the cash either.
- **Evidence:** Probe `test_probe_closed_shift_and_cash_without_account`:
  - `pay onto closed shift 200 …`;
  - `cash pay without account 200 {… "status":"paid" …} till 40`;
  - `payments [(40, '<closed shift id>', '<acct>'), (60, None, None)] txns [(40,)]`.
- **Verification level:** test-verified.
- **Why it's a problem:**
  - Cash Income / the P&L cash side and the drawer miss the money, but the invoice and customer show paid.
  - The current drawer counts "over" while the closed one never reflects it.
- **Real-world failure scenario:** An API or integration client (or a UI that omits the account) records cash payments that never reach any cashbox. The month's cash report is short with no exception raised.
- **Recommended fix:**
  - Require `account_id` for every method except `balance` (as `pay_order` does, `cash.py:762`).
  - Refuse a `shift_id` whose `closed_at` is set.
- **Fix dependencies/risks:** Check whether any client deliberately records "money received elsewhere". If so, model it as an explicit method.

### FIN-05 [MEDIUM] Per-cashbox access (`roles.account_ids`) is only applied to the account list
- **Module:** cash box RBAC (Orderry Q30)
- **Tables/columns:** `roles.account_ids`, `accounts`, `transactions`
- **Functions/APIs:**
  - `services/scoping.py:87-101` (`allowed_accounts`, `may_use_account`);
  - used only at `cash.py:154` (`list_accounts`) and `payroll.py:1116`. `grep may_use_account` finds no callers.
  - Unscoped:
    - `cash.py:161` `total_money` (all balances);
    - `cash.py:1011` `list_transactions` (any `account_id`);
    - `cash.py:281` `create_transaction`, `:480` `transfer`, `:678` `pay_order`, `:786` `prepay`;
    - `finance.py:643` `record_payment`;
    - `returns.py:170` `create_refund`;
    - `sales.py:485` `create_sale`.
- **Execution flow:** A role limited to "Front till" calls GET `/api/cash/transactions?account_id=<safe>` or `/total-money`, or posts an expense or refund against the safe's `account_id`.
- **Evidence:** Code-read. The only filter is `rows = [a for a in rows if a.id in allowed]` in `list_accounts`.
- **Verification level:** schema-level / code-read.
- **Why it's a problem:** The setting looks like an access control, but it only hides accounts in one dropdown. Anyone who knows an account id bypasses it.
- **Real-world failure scenario:** A cashier restricted to the till sees the owner's bank balance on Total money and moves money out of it with a transfer.
- **Recommended fix:** Enforce `may_use_account` in `post_transaction` (the single choke point for writes) and filter every read (`total_money`, `list_transactions`, `/balances`).
- **Fix dependencies/risks:** System paths such as payroll payouts and webhooks call `post_transaction` without an employee. Pass the actor through explicitly.

### FIN-06 [MEDIUM] The "prevent backdating invoices" setting isn't enforced on manual invoices, and the check it runs is the wrong comparison
- **Module:** invoices
- **Tables/columns:** `organisations.prevent_backdating_invoices`, `invoices.issued_at`
- **Functions/APIs:**
  - `cash.py:36-44` `_check_backdating` compares `now_utc() < last.issued_at`;
  - it is called only from `cash.py:596` (payment creating a job's first invoice) and `:945` (sub-invoice);
  - `finance.py:401-487` `create_invoice` passes `issued_at=_pdate(data.issued_at)` straight to `new_invoice`, with no check.
- **Execution flow:** POST `/api/finance/invoices` with `issued_at: "2025-01-01"` creates a backdated, numbered invoice even with the setting on. The existing check only fires when an earlier invoice was future-dated, which in turn blocks every later payment-created invoice.
- **Evidence:** Code-read. `grep prevent_backdating` shows the only read is `cash.py:40`.
- **Verification level:** schema-level / code-read.
- **Why it's a problem:** The compliance setting (fiscal sequence order) gives false assurance. A future-dated manual invoice also creates a self-inflicted block.
- **Recommended fix:**
  - When the flag is on, refuse a new `issued_at` earlier than the latest issued invoice's `issued_at`, in `new_invoice` itself so every path is covered.
  - Refuse future `issued_at` outright.
- **Fix dependencies/risks:** Low.

### FIN-07 [MEDIUM] "Record payment" on an invoice and "Take payment" on its job lock different rows, so both can fill the same balance
- **Module:** payments
- **Tables/columns:** `invoices.amount_paid`, `payments`, `orders`
- **Functions/APIs:**
  - `finance.py:676-680` locks the **Invoice** (`with_for_update(of=Invoice)`) and computes `remaining` from `inv`;
  - `cash.py:711-725` `_pay_order` locks the **Order** and computes `balance_due(order)` from Payment sums;
  - `invoice_money.apply_order_payment` → `room_for_job` reads `inv.amount_paid` unlocked, then `_credit` does an atomic add.
- **Execution flow:**
  1. On Postgres READ COMMITTED, two cashiers pay the same 100 job at once: one on the invoice, one on the job.
  2. Each sees 100 owing.
  3. Both commit, and `amount_paid` becomes 200 on a 100 invoice.
- **Evidence:** Code-read. The comments in `lock_invoices_of` set the rule "Order → Invoice", but `_record_payment` never takes the order lock.
- **Verification level:** NOT VERIFIED (SQLite serializes writes; needs Postgres).
- **Why it's a problem:** It causes a double collection. The `/finance/reconciliation` "double_paid" queue would show it after the fact.
- **Recommended fix:** In `_record_payment`, `lock_order` every job the invoice bills (sorted ids) **before** locking the invoice, then recompute.
- **Fix dependencies/risks:** Deadlock ordering. Follow the documented Order → Invoice → Client order.

### FIN-08 [MEDIUM] Membership allowances can be over-used by two jobs at once
- **Module:** memberships / discount cards
- **Tables/columns:** `membership_usage(qty)`, `order_lines.membership_id`, `memberships`
- **Functions/APIs:** `services/memberships.py:371-386` (`remaining` = allowance − used − pending lines), `:449-463` `apply_to_order`, `:465-496` `check_order_lines`, `:503-544` `record_usage`.
- **Execution flow:** Two different jobs on the same asset and member each run `apply_to_order`/`check_order_lines` concurrently. Each takes only its own order lock, so each sees `remaining >= 1`, and both get the 100% covered line. The only unique key is `uq_membership_usage_line(order_line_id)`, which doesn't stop this.
- **Evidence:** Code-read. There is no lock on `memberships` or on a per-membership counter.
- **Verification level:** NOT VERIFIED.
- **Why it's a problem:** Free services beyond the plan.
- **Recommended fix:** Take `SELECT … FOR UPDATE` on the `memberships` row in `apply_to_order`/`check_order_lines`/`record_usage`.
- **Fix dependencies/risks:** Low.

### FIN-09 [MEDIUM] Gift cards are a tender at the POS but a discount on work orders, and issuing one records no money or liability
- **Module:** gift cards / revenue
- **Tables/columns:** `gift_cards`, `gift_card_ledger`, `order_lines`, `sales`
- **Functions/APIs:**
  - `engage.py:83-117` `issue_giftcard` writes only the card and a ledger "Issued" row: no Transaction and no payment;
  - `engage.py:149` adds a negative fee line on the job (lowers `orders.total`, i.e. revenue);
  - `sales.py:226-250` treats the card as a tender ("B5: a tender, not a discount"), so the sale total is unchanged.
- **Execution flow:** The same card spent on a job reduces revenue, while spent at the counter it doesn't. There is no deferred-revenue (liability) balance anywhere for unredeemed cards.
- **Evidence:** Code-read.
- **Verification level:** schema-level / code-read.
- **Why it's a problem:** Revenue, tax base and the P&L differ by channel for the same economic event. Selling a card for cash has no proper path.
- **Recommended fix:**
  - Record a gift-card sale as cash-in to a liability.
  - Make job redemption a Payment with `method="gift_card"`, as at the POS.
- **Fix dependencies/risks:** Reports and payroll's discount handling assume the negative line. This needs coordination with the revenue owners.

### FIN-10 [MEDIUM] Accounts with a balance can be deactivated, and account settings are editable with the weaker finance-view right
- **Module:** cash box
- **Tables/columns:** `accounts.active`, `accounts.balance`, `payment_option`, `fee_pct`, `fee_fixed`
- **Functions/APIs:**
  - `cash.py:224-235` `delete_account` sets `active=False` with no balance or open-shift check (create/delete need `can_manage_accounts`);
  - `cash.py:208-221` `update_account` uses `require_perm("can_view_finance")` and changes `payment_option` and fees.
- **Execution flow:**
  - Deactivating a till holding 500 removes it from `total_money` (which filters `active == True`), so the 500 disappears from the reports.
  - A finance viewer can flip a till from cash to card, so its future cash stops linking to the drawer (`open_drawer_shift` requires `payment_option == "cash"`), or change processing fees.
- **Evidence:** Code-read.
- **Verification level:** schema-level / code-read.
- **Why it's a problem:** Money vanishes from Total money, and there is privilege inconsistency.
- **Recommended fix:**
  - Refuse deactivation while `balance != 0`.
  - Gate `update_account` with `can_manage_accounts`.
  - Keep `currency_code` locked (it already is: no update path).
- **Fix dependencies/risks:** None.

### FIN-11 [LOW] Missing amount validation on several money inputs
- **Module:** cash box / expenses
- **Tables/columns:**
  - `cash_shifts.opening_float`, `cash_shifts.closing_float`;
  - `expenses.amount`;
  - `payments.amount` (prepay);
  - `refunds.amount`;
  - `transactions.actual_date`.
- **Functions/APIs:**
  - `finance.py:87-107` (`ShiftOpen`, `ShiftCloseIn`, `ExpenseIn`: no validators);
  - `cash.py:782` `PrepayIn` (no `check_money`, no `q2`; `o.prepayment_total += data.amount`);
  - `returns.py:162` `RefundIn`;
  - `cash.py:299-304`: a manual transaction may be **future**-dated with no permission check (only past dates need `can_retroactive_cash`).
- **Evidence:** The probe opened a shift with `opening_float=-50` (200) and created an expense of `-1000` (201). A negative expense raises P&L profit (`operating_expenses`).
- **Verification level:** test-verified (negative float, negative expense); code-read (rest).
- **Recommended fix:** Use `check_money` validators on these models, `q2` on intake, and refuse a future `actual_date`.

### FIN-12 [LOW] Close-shift and drawer movements aren't claimed atomically
- **Module:** cash box
- **Tables/columns:** `cash_shifts.closed_at`, `closing_float`, `expected_cash`, `cash_drawer_movements`
- **Functions/APIs:** `finance.py:1129-1180` `close_shift` (checks `closed_at is None`, then writes, with no `claim_status`/lock); `:1100-1126` `drawer_movement` (same).
- **Execution flow:**
  - Two closes: the second overwrites the counted float and the expected snapshot.
  - A movement committed between the close's `_expected_cash` read and its commit sits on a closed shift but isn't counted.
- **Verification level:** code-read; NOT VERIFIED on Postgres.
- **Recommended fix:** Use `claim_status(shift, None→now)` on `closed_at` as the first step, and lock the shift row in `drawer_movement`.

### FIN-13 [LOW] A correction can unbalance a transfer, and corrections have no idempotency
- `cash.py:347-375` `correct_transaction`:
  - it doesn't refuse a leg with a `transfer_pair_id`, so one half can be shifted and the pair no longer nets;
  - a double click posts two deltas;
  - the correction of a `ref_type="client"`/`"supplier_payment"` leg is allowed (see FIN-02).
- Verification level: code-read.
- Recommended fix: refuse corrections on transfer legs (correct by a new transfer), and add `run_once`.

### FIN-14 [LOW] Precision and currency remnants (R4)
- 95 money columns still read as float, and fx rates and tax rates are `Float` (introspection, see the prior-items table).
- `money.minor_units` supports 3-decimal currencies (KWD/BHD/OMR), but every money column is `Numeric(12,2)` and `round_price` clamps to 2 dp. A KWD shop can't hold fils.
- The org currency (`organisations.currency_code`, default `"INR"`) is per org. Accounts carry their own `currency_code` (immutable: no update path). Documents in a non-base till are refused (`cash.py:76-82`), except online payments, which only log a warning (`payments.py:350`).
- The bare `round()` in `returns.py:215` is banker's rounding on a refund amount.
- Verification level: schema-level (metadata introspection).

### FIN-15 [LOW] An online pay link with no account set lands in "the first active account", possibly a cash till
- `services/payments.py:325-330`: `order_by(Account.is_company.desc()).first()`.
- The till's `balance` then includes card money that isn't in the drawer. The drawer's expected cash is unaffected (`txn_type invoice_payment`), so the account balance and the drawer disagree.
- Verification level: code-read.
- Recommended fix: require an online/default-card account in settings, and fall back to a virtual "Online" account.

### FIN-16 [LOW] The tax engine stacks taxes independently with per-line rounding
- `services/tax.py:resolve_taxes`: every tax in the stack is computed on the same `net_amount`. Two `included` taxes each extract `net − net/(1+r)` from the same gross, which overstates the tax. There is no compound (tax-on-tax) option despite the "ordered stack".
- Rounding is per line and per tax, then summed: POS `sale.tax` (`_price_sale`) and Σ `sale_lines.tax` (`pos_line_tax`, a separate call) can differ by cents.
- Inclusive vs exclusive is handled correctly for a single tax (code-read).
- Recommended fix: document the single-included-tax limit, or implement a combined-rate extraction. Store line tax from the same computation as the total.

### FIN-17 [LOW] Sale numbers have no unique constraint
- `sales.sale_number` has no `UniqueConstraint`, unlike `invoices`, `credit_notes`, `orders` and `purchase_orders`.
- `numbering.next_number` is atomic (UPDATE `next_value+1`, plus a `_number_taken` skip for invoice/po/credit_note/estimate only).
- A manual sequence reset in Settings can therefore re-issue `S-00001`.
- Recommended fix: add `uq_sales_org_number` and include `sale` in `_number_taken`.

---

## Checks requested (precision, tax, invoices, payments, races, ledger, planning)

**Precision and currency** (FIN-14, R4):
- Storage is `Numeric(12,2)`.
- Compute is mostly `q2`/`d2`/`msum` with half-up rounding, quantizing at the boundaries.
- There is one currency per org; accounts are single-currency and their currency is locked.

**Tax and discounts:**
- **Inclusive vs exclusive** is handled per line (`tax.line_tax`) and per POS line (`resolve_taxes`).
- **Discount order at the POS:** line discount → sale discount → coupon (% of what's left) → loyalty → tax on the remainder → gift card as tender (`sales.py:_price_sale` docstring and code).
- **Discount order on a job:** membership coverage sets the line `discount`; coupon lines are split by tax group (`coupons.order_coupon_lines`) and capped at `balance_due`; gift card and loyalty are capped at `balance_due`.
- **Coupon caps** are atomic: a guarded `UPDATE … WHERE uses < max_uses` on the job (`engage.py:345`) and the POS (`sales.py:170`).
- **Totals are floored at 0:** order (`orders.py:4357`), invoice (`retotal`), POS (`after = max(remaining, 0)`).

**Invoices:**
- Stored totals follow job lines while the invoice is unlocked (`sync_order_invoices`).
- Once sent, paid or e-issued (`is_locked`), lines can't be repriced (`assert_line_editable`, `release_line`) and the job history logs the difference.
- `update_invoice` changes only display mode and payer (issued invoices need `can_edit_issued_invoices`).
- **Void:** `delete_invoice` cancels; it refuses when credit notes exist and moves job money back to held.
- **Credit notes:**
  - drafts only are editable or deletable;
  - issue is capped at `total − credited` (`credit_notes.creditable`);
  - cancel reverses `credited` and the ledger.
- The manual-invoice `subtotal` override can intentionally differ from the job (by design).
- A manual `tax_pct` on an invoice that bills jobs computes tax on the job total, which already includes tax: possible double tax (code-read, uncertain).

**Payments:**
- **Allocation:** oldest invoice first, each up to its balance; the remainder is held on the job (`apply_order_payment`).
- **Overpayment:** refused at the job (`cash.py:743`). On an invoice it needs an explicit `keep_balance|refund`.
- **Refunds over paid:** refused (`create_refund` caps at `net_paid` under the order lock; the overpayment endpoint caps at overpaid or deposit).
- **Concurrent refunds:** serialized by the order lock (Postgres `FOR NO KEY UPDATE`, SQLite no-op UPDATE). **Duplicate** refunds under the cap: FIN-01.
- **Split payments:** one dialog per method. A multi-job invoice is split per job (`apply_payment_to_invoice`).
- **Deposits:** `Payment(kind='prepayment', invoice_id NULL)` lands on the next invoice (`apply_order_money`).

**Duplicates and races:**
- Idempotency keys exist on pay, prepay, invoice pay, transaction, transfer, sale create/pay/return, payout and credit-note create, but not on refunds (FIN-01), client advance/payout, supplier pay, drawer movement, gift-card issue, loyalty redeem or correction.
- **Two shifts on one box:** prevented by locking the Location row (`finance.py:1036`; `test_one_open_drawer` passes). There is no DB partial unique index, and SQLite isn't protected (dev only).
- **Close math:**
  - `expected = opening + cash Payments (kind≠refund) − cash/refund-method refunds − POS refund txns + pay-ins − pay-outs + manual income/expense txns`;
  - deleted transactions are excluded (`finance.py:853-882`);
  - **POS cash sales are counted through the `income` txn linked to the shift**;
  - job cash is counted through `Payment.shift_id`.
- Delete, restore and void of cash transactions are claimed atomically (M4).

**Ledger:**
- `transactions` is append-only in spirit: soft delete via `status`, corrections as delta rows, and `balance_after` written once in `post_transaction` and never updated.
- `accounts.balance`, `clients.balance` and `invoices.amount_paid` are **mutable caches**. They can be re-derived, and the nightly `reconcile.sweep` (`core/scheduled.py:377`) checks account = Σ active txns, client = Σ ledger, and invoice = Σ payments − refunds.
- **Not checked** by the sweep:
  - gift-card balance vs its ledger;
  - `coupons.uses`;
  - `suppliers.balance`;
  - `orders.prepayment_total`;
  - cross-ledger consistency between a cash leg and its contact-ledger twin (FIN-02 is invisible to it).

**Planning/budgets:**
- **There is no financial planning or budget module in the backend.** A grep for budget/planned found only `schedules.py` (planned vs actual working hours, not money).
- The money reports (P&L, accountant export, transaction summary) use shop-timezone day edges (`range_edge`, `shop_tzname`) and filter by `org_id`. The Transaction list is limited to today without `can_view_cashflow_any_period` (`cash_flow_floor`).

---

## Orderry rule checks (section 4 of 00-competitor-orderry.md)

| # | Answer | Evidence |
|---|---|---|
| 9 | **Yes**, with exceptions | `accounts.balance` changes only via `add_to_column` (atomic UPDATE) in `post_transaction`, `delete_transaction`/`restore_transaction` (transaction status claimed) and `void_document_legs`. `reconcile._accounts` checks Σ active txns = balance nightly. |
| 10 | **Yes** | `balance_after` is set once in `post_transaction` (`cash.py:89`). Delete and restore never touch it (comment and code at `cash.py:385-418`). |
| 11 | **Partly** | `_transfer` writes both halves with one `transfer_pair_id` in one commit (`cash.py:534-540`), with opposite amounts (fx-converted). Delete and restore act on both legs. A **correction can hit one leg** (FIN-13). |
| 12 | **Yes / yes**, with exceptions | It is a soft delete with a status claim and a reversal. Restore is symmetric and refuses document legs, but not `client`/`supplier_payment` legs (FIN-02). No hard-delete route exists for `transactions` (grep for `delete(Transaction)` finds none in the routers). |
| 13 | **Yes** | `currency_code` is set only in `create_account`. `update_account` doesn't touch it. A document posting to a non-base account is refused (`cash.py:76-82`); transfers convert explicitly with `fx_rate`. Online payments are posted anyway with a warning (`payments.py:350`). |
| 14 | **No** | `delete_account` deactivates regardless of balance (FIN-10). |
| 15 | **No** | Set on order payments, prepay, fees, online payments and payroll payouts. **Not set** on invoice payments (`finance.py:778`), POS sales (`sales.py:626,737`), refunds (`returns.py:283`, `sales.py:899`), transfers, client advances and supplier payments. It is optional on manual entries (`TxnIn.cash_flow_item_id: Optional`). |
| 16 | **Yes**, partly | Past `actual_date` needs `can_retroactive_cash` and sets `is_retroactive` (`cash.py:299-304`; invoice pay `finance.py:735-745`). "Any period" view is gated (`cash_flow_floor`). Future dates are unchecked on manual entries (FIN-11). |
| 17 | **Partly** | Storage is exact. 41 columns are Decimal, and 95 money columns surface as float (R4 PARTIAL, FIN-14). |
| 18 | **Yes** for the happy paths (code-read plus existing tests) | Order payment: Payment + Transaction(`ref_type=order`) + ClientLedgerEntry(`income`) (`cash.py:599-655`). Refund to cash: Refund + refund Payment + Transaction(`refund`) + ledger(`refund`) (`returns.py:265-292`). Refund to balance = `overpay=keep_balance` advance. **But:** the cash leg can be separated (FIN-02), cash without an account books no Transaction (FIN-04), and the sweep has no cross-ledger check. |
| 21 | **Yes / yes** | A refund on an open order is allowed: a deposit refund leaves the total alone (`refundable_without_discount`). Over-refund is refused: `amount > net_paid` gives 400 (`returns.py:205`). Duplicates under the cap: FIN-01. |
| 22 | **Yes** for jobs | `_create_return` (`returns.py:58-122`) writes only stock lines, and `/returns/refunds` writes only money. The POS return (`sales.py:791`) intentionally writes both (ClientReturn + Refund) in one action. |
| 23 | **Yes** | Clawback is keyed `refund:{refund_id}:{accrual_key}`, and restore is keyed `refund-void:{refund_id}:…` (`services/payroll.py:488-512`). It is once per refund id, but a duplicated refund (FIN-01) has a new id and claws back again. |
| 24 | **Mostly** | A sent/paid invoice is locked (`is_locked`, `assert_line_editable`). Edits after issue need `can_edit_issued_invoices`. Issued credit notes are immutable (`credit_notes.py:190`). **Anti-backdating isn't enforced** on manual invoices (FIN-06). |
| 26 | **Yes**, with exceptions | `next_number` uses an atomic UPDATE plus an insert-ignore for the first sequence. Unique `(org_id, invoice_number)`, `(org_id, number)` for credit notes, and orders/POs. Sales are not unique (FIN-17). A per-location or per-entity scope renders through the same template. Two scopes with identical templates could collide on the org-wide unique and then rely on the `_number_taken` skip (invoice/po/credit_note/estimate only). |
| 30 | **Partly** | `roles.account_ids` exists (`models:310`), but it is enforced only on the account list (FIN-05). |

---

## Sequence diagrams

(a) Work order → invoice → partial payment → refund
```mermaid
sequenceDiagram
  participant UI
  participant Cash as routers/cash.py
  participant IM as services/invoice_money
  participant Ret as routers/returns.py
  participant DB
  UI->>Cash: POST /cash/orders/{id}/pay {amount:60, account}
  Cash->>DB: idem_begin (Idempotency-Key)
  Cash->>DB: SELECT order FOR NO KEY UPDATE
  Cash->>Cash: balance_due(order) = total − written_off − net_paid − credited
  Cash->>DB: post_transaction(invoice_payment,+60): accounts.balance += 60 (atomic), balance_after
  Cash->>IM: apply_order_payment(order, 60)
  IM->>DB: no invoice → new_invoice() (next_number, InvoiceOrder, apply_order_money)
  IM->>DB: Payment(kind=payment, invoice_id, shift_id if cash till) ; invoices.amount_paid += 60 ; settle_status=partial
  Cash->>DB: client_ledger(income −60) ; clients.balance −= 60
  Cash->>DB: COMMIT ; idem finish
  UI->>Ret: POST /returns/refunds {amount:20, order_id, account}  (no idempotency: FIN-01)
  Ret->>DB: SELECT order FOR NO KEY UPDATE + lock_order
  Ret->>Ret: cap: 20 ≤ net_paid(60)
  Ret->>DB: Refund row
  Ret->>IM: refund_payments → Payment(kind=refund) off held money, then newest invoice; amount_paid −= 20
  Ret->>DB: post_transaction(refund, −20) ; client_ledger(refund +20)
  Ret->>DB: take_off_job: negative 'refund' line → _recalc_order → sync_order_invoices
  Ret->>DB: CommissionEngine.clawback_for_refund (key refund:{id}:…) ; COMMIT
```

(b) POS sale with coupon + membership → cash box
```mermaid
sequenceDiagram
  participant POS
  participant S as routers/sales.py
  participant DB
  POS->>S: POST /sales {lines, coupon_code, gift_card_code, account_id, expected_due}
  S->>DB: idem_begin
  S->>S: _price_sale (quote): line disc → sale disc → coupon % of remainder → loyalty → tax → gift card tender
  S->>S: refuse if due ≠ expected_due
  S->>DB: Sale row (next_number "sale")
  loop each product line
    S->>DB: reservations.guard_and_draw ; stock_rows.draw (guarded) ; consume_layers (FIFO COGS)
  end
  S->>DB: _price_sale(sale): UPDATE coupons SET uses+1 WHERE uses<max_uses (atomic)
  S->>DB: loyalty: lock client row, LoyaltyLedger −points
  S->>DB: UPDATE gift_cards SET balance−x WHERE balance≥x ; GiftCardLedger(sale)
  Note over S: Memberships do not apply to counter sales (only order lines; services/memberships.apply_to_order)
  S->>DB: post_transaction(income, due, ref_type=sale) → accounts.balance += due; shift_id = open drawer (cash till)
  S->>DB: COMMIT ; idem finish
```

(c) Shift open → movements → close/reconcile
```mermaid
sequenceDiagram
  participant Staff
  participant F as routers/finance.py
  participant DB
  Staff->>F: POST /finance/shifts/open {location, opening_float|denoms}
  F->>DB: SELECT location FOR UPDATE ; refuse if an open shift exists
  F->>DB: INSERT cash_shifts(opening_float)  (negative allowed: FIN-11)
  Staff->>F: cash job payments → Payment.shift_id ; POS cash → Transaction(income).shift_id
  Staff->>F: POST /shifts/{id}/movement {in|out, amount, note}  (out needs finance+expense rights)
  F->>DB: INSERT cash_drawer_movements  (no shift lock: FIN-12)
  Staff->>F: POST /shifts/{id}/close {closing_float|denoms}
  F->>DB: _expected_cash = opening + cash Payments − cash refunds − POS refund txns + pay-ins − pay-outs + manual txns (not deleted)
  F->>DB: UPDATE shift SET expected_cash, closing_float, closed_at  (no claim: FIN-12)
  F-->>Staff: blind count unless can_view_finance → expected + discrepancy + checklist
  Note over F,DB: /finance/reconciliation lists unbalanced closed drawers, overpaid invoices, supplier drift, payout gap
```

---

## 1. Inspected
- `backend/services/`:
  - `money.py`, `order_money.py`, `invoice_money.py`, `invoicing.py` (balance/status);
  - `ledger.py` (header + API), `reconcile.py`, `tax.py`, `coupons.py`, `credit_notes.py` (issue/cancel), `finance_pnl.py`, `idempotency.py`, `numbering.py` (`next_number`);
  - `memberships.py` (coverage/usage), `payments.py` (`mark_succeeded`), `payroll.py` (clawback).
- `backend/routers/`:
  - `cash.py` (all of it except `list_transactions` and the summary internals);
  - `finance.py` (invoices, pay, shifts, reconciliation, payouts, P&L, export);
  - `engage.py` (all);
  - `sales.py` (pricing, create, pay, void, return);
  - `returns.py` (refunds, delete refund, job lines);
  - `credit_notes.py` (routes);
  - `clients.py` (balance move), `suppliers.py` (pay);
  - `orders.py` (`_recalc_order`, line patch/delete guards).
- `backend/models/__init__.py`: money tables, plus a metadata introspection of every Numeric/Float column.
- Docs: `SPEC_money_decimal.md`, `docs/FINANCE_STRATEGY.md`, `docs/audits/2026-10-30-estimate-to-invoice-flow.md`, prior audit H5/M3/M4/R4, `ARCHITECTURE.md`.

## 2. Not inspected
- `services/revenue.py`, `profit.py`, `payroll.py` (beyond the clawback), `payroll_views.py`, `commission_input.py`, `currency.py`.
- `routers/payroll.py` (beyond the payout leg), `payments.py` (gateway router), `terminals.py`/`services/terminal.py` (card readers), `billing.py` (platform SaaS billing), `settings_finance.py`, `currencies.py`, `pricing.py`.
- QBO/Xero sync, e-invoice issue, `memberships` router (sell, cancel, refund_estimate), `ledger.spend_credit`/`available_credit` internals, `rebuild_org_ledger`.
- Transfers and stock-doc parts of M4 (another scope).

## 3. Verified (by test/probe)
- **FIN-01:** a duplicate refund through both endpoints is booked twice with the same Idempotency-Key.
- **FIN-02:** deleting a client-advance leg leaves spendable credit, the reconcile sweep reports `[]`, and the supplier-payment leg delete reverses only the till.
- **FIN-03:** deleting the gift-card line doesn't restore the card.
- **FIN-04:** a cash invoice payment with no account books no Transaction, and a payment can be attached to a closed shift.
- **FIN-11:** a negative opening float and a negative expense are accepted.
- **Existing tests passed:** `test_audit_m3_m4_cash_stock_races.py`, `test_one_open_drawer.py`, `test_balance_pay_double_spend.py` (28 passed, 1 skipped).

## 4. Uncertain
- FIN-07 and FIN-08 race windows (Postgres-only behaviour).
- Whether any client intentionally records cash invoice payments without an account (FIN-04).
- Double tax when a manual `tax_pct` is set on an invoice that bills already-taxed jobs (`new_invoice` → `pct_amounts` on `source_subtotal`).
- Whether the frontend sends Idempotency-Keys on the refund dialogs (irrelevant to FIN-01, since the backend ignores them).

## 5. Needs staging to test properly
- Concurrent invoice-pay vs job-pay on Postgres (FIN-07).
- Concurrent membership coverage (FIN-08).
- Concurrent shift close and movement (FIN-12).
- Deadlock ordering of the Order → Invoice → Client locks under load.
- Real Stripe/Square webhook retries against `mark_succeeded` (the claim on `status='processing'`, and a stuck `processing` state after a crash).
