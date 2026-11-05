# Hardening council: hand-over to the cloud session (5 Nov 2026)

At the owner's request, all local agents have stopped. Every branch below is pushed to `origin`, and each one already has the latest `roadmap/orderry-parity` (including #100 and #121) merged in. Nothing was force-pushed or merged.

Unfinished commits start with `WIP:`. Their commit messages carry the design notes.

**Key:**
- **done** = fix plus a test that fails before the fix and passes after (RED → GREEN) on Postgres 16;
- **partly done** = some of the code or tests are pushed, but something listed is still missing;
- **not started** = nothing pushed.

CI after the last merge push has **not** been watched on any of these: check each one for ≥17 passing checks before review. A known unrelated flake is `test_sense_sprint2` failing on "999" appearing in a random UUID (fixed by #123).

## Rules every fix follows
- **Money lock order: Order → Invoice → Client.**
  - Lock the Order `FOR NO KEY UPDATE` (`with_for_update(key_share=True)`). `invoices.order_id` is an FK, so a plain `FOR UPDATE` on Order deadlocks against invoice updates.
  - Prefer `add_to_column` to new locks.
- **Stock lock order: Order → StockLevel (sorted by part_id, location_id) → StockReservation / layers.**
- Each concurrent test is Postgres-only (an `_URL` skipif, never imported from conftest) and is run 5/5 times.
- The full report is `HARDENING_COUNCIL.md` on this branch.

## Round-1 council PRs, with the PM's review fixes

| PR | Branch | Review item | State |
|---|---|---|---|
| #100 | corrected-txn-totals | — | **merged** |
| #101 | avg-cost-lock | `_do_posting` uses `lock_parts` | **done** (6f11cfbc, 5/5) |
| #101 | | `recost_po` lock order (PO lines → parts → PO) | **done** (6f11cfbc, 5/5; the base deadlocked) |
| #102 | clock-in-once | Employee `key_share=True` | **not started** |
| #102 | | strengthen `test_leaked_duplicate_shift_is_not_paid_twice` so it fails on base | **not started** |
| #102 | | duplicate-open-shift check query in DEPLOY.md and the PR body | **not started** |
| #103 | void-legs-atomic | legs `order_by(account_id, id)` | **done** (624e6f60, 5/5). Related suites not re-run |
| #104 | bins-layer-routes | warehouse `guard_action("post")` | **done** (72d6f5f7, 5/5) |
| #104 | | `apply_receipt_line(out=)` returns its own batch id | **done** (72d6f5f7, 5/5). Related suites not re-run |
| #105 | client-balance-ledger | — | PM approved; waiting to merge |
| #106 | invoice-keeps-job-tax | — | PM approved; waiting to merge |
| #107 | sms-send-budget | 1. `from`-only creds bypass the cap | **not started**. Design: operator account ⇔ `(creds.account_sid or settings sid) == settings sid` |
| #107 | | 2. cap resets on the shop's day; durable counter | **not started**. Design: `OperatorTextUsage(org_id, day, sent)` with PK (org_id, day), migration `ot01` chained on the current head, upsert `… ON CONFLICT DO UPDATE … WHERE sent < :limit RETURNING` |
| #107 | | 3. full cap only when `sub_status == "active"` | **not started** (statuses are listed in `models/__init__.py:150-157`) |
| #107 | | 4. reserved transactional budget; outbox handling | **not started**. #95 isn't merged, so: add `OPERATOR_SMS_RESERVE_PCT=20` and set it to 0 in the existing test fixtures; the outbox reschedule is a follow-up |
| #108 | reserve-lock-positive | lock the Order in `reserve_stock_for_order` | **partly done** (b089cb81 WIP). RED confirmed; GREEN 5/5 not run |
| #108 | | iterate `sorted(need.items())` | **partly done** (b089cb81). RED confirmed (deadlock); GREEN not run |
| #109 | balance-pay-double-spend | 1. the status resync reverses the spend row (HIGH) | **not started**. Design: new entry type `credit_spent`; `order_charge_net` nets it; a non-closing job's target becomes `credit_spent_net`; archive keeps target 0 |
| #109 | | 2. `available_credit` nets only the target job | **not started**. Design: exclude the payment and refund rows of all the client's open, non-archived jobs |
| #109 | | 3. test reversing the standalone-invoice charge rows | **not started**. A job-less invoice has no refund route, so test credit-note issue then cancel |
| #110 | archive-releases-holds | lock order: holds unlocked → StockLevel sorted → holds `FOR UPDATE` + `populate_existing` | **partly done** (140e8974 WIP). RED confirmed (deadlock); GREEN not run |
| #110 | | `order_by(part_id, location_id)` on the holds queries | **partly done** (140e8974) |
| #110 | | `_release_expired` / `release_reservation` lock and re-check `released` | **partly done** (140e8974). RED confirmed for both; GREEN not run |
| #111 | invoice-amount-paid-lock | **DO NOT MERGE yet.** Drop the invoice pre-lock | **done** (7eaa7198 WIP) |
| #111 | | `FOR NO KEY UPDATE` at `_pay_order`, `lock_order`, terminal capture, returns refund, `refund_overpayment` | **done** (7eaa7198). The `routers/orders.py` Order locks were left as plain FOR UPDATE; list them in the PR body |
| #111 | | pay-vs-prepay concurrent test | **partly done**. RED confirmed. After the fix the file gives "1 failed, 3 passed" every run, and the failing test hasn't been identified: **find it first** |
| #111 | | `_prepay_order`: `apply_order_money` before `post_contact_ledger` | **partly done** (7eaa7198). RED confirmed; GREEN blocked by the failure above |
| #112 | reader-outage-cash | 1. strict in-flight check for archive and credit notes | **partly done** (7c0ed0f5 WIP). RED confirmed; GREEN not run |
| #112 | | 2. no unblock based only on age; unblock only after a cancel attempt | **partly done** (7c0ed0f5). RED confirmed; GREEN not run |
| #112 | | 3. note on the overlap with #99 | **partly done**: the text is in the commit message; post it as a PR comment |
| #112 | | 4. the route, not the service, commits the unconfirmed-cancel note | **partly done** (7c0ed0f5). GREEN not run |
| #113 | archived-job-no-money | `refuse_archived` in `terminal.start_charge` | **partly done** (7627e224 WIP). Code and test pushed; RED/GREEN not run |

## Round-2 Medium fixes (TDD)

| Finding | Branch | PR | State |
|---|---|---|---|
| M9 POS refund counted in the drawer | pos-refund-in-drawer | #125 | **done** |
| M10 one open drawer per location | one-open-drawer | #129 | **done** (Location row lock, no migration) |
| M11 sub-invoice line tax | sub-invoice-line-tax | #132 | **done**. Related suites not re-run after the merge |
| M22 lock order on the remaining money paths | lock-order-remaining-paths | none | **partly done** (03415680 WIP). Two prepay and pay-link races deadlock on the Client/till rows, separately from #111. The fix locks the job's invoices right after `lock_order`. GREEN not run; probably needs to be stacked on #111 |
| M5 write-off edit restores layers | writeoff-edit-restores-layers | #124 | **done** |
| M8 serialized supplier return | supplier-return-serial | #130 | **done** |
| M6 serial in transit counts as live | in-transit-serial-is-live | #134 | **done**. Widening the DB index is a follow-up |
| M7 expired holds released | expired-holds-release | none | **partly done**. Fix pushed; final GREEN not re-run; no PR opened (it pairs with #110) |
| M4 PO edit of a received line | po-edit-received-line | none | **partly done**. RED confirmed; GREEN not run; no PR |
| M3 stocktake cost layers | stocktake-layers | none | **partly done**. RED confirmed; GREEN not run; no PR |
| M3 transfer cost layers | transfer-layers | none | **partly done**. RED test pushed, no fix; the design is in the commit message (layer movements on ready/completed/cancel) |
| M16 audit trail for line edits | line-edit-audit | #126 | **done** |
| M12 reports follow the shop's day | reports-shop-day | #131 | **done**. Not re-run after the merge |
| M14 Mac Roman CSV | import-encoding | #138 | **done**. Showing the note in the UI is a follow-up |
| M15 import re-run guard | import-rerun-guard | none | **done, PR not opened** (the PR body is in the commit message) |
| M13 add-line idempotency (backend) | add-line-idempotency | none | **done, PR not opened** |
| M13 web keys and timeout | add-line-idempotency-web | none | **partly done** (3d9c27c9 WIP). The `api/client.ts` timeout and error mapping, vitest and tsc are all still to do |
| Q8 flaky sense brief test | flaky-sense-brief | #123 | **done** |
| Q1 coverage traces greenlets | coverage-greenlet | #127 | **done**. Raise `fail_under` from the CI number afterwards |
| Q5 Postgres lock tests | pg-lock-tests | #133 | **done** (no bugs found) |
| Q3 drift gate on Postgres; `org_id` NOT NULL on 16 tables | drift-gate-on-postgres | #137 | **done**. Migration `nn01` revises `trg01`; re-chain it if #95's `nob01` merges first |
| M17 invite rate limit | invite-rate-limit | #136 | **done** |
| M19 Redis fallback visible | redis-fallback-visible | none | **partly done**. Code and tests are green before the merge; re-run after the merge and open the PR |

## Not in the council's scope (owned by #99)
H13, the idempotency claim: add a lease, and never `release` after a commit. Details are in `HARDENING_COUNCIL.md`.

## Merge order the PM asked for
1. **Stock:** #104 → #101 → #108 → #110.
2. **Cash:** #103 → #113 → #99 → #112 → #109 (after #111).
3. **Invoices, clients, time and comms:** #105, #106 and #102 (approved) → #107 → #109 → #111 last.
