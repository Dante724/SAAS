# Hardening council — Tekly closed beta (Nov 2026)

Branch under review: `roadmap/orderry-parity` @ `90cf6025` (re-checked against `7ad28e5a` after #91, #92, #97 and #98 merged).
Seven roles each worked alone, then cross-examined each other's claims. The chair re-ran every repro on Postgres 16 before anything went into this report.

The roles were:
- principal engineer;
- forward-deployed engineer (FDE);
- software architect;
- backend money;
- backend stock;
- backend platform;
- QA lead.

## Verdict

**NO-GO today. GO for a closed beta of about 20 shops once the beta-blocker PRs below are merged.**

The things that were already hardened hold up under the council's probes:
- the gate's double-submit races;
- tenant isolation;
- auth, sessions and MFA;
- webhooks, both inbound and outbound;
- config that fails closed;
- the webhook outbox.

What isn't safe yet is a set of **silent money, stock and payroll drifts**: totals that disagree with their own ledger, with nothing in the UI that can repair them. They show up under ordinary two-cashier concurrency or after a routine correction. One gap also has a direct cost to the operator: SMS toll fraud through the shared Twilio account. Each of these has a small fix with a failing test. All but H13 have a PR, and H13 belongs in #99.

The beta also needs these operating conditions:
- **one web worker per instance** until `REDIS_URL` is set (M19, M20);
- `FORWARDED_ALLOW_IPS` set to the load balancer's address in the beta deployment (M18);
- **production host TZ is UTC** until memberships use the shop's clock (Q2);
- `regress.py` and `isolation.py` re-run green on the beta cut, after the council PRs merge.

## How to read the evidence

- **Repro:** a pytest that fails on the beta branch under Postgres 16, unless the entry says "live", meaning a threaded probe against a uvicorn server.
- **Chair-verified:** the chair re-ran the repro and saw the same failure.
- **Fix PRs:** each was shown failing before the fix and passing after, then the related suites were run on Postgres. All target `roadmap/orderry-parity` and none is merged.

---

## Findings

### Critical

None. No finding lets one shop read or alter another shop's data, and none loses committed money outright.

### High

| # | Finding | Evidence | Failure scenario | Smallest robust fix | Owner | Status |
|---|---|---|---|---|---|---|
| H1 | **Corrected cashbox transactions vanish from every total** | `cash.py:364` flags the original `corrected`. Ten aggregates filter `status == "active"`: `finance.py:853,934,937,1227`, `finance_pnl.py:59`, `revenue.py:347,410,446`, `orders.py:3570`, `cash.py:653`. Repro `test_corrected_txn_totals.py` | Correct a 1000 till income by +5 and the drawer expects **5**, reporting 1000 over. Correct a 300 expense by −10 and P&L opex shows **10** | Exclude only `deleted` rows | finance/cash | **PR #100** |
| H2 | **`Part.avg_cost` lost update on concurrent receipts** | `stock_posting.py:49-56,143`: an unlocked read-modify-write. Two members reproduced it independently | Receive 10 @10, then 10 @20 and 10 @40 at once: average **15** instead of 23.33. Valuation and COGS drift permanently | Row-lock the part. Lock all of a PO's parts in id order first; without that, two POs **deadlock**, which the architect caught and the chair reproduced | inventory | **PR #101** |
| H3 | **Double-tap clock-in opens two shifts, and payroll pays the hours twice** | `timeclock.py:90-109`: check-then-insert with no lock. Repro `[201, 201]`, and one shift stays open after clock-out | A technician double-taps on flaky wifi and their hours keep growing | Lock the employee row. Clock-out closes every open shift and gives a leaked duplicate 0 h; the chair added that last part after finding the first fix still double-paid | timeclock/payroll | **PR #102** |
| H4 | **Till balance drifts when a void races a sale** | `cash.py:951` `void_document_legs` does a read-modify-write while `post_transaction` is atomic. Live: balance 90 vs ledger 120 | Two cashiers, one voiding and one selling: the cashbox silently loses sales | `add_to_column` | cash | **PR #103** |
| H5 | **`Client.balance` drifts from its ledger** | `clients.py:1107` does a read-modify-write and bypasses `post_contact_ledger`. 6 concurrent advances of 10 leave the balance at **−10** against a ledger of −60 | The Balance tab disagrees with its own statement, and available credit is computed on the wrong number | `add_to_column` / `post_contact_ledger` | clients/ledger | **PR #105** |
| H6 | **Client credit can be spent any number of times** | `cash.py:283-301` and `finance.py:702-708` post nothing that consumes credit until the job closes. `ledger.available_credit` nets only this job | An advance of 100 marks a 100 job paid, then a second 100 job paid. Two invoices likewise | Consume the credit in the same transaction, through the atomic ledger. No new FOR UPDATE; the client balance moves in one guarded UPDATE | payments/ledger | **PR #109** |
| H7 | **`Invoice.amount_paid` lost update** | `invoice_money.py:197` `_credit` does a read-modify-write and locks only the Order. Live 3/3 runs: 100 recorded instead of 160 | Two jobs on one invoice paid at once: the invoice stays "partial" and dunning chases money already received | `add_to_column` on `amount_paid`, plus `_pay_order` locking the job's invoices **before** the job (Invoice → Order). Without that the atomic UPDATE **deadlocked**: 500, 3 runs out of 3 in the chair's run | invoicing | **PR #111** |
| H8 | **Invoices created from the Invoices screen carry tax 0 on taxed jobs** | `finance.py:476-480` passes `tax_pct=0`, so `invoice_money.py:158` takes the percent branch. The e-invoice exports the job as zero-rated | Every VAT/GST shop's API-created invoices and UBL files under-state tax | Pass `None` when no override was given | invoicing | **PR #106** |
| H9 | **Stock `reserve` is unlocked, and a negative qty releases other jobs' holds** | `inventory.py:1160-1186`, `orders.py:3418-3435`, `stock_rows.reserve:211`. With 1 on hand, two reserves both succeed (`[201, 201]`). `qty=-3` → 201 | Phantom holds block sales and transfers; one request wipes another job's earmark | Lock the rows before reading totals; `qty > 0` | inventory | **PR #108** |
| H10 | **`/api/bins/batches`, `/consume` and `/allocate-serial` move cost layers and serials without stock** | `bins.py:383-458`. `consume` is ungated. Batches qty 2 → on_hand 0, units 2, movements 0 | Any staff user can zero FIFO layers or mint in-stock IMEIs that don't exist | Remove the routes (no UI caller) or route them through `stock_posting` | inventory/bins | **PR #104** |
| H11 | **A pending card-reader charge plus a Stripe outage locks the job to card** | `terminal.py:581-608` `cancel()` re-raises; the clear route fails the same way; `order_money.py:295` → `cash.py` returns 409. Pending charges never expire | Shop wifi drops mid-charge: cancel 502, clear 502, cash **409**. The job can't be settled at all | Expire or locally cancel stale pending charges; a late Stripe success still posts through the webhook | payments/terminal | **PR #112** |
| H12 | **SMS toll fraud: a trial shop sends unlimited texts through the operator's Twilio** | `chats.py:144-200` falls back to `notifications._adapter_for` and then to `settings.TWILIO_*`. No per-org budget. Repro: 40 threads to 40 numbers, 40 × 201 | A throwaway sign-up pumps premium-rate SMS, then Twilio suspends the account and every shop's notifications stop | Per-org daily cap on sends that use the operator's credentials | comms/notifications | **PR #107** |
| H13 | **An Idempotency-Key claim has no lease, and `run_once` releases it after the money has committed** | `idempotency.py:72-86`: a claim with `response=NULL` blocks the key for 48 h. Repro `test_arch_idempotency_stuck_claim` → 409. On #99, `run_once` calls `release()` on **any** exception, even after `work()` committed (architect + PE) | A worker dies or a deploy restarts mid-payment: the phone's retry gets 409 for two days. Or a post-commit notify raises, the claim is deleted, and the retry **posts the payment twice** | Take over stale claims after about 60 s with a conditional UPDATE. Never release after a commit; finish with the partial result instead | services/idempotency | **Not fixed here.** It overlaps in-flight PR #99, so it is handed to that PR |

### Medium

| # | Finding | Evidence | Fix | Owner |
|---|---|---|---|---|
| M1 | Money can be taken on an archived job. The cross-check downgraded this from High: postings stay consistent, but it is a policy gap | `cash.py:260,356` have no `archived` check. Repro: archive, then pay 100 and prepay 50 → 200/201 | 409 "This job was deleted" on every money-in path | payments — **PR #113** |
| M2 | Archiving a job keeps its stock holds for up to 180 days (downgraded from High) | `orders.py:3478-3700` releases no `StockReservation`. Repro: reserve then delete → a later reserve gets 409 | Release holds on archive | orders — **PR #110** |
| M3 | Stocktake and transfers don't move FIFO layers, so COGS is wrong | `sales.py:1393-1494`; `transfers.py` never touches `StockItemBatch`. Count 0 then sell → COGS 50 instead of 150 | Consume or create layers on variance and transfer | stocktake/transfers |
| M4 | A PO edit after a partial receipt rewrites the received line (part, qty, price) | `inventory.py:1596-1603`. PATCH → 200 | 409 when a received line changes | purchasing |
| M5 | Editing a write-off line down restocks without restoring layers | `stock_docs.py:514-519` | Call `_restore_batches` | stock docs |
| M6 | A serial `in_transit` can be received again, which then wedges the transfer with 409 | `serial_intake.py:27`; index `models:2306` | Treat `in_transit` as live | serials |
| M7 | Expired holds keep blocking until someone opens the reservations list | `inventory.py:1142` | Release expired holds in the sweep or in the `free()` paths | reservations |
| M8 | A supplier return of a serialized product leaves the unit `in_stock` | `stock_docs.py:260-278` | Same rule as write-off | stock docs |
| M9 | A POS cash return isn't subtracted from the drawer's expected cash, so every close reads short | `sales.py:~895` refund leg has no `shift_id`. Expected 110 vs counted 100 | Link the refund leg to the open shift | POS/drawer |
| M10 | Several drawers can open at one location; `/shifts/current` then returns 500 | `finance.py:1007-1016` has no lock and no unique index. 3 × 200 | Partial unique index plus 409 | drawer |
| M11 | Sub-invoices and "bill the rest" invoices carry tax 0 (same root cause as H8) | `invoice_money.py:160` | Per-line tax when lines are given | invoicing |
| M12 | Table reports, xlsx/csv exports and total profit treat dates as UTC days, not the shop's day | `reports.py:150-162`. Kiritimati shop: today's report rows=[] | `day_start_utc` / `day_end_utc` with the shop's timezone | reports |
| M13 | A retried "add line" doubles qty and stock. Web job-create sends no key; `fetch` has no timeout and shows the raw "Failed to fetch". (The PE's cross-check claim that add-line has `idem_begin` was **refuted** by the chair: only create and finish do) | `orders.py:4590-4616`, `CreateOrderDrawer.tsx:158`, `api/client.ts:44-71` | `idem_begin` on add_line; `postOnce` in the web client; 25 s `AbortSignal` | orders + frontend |
| M14 | A Mac Roman CSV (Excel for Mac's default) is silently corrupted (é → Ž) or refused | `import_cells.py:62-68` | Detect the encoding and warn in the preview | imports |
| M15 | Re-running an import of name-only clients doubles them, and the first batch can no longer be undone | `importer.py:642-712,1101-1107` | Match on name as a last resort (with a warning); refuse a byte-identical re-commit | imports |
| M16 | No audit trail for line price, qty or discount edits, or for line deletes | `orders.py` `update_line` / `add_line` | `audit.record(... diff_fields)` | orders |
| M17 | Invite emails form an unlimited spam relay with shop-controlled subject text | `staff.py:502-560`; 30 accepted | Per-org rate limit; fixed subject | staff |
| M18 | Behind a load balancer with the Dockerfile default `FORWARDED_ALLOW_IPS=127.0.0.1`, every anonymous user shares one rate bucket (10 sign-ups/h platform-wide) | `Dockerfile:29`, `config.py:293` only warns. Repro: 11 clients → `[201×10, 429]` | Deploy checklist now; when deployed, refuse to boot unless proxy trust is configured | config/deploy |
| M19 | If Redis is configured but unreachable, rate limits silently fall back to per-process counters | `ratelimit.py:84-124`; no log line, no readiness signal | WARN log; report the rate-limit store in `/readyz` | observability |
| M20 | The "REDIS_URL required when WEB_CONCURRENCY > 1" guard is bypassed by `--workers N` and by replicas | `config.py:380` reads only the env var | Require Redis whenever deployed, or count workers in the DB | config |
| M21 | `status-sla-sweep` loads every open order of every shop every 10 minutes | `sla.py:43-70`: 1.9 s / 52.7k rows at 103 shops, about 19 s at 1,000 | Skip shops with no SLA; do the breach test in SQL | services/sla |
| M22 | Invoice → Order vs Order → Invoice lock-order cycle | `finance.py:669,804` vs `invoice_money.py:248` (architect) | Lock invoice(s) → order → client everywhere. #111 does this for `cash._pay_order`; still to do: `_prepay_order`, the terminal/webhook paths and `credit_notes.py:58` | invoicing |

### Low

- **Per-line rounding doesn't foot.** Σq2(net) + Σq2(tax) misses the total by 1–2 cents on 7 of 12 random jobs (`orders.py:244-259`, `tax.py:119-131`). Fix: SPEC_money_decimal phase 2 penny allocation.
- **Voiding a sale or deleting a manual cash transaction after its shift closed isn't guarded** (`sales.py:383`, `cash.py:369`).
- **Unguarded read-modify-writes** in `clients.py:885,959` (merge), `importer.py:1152` (undo), `stock_docs.py:128` and `timeclock.py:1148`. These weren't caught racing; convert them to `add_to_column` in a mechanical pass.
- **The MFA failure lockout is checked after the code** (`auth.py:456-461`): a correct code still signs in after 7 failures.
- **`DELETE /api/conversions/{id}` has no auth dependency.** It is a stub that always returns 405.
- **Oversized strings return 500 on Postgres.** `RegisterRequest` has no `max_length` and `DataError` isn't mapped to 422.
- **Config accepts some nonsense when deployed:** `ALGORITHM=none`, a short `PLATFORM_SECRET_KEY` or `METRICS_TOKEN`, a weak admin password.
- **Per-worker 30 s dashboard cache** (`reports.py:21-38`). Staleness only.
- **Per-worker `/metrics`**, with no slow-query timing. `pg_stat_statements` isn't in DEPLOY.md.
- **A status change commits in four stages** (`orders.py:1989-2051`). The money writes come before the first commit, so this is downgraded.
- **Missing `tasks(org_id, status)` index:** the dashboard count is a sequential scan.
- **Old migrations** rewrite money columns under ACCESS EXCLUSIVE. 16 have `downgrade: pass`.
- **Cancelling a transfer after a short receipt** returns the full shipped qty.
- **A PO can be "cancelled" after a partial receipt.**
- **Web intake photos are uploaded raw,** HEIC included. Tekly Go already shrinks them.

### QA coverage and gaps (QA lead)

Baseline, all on Postgres 16:

| Run | Result |
|---|---|
| Full suite, 4 shards (90cf6025) | **3640 passed, 0 failed**, 4 skipped |
| `regress.py` | **80/81 PASS**. W3-E-7 failed because the check read the wrong field; #97, since merged, fixes the harness. The server was right |
| `isolation.py` | **0 leaks, 0 5xx**; the 21 "2xx-without-A-data" are closed by #98 |
| Date/money subset in reversed order at UTC-11 | 0 failures |
| Same subset at UTC+14 | **10 failures** (Q2) |
| QA lead's own concurrency probes (refund ×3, pay with distinct keys ×3, last-unit sale ×2, deactivated token, password-reset sign-out, revoked API key) | All pass |

| # | Sev | Finding | Evidence | Fix |
|---|---|---|---|---|
| Q1 | Medium (rated High by the QA lead as a process issue; downgraded by the chair because it isn't a product failure) | The coverage ratchet under-counts every async DB path | `pyproject.toml [tool.coverage.run]` has no `concurrency`, so SQLAlchemy greenlets drop the trace. On the same 32 tests, `cash.py` measures 35% without it and 44% with it; `invoice_money.py` 31% vs 58% | `concurrency = ["greenlet", "thread"]`, re-measure, then raise the floor |
| Q2 | Medium | Memberships and billing use the server's clock (`date.today()`, 17 sites in `services/memberships.py` and `routers/memberships.py`), not `org_today` | With `TZ=Pacific/Kiritimati`: `test_memberships` counts usage wrong (`2.0 == 0`), plus 7 `test_billing` failures and 2 tests that assume the machine date | Use `org_today`/`today_in`; fix the tests to use `platform_today()`; add a CI leg at TZ=UTC+14. **Beta condition: the production host's TZ is UTC** |
| Q3 | Medium | The model↔migration drift gate runs only on SQLite (`test_144eo_migrated_schema.py:42`), while 10+ migrations branch on Postgres | Against an alembic-built Postgres database: **96 columns are nullable in the DB but NOT NULL in the models**, including `org_id` on 14 tenant tables (`tags`, `taggables`, `recommendations`, `consent_log`, `currencies`, …) | Run the drift gate in the Postgres job, then add a `NOT NULL org_id` migration after an orphan check |
| Q4 | Medium | Money and stock endpoints with no race test anywhere: invoice pay, gift-card and loyalty redeem, payroll pay, supplier pay, write-offs, bins consume, transfer receive, postings commit, credit notes, simulate-pay, terminal charge and refund, membership billing | grep of route ∩ `gather`/`Barrier` = 0 | Add gate `race()` checks (QA lead's probe code is in the council notes) |
| Q5 | Medium | Postgres-only lock paths have no Postgres behavioural test: `skip_locked` serial allocation, the `pg_advisory_xact_lock` sites, two-runner `run_exclusive`, `insert_ignore` | `grep pg_advisory backend/tests` returns nothing | A small two-session `test_pg_locks.py` |
| Q6 | Low | Machine date in `stock_posting.py:83`, `serials.py:136`, `pilot.py:101`, `public.py:152` | — | `org_today` |
| Q7 | Low | Slow tests: 76 s, 16 s, 12.7 s | — | Trim or mark them |

---

## Cross-examination summary

- **Upgraded:** H2 avg_cost, from Medium to High. `avg_cost` is the basis for valuation, adjust/write-off cost and repricing.
- **Downgraded:**
  - money on archived jobs (M1) and job-archive holds (M2): policy gaps or blocking, not corruption;
  - the dashboard cache, `/metrics` and the status-change commits: now Low.
- **Merged:**
  - PE F5 into H13;
  - sub-invoice tax (M11) under H8's root cause;
  - PE F3 into the stock engineer's S2 (= H2).
- **Refuted:** the claim that add-line already takes an Idempotency-Key. Only `orders.create` and `orders.finish` call `idem_begin` (`orders.py:927,2936`).
- **Fix-design catches:**
  - H2's first fix deadlocked two POs (reproduced, then fixed with id-ordered locks).
  - H3's first fix still double-paid hours (reproduced, then fixed).
  - H7's fix, as delivered, deadlocked two payments on a shared invoice. The chair traced it in the Postgres log and fixed it by taking the invoice locks before the job.
  - Lock order: the architect proposed Order → Invoice → Client. The chair's deadlock trace showed that the existing `finance._record_payment` order, **Invoice → Order → Client**, is the one every path must follow. Prefer atomic `add_to_column` to new `FOR UPDATE` locks.

## Hardening plan (excluding in-flight work)

In-flight work that this plan does not duplicate:
- #95 notification outbox;
- #99 Idempotency-Key pass;
- already merged: #91 job locks and readiness, #92 test DB isolation, #97 gate harness, #98 foreign-id 404s.

**Before the first beta shop (blockers):**
1. Merge the council PRs for H1–H12 after review.
2. Fold H13 into #99: a stale-claim lease, and never `release` after commit.
3. Deployment conditions: one worker or Redis; `FORWARDED_ALLOW_IPS` set to the load balancer (M18); `TRUSTED_PROXY_HOPS` documented in the beta compose.
4. Run the restore drill once (DEPLOY.md §4 is still unchecked in PROD-HARDENING.md).
5. Production host TZ is UTC until Q2 lands. Re-run `regress.py` and `isolation.py` on the beta cut.
6. Coverage `concurrency = greenlet` (Q1), so the ratchet can see the money handlers these PRs change.

**First beta week:**
1. Drawer correctness: M9 POS refund leg, M10 one open drawer, Low-1 guards after the shift has closed.
2. Lock order Invoice → Order → Client on the remaining paths (M22). Mechanical pass converting the remaining read-modify-writes to `add_to_column` (Low list).
3. Stock cost layers: M3 stocktake/transfer, M5, M8, M6, M7. PO edit guard (M4).
4. Shop-day reports (M12). Line-edit audit trail (M16).
5. Web client: request timeout and "no connection" message; keys on add-line and job-create (M13).
6. Imports: encoding detection and re-run guard (M14, M15).
7. Platform: invite rate limit (M17); Redis-down log and readiness signal (M19); MFA lockout ordering, DataError → 422 and config validation (Low).
8. Test coverage: drift gate on Postgres and `NOT NULL org_id` (Q3); race checks for the uncovered money and stock endpoints (Q4); Postgres lock tests (Q5); memberships on the shop's clock (Q2).

**Before about 300 shops:**
1. SLA sweep in SQL (M21).
2. `tasks(org_id, status)` index.
3. Dashboard cache in Redis or removed.
4. Multiprocess metrics and slow-query logging.
5. Already queued: correlated logs, S3 media off the event loop, chunked imports, `pg_trgm`, PITR.

**Reconciliation guard (recommended by both the PE and the money engineer).** Add a nightly integrity sweep that asserts:
- Σ payments − refunds = `invoice.amount_paid`;
- Σ ledger = `client.balance`;
- Σ active transactions = `account.balance`;
- Σ movements = on-hand.

It would have caught H1, H4, H5 and H7 in production. It belongs in the first beta week.

## FDE first-week runbook gaps

1. No support "see what the shop sees": the `support_access` permission exists but nothing implements it.
2. The backup restore drill has never been run, and there is no per-tenant restore procedure.
3. A mistaken import can't be undone after a re-run or after 24 h. There is no batch-scoped force-undo.
4. A stuck reader charge has no operator override (H11), and pending charges are never reconciled on a schedule.
5. The audit UI is missing, and line edits aren't audited (M16).
6. A shop's data can't be exported whole: jobs with lines and payments, stock, devices.
7. Users see the raw browser error "Load failed", with no correlation id in error toasts.
8. Auto-print failures are silent; there is no "reprint last" shortcut.
9. Onboarding doesn't confirm the shop's clock or timezone.

## Council PRs (into `roadmap/orderry-parity`, none merged)

Every PR has a test written first. The chair re-ran each one on Postgres 16 with the fix reverted (fails) and with it applied (passes). For #107 and #112, the "before" run kept the new setting or constant the test imports.

The chair's review changed three of the PRs, each with a test:
- #101: deadlock between two POs;
- #102: a leaked shift was paid twice;
- #111: a deadlock between two payments on one invoice.

| Finding | Sev | PR | Chair note |
|---|---|---|---|
| H1 corrected transactions dropped from totals | High | #100 | 484 related tests pass |
| H2 avg_cost lock, plus id-ordered PO locks | High | #101 | Chair added the deadlock fix and test; 257 PO/inventory tests pass |
| H3 one clock-in; a leaked duplicate shift paid 0 h | High | #102 | Chair added the no-double-pay fix and test |
| H4 void legs posted atomically | High | #103 | |
| H5 client balance moved through `add_to_column` | High | #105 | |
| H6 balance-pay spends the credit at once | High | #109 | **Most invasive.** Early `charge` row plus a guarded balance UPDATE; review the reopen semantics carefully |
| H7 `Invoice.amount_paid` moved atomically | High | #111 | `add_to_column` at 7 sites. **Chair fixed a deadlock** (the test returned 500 three times out of three): `_pay_order` now locks invoices before the job. 180 related tests pass |
| H8 invoice keeps the job's tax | High | #106 | |
| H9 reserve locks rows; qty > 0 | High | #108 | |
| H10 `/bins/batches` goes through stock posting; `consume` and `allocate-serial` removed | High | #104 | No UI callers. Two route lines dropped from `ungated_write_routes.txt` |
| H11 reader outage no longer blocks cash | High | #112 | A late Stripe success still posts (test). No hunks shared with #99 |
| H12 operator SMS daily cap (200, trial 50) | High | #107 | Config `OPERATOR_SMS_DAILY_CAP[_TRIAL]`; shops with their own Twilio account are uncapped |
| M1 no money on archived jobs | Medium | #113 | |
| M2 archiving releases stock holds | Medium | #110 | Estimate decline still keeps its holds (noted in the PR) |
| H13 idempotency lease / no release after commit | High | **in #99's scope, not fixed here** | |

**Merge-order notes:**
- #103, #105, #109, #111 and #113 all touch `cash.py`, `finance.py` or the invoice/ledger services. Expect small textual conflicts; re-run `test_b3_money_blockers`, `test_invoice_pay_ledger` and `test_money_hardening` after each merge.
- None of the PRs adds a migration.
