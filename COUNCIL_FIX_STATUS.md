# Council fix status

This file tracks the hardening council's fix PRs, all against `roadmap/orderry-parity`. It is maintained by the cloud session that took over from the council's hand-over (`HANDOVER.md` on `claude/hardening-council-b6179a`). Nothing here is merged by this session; the PM session reviews and merges.

- **RED:** the test that failed before the fix, run on Postgres 16 unless it says otherwise.
- **CI:** the result on the PR's current head, checked at 21:00 and 22:10 UTC on 5 Nov and at 11:20 UTC on 6 Nov. At 11:20 every open PR into `roadmap/orderry-parity` had no failing check.
- **Ready** means: review fixes done, RED → GREEN shown, related suites pass locally on Postgres 16, and CI is green with at least 17 checks. It does not mean "approved".

## Merge order (PM)
1. **Stock:** #104 → #101 → #108 → #110 → #143 (M7, stacked on #108 and #110).
2. **Cash:** #103 → #113 → #99 → #112 → #109 (after #111).
3. **Invoices, clients, time and comms:** #105, #106, #102 → #107 → #109 → #111 last. #111 now carries M22.

## Round 1: High findings and the PM's review fixes

| PR | What changed (this round) | Test that failed before the fix | CI | Ready for PM merge |
|---|---|---|---|---|
| #100 | H1 corrected transactions in totals | — | merged | merged |
| #101 | H2: `_do_posting` calls `lock_parts` up front; `recost_po` takes receive's lock order (council, 6f11cfbc) | Two opposite-order postings deadlocked; recost vs receive deadlocked (council, 5/5) | 18/18 green | **yes**. Related suites re-run here: 69 passed |
| #102 | H3: Employee locked `FOR NO KEY UPDATE`; leaked-duplicate test strengthened; duplicate-open-shift query in DEPLOY.md | `test_leaked_duplicate_shift_is_not_paid_twice` on base: "the same 8 h was paid 16.00 h" | 18/18 green | **yes**. 310 payroll/clock tests passed; race 5/5 |
| #103 | H4: void legs `order_by(account_id, id)` (council, 624e6f60) | Two voids over the same tills deadlocked (council, 5/5) | 18/18 green | **yes**. Related suites re-run here: 64 passed |
| #104 | H10: `/bins/batches` checks the warehouse's `post` permission; `apply_receipt_line` returns its own batch (council, 72d6f5f7) | 201 instead of 403; the earlier layer's id came back | 18/18 green | **yes**. Related suites re-run here: 150 passed |
| #105 | H5 client balance moved atomically | — | — | PM approved |
| #106 | H8 invoice keeps the job's tax | — | — | PM approved |
| #107 | H12: `from`-only creds are capped; durable per-shop-day counter (`OperatorTextUsage`, migration `ot01`, atomic upsert); full cap only for `active` shops; `OPERATOR_SMS_RESERVE_PCT`=20 kept for transactional sends (campaigns, chats, birthday and win-back count as marketing) | from-only creds sent a 4th text (201, not 429); cap lost when memory was cleared; no reset at the shop's midnight; non-active shops got the full cap; marketing used up the reserve. Postgres race: exactly 1 of 12 parallel sends at cap−1, 10/10 runs | 18/18 green | **yes**. 686 comms tests passed on Postgres. Outbox reschedule waits for #95. **Migration:** `ot01` revises `trg01` |
| #108 | H9: reserve-stock locks the job `FOR NO KEY UPDATE`; products taken in sorted order | Double click held 2 for a job needing 1; [X,Y] vs [Y,X] → 500 deadlock | 18/18 green | **yes** |
| #109 | H6: status resync keeps the credit already spent while a job is open; `available_credit` nets money on all open jobs; job-less invoice reversal test | Move-to-in-repair: balance −100 (credit handed back); paying J from balance succeeded with no credit; closed job paid twice from credit | 18/18 green | **yes**. 225 money tests passed. **Look at:** `test_invoice_money` setup gained a 100 advance (the old test relied on the double-spend) |
| #110 | M2: archive locks every stock row it touches, sorted, before the holds; the expiry sweep and manual release lock and re-check `released` | Archive racing an override draw → deadlock; release + archive took B's unit; archive of a job that drew Y and held X → 500 deadlock | 18/18 green | **yes**. 6 tests 5/5; 138 related tests passed |
| #111 | H7 + M22: Order `FOR NO KEY UPDATE` on every money path; invoice pre-lock dropped; the job's invoices locked after the job in prepay and pay-link settle; `apply_order_money` before the ledger in prepay | Pay vs prepay → 500 deadlock; prepay vs Record payment → 500 deadlock (Account ↔ Invoice) | 18/18 green | **yes**. 6 race tests 5/5; 98 money tests passed. **Look at:** a never-green new test expected balance 0; it is now −100, the result when the two payments run one after the other |
| #112 | H11: `reader_charge_in_flight` strict everywhere (archive, credit notes, inline finish-and-pay); new `reader_blocks_other_money` only for till pay and Record payment; no age-based unblock; the route commits the unconfirmed-cancel note | Old pending charge let cash through; archive, credit note and finish-and-pay all went through with a charge in flight | 18/18 green | **yes**. 112 related tests passed, 3/3 runs |
| #113 | M1: `refuse_archived` in `terminal.start_charge` | Reader charge on an archived job → 201 (a pending charge) | 18/18 green | **yes**. 79 passed |

