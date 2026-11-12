# 02 — Database schema findings (prefix DB)

Snapshot: `roadmap/orderry-parity` @ d353c67b (`scratchpad/audit-src`). All paths below are relative to that tree.
Companion files: `02-schema-inventory.md` (one row per table) and `02-er-diagram.md`.

## Method
- I introspected `Base.metadata` with a script (`scratchpad/dbprobe/introspect.py` → `meta.json`): 193 tables, 2,446 columns, 525 FKs, 450 indexes and 0 CHECK constraints.
- I ran the existing hard drift gate `backend/tests/test_144eo_migrated_schema.py` on SQLite: **4 passed**.
- I wrote two **probe scripts** in the scratchpad. Each runs against a throwaway SQLite file built with `create_all`, with `PRAGMA foreign_keys=ON`:
  - `dbprobe/probe_constraints.py` tries inserts the schema ought to refuse.
  - `dbprobe/probe_guard.py` tests the ORM tenant guard against Core SQL.
  - Both show what the **database schema** accepts. They do not show what the API lets through.
- I read the relevant routers and services to establish the real write paths.

### Probe results (test-verified, `probe_constraints.py` / `probe_guard.py`)
| Attempt | Result |
|---|---|
| Payment with `amount = -500` | ACCEPTED |
| Payment linked to nothing (no order, invoice, client or account) | ACCEPTED |
| Payment: order of shop A, client of shop B, session with no tenant | ACCEPTED |
| Same, with tenant=A set (ORM flush) | REJECTED (`CrossTenantReference`) |
| Same, with tenant=A, via Core `insert(Payment)` | ACCEPTED |
| Order of A pointing at B's location, via Core `insert(Order)` with tenant=A | ACCEPTED |
| Order of A pointing at B's client or location (ORM, no tenant) | REJECTED (the guard checks rows that have an org_id) |
| Order line with qty −3 and unit_price −10 | ACCEPTED |
| StockLevel with qty_available −99 | ACCEPTED |
| `orders.status = 'totally_made_up'`; payment kind/method garbage | ACCEPTED |
| Two open `cash_shifts` at one location | ACCEPTED |
| Two `running` `time_entries` for one employee | ACCEPTED |
| Two open attendance `shifts` for one employee | ACCEPTED |
| Duplicate coupon code in one org; duplicate gift-card code in one org | ACCEPTED |
| Duplicate `sales.sale_number`; duplicate `stock_transfers.transfer_number` in one org | ACCEPTED |
| Duplicate `invoices.invoice_number` in one org | REJECTED (`uq_invoices_org_number`) |
| Gift card with balance −50; coupon with uses 5 > max_uses 1; invoice with amount_paid 1000 > total 10 | ACCEPTED |
| Two active memberships, same plan and same asset | ACCEPTED |

---

### DB-01 [HIGH] `payments` — the core money table — has no org_id; its tenant is inferred from four nullable FKs
- **Module:** Money (payments, cash, reporting)
- **Tables/columns:**
  - `payments`: no `org_id`. The candidate owners are `invoice_id?`, `order_id?`, `client_id?` and `account_id?`, all nullable. There is also `shift_id?`.
  - Index: `ix_payments_paid_at` (paid_at only).
- **Functions/APIs:**
  - `backend/models/__init__.py:1559-1574` `class Payment(Base)`: not `OrgScoped`.
  - `backend/db/tenancy.py:250-252`: the flush guard skips org-less rows when no tenant is set.
  - `backend/services/revenue.py:378-389` `cash_collected`.
  - `backend/services/qbo.py:345-350` `_payment_in_org`.
  - `backend/services/payments.py:392-394`: a Payment is built with only `account_id`.
  - `backend/routers/cash.py:957-966`; `backend/routers/clients.py:1147-1151`.
- **Execution flow:**
  1. Payment rows are written by about 12 call sites: `invoice_money.py:217-567`, `finance.py:752`, `cash.py:836` and `payments.py:383/393`.
  2. Because the table is not `OrgScoped`, the read guard (`tenancy.py:120-136`) never adds `org_id = :tenant` to Payment SELECTs.
  3. So every read must hand-scope through a parent, and each reader picks a *different* parent:
     - `cash_collected` uses order OR invoice OR account (`revenue.py:386-389`);
     - `_payment_in_org` uses the first non-null of invoice, order, client (`qbo.py:347-349`);
     - order and client tabs pre-check the parent (`cash.py:957-959`, `clients.py:1147-1150`).
  4. On flush, an org-less row is checked only when `session.info["tenant_org"]` is set (`tenancy.py:250-253`). Background jobs and any session with no tenant skip the check.
- **Evidence:**
  - `revenue.py:385`: `# Payments carry no org_id: they belong to the shop through their job, invoice or account.`
  - `cash.py:959`: `# payments below aren't org-filtered`.
  - Probe: a payment linked to nothing, and a payment with A's order and B's client, were both ACCEPTED with no tenant set.
- **Verification level:**
  - test-verified: the schema accepts orphan and mixed-tenant payments.
  - code-read: the read paths.
  - I did **not** find a live request path that writes a mixed-tenant payment.
- **Why it's a problem:**
  - Every money report pays for a 3-way `OR … IN (subquery)` against parent tables. Nothing can be index-only, and there is no `(org_id, paid_at)` index.
  - Readers disagree on ownership when the FKs conflict.
  - A Payment with only `client_id` is invisible to `cash_collected`. One with no FK at all belongs to no shop.
  - A future endpoint that forgets the parent pre-check leaks another shop's payments, because the read guard does not cover this table.
- **Real-world failure scenario:**
  - `services/payments.py:392-394`: an online intent with no order or invoice records `Payment(kind="payment", amount=…, account_id=…)`.
  - If the org has no active account, `account_id` is None. The money exists, but no report, reconciliation or QBO push can attribute it to the shop.
  - Separately, a background job running without `set_tenant` could attach a payment to one shop's order and another shop's client with no error. Probe-verified that the schema and guard allow it.
- **Recommended fix:**
  1. Add `payments.org_id` NOT NULL with FK. Backfill it from order → invoice → account → client, in the same order as `tenancy._ORG_FROM_LINKS`.
  2. Mark `Payment` as `OrgScoped`.
  3. Add `(org_id, paid_at)` and `(shift_id)` indexes.
  4. Add a CHECK that at least one of order_id or invoice_id is set (or account_id for unattached receipts).
- **Fix dependencies/risks:**
  - The backfill needs an orphan report first: rows with no derivable org go to a quarantine list.
  - `cash_collected`, `ledger.py:311`, `invoice_money` and the v2 API can then drop the OR-subqueries.
  - The same approach should then be applied to `cash_shifts` (DB-02).

