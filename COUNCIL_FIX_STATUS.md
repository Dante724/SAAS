# Council fix status

This file tracks the hardening council's fix PRs, all against `roadmap/orderry-parity`. It is maintained by the cloud session that took over from the council's hand-over (`HANDOVER.md` on `claude/hardening-council-b6179a`). Nothing here is merged by this session; the PM session reviews and merges.

- **RED:** the test that failed before the fix, run on Postgres 16 unless it says otherwise.
- **CI:** the result on the PR's current head. "pending" means the head was pushed recently and CI hasn't been checked since.
- **Ready** means: review fixes done, RED → GREEN shown, related suites pass locally on Postgres 16, and CI is green with at least 17 checks. It does not mean "approved".

## Merge order (PM)
1. **Stock:** #104 → #101 → #108 → #110 → #143 (M7, stacked on #108 and #110).
2. **Cash:** #103 → #113 → #99 → #112 → #109 (after #111).
3. **Invoices, clients, time and comms:** #105, #106, #102 → #107 → #109 → #111 last. #111 now carries M22.

## Round 1: High findings and the PM's review fixes

| PR | What changed (this round) | Test that failed before the fix | CI | Ready for PM merge |
|---|---|---|---|---|
| #100 | H1 corrected transactions in totals | — | merged | merged |
| #101 | H2: `_do_posting` calls `lock_parts` up front; `recost_po` takes receive's lock order (council, 6f11cfbc) | Two opposite-order postings deadlocked; recost vs receive deadlocked (council, 5/5) | pending (re-check) | yes, if CI is green. Related suites re-run here: 69 passed |
| #102 | H3: Employee locked `FOR NO KEY UPDATE`; leaked-duplicate test strengthened; duplicate-open-shift query in DEPLOY.md | `test_leaked_duplicate_shift_is_not_paid_twice` on base: "the same 8 h was paid 16.00 h" | pending | yes, if CI is green. 310 payroll/clock tests passed; race 5/5 |
| #103 | H4: void legs `order_by(account_id, id)` (council, 624e6f60) | Two voids over the same tills deadlocked (council, 5/5) | pending (re-check) | yes, if CI is green. Related suites re-run here: 64 passed |
| #104 | H10: `/bins/batches` checks the warehouse's `post` permission; `apply_receipt_line` returns its own batch (council, 72d6f5f7) | 201 instead of 403; the earlier layer's id came back | pending (re-check) | yes, if CI is green. Related suites re-run here: 150 passed |
| #105 | H5 client balance moved atomically | — | — | PM approved |
| #106 | H8 invoice keeps the job's tax | — | — | PM approved |
| #107 | H12 SMS cap review items 1–4 | in progress | — | no |
| #108 | H9: reserve-stock locks the job `FOR NO KEY UPDATE`; products taken in sorted order | Double click held 2 for a job needing 1; [X,Y] vs [Y,X] → 500 deadlock | 18/18 green (600e375) | **yes** |
| #109 | H6: status resync keeps the credit already spent while a job is open; `available_credit` nets money on all open jobs; job-less invoice reversal test | Move-to-in-repair: balance −100 (credit handed back); paying J from balance succeeded with no credit; closed job paid twice from credit | pending | yes, if CI is green. 225 money tests passed. **Look at:** `test_invoice_money` setup gained a 100 advance (the old test relied on the double-spend) |
| #110 | M2: archive locks every stock row it touches, sorted, before the holds; the expiry sweep and manual release lock and re-check `released` | Archive racing an override draw → deadlock; release + archive took B's unit; archive of a job that drew Y and held X → 500 deadlock | pending | yes, if CI is green. 6 tests 5/5; 138 related tests passed |
| #111 | H7 + M22: Order `FOR NO KEY UPDATE` on every money path; invoice pre-lock dropped; the job's invoices locked after the job in prepay and pay-link settle; `apply_order_money` before the ledger in prepay | Pay vs prepay → 500 deadlock; prepay vs Record payment → 500 deadlock (Account ↔ Invoice) | pending | yes, if CI is green. 6 race tests 5/5; 98 money tests passed. **Look at:** a never-green new test expected balance 0; it is now −100, the result when the two payments run one after the other |
| #112 | H11: `reader_charge_in_flight` strict everywhere (archive, credit notes, inline finish-and-pay); new `reader_blocks_other_money` only for till pay and Record payment; no age-based unblock; the route commits the unconfirmed-cancel note | Old pending charge let cash through; archive, credit note and finish-and-pay all went through with a charge in flight | pending | yes, if CI is green. 112 related tests passed, 3/3 runs |
| #113 | M1: `refuse_archived` in `terminal.start_charge` | Reader charge on an archived job → 201 (a pending charge) | pending | yes, if CI is green. 79 passed |

## Round 2: Medium findings

| PR | Finding / what changed | Test that failed before the fix | CI | Ready for PM merge |
|---|---|---|---|---|
| #145 | M3 stocktake moves FIFO layers | COGS 50 vs 150; layers ≠ on-hand (4 failed) | pending | yes, if CI is green |
| #146 | M3 transfers move FIFO layers | 5 failed; race: layers held 10 with 4 on the shelf (3/3) | pending | yes, if CI is green. **Look at:** short-receipt test status flow fixed |
| #147 | M4 PO edit can't rewrite a received line | 3 edits returned 200; race left ordered 2 < received 4 | pending | yes, if CI is green |
| #124 | M5 write-off edit restores layers | council | pending (re-check) | per council: done |
| #134 | M6 serial in transit counts as live | council | pending (re-check) | per council: done |
| #143 | M7 expired holds released on every `free()` path; the sweep runs before the job lock | 409/short on three paths; stacked on #108: held 2 for 1 | pending | yes, after #108 and #110 |
| #130 | M8 serialized supplier return | council | pending (re-check) | per council: done |
| #125 | M9 POS cash refund on the open shift | council | pending (re-check) | per council: done |
| #129 | M10 one open drawer per location | council | pending (re-check) | per council: done |
| #132 | M11 sub-invoice line tax | council | pending (re-check) | per council: done |
| #131 | M12 reports on the shop's day | council | pending (re-check) | per council: done |
| #148 | M13 backend: add-line Idempotency-Key | Same-key retry added twice; race qty 2 (3/3) | pending | yes, if CI is green. Follow-up after #99: pass `body=` |
| #149 | M13 web/Go: 25 s timeout, "No connection", `postOnce` | Vitest: no key sent; no timeout | pending | yes, if CI is green |
| #138 | M14 Mac Roman CSV | council | pending (re-check) | per council: done |
| #150 | M15 refuse a byte-identical re-commit | council | pending | yes, if CI is green |
| #126 | M16 line-edit audit trail | council | pending (re-check) | per council: done |
| #136 | M17 invite rate limit | council | pending (re-check) | per council: done |
| #152 | M18 + M20 boot guards (proxy trust, Redis) | 11 of 20 new tests failed on base | pending | yes, if CI is green. **Deploy impact:** needs config |
| #151 | M19 Redis fallback logged and shown in `/readyz` | council | pending | yes, if CI is green |

## QA items (council)
#123 (Q8 flaky test), #127 (Q1 coverage greenlet), #133 (Q5 Postgres lock tests) and #137 (Q3 drift gate, migration `nn01`) are done per the council. Their CI is to be re-checked.
