# RepairOS — Full-App Audit (senior QA + dev + PM team)

*Six parallel reviewers swept the whole codebase: security/public-surface, money/data-integrity,
backend correctness, models/migrations/DB, frontend, and PM completeness. ~32k LOC backend (77
routers), ~18k LOC frontend (73 files), 3.1k-line model, 262 test files.*

---

## Executive summary

The app is in **far better shape than a typical mid-build product**. Confirmed healthy:
- **Migration graph clean** — single head, one base, zero duplicate revision ids, zero drift between
  models and what Postgres builds (no prod-only-missing columns).
- **FE↔BE wiring tight** — effectively **zero broken frontend API calls**.
- **Auth is solid** — bcrypt, JWT token-version revocation, hashed single-use reset/verify/invite
  tokens, no email-enumeration, prod refuses to boot with placeholder secret/CORS.
- Webhook signatures verified (Stripe/Square/Twilio), media is org-scoped, no raw SQL/eval/pickle,
  promise rejections handled, no missing-key/`.map` issues, loading/empty states mostly present.

The real risk is concentrated in: **(1) a handful of exploitable auth/isolation gaps, (2) money-path
idempotency & archive-reversal, (3) prod-migration hazards (FK enforcement, NOT-NULL add-columns,
two missing data backfills), and (4) frontend "slop" that shows fake/decorative data as real.**

---

## FIXED in this pass (build 144am)

| Sev | Issue | Fix |
|-----|-------|-----|
| HIGH (sec) | `cash.post_transaction` looked up `Account` by id **without org scope** → a client-supplied `account_id` could corrupt another tenant's balance | scoped the lookup to `org_id` |
| HIGH (sec) | Staff **invites** (`POST/DELETE /staff/invites`) had no permission → a restricted employee could invite a teammate as **admin** (privilege escalation) | added `require_perm("can_manage_employees")` to both |
| MED-HIGH (sec) | `PUT /staff/company` (org-wide settings incl. payment provider) had **no authorization** | added `require_perm("can_manage_settings")` |
| HIGH (money) | Deleting a **cash-settled** stock posting ran `sup.balance -= total` unconditionally, driving the supplier balance negative (cash postings never added to it) | gated the reversal on `settlement == "balance"` |
| HIGH (corr) | `list_orders` parsed `created_from/to` with raw `fromisoformat` → **500** on bad input (the most-used list endpoint) | switched to `parse_dt` (clean 400) |
| MED (corr) | Bulk **set-priority** did `int(data.value)` on a free-text field → 500 | guarded → 400 |
| MED (corr) | `list_orders` / inventory list `limit` had **no ceiling** (eager-loads per row → DoS) | capped via `Query(…, le=200/500)` |
| HIGH (slop) | WODrawer showed **fabricated checklist progress** ("3/4 · 5/5") on orders with none | real loading + empty state; real checklist names |
| MED (slop) | WODrawer **Type/Priority** `<select>`s looked editable but never saved | rendered as read-only fields |
| MED (slop) | "**Client Approved**" checkbox was always-checked with no handler | reflects real `estimate_approved_at`, read-only |

Gates: backend imports clean; `test_staff_authz` + `test_idor_tenant_isolation` green; `tsc` + `vite build` clean.
*Note: the deeper fixes below warrant dedicated regression tests before a prod push.*

---

## Remaining findings — triaged (severity × effort)

### P0 — exploitable / correctness, mostly small (do next)
- **`/api/public/lookup` not org-scoped** (public.py) — order numbers are per-org, so an unscoped
  `.first()` can resolve another org's order and hand back its `public_token`. Scope lookup to a
  resolved org (shop slug), like the booking routes.
- **Bulk status-change bypasses all gates** (orders.py `BulkActionIn set_status`) — sets terminal
  statuses with no transition RBAC, no stock write-off, no notifications, and accepts arbitrary
  strings. Route bulk items through the same validator as single `change_status`.
- **`fromisoformat` 500 cluster** still broad: timeclock (list/create/edit/summary/approve),
  bookings list, conversions, feedback, tasks, staff — wrap in `parse_dt`/try-except (the pattern
  already exists in siblings). *(list_orders done this pass.)*
- **timeclock naive-vs-aware datetime** compare → 500 on tz-aware input (normalize to naive).
- **Meta inbound webhook unsigned** (inbox.py `/webhook/meta/{org_id}`) — verify `X-Hub-Signature-256`
  with `META_APP_SECRET`, fail-closed, like the Twilio path.
- **Payment webhooks**: require the webhook secret in production (`_fail_closed_in_prod`) and add
  Stripe **timestamp/replay** tolerance.

### P1 — money integrity (needs care + tests)
- **No idempotency/locking on pay/refund** (`cash.pay_order`, `finance.record_payment`,
  `returns.create_refund`, `cash.refund_overpayment`, `services.payments.mark_succeeded`) → double
  pays/refunds on retry/webhook-race. Build one shared idempotency+row-lock helper for all of them.
- **Refunds have no cap** — can refund more than was paid; `create_refund` also posts no client
  ledger entry (balance drifts).