### DB-02 [MEDIUM] 23 tenant child tables have no org_id and are invisible to the read guard
- **Module:** Tenancy, across all modules
- **Tables/columns:** no `org_id` on these tables:
  - jobs: `order_lines`, `order_comments`, `order_status_logs`;
  - billing and sales: `invoice_lines`, `invoice_orders`, `credit_note_lines`, `sale_lines`;
  - cash and people: `cash_shifts`, `shifts`, `wage_records`;
  - messaging: `chat_messages`;
  - stock: `stock_levels`, `stock_movements`, `stock_transfer_lines`, `stocktake_lines`, `stocktake_serials`, `po_lines`, `posting_lines`, `purchase_return_lines`, `write_off_lines`, `client_return_lines`;
  - catalog: `bundle_components`.

  `payments` is covered in DB-01.
- **Functions/APIs:**
  - `backend/db/tenancy.py:20-26` and `:250-253` (writes checked through FKs only when a tenant is set).
  - `tenancy.py:32-45` (the read guard covers only `OrgScoped` classes).
  - Example of an unscoped read: `backend/routers/timeclock.py:315` `select(OrderLine).where(OrderLine.id == data.order_line_id)`, later validated against the order at :318-321.
  - `cash_shifts` is scoped by joining `Location` (`routers/finance.py:846, 923, 1091-1093`).
- **Execution flow:**
  1. A SELECT of any of these entities by id returns another shop's row.
  2. Safety depends on each call site joining or pre-checking a parent. The sites I sampled do this (timeclock :318-321, finance.py:1092-1093, cash.py:957-959).
  3. The flush guard checks their FKs only when a tenant is set.
- **Evidence:** `tenancy.py:22-24` says these rows "are checked through their foreign keys". The read-guard list `OrgScoped` excludes them (introspection: 159 OrgScoped of 163 org tables; the 23 above have no column to filter on).
- **Verification level:**
  - schema-level / code-read.
  - Sampled call sites are hand-scoped. **Not every call site was verified** — that is the API auditors' scope.
- **Why it's a problem:**
  - Defence in depth stops at the parent table.
  - Every stock and cash report needs a join to reach the org. For example, the stock reconcile joins `Location` for every movement (`services/reconcile.py:109-125`).
  - High-volume tables (`stock_movements`, `order_lines`) cannot be partitioned or indexed by tenant.
- **Real-world failure scenario:** a new endpoint does `select(StockMovement).where(StockMovement.ref_id == x)`, or loads an `OrderLine` by id from the body without the order check. It returns or edits another shop's row, and the read guard that protects every OrgScoped table does nothing.
- **Recommended fix:**
  1. Add `org_id` (NOT NULL, indexed, OrgScoped) first to the money and stock ledgers: `stock_movements`, `stock_levels`, `cash_shifts`, `order_lines`, `sale_lines`, `invoice_lines`.
  2. Later, to the remaining line tables.
  3. Drop `wage_records` instead (see DB-17).
- **Fix dependencies/risks:**
  - Large backfills (`stock_movements`, `order_lines`). Use batched UPDATEs and `CREATE INDEX CONCURRENTLY` (the pattern in `migration_safety.py`).
  - The flush guard's org-less path (`tenancy.py:250-253`) becomes unnecessary for these tables.

### DB-03 [MEDIUM] No composite (org_id, id) foreign keys: the database itself accepts cross-tenant references. The only barrier is an ORM flush hook
- **Module:** Tenancy
- **Tables/columns:** all 525 FKs are single-column (introspection: composite FKs = 0). No target table has a UNIQUE `(org_id, id)` to reference.
- **Functions/APIs:**
  - `backend/db/tenancy.py:234-302` `_guard_cross_tenant_references` (a before_flush listener).
  - `tenancy.py:47-58` lists its known gaps.
- **Execution flow:**
  1. ORM `session.add` → flush → the guard compares the referenced rows' `org_id`.
  2. Core `insert()`, `update().values()`, `text()` and raw SQL never reach `before_flush`.
  3. Sessions without a tenant skip org-less rows (DB-01).
- **Evidence:** probe with tenant=A:
  - Core `insert(Order).values(org_id=A, location_id=<B's location>)` was ACCEPTED;
  - Core `insert(Payment).values(order_id=<A>, client_id=<B>)` was ACCEPTED;
  - the same via the ORM was REJECTED.
- **Exploitable vs theoretical:**
  - **Realistically exploitable: none found.** I grepped every Core `insert(...)`/`.values(` in `routers/` and `services/`. The Core inserts are:
    - `statuses.py:181` (seed);
    - `idempotency.py:195`;
    - `task_alerts.py:91`;
    - `notifications.py:211`;
    - `tax_backfill.py:57`.

    None takes a request-supplied FK. The bulk `update(...).values(<fk>=…)` calls set server-derived values: `order_items.py:213-305` (`new.id`, None) and `push.py:50` (`current.id`).
  - **Theoretical:**
    1. any future Core or bulk write that copies an id from a request body;
    2. jobs and webhooks that write org-less child rows before `set_tenant`;
    3. `session.get` identity-map hits (`tenancy.py:53-54`);
    4. soft references with no FK at all (DB-13).
- **Verification level:** test-verified (probe). The exploit search is code-read (grep).
- **Why it's a problem:** an FK is not tenant isolation (RULES). One missed code path corrupts another shop's data silently: the FK is satisfied, so nothing errors.
- **Real-world failure scenario:** a developer optimises "bulk reassign orders" to `update(Order).where(Order.id.in_(ids)).values(assigned_to_id=data.employee_id)`. The read guard filters the WHERE to this shop's orders, but nothing checks `employee_id`, so orders now point at another shop's employee.
- **Recommended fix:**
  1. Add `UNIQUE (org_id, id)` on the hot parents: clients, devices, orders, parts, locations, employees, accounts, invoices.
  2. Convert the high-risk child FKs to composite `(org_id, x_id) → parent(org_id, id)`. Start with orders.client_id/device_id/location_id, invoices.order_id/client_id, transactions.account_id and stock_reservations.part_id.
  3. Keep the ORM guard as the friendly-404 layer.
- **Fix dependencies/risks:**
  - Needs DB-01 and DB-02 first: child tables need `org_id` to take part in a composite FK.
  - Orphan and mismatch pre-checks are required before validation. Use `NOT VALID` then `VALIDATE CONSTRAINT` on Postgres.

### DB-04 [HIGH] "Only one open at a time" invariants have no DB backing; two of them have racy app checks
- **Module:** Cash, time clock, memberships
- **Tables/columns:**
  - `cash_shifts(location_id, closed_at)`;
  - `time_entries(employee_id, state, ended_at)`;
  - `shifts(employee_id, ended_at)`;
  - `memberships(plan_id, device_id, status)` with `membership_assets(membership_id, device_id)` (unique per membership only).
- **Functions/APIs:**
  - `backend/routers/finance.py:1030-1055` `open_shift`: a Location `FOR UPDATE` mutex, then check-then-insert.
  - `finance.py:1068-1072` `current_shift`: the comment proves duplicates exist in the field.
  - `backend/routers/timeclock.py:298-360` `start_timer` and `:409-430` `switch_timer`: **no lock**. `_running_for` at :257-260 is a plain SELECT.
  - `timeclock.py:90-114` `shift_clock_in`: has an employee-row lock.
  - `backend/services/memberships.py:291-308`, `backend/routers/memberships.py:611-613`: `live_on_device` check-then-insert, no lock.