## Round 2: Medium findings

| PR | Finding / what changed | Test that failed before the fix | CI | Ready for PM merge |
|---|---|---|---|---|
| #145 | M3 stocktake moves FIFO layers | COGS 50 vs 150; layers ≠ on-hand (4 failed) | 18/18 green | **yes** |
| #146 | M3 transfers move FIFO layers | 5 failed; race: layers held 10 with 4 on the shelf (3/3) | 18/18 green | **yes**. **Look at:** short-receipt test status flow fixed |
| #147 | M4 PO edit can't rewrite a received line | 3 edits returned 200; race left ordered 2 < received 4 | 18/18 green | **yes** |
| #124 | M5 write-off edit restores layers | council | 18/18 green | **yes** |
| #134 | M6 serial in transit counts as live | council | 18/18 green | **yes** |
| #143 | M7 expired holds released on every `free()` path; the sweep runs before the job lock | 409/short on three paths; stacked on #108: held 2 for 1 | 18/18 green (the e2e job re-ran after an npm registry failure) | **yes**, after #108 and #110 |
| #130 | M8 serialized supplier return | council | 18/18 green | **yes** |
| #125 | M9 POS cash refund on the open shift | council | 18/18 green | **yes** |
| #129 | M10 one open drawer per location | council | 18/18 green | **yes** |
| #132 | M11 sub-invoice line tax | council | 18/18 green | **yes** |
| #131 | M12 reports on the shop's day | council | 18/18 green | **yes** |
| #148 | M13 backend: add-line Idempotency-Key | Same-key retry added twice; race qty 2 (3/3) | 18/18 green | **yes**. Follow-up after #99: pass `body=` |
| #149 | M13 web/Go: 25 s timeout, "No connection", `postOnce` | Vitest: no key sent; no timeout | 18/18 green | **yes** |
| #138 | M14 Mac Roman CSV | council | 18/18 green | **yes** |
| #150 | M15 refuse a byte-identical re-commit | council | 18/18 green | **yes** |
| #126 | M16 line-edit audit trail | council | 18/18 green | **yes** |
| #136 | M17 invite rate limit | council | 18/18 green | **yes** |
| #152 | M18 + M20 boot guards (proxy trust, Redis) | 11 of 20 new tests failed on base | 18/18 green | **yes**. **Deploy impact:** needs config |
| #151 | M19 Redis fallback logged and shown in `/readyz` | council | 18/18 green | **yes** |

## QA items (council)
#123 (Q8 flaky test), #127 (Q1 coverage greenlet), #133 (Q5 Postgres lock tests) and #137 (Q3 drift gate, migration `nn01`) are done per the council. #123, #127 and #133 are 18/18 green. **#137** was red: `backend-postgres (shard 1)` had 3 setup errors in `test_144eo_migrated_schema.py`, "password authentication failed for user repairos". The fixture built the migrated database's URL with `str(url)`, which masks the password as `***`. Reproduced locally with a password-protected role and fixed with `render_as_string(hide_password=False)` (3106d6e4). It now passes 3/3 runs. On the new head (cb7ea6c4) every Postgres shard is green; 17/18 checks pass. The one red check, `ruff correctness rules (whole backend)`, comes from the **base**: `scripts/design-capture/analyze.py` has an unused `import re` since 8ba3ca32. **#157** fixes it in one line; merge it first, then re-run that job here.