- **Archive/delete order doesn't fully reverse** — doesn't restock product lines, doesn't reverse the
  closing `charge` ledger entry (phantom receivable), and refunds *balance-method* payments as real
  cash.
- **Invoice `record_payment` posts no cashbox `Transaction`**, and **cash-settled postings** and
  **client advances** also skip the cashbox → cash totals can't reconcile against revenue.
- **Non-atomic stock deductions** on WO paths (oversell/negative-stock race) — use the guarded
  `UPDATE … WHERE qty_available >= qty` the POS path already uses.
- **Loyalty redemption** has no atomic guard (double-spend race); coupon/gift-card already do.
- **`expected_cash` never computed** → shift reconciliation can't detect a till shortage.
- **Money stored as `Float`** (~80 columns) not `Numeric` — schedule a deliberate migration.

### P1 — prod-migration hazards (platform)
- **SQLite FK enforcement off** + **406/414 FKs have no `ondelete`** → deletes that pass in dev/tests
  raise IntegrityError on Postgres. Add a connect-time `PRAGMA foreign_keys=ON` (so tests catch it)
  and decide CASCADE vs app-cascade per relationship. *(This is also why the merge-completeness bug
  was invisible in tests — fixed earlier in 144al.)*
- **Two `init_db` backfills have no Alembic equivalent** (supplier→contact unification 144am; default
  notification-rule seed 144ah) → existing **prod** orgs miss them. Write data migrations.
- **39 `add_column(nullable=False)` with no `server_default`** → `ALTER` aborts on a populated prod
  table. Add `server_default` to each.
- **Missing indexes** on hot FKs/tenant keys (`orders.org_id/client_id/…`, `order_lines.order_id`,
  `payments.*`, `invoices.*`, `transactions.*`) → full scans at scale.
- Add **CI**: `alembic upgrade head` on scratch Postgres + `--autogenerate` diff must be empty.

### P2 — frontend slop & consistency (user-visible polish)
- **Hardcoded 8% tax** on new orders (CreateOrderDrawer) — wrong for a ₹/configurable-tax shop; use
  org tax config / server-computed total.
- **`money()` duplicated 6× hardcoding `₹`** — a non-₹ shop sees ₹ on POS/Finance/Reports/Inventory/
  Cash/Company. Consolidate to the currency-aware helper.
- **PO table checkboxes** are decorative (no state).
- **~70 native `prompt()/alert()/confirm()`** instead of the app's Modal/toast — worst are the
  `prompt()` data-entry ones (gift-card/coupon value, cash correction, view name). Standardize.
- **~25 icon-only buttons without `aria-label`** (close buttons, bell) — a11y.
- **Raw hex** instead of tokens in several surfaces; `ClientsHub` merge-balance `NaN` guard.

### P2 — product completeness & cruft (PM)
- **Backend-only features with no UI**: payroll coefficients/run-all/attendance-wages, RBAC permission
  groups, compliance/GDPR org export, pricing margin-check, inventory reorder-digest/markup/merge,
  clients CSV export, auth logout-all-devices. Surface or label API-only.
- **Inventory tabs are view-only** except stocktake (create flows for transfers/conversions/write-offs
  absent); **`REACT-PARITY-MAP.md` is stale** (still lists shipped features as missing).
- **Dead code / cruft**: `components/Drawer.tsx` + `pages/WorkOrdersPreview.tsx` (empty stubs), root
  `WorkOrderControlCenter.tsx`/`*_reference.ts`, committed `*.db` files + both `venv/` and `.venv/`.
- **Two frontends coexist** (legacy `frontend/` still hosts login/admin/portal) — plan the retirement.
- **Thin tests** on high-risk money/auth surfaces: loyalty redemption (0), dedicated refund suite,
  RBAC-groups, payroll math, public-portal behavior (not just shape).

### Noted, NOT bugs (so they aren't re-chased)
- Automated **customer notifications DO send** — the canonical `notification_rules.evaluate` path
  calls real channel adapters; the log-only engine in `notifications.py` is just the fallback.
- `require_perm` is **default-allow** by design (owner/no-role = all perms); relevant when reasoning
  about "missing perm" findings.
- AI features return a marked `provider:"stub"` when no key is set (by design).
- `services.payments.PaymentGateway.create_checkout` `NotImplementedError` is an abstract base.

---

## Recommended sequence
1. **P0 batch** (small, exploitable): public/lookup scope, bulk-status gate, the rest of the
   fromisoformat/timeclock-tz cluster, Meta webhook signature, webhook prod-enforcement + replay.
2. **Money hardening** (P1): one shared idempotency+lock helper across pay/refund/reconcile; make
   archive fully reverse (stock + charge + non-cash payments); refund caps + ledger posting.
3. **Prod-migration safety** (P1): `PRAGMA foreign_keys=ON` in tests, `server_default` on NOT-NULL
   adds, the two missing data migrations, hot-path indexes, the alembic CI check.
4. **Polish** (P2): tax config, `money()` currency, native-dialog → Modal, a11y labels, dead-code
   purge, parity-map refresh.
5. **Then** the P1 money column `Float→Numeric` migration as a scheduled, well-tested change.