- **Execution flow (timer):**
  1. Two taps on "Start" (Tekly Go): both requests run `_running_for` → None.
  2. Both insert `TimeEntry(state="running")`.
  3. `stop` stops only `.first()`. The second runs until `AUTO_STOP_HOURS` caps it, with `needs_review=True` (`timeclock.py:263-273`).
- **Evidence:**
  - `finance.py:1068-1069`: `# Newest open drawer: shops that doubled a drawer before the open lock (M10) still have two open rows`.
  - The timer start has no `with_for_update` (grep of `timeclock.py` finds the only `with_for_update` at :100, in clock-in).
  - Probe: two open cash shifts, two running timers, two open attendance shifts and two same-plan memberships on one asset were all ACCEPTED.
- **Verification level:**
  - test-verified: the schema accepts duplicates.
  - code-read: the races.
  - The race itself was not reproduced under concurrency.
- **Why it's a problem:**
  - A duplicate open drawer splits cash payments between two shifts, and each close reports a variance.
  - Duplicate timers double the labour hours that feed payroll and job costing.
  - A duplicate membership bills the client twice.
  - The cash drawer lock is app-only. It holds on Postgres, is a no-op on SQLite, and is bypassed by any other writer.
- **Real-world failure scenario:** a technician double-taps Start on a slow connection. The job shows 2× labour cost, and payroll accrues extra hours until a manager catches the `needs_review` flag.
- **Recommended fix:** add partial unique indexes (both dialects support them):
  - `cash_shifts(location_id) WHERE closed_at IS NULL`;
  - `time_entries(employee_id) WHERE state='running' AND ended_at IS NULL`;
  - `shifts(employee_id) WHERE ended_at IS NULL`;
  - `memberships(device_id, plan_id) WHERE status IN ('scheduled','active','paused') AND device_id IS NOT NULL`.

  Then turn the IntegrityError into a 409.
- **Fix dependencies/risks:** existing duplicates must be closed first. The code comment says field data has them, so the migration needs a pre-check plus repair. **Needs staging data.**

### DB-05 [MEDIUM] Document numbers without per-org uniqueness: sales, transfers, write-offs, purchase returns, postings
- **Module:** Numbering, documents
- **Tables/columns:**
  - `sales.sale_number` (`models:2748`);
  - `stock_transfers.transfer_number` (`:2893`);
  - `write_offs.number` (`:3806`);
  - `purchase_returns.number` (`:3770`);
  - `postings.number` (`:3843`).

  None has a unique key. The ones that do have `(org_id, number)` unique: orders, invoices, purchase_orders and credit_notes.
- **Functions/APIs:**
  - `backend/services/numbering.py:115-153` `next_number`;
  - `:156-166` `_number_taken`, which maps only invoice, po, credit_note and estimate;
  - `backend/routers/settings_finance.py:75-101` (editing the template or counter);
  - callers: `routers/sales.py:527`, `routers/transfers.py:331`, `routers/stock_docs.py:248, 441`.
- **Execution flow:**
  1. The counter is claimed by an atomic UPDATE (good).
  2. The rendered number is checked against existing documents **only** for the 4 mapped types.
  3. For a sale, transfer, write-off or return, a template change (e.g. dropping `{Y}`, or moving to a location scope) can re-render a number already issued. It is inserted silently.
- **Evidence:**
  - `numbering.py:157-160`: the `col = {...}` map has no sale, transfer, write_off, purchase_return or posting.
  - The rewind guard at `settings_finance.py:80-84` compares counters, not rendered strings.
  - Probe: duplicate sale_number and duplicate transfer_number were ACCEPTED.
- **Verification level:** test-verified (schema). The template-change collision path is code-read.
- **Why it's a problem:** duplicate fiscal document numbers on sales receipts. Many jurisdictions require gapless, unique receipt numbering.
- **Real-world failure scenario:**
  1. The owner switches sales numbering from `S-{C:5}` to `S-{Y}-{C:3}` and back.
  2. `S-00042` is issued twice.
  3. Accountant exports and the QBO sync key on the number.
- **Recommended fix:**
  - Add `UNIQUE(org_id, sale_number)` and the same for transfer_number, write_offs.number, purchase_returns.number and postings.number. Partial `WHERE number IS NOT NULL` for the nullable ones.
  - Extend `_number_taken` to every doc type.
- **Fix dependencies/risks:** existing duplicates need a pre-check.

### DB-06 [MEDIUM] Coupon and gift-card codes are not unique per org; a duplicate makes the code unusable (500)
- **Module:** Engage, POS
- **Tables/columns:** `coupons(org_id, code)` and `gift_cards(org_id, code)`: plain non-unique indexes on `code` (`models:3279, 3307`).
- **Functions/APIs:**
  - `backend/routers/engage.py:201-217` `create_coupon` (check-then-insert);
  - `engage.py:84-110` `issue_giftcard` (random `GC-` + 6 hex, check-then-insert);
  - lookups use `scalar_one_or_none()`: `routers/sales.py:103` (coupon) and `:121` (gift card); `engage.py:135, 155, 318`.
- **Execution flow:** two concurrent creates of `SAVE10` both pass the `dup` check and both insert. Every later redemption runs `scalar_one_or_none()` over 2 rows → `MultipleResultsFound` → HTTP 500 at checkout.
- **Evidence:** probe: duplicate coupon code and duplicate gift-card code in one org were both ACCEPTED. `engage.py:211-214`: `dup = … scalar_one_or_none(); if dup: raise HTTPException(409…)`.
- **Verification level:** test-verified (schema). The race is code-read.
- **Why it's a problem:** a lost sale at the counter, and a gift card with a balance that can no longer be redeemed.
- **Real-world failure scenario:** a staff member double-clicks "Create coupon". Every customer presenting that code hits an error.
- **Recommended fix:**
  - `UNIQUE(org_id, code)` on coupons. Use `upper(code)` as an expression index, or store the code normalised (it already is uppercased).
  - The same on gift_cards.
- **Fix dependencies/risks:** dedupe existing rows first. Merge `uses`, keep the active one.

### DB-07 [MEDIUM] Zero CHECK constraints: amount/qty signs, balances, limits and 175 status/kind strings are unconstrained
- **Module:** All
- **Tables/columns:**
  - 0 CHECK constraints in the models, and 0 in the migrations (grep of `create_check_constraint|CheckConstraint|CHECK (` across the 249 migrations: none).
  - 175 free-text enum columns, e.g. `orders.status`, `payments.kind`/`method`, `transactions.txn_type`/`status`, `stock_movements.movement_type`, `invoices.status`, `time_entries.state`, `memberships.status`. Full list from the introspection is in `scratchpad/dbprobe/meta.json`.