## Alembic heads
Four open PRs each add a migration that revises `trg01`: #95 `nob01`, #99 `idg01`, #107 `ot01` and #137 `nn01`. Only the first to merge keeps a single head. Each later one must be re-chained onto the new head, and `alembic heads` checked again, before it merges.

## Base branch
#157 fixes a red `ruff correctness rules (whole backend)` check on `roadmap/orderry-parity`, from an unused import added in 8ba3ca32. Any PR that merges the base after that commit shows the same red check until #157 lands.

## Round 3: infrastructure (after the council)

| PR | What changed | Test that failed before the fix | CI | Ready for PM merge |
|---|---|---|---|---|
| #157 | **Base is red:** an unused `import re` (whole-backend ruff, since 8ba3ca32) and E741 in `test_design_followups.py` (lint, since #156) | Both ruff commands fail on the base | **18/18 green** (0586e54f, 11:20 UTC) | yes, **merge first** |
| #162 | Nightly integrity sweep: till, client, invoice and stock totals vs their ledgers; ERROR log plus the `repairos_integrity_mismatches` metric; read-only. Automated restore drill `scripts/restore_drill.py`, run in CI; runbook `docs/runbooks/restore-drill-and-integrity.md` | Metric and schedule tests failed before the wiring. Probe across 104 money/stock files: 0 false alarms on money; 6 on stock, all from tests that seed stock directly | **18/18 green** (9e103c6c, 11:20 UTC) | **yes**. **Owner:** run the drill once on a real production dump |
| #161 | M21: the SLA sweep runs in SQL and skips shops with no time limits | 60 orders read for a shop with no limits (now 0); 83 vs 9 statements as breaches grow (now equal) | **18/18 green** (fa3c8505, 11:20 UTC) | **yes** |
| #164 | `SLOW_QUERY_MS` slow-query WARNING (no parameters, with request id) and `repairos_slow_queries_total`; the dashboard cache in Redis with an in-process fallback; `pg_stat_statements` in DEPLOY.md | No slow-query log; nothing written to Redis; a Redis failure not handled | **18/18 green** (45d16fe8, 11:20 UTC) | **yes**. Expect a small `render_metrics` conflict with #162 |
| #165 | Q2 + Q6: memberships (sell, sweep, usage, prepaid, refunds, snooze, profitability), supplier warranty, the pilot report and estimate expiry use the shop's day. CI runs the clock-sensitive tests at TZ=Pacific/Kiritimati and Pacific/Pago_Pago | 11 new tests with the clock frozen and two shops two days apart all failed (e.g. `starts_on '2026-11-06' == '2026-11-07'`; profitability `(0.0, 0.0) == (449, 80)`); at TZ +23 the council's `2.0 == 0` reproduced | **18/18 green** (dcbe4418, 11:20 UTC) | **yes**. 308 related tests pass on Postgres and SQLite at UTC, +14, −11 and +23. After it merges, the "production host on UTC" beta condition can go |