- **Functions/APIs:** validation lives only in Pydantic models and service code (e.g. `services/coupons.check_coupon_value`, `engage.py:208-209`, `timeclock.py:306-307`).
- **Execution flow:** any write path that skips the service validation persists the bad value: a script, an import, a Core update, or a future endpoint.
- **Evidence:** probe ACCEPTED all of these:
  - payment amount −500; order line qty −3 at price −10; stock −99;
  - `orders.status='totally_made_up'`; payment kind `'whatever'`;
  - gift card balance −50; coupon uses 5 > max_uses 1; invoice amount_paid 1000 > total 10.
- **Verification level:** test-verified.
- **Why it's a problem:**
  - Status-driven logic (`TASK_CLOSED`, `VOID_STATUSES`, status groups) silently mis-classifies unknown values.
  - Sign conventions differ by table: `payments.amount` is always positive with `kind` giving the direction, while `transactions.amount` and `gift_card_ledger.amount` are signed. Nothing enforces either convention.
- **Real-world failure scenario:** an import or a bug writes a negative payment `amount` with `kind='payment'`. `cash_collected` subtracts it from income, and the shift's expected cash is off.
- **Recommended fix:** start with the money-critical CHECKs:
  - `payments.amount >= 0`;
  - `payments.kind IN (...)`;
  - `gift_cards.balance >= 0`;
  - `coupons.uses >= 0 AND (max_uses = 0 OR uses <= max_uses)`;
  - `invoices.amount_paid >= 0`;
  - `order_lines.qty <> 0`;
  - `transactions.status IN ('active','corrected','deleted')`;
  - `time_entries.state IN (...)`.

  Use `NOT VALID` then `VALIDATE` on Postgres. SQLite needs batch recreate.
- **Fix dependencies/risks:**
  - Existing bad rows must be found first.
  - alembic autogenerate does not compare CHECKs, so the drift gate will not catch them going missing (DB-16). Add a dedicated test.

### DB-08 [MEDIUM] Float storage for rates, percentages, quantities and points (63 columns), and 95 Numeric columns that return floats
- **Module:** Money, inventory
- **Tables/columns:** every `Float` column (introspection, complete list):
  - **Money-affecting rates and percentages:**
    - `taxes.rate`; `order_lines.tax_rate`; `sale_lines.tax_rate`;
    - `currencies.rate`; `currency_rate_history.rate`; `orders.fx_rate`; `transactions.fx_rate`;
    - `accounts.fee_pct`;
    - `employees.commission_labour`, `employees.commission_parts`;
    - `price_types.markup_pct`; `price_type_tiers.markup_pct`; `posting_lines.markup_pct`;
    - `membership_plan_items.pct`; `platform_coupons.percent`;
    - `organisations.loyalty_earn_rate`, `.min_margin_pct`, `.discount_approval_pct`, `.membership_prompt_threshold_pct`;
    - `order_lines.work_share`.
  - **Points (money-equivalent):** `loyalty_ledger.points`.
  - **Quantities:**
    - `order_lines.qty`, `.pending_stock`, `.bundle_qty`, `.booked_hours`;
    - `sale_lines.qty`; `invoice_lines.qty`; `recommendations.qty`; `membership_usage.qty`; `bundle_components.qty`;
    - `stock_levels.qty_available`, `.qty_reserved`; `stock_movements.qty`; `stock_reservations.qty`;
    - `po_lines.qty_ordered`, `.qty_received`; `posting_lines.qty`; `purchase_return_lines.qty`; `write_off_lines.qty`;
    - `stock_transfer_lines.qty`, `.qty_received`;
    - `stocktake_lines.system_qty`, `.counted_qty`, `.variance`;
    - `client_return_lines.qty`;
    - `parts.min_order_qty`; `price_type_tiers.min_qty`.
  - **Time and measures:**
    - `shifts.hours_worked`; `employees.weekly_hours`;
    - `checkins.reading_value`; `parts.weight`; `storage_bins.max_weight_capacity`;
    - `document_templates.page_w_mm`, `.page_h_mm`, `.margin_mm`; `label_templates.width_mm`, `.height_mm`;
    - `time_entries.gps_lat`, `.gps_lng`;
    - `custom_fields.gate_threshold`, `.upsell_threshold`; `checklist_template_items.upsell_threshold`; `order_checklist_items.upsell_threshold`.
  - Plus **95 `Numeric(..., asdecimal=False)`** columns: every money column on orders, order_lines, invoices, payments, transactions, accounts, clients, sales, parts and so on. They are exact in the DB but floats in Python arithmetic. Only 41 Numeric columns return `Decimal`.
- **Functions/APIs:**
  - `backend/models/__init__.py`, e.g. `:739` `OrderLine.qty`, `:761` `tax_rate`, `:1311-1312` StockLevel, `:1325` StockMovement.qty, `:1657` `Account.fee_pct`.
  - `services/reconcile.py:42-43` tolerates `_MONEY_EPS = 0.005` and `_QTY_EPS = 1e-6` precisely because of this.
- **Execution flow:** `line total = qty(float) × unit_price(float from Numeric) × (1 + tax_rate(float))`, rounded at the end. Stock on hand is the sum of float movements.
- **Evidence:** introspection type counts: Float 63; Numeric with `asdecimal=False` 95; Numeric with Decimal 41.
- **Verification level:** schema-level / code-read.
- **Why it's a problem:**
  - Cent-level rounding differences between line, document and ledger.
  - Fractional-unit stock (m, kg, l are supported uoms, `parts.uom`) accumulates binary error. After many movements, `qty_available == 0` comparisons fail.
  - FX rates stored as binary floats are not reproducible on re-render.
- **Real-world failure scenario:** a 0.1 m cable is used 30 times. On-hand reads `2.9999999999999996`, so a "use 3" request is refused as insufficient stock, or a low-stock alert never clears.
- **Recommended fix:**
  - Rates: `Numeric(9,6)`.
  - Percentages: `Numeric(7,4)`.
  - Quantities: `Numeric(14,4)`.
  - Points: `Numeric(14,2)`.
  - Then move the `asdecimal=False` money columns to `Decimal`, per `SPEC_money_decimal.md`.
- **Fix dependencies/risks:**
  - Type migration on large tables (`stock_movements`, `order_lines`): Postgres `ALTER TYPE` rewrites the table.
  - App code that does float math needs the Decimal pass. Prior-audit item R4 is **PARTIAL** (see the table at the end).

### DB-09 [MEDIUM] Stored running totals that can drift, and most are not covered by the nightly integrity sweep
- **Module:** Money, inventory, engage
- **Tables/columns:**
  - **Covered by `services/reconcile.py:9-14` (CHECKS = account, client, invoice, stock):**
    - `accounts.balance`;
    - `clients.balance`;
    - `invoices.amount_paid`;
    - Σ `stock_levels.qty_available` per (part, location).
  - **Not covered:**
    - `gift_cards.balance` vs Σ `gift_card_ledger.amount` (the docstring says "balance is always derivable", `models:3289`);
    - `suppliers.balance` (`:1381`);
    - `orders.labour_total`/`parts_total`/`total`/`tax`/`discount`/`prepayment_total`/`written_off` vs `order_lines` and payments;
    - `invoices.subtotal`/`total`/`credited` vs `invoice_lines` and `credit_notes`;
    - `sales.total`/`total_cost`/`tax` vs `sale_lines`;
    - `purchase_orders.total`/`received_cost` vs `po_lines`;
    - `postings.total`, `write_offs.total_cost`, `purchase_returns.total`, `credit_notes.subtotal`/`tax_total`/`total` vs their lines;
    - `stock_levels.qty_reserved` vs `stock_reservations`;
    - `coupons.uses` vs sales with `coupon_id`;
    - `orders.client_msg_unread`/`rec_unread`;
    - `payroll_runs.*_total` vs `employee_balance_ledger`.
- **Functions/APIs:**
  - `backend/services/reconcile.py:41, 127` `_RUNNERS`;
  - `core/scheduled.py:377-379` (the nightly sweep);
  - writers use `services/money.add_to_column` (`services/money.py:135, 156`) and atomic UPDATEs (e.g. `sales.py:241`, `engage.py:176`).
- **Execution flow:** each total is maintained incrementally by its writers. Any missed path (a void, a restore, an edit, a merge) leaves it wrong, and nothing reports it.
- **Evidence:**
  - `reconcile.py:41` `CHECKS = ("account", "client", "invoice", "stock")`.
  - Prior-audit M3/M4 (restore and void races) were exactly this class of drift.
- **Verification level:** code-read.
- **Why it's a problem:** gift-card and supplier balances are money owed. Drift there is a financial misstatement no one is alerted to.
- **Real-world failure scenario:** a voided sale restores the gift card through `sales.py:459`. A concurrent restore path (M3-type) double-credits it, and the card balance exceeds its ledger with no alert.
- **Recommended fix:**
  1. Add `gift_card`, `supplier`, `order_total`, `sale_total`, `po_total`, `reserved_qty` and `coupon_uses` runners to `reconcile.py` (read-only, the same pattern).
  2. Longer term, derive the document totals in views instead of storing them.
- **Fix dependencies/risks:** low risk. Read-only queries, but they need the indexes in DB-10 to stay cheap.

### DB-10 [MEDIUM] FK and org_id index gaps, including on hot tables (208 FK columns and 29 org_id columns without a leading index)
- **Module:** Performance
- **Tables/columns:**
  - FK columns on the big tables with no leading index:
    - `payments.shift_id` (summed at drawer close: `finance.py:858, 862`), `payments.received_by_id`;
    - `stock_levels.location_id` (only `(part_id, location_id)` exists; filtered alone at `inventory.py:129, 187`, `sales.py:344`);
    - `sales.location_id`/`seller_id`/`account_id` (`services/profit.py:127, 131`, `revenue.py:324, 350`);
    - `asset_movements.asset_id` (`assets.py:593`) **and** `asset_movements.org_id`;
    - `refunds.payment_id`/`account_id`/`return_id`;
    - `transactions.cash_flow_item_id`;
    - `invoice_lines.order_line_id` (`invoice_money.py:404`);
    - `invoices.payer_client_id`;
    - `notification_logs.rule_id`/`template_id`/`employee_id`;
    - `audit_log.actor_id`;
    - `stock_movements.created_by_id`;
    - `tasks.assigned_to_id`/`created_by_id`/`parent_task_id`/`location_id`;
    - `leads.client_id`/`converted_order_id`;
    - `appointments.client_id`/`employee_id`;
    - `calls.client_id`;
    - `clients.payer_id`/`manager_id`;
    - `devices.location_id`;
    - `serialized_items.po_id`/`batch_id`.
  - `org_id` with no index: `employees`, `locations`, `roles`, `services`, `bundles`, `accounts`, `suppliers`, `taxes`, `stock_transfers`, `stocktakes`, `stock_reservations`, `stock_item_batches`, `asset_movements`, `price_types`, `payroll_runs` and 14 more (inventory column 5, "NO IDX").
- **Functions/APIs:**
  - The read guard appends `WHERE <model>.org_id = :tenant` to **every** ORM select (`tenancy.py:135-136`).
  - Client delete and merge probe every FK to clients (`routers/clients.py:524-530, 897-905`). Of those, `invoices.payer_client_id`, `clients.payer_id`, `calls.client_id`, `leads.client_id` and `appointments.client_id` are unindexed.
- **Execution flow:**
  1. A drawer close sums payments by `shift_id`: a seq scan of `payments`.
  2. An asset history view runs a seq scan of `asset_movements`.
  3. A client delete or merge runs about 5 seq scans.
  4. Every list of an un-indexed OrgScoped table is a full scan plus filter.
- **Evidence:** introspection (`scratchpad/dbprobe/fk_noindex.json`). The prior audit said "220 of 450 foreign keys have none". It is now 208 of 525. The three named hot ones — `POLine.part_id`, `StockReservation.part_id` and `Task.order_id` — are now indexed.
- **Verification level:** schema-level. Query plans **NOT VERIFIED** (needs staging volumes).
- **Why it's a problem:** latency grows linearly with table size on the money and stock paths. On Postgres, a parent DELETE scans every un-indexed child.
- **Real-world failure scenario:** a busy multi-location shop with 500k payments closes the till. The expected-cash query does a sequential scan on every close.
- **Recommended fix:** use `CREATE INDEX CONCURRENTLY` (the existing pattern). Add:
  - `payments(shift_id)`;
  - `stock_levels(location_id)`;
  - `sales(org_id, location_id, created_at)`;
  - `asset_movements(asset_id)` and `asset_movements(org_id)`;
  - `refunds(payment_id)`;
  - `invoice_lines(order_line_id)`;
  - `invoices(payer_client_id)`;
  - `tasks(org_id, assigned_to_id)`;
  - `leads(client_id)`;
  - `appointments(org_id, employee_id)`;
  - `calls(client_id)`;
  - `org_id` on the 29 tables.

  Also drop the 40 redundant `ix_<t>_org_id` indexes that are prefixes of a composite (DB-17).
- **Fix dependencies/risks:** write amplification is minimal. Each new index must be added to the models too, or the drift gate fails (it fails on DB-only indexes).

### DB-11 [MEDIUM] Hard deletes of financial rows contradict the soft-void model used elsewhere
- **Module:** Money, payroll, CRM
- **Tables/columns:**
  - `payments` (hard-deleted when a refund is voided);
  - `employee_balance_ledger` (documented "Append-only", `models:2293`, but manual bonus and penalty rows are deleted);
  - `clients` (merged-away sources are deleted);
  - `invoice_lines` and `invoice_orders` (deleted when a job is unlinked);
  - `stocktakes` and their lines (drafts).

  By contrast, these are soft-voided: `transactions.status='deleted'` (`models:1745`), `refunds.voided_at`, `sales.voided_at`, `payroll_runs.voided_at`, `credit_notes` (canceled), and `orders.archived`.