## Known flake
SQLite `test (shard 1)` once failed with 90 "database is locked" setup errors in `test_inventory_bins_passport_stocktake`, `test_ws_a_stock_intake` and `test_wsc_inquiries` (#162, run 42187908770). It passed on the next CI run of the same code and twice locally. Suspected but not confirmed cause: on SQLite the app engine (`AsyncSessionLocal`, used by background work such as webhook retries) shares the test file but is not disposed between tests. If it recurs, capture the full log; the first locked statement and the test before it on the same worker identify the leak.

## Re-check on 10 Nov (base 4160a5a6, after #99, #140, #174 and the UI merges)
Every open council/hardening PR was dry-run merged against the current base, then fixed where needed.

- **Two alembic heads:** #99 merged `idg01` → `idg02`, so with the base merged in, #107 (`ot01`) and #137 (`nn01`) each had two heads. Both are re-chained onto `idg02` and pushed. Checked on Postgres: `alembic upgrade head` from an empty database runs, and the drift test passes on Postgres and SQLite. **#95 (`nob01`, not a council PR) needs the same re-chain.** Whichever of #107 and #137 merges second must re-chain onto the other.
- **#149 merge conflict:** import lines in `Sense.tsx` and `CreateOrderDrawer.tsx` (TeklySelect). Resolved; `tsc`, both content lints and `test:coverage` (879 tests) pass.
- **#148 follow-up to #99:** `add_line` now uses `run_once` with the job id and line bound to the key. A reused key with a different line or job is a 422 (RED: 201 before); a key is never released after commit. 136 related tests pass on Postgres.
- **#157 is now redundant:** the base fixed both lint errors with identical changes. Close it as superseded (PM's call).
- **All other branches** (37) had the current base merged in and pushed, so CI re-checks them against today's code. Every merge was clean, with one alembic head and both lint commands passing.
- **Stale local worktrees** sharing those branches were removed. The "files changed on disk" notices came from them; no work was lost.

## Merged on 11 Nov (the important set, on the owner's instruction)
Merged into `roadmap/orderry-parity` in the PM's order, each only when all 18 CI checks were green on the exact commit tested:
#104, #101, #108, #110, #105, #106, #102, #103, #113, #107, #111 (also carries M22), #109, #127.
Before merging, the 13 were merged together on today's base and the full suite run: **Postgres 3818 passed, SQLite 3791 passed, 0 failed.**

- **#112 stays closed:** the owner closed it on 6 Nov because card readers are turned off (`CARD_READERS_ENABLED=false`, #171).
- **Two alembic heads after the merges:** #183 (`sds01`), merged earlier the same day, and #107 (`ot01`) both revise `idg02`. **#185** (merge revision `mh01` plus a single-head guard test) went green and is **merged**: the base is back to one head (`mh01`).
- **Still open, for later:** the Medium fixes (#124, #125, #126, #129, #130, #131, #132, #134, #136, #138, #143, #145–#152), the QA PRs (#123, #133, #137) and the infra PRs (#161, #162, #164, #165). #137 must re-chain `nn01` onto the current head before it merges. #157 is redundant and can be closed.

## Second merge pass on 11 Nov (the rest)
Before merging, all 26 remaining PRs were merged together on that day's base (c38bcd55) and tested: **Postgres 3973 passed, SQLite 3936 passed, 0 failed; `tsc` clean; Vitest 918/918.** The 47 skipped tests (5 Postgres, 42 SQLite) were checked one by one. 37 are Postgres-only race tests that ran and passed in the Postgres run. Deploy readiness needs a React build: built and run, 6/6 pass. The Stripe, Square and Twilio sandbox tests need real test keys. One card-reader check stays off with card readers.

Merged into `roadmap/orderry-parity`, each on the exact commit tested with all 18 checks green (merge commit in brackets):
#143 (0ad394b5), #124 (1aa4a989), #130 (8698d85f), #134 (14f6706e), #145 (0f1a423c), #146 (1f978d7f), #147 (65a76fbc), #125 (6f4eec57), #129 (46ff32c7), #132 (1bd5a384), #131 (8b673de9), #148 (fc0e62f1), #149 (aa6c627a), #138 (d5e0f5e6), #150 (fcabfb61), #126 (18d6c0f8), #136 (c4b18076), #151 (3fbd25cc), #152 (d5d5d3de), #123 (8aea0ca6), #133 (3d3c7fec), #137 (b2e9220f), #161 (2cdcbbf4), #162 (3f48d438), #164 (e8eeadc2), #191 (e16a42ef).

- **#164:** GitHub reported merge conflicts after the earlier merges, although `git merge-tree` showed a clean merge. The base was merged into its branch (ce1fd143, normal push). Lint and the metrics/cache/integrity tests passed on Postgres, CI went 18/18 green, and it merged.
- **#191 (new, flaky test):** #165's `test (shard 4)` failed in `test_portal_shows_only_portal_fields`. The test asserted the passcode `"4321"` never appears in the portal response, and a timestamp (`09:11:14.432168`) contained those digits. The test now uses the passcode `pw-Kx#4321`, and the checks are unchanged. Reproduced: `'4321' in str({'at': '2026-11-11T09:11:14.432168'})` is `True`.
- **#165:** the base, now with #191, was merged into its branch (3eda197b). Lint is clean and 98 related tests pass on Postgres and SQLite. CI went 18/18 green, including the shard that had failed. **Merged (04b89447).** The "production host on UTC" beta condition can now be dropped.
- **All council and hardening PRs are merged.** The base is at 04b89447 with one alembic head (`nn01`).
- **Alembic:** the base has one head (`nn01`), and `test_alembic_single_head` passes at e16a42ef.
- **#95** (not a council PR) still needs `nob01` re-chained onto `nn01`.