- **Functions/APIs:**
  - `backend/services/invoice_money.py:581-588` `unrefund_payment` → `await db.delete(pay)`, called from `routers/returns.py:363-401` `delete_refund`.
  - `invoice_money.py:574-578` `refund_siblings`.
  - `routers/payroll.py:828-846` `remove_adjustment` → `db.delete(e)`.
  - `routers/clients.py:893-905` (merge) and `:509-536` (delete; refused when history exists).
- **Execution flow (refund void):**
  1. `delete_refund` locates the refund's Payment(s).
  2. `refund_siblings` matches the "siblings" heuristically on (order_id, kind='refund', paid_at, account_id). There is no refund_id on payments.
  3. `unrefund_payment` deletes each row.
  4. The Transaction legs are soft-voided (`void_document_legs`).
- **Evidence:**
  - `invoice_money.py:576-577`: `Payment.order_id == pay.order_id, Payment.kind == "refund", Payment.paid_at == pay.paid_at, Payment.account_id == pay.account_id`.
  - `returns.py:397`: `refund.payment_id = None  # the rows go`.
- **Verification level:** code-read.
- **Why it's a problem:**
  - The payments table loses history that the transactions table keeps, so the two ledgers stop being comparable after a void.
  - The heuristic can delete another refund's Payment when two refunds on one order share `paid_at` and account (same request or same second).
  - A payment already synced to QBO or Xero (`integration_mappings.local_id`) leaves a dangling mapping.
- **Real-world failure scenario:**
  1. Two partial refunds are issued on one order by a batch action within the same timestamp.
  2. Voiding one deletes both refund Payments.
  3. The order shows as fully paid again, while one refund's cash already left the drawer.
- **Recommended fix:**
  - Add `payments.voided_at`, `voided_by_id` and `refund_id` (or a `group_id`). Void instead of delete, and match siblings by the explicit link.
  - Make `employee_balance_ledger` truly append-only: post a reversing entry instead of deleting.
- **Fix dependencies/risks:** every Payment reader must add `voided_at IS NULL`, the same way `Transaction.status` is handled (`reconcile.py:9-10`).

### DB-12 [LOW] Latent CASCADE / delete-orphan on financial children, and no org-offboarding path
- **Module:** Orders, invoices, purchasing
- **Tables/columns:**
  - ORM `cascade="all, delete-orphan"` on:
    - `orders.lines` / `status_logs` / `comments` (`models:726-728`);
    - `invoices.order_links` / `lines` (`:1493-1496`);
    - `purchase_orders.lines` (`:1435`);
    - `credit_notes.lines`.
  - DB `ON DELETE CASCADE`:
    - `credit_note_lines.credit_note_id`;
    - `order_checklists.order_id`, `order_checklist_items.order_id`;
    - `stocktake_serials.*`;
    - `serial_events.item_id`;
    - `product_barcodes.part_id`.
  - All 163 `org_id` FKs are NO ACTION except `auth_sessions`.
- **Functions/APIs:**
  - I found no hard-delete path for orders, invoices or POs (grep of `db.delete(` across 63 call sites in routers and services). Orders are archived (`orders.archived`, `models:713`).
  - Credit notes can only be deleted as drafts (`routers/credit_notes.py:199-207`).
- **Execution flow:** latent only. An admin script or future endpoint that calls `db.delete(order)` would silently remove its order_lines and status history. A paid order's lines would vanish while its payments remain.
- **Evidence:** introspection of relationship cascades. `serial_events.item_id` uses CASCADE, so deleting a serialized unit erases its passport history.
- **Verification level:** schema-level / code-read.
- **SET NULL orphaning money:** none of the 14 SET NULL FKs is on a money record. `time_entries.task_id` SET NULL keeps payroll time, which is intended (`models:2511`).
- **Recommended fix:**
  - Replace `delete-orphan` with `save-update, merge`, plus explicit line deletion where it is needed (line edits).
  - Make `serial_events.item_id` RESTRICT.
  - Design an org-offboarding/export-and-purge job rather than relying on FK behaviour.
- **Fix dependencies/risks:** line-edit code paths (`orders.py:5429, 5468`) already delete lines explicitly.

### DB-13 [MEDIUM] 86 "*_id" columns are soft references with no FK; only 4 are covered by the tenant guard
- **Module:** Tenancy, integrity
- **Tables/columns:** (introspection: `*_id` columns not declared as FKs):
  - `sales.coupon_id`;
  - `order_lines.membership_id`, `tax_id`, `ref_id`;
  - `membership_usage.order_line_id`;
  - `recommendations.order_line_id`;
  - `transactions.ref_id`, `reverses_txn_id`, `transfer_pair_id`;
  - `payroll_runs.cash_txn_id`;
  - `orders.converted_order_id`, `converted_from_id`;
  - `locations.price_type_id`;
  - `organisations.owner_id`, `price_type_*_id`, `cash_category_*_id`, `assigned_admin_id`;
  - `price_type_items.item_id`;
  - `tax_exceptions.target_id`;
  - `bundle_components.ref_id`;
  - `event_feed_items.entity_id`; `taggables.entity_id`;
  - `invoice_lines.source_id`;
  - `stock_movements.ref_id`;
  - `admin_approval_requests.org_id`, `admin_shop_notes.org_id`, `admin_assignment_log.org_id` (OrgScoped but no FK to organisations);
  - and others.
- **Functions/APIs:** `backend/db/tenancy.py:174-177` `_SOFT_REFS` covers only `order_lines.ref_id/tax_id`, `sale_lines.ref_id` and `recommendations.ref_id`.
  - Router-level checks exist for some. I verified bundles: `routers/bundles.py:17-25, 48-53` `_resolve` filters by `org_id`.
- **Execution flow:** the DB accepts any string, including another shop's id or a deleted row's id. Integrity depends on each writer.
- **Evidence:** `models:746` `membership_id … # set by the server only`; `models:2759` `coupon_id: Mapped[Optional[str]] = mapped_column(String)`.
- **Verification level:** schema-level. Per-writer checks were verified only for bundles.
- **Why it's a problem:** orphans after deletes, for example:
  - `transactions.reverses_txn_id` pointing to nothing;
  - `orders.converted_order_id` pointing to an archived or removed order;
  - a payroll run with a `cash_txn_id` for a non-existent txn.

  There is also no cross-tenant protection on these links.
- **Real-world failure scenario:** a void flow looks up `Transaction.reverses_txn_id`. If the original row was hard-deleted by some future path, the correction row's link silently dangles, and the account reconcile still balances, so nothing alerts.
- **Recommended fix:**
  - Real FKs, with `ondelete` decided per column, for the single-target refs: coupon_id, membership_id, order_line_id, reverses_txn_id, cash_txn_id, converted_*_id, price_type_id, owner_id, and the admin tables' `org_id`.
  - Add polymorphic refs to `_SOFT_REFS`.
- **Fix dependencies/risks:** orphan pre-check, the same procedure as `fk144ep01`.

### DB-14 [LOW] Nullable org_id on tenant tables `devices` and `purchase_orders`
- **Tables/columns:** `devices.org_id` (`models:518`) and `purchase_orders.org_id` (`:1394`) are nullable, with index and FK.
- **Functions/APIs:**
  - `backend/db/tenancy.py:204-231` `_fill_missing_org` (fills org on new rows from their links);
  - migration `trg01_backfill_device_po_org.py`;
  - `test_144eo_migrated_schema.py:125-131` checks only org_ids the **model** declares NOT NULL.
- **Execution flow:** a row with NULL org is invisible to every shop (`tenancy.py:38-40`). New ORM rows are filled, but Core inserts are not.
- **Evidence:** `models:514-517`: "Nullable only to allow the backfill migration."
- **Verification level:** code-read. The count of NULL rows in production is **NOT VERIFIED**.
- **Recommended fix:** after confirming that 0 NULL rows remain, set NOT NULL, mirroring `nn01_org_id_not_null`.
- **Fix dependencies/risks:** needs staging counts.

### DB-15 [LOW] Unique keys not scoped by org
- **Tables/columns:**
  - `employees.email` global, plus `uq_employees_email_lower` (`models:352-358`): one email can work at only one shop.
  - `orders.barcode` global (`:643`; 12 hex chars, 48 bits).
  - `employee_balance_ledger.idempotency_key` global (`:2306`).
  - `payment_terminals (provider, reader_id) WHERE NOT test_mode` global.
  - `auth_sessions.sid`, `client_portal_links.token_hash`, `checkin_points.token`, `orders.public_token` (global by design: tokens).
- **Verification level:** schema-level.
- **Assessment:** all of these are intentional, and documented in comments, except:
  - the global email, a product decision that blocks multi-shop staff; and
  - `orders.barcode`: a random collision raises an IntegrityError (500) on order create. That is very unlikely, at roughly n²/2⁴⁹.
- **Recommended fix:** document them. Retry on barcode collision.

### DB-16 [LOW] Model↔migration drift guard: strong, with known blind spots
- **Module:** CI / migrations
- **Functions/APIs:**
  - `.github/workflows/ci.yml:46-50`: a clean `alembic upgrade head` on SQLite.
  - `ci.yml:89-97`: the full suite includes `backend/tests/test_144eo_migrated_schema.py`, which runs `compare_metadata` on the migrated DB vs `Base.metadata` and fails on any new difference.
  - `ci.yml:204-211`: migrations from scratch on Postgres 16, plus the restore drill.
  - The Postgres suite re-runs the drift gate against a migrated PG database (`test_144eo:35-36, 64-73`).
  - `backend/tests/test_alembic_single_head.py`.
  - `backend/db/migration_safety.py:23-34` (lock_timeout 5s) and `:47-73` (REINDEX of invalid CONCURRENTLY indexes after upgrade).
- **Evidence:** the gate passed locally (4 tests). It emitted warnings that alembic skipped the expression indexes `uq_stock_levels_part_loc_unbinned`, `uq_storage_bins_name` and `ix_tasks_assignee_ids_gin` on SQLite.
- **Blind spots (code-read; alembic 1.19.1):**
  1. CHECK constraints are never compared. That is moot today, since there are none, but it matters once DB-07 lands.
  2. `server_default` is not compared: `compare_server_default` is not set in `test_144eo:84` or `migrations/env.py:36-37`.
  3. Partial-index `WHERE` predicates are not compared, so a migration that built `uq_tasks_open_source_ref` with a different predicate passes.
  4. Expression indexes are skipped on SQLite. They are compared on Postgres only if reflection supports them (**NOT VERIFIED**).
  5. A DB column that is *looser* than the model (nullable) is tolerated, except for `org_id` (`test_144eo:99-101, 125-131`).
- **Recommended fix:**
  - Set `compare_server_default=True`.
  - Add an explicit test that asserts the partial-index predicates and the expression indexes on the PG migrated DB (via `pg_indexes.indexdef`).
  - Add a CHECK-constraint inventory test when DB-07 is done.

### DB-17 [LOW] Redundant tables, indexes and columns; naming inconsistency; circular FK
- **Dead table:** `wage_records` (`models:415-425`). There are 0 references in `routers/`, `services/`, `core/` or `jobs/`, and it has been superseded by `payroll_runs` and `employee_balance_ledger`.
- **Overlapping concepts:**
  - `chat_threads`/`chat_messages` vs `inbox_threads`/`inbox_messages` (two conversation stores);
  - `email_accounts` vs `email_connections`;
  - `approval_requests` vs `admin_approval_requests`;
  - `notification_templates` vs `message_templates`;
  - `order_comments` vs `event_feed_items` (comments);
  - `postings`/`posting_lines` vs the `stock_posting` service (prior audit: "two posting engines").
- **Duplicate and redundant indexes:**
  - `orders.barcode` has both `ix_orders_barcode` and `uq_orders_barcode` on the same column (`models:643, 709`);
  - 40 single-column `ix_*_org_id` / `ix_*_<fk>` indexes are prefixes of an existing composite or unique index (e.g. `ix_orders_org_id` ⊂ `ix_orders_org_created`; `ix_invoices_org_id` ⊂ `uq_invoices_org_number`);
  - `employees.email` has `unique=True` plus a `lower(email)` unique index.
- **Docstring copy-paste:** `XeroConnection` says "QuickBooks Online OAuth connection state" (`models:3555-3556`).
- **Naming inconsistency:**
  - part vs product: the table is `parts`, but `kind`/`ref_type` values say `product`, and there is `ProductBarcode.part_id`;
  - labour vs labor: `order_lines.line_type='labour'` and `employees.commission_labour`, but `invoice_lines.kind='labor'`, `services.is_labor` and `tax_exceptions.target_type='labor'`;
  - canceled vs cancelled: `appointments`, `stock_transfers`, `payment_intents`, `credit_notes` and `terminal_charges` use `canceled`; `tasks`, `purchase_orders`, `memberships`, `admin_approval_requests` and `invoices.einvoice_status` use `cancelled`;
  - device vs asset: the table is `devices`, but the columns are `order_items.asset_id`, `asset_movements.asset_id` and `devices.asset_status`;
  - location vs warehouse: both `location_id` and `warehouse_id` on stock rows;
  - three different "shift" concepts: `shifts` (attendance), `cash_shifts` (drawer) and `work_schedules`.
- **God table:** `organisations` has 150 columns.
- **Circular FK:** `warehouses.default_bin_id` (SET NULL) ↔ `storage_bins.warehouse_id`.
- **Verification level:** schema-level / grep.
- **Recommended fix:**
  - Drop `wage_records` and the duplicate barcode index.
  - Consolidate the redundant prefix indexes.
  - Pick one spelling (`cancelled`) behind a CHECK (DB-07). This needs a data migration.
  - Move the organisation settings into typed settings tables over time.

### DB-18 [LOW] All 279 timestamp columns are naive `DateTime`
- **Tables/columns:** every `DateTime` column. 0 have `timezone=True`.
- **Functions/APIs:** `backend/services/dates.py` (`now_utc`), plus model defaults `_now` and `now_utc`.
- **Verification level:** schema-level.
- **Why it's a problem:** UTC is a convention, not enforced. The prior audit counted 211 `utcnow()` calls, and shop-local dates are computed in code. A writer that stores local time is indistinguishable from one that stores UTC.
- **Recommended fix:** move to `timestamptz` on Postgres in a planned migration. Until then, add a test that asserts every writer uses `now_utc`.
- **Prior audit:** "Timestamps: 0 timezone-aware columns" — **STILL OPEN**.

---

## Prior audit (2026-10-29) — items in scope
| Prior item | Status | Evidence |
|---|---|---|
| R4: 87 of 112 money columns return floats; several money fields are `Float` | **PARTIAL** | Every money *amount* column is now `Numeric`, but 95 are still `asdecimal=False` (they return floats), and 63 `Float` rate, percentage, quantity and points columns remain (DB-08) |
| Missing indexes: 220 of 450 FKs; hot `POLine.part_id`, `StockReservation.part_id`, `Task.order_id` | **PARTIAL** | The three named hot ones are now `index=True`. 208 of 525 FK columns still lack a leading index (DB-10) |
| Timestamps: 0 timezone-aware columns | **STILL OPEN** | 279 naive `DateTime` (DB-18) |
| Two posting engines | **STILL OPEN** (schema side) | `postings`/`posting_lines` are still mapped and referenced in 6 router/service files. Whether the UI uses them was NOT VERIFIED |
| No data-retention purge jobs | **FIXED** | `services/retention.py:1-90` (opt-in per shop, min 30 days); the daily job is in `core/scheduled.py` |
| H1–H3, M2: line tables accepted another shop's part, serial or ref ids | **FIXED at the ORM layer** for flushes with a tenant set: `tenancy.py:16-26, 174-177, 244-302`. Probe: an ORM flush with tenant=A is refused. **Not fixed at DB level**: no composite FKs, and Core writes bypass the guard (DB-03) |
| "A single Alembic head and a model↔migration drift gate" (solid) | **Still holds** | `test_144eo` passed locally; `test_alembic_single_head.py`; PG CI job. Blind spots are in DB-16 |

## Severity counts
CRITICAL 0 · HIGH 2 (DB-01, DB-04) · MEDIUM 10 (DB-02, 03, 05, 06, 07, 08, 09, 10, 11, 13) · LOW 6 (DB-12, 14, 15, 16, 17, 18)

## 1. Inspected
- `backend/models/__init__.py` (all 193 classes via metadata introspection; read in full: Location, Role, Employee, Shift, WageRecord, Device, AuthSession, OrderStatus, Order, OrderLine, OrderItem, OrderStatusLog, OrderComment, Bundle, BundleComponent, Appointment, ChatThread, ChatMessage, Part, StockLevel, StockMovement, PurchaseOrder, POLine, Invoice, DocumentSignature, EstimateRevision, InvoiceOrder, Payment, CashShift, CashDrawerMovement, ProcessorPayout, Expense, Account, Currency, CashFlowItem, Transaction, AuditLog, EventFeedItem, InvoiceLine, StatusTransition, AssetMovement, StockReservation, EmployeeBalanceLedger, PayrollRun, TimeEntry, Sale, SaleLine, Stocktake/Line/Serial, ClientReturn(+Line), Refund, StockTransfer(+Line), GiftCard(+Ledger), Coupon, LoyaltyLedger, NumberSequence, ClientLedgerEntry, Membership*, OperatorTextUsage)
- `backend/db/tenancy.py` (full), `backend/db/migration_safety.py` (full), `backend/migrations/env.py` (compare options)
- `backend/tests/test_144eo_migrated_schema.py` (full); `.github/workflows/ci.yml` (migration and drift jobs)
- Migration directory: 249 files grepped for CHECK constraints, partial and expression unique indexes, and triggers
- Call sites: `routers/finance.py` (open/current/list shifts, invoice payment), `routers/timeclock.py` (clock-in, start, switch), `routers/engage.py` (coupon, gift card), `routers/sales.py` (coupon/gift lookups, atomic updates), `routers/returns.py` (delete_refund), `routers/payroll.py` (remove_adjustment), `routers/clients.py` (delete, merge, payments tab), `routers/cash.py` (order invoices), `routers/settings_finance.py` (numbering), `routers/bundles.py` (component validation), `services/numbering.py`, `services/invoice_money.py`, `services/payments.py`, `services/revenue.py`, `services/qbo.py`, `services/memberships.py`, `services/reconcile.py`, `services/retention.py`
- Prior audit `docs/audits/2026-10-29-full-codebase-audit.md`

## 2. Not inspected
- The body of each of the 249 migrations, line by line. Equivalence is relied on through the drift gate.
- Every read site of the 23 org-less child tables. I sampled about 10; a full sweep is the API/tenancy auditors' scope.
- Router-level validation for each soft reference in DB-13, apart from bundles.
- The `Organisation` columns individually (150), beyond types.
- Frontend and API-schema validation (out of scope; it is not backend validation in any case).

## 3. Verified (by test/probe)
- `test_144eo_migrated_schema.py`: 4 passed (SQLite migrated schema = models).
- `scratchpad/dbprobe/probe_constraints.py`: every schema-acceptance result in the table at the top.
- `scratchpad/dbprobe/probe_guard.py`:
  - ORM flush with a tenant refuses a mixed-tenant Payment;
  - Core `insert()` of a mixed-tenant Order or Payment is accepted;
  - with no tenant set, an org-less Payment with mixed-tenant FKs is accepted.

## 4. Uncertain
- Whether the drift gate on Postgres compares the expression indexes (alembic and PG reflection support). Partial-index predicates are definitely not compared.
- Whether any background job writes org-less child rows (payments, order_lines, stock_movements) before `set_tenant`, and so outside the guard's org-less check.
- Whether the timer double-start and the membership double-sell races (DB-04) actually interleave under the production worker model. Code-read only; not reproduced concurrently.
- The real collision exposure of DB-05 depends on how often owners change sales, transfer or write-off templates.

## 5. Needs staging to test properly
- Row counts and `EXPLAIN ANALYZE` for the DB-10 index gaps (drawer close by `payments.shift_id`, asset history, client merge or delete).
- Existing duplicates, to plan the DB-04, DB-05 and DB-06 unique-index migrations:
  - open `cash_shifts` per location (the code comment says they exist);
  - running `time_entries` per employee;
  - coupon and gift-card codes;
  - sale and transfer numbers.
- NULL `org_id` counts in `devices` and `purchase_orders` (DB-14). Payments with no derivable org (DB-01).
- Reconcile mismatch counts on production data for the uncovered totals (DB-09).
- Concurrency tests on Postgres for the DB-04 races.
