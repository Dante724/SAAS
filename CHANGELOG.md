# Changelog

## phase 4 — Customer status messages: the notification rules are the one source of truth

- **Problem:** two settings disagreed. The status editor's "Text the customer" switch
  (`OrderStatus.triggers_notification`) drove a legacy template send that ran only when **no**
  notification rule matched; every new shop is seeded with an "email on every status change" rule,
  so the switch was nearly always ignored and owners couldn't tell which setting was real.
- **Decision:** rules win, everywhere. The legacy fallback in `_apply_status_change` is **removed**
  (not merely unreachable). `triggers_notification` stays as a column so old clients don't break, but
  nothing reads it: a `true` from the API is converted into a rule and the flag cleared.
- **No behaviour change for existing shops** (`services/legacy_status_notifs.py`, shared by
  migration `nt144p4` and the dev startup self-heal): each flagged status becomes one active
  per-status rule per status-change template the fallback would have sent (that template's channel,
  event `closed` for a finishing status), **only** when no rule already fires for it (the fallback
  never ran then) and only when such a template exists (it sent nothing otherwise). A
  location-scoped rule counts as covering (can't double-send). Statuses not in the status table
  (hard-coded `done/closed/delivered`) no longer get the template fallback.
- **Status editor:** the switch is replaced by a live read-out from the rules ("Email · via Every
  status change", "Text"), Text/Email(/WhatsApp) quick switches that create or turn off this
  status's own rule (the same `toggleCell` the Notifications table uses, `src/lib/notifRules.ts`), and
  "Edit message" → Settings › Notifications. The summary sentence and the list badge read the rules.
- Changing a status between open and finishing stages moves its own rules between `status_changed`
  and `closed`, so they keep firing. New shops no longer seed the flag.
- Known gap (unchanged, follow-up): rule-engine email doesn't use the shop's connected Gmail/Outlook
  the way the old template path did (`try_oauth_email`); it goes through SMTP / simulated.

## build 144ep — Enforce the last 11 foreign keys; fix checklist orphans; repair dev DB

- **Root cause, checklist orphans:** `_write_structure` (template save) deleted steps and relied on the
  DB's `ON DELETE CASCADE` to remove their sections; on a connection without FK enforcement that never
  ran, leaving orphan sections. It now deletes child-first (items → sections → steps) explicitly.
  Regression test runs the rewrite with FK enforcement OFF and asserts zero orphans (RED: 2 orphans).
- **Hard-deleted parents → `ON DELETE SET NULL`** (FK audit of every parent's delete path):
  `time_entries.task_id` (deleting a task keeps the labour/payroll time) and
  `parts.default_supplier_id` (un-flagging a supplier contact removes its mirror; the part keeps, the
  preference clears). Both returned 500 before (RED tests).
- **Migration `fk144ep01`** adds the 11 FKs the models declared but production never enforced:
  - Pre-flight orphan check: any orphan → aborts with the list, **changes nothing** (no silent rewrite).
  - Postgres: `ADD CONSTRAINT … NOT VALID` then `VALIDATE` in autocommit (no long table lock).
  - Idempotent: correct FKs are left alone; wrong `ON DELETE` (create_all DBs) is replaced.
  - Verified: fresh DB; seeded up→down→up with all 11 tables content-identical; planted orphan aborts
    with schema, data and revision unchanged; real dev DB copy upgrades NO ACTION → SET NULL.
  - Drift gate `KNOWN_MISSING_FKS` is now empty.
  - **Deploy step:** run `scripts/check_fk_orphans.py` on production first (see TESTING.md).
- **`scripts/repair_dev_db.py`:** for create_all-built dev DBs. Moves legacy `bookings` rows into
  `appointments` (verified identical before the old table is dropped) and deletes unreachable orphan
  checklist sections. Dry run by default; `--apply` backs up first, one transaction, idempotent,
  refuses on id clashes and in production. Applied to the local dev DB: 3 appointments recovered,
  3 orphans removed, `foreign_key_check` clean.
- **Gates:** full suite 1154 passed (304 files, CI-replica venv); backend coverage 50.2% → floor raised 49.5 → 50.0; ruff (pinned 0.6.9) clean.

## build 144eo — Close the model ↔ migration gap (drift fixes + hard drift gate)

The test suite builds its schema from the MODELS; production builds it from the MIGRATIONS, so any
disagreement between them was invisible to tests. A drift audit found real gaps. Correction to an
earlier claim: the notification-rule FK bug is NOT on Postgres production (fk144as01 fixed it there);
it is real on SQLite databases built before that fix — including the dev database.

- **Migration `sc144eo01`** (single head, chains off `c8e0g2i4k6m8`), idempotent + dialect-aware:
  - `notification_logs.rule_id` FK → `ON DELETE SET NULL` on every dialect (fk144as01 skipped SQLite;
    `create_all` never alters existing tables, so older DBs kept `NO ACTION` and deleting a rule that
    had fired was rejected). Postgres: no-op when already correct. SQLite: batch rebuild.
  - `document_templates.page_size` 12 → 16 chars (Postgres enforces length; model says 16).
  - 7 single-column indexes the models declare but no migration created — built `CONCURRENTLY IF NOT
    EXISTS` on Postgres (no write lock), as in ix144dh01.
  - `ix_bookings_scheduled_for` → `ix_appointments_scheduled_for` (missed by the rename).
  - Clears its own leftover `_alembic_tmp_*` table first, so an interrupted SQLite run can be retried
    (Alembic doesn't run SQLite DDL in one transaction; found by testing a failed downgrade).
- **Model:** `orders.barcode` now declares exactly what production has (lookup index + separate
  `uq_orders_barcode` unique index) instead of migrating the largest table. Uniqueness unchanged.
- **API:** `page_size` validated against the render presets (+`label`); unknown → 422, empty → `a4`.
- **Hard drift gate** `test_144eo_migrated_schema.py` (replaces CI's advisory `alembic check || true`):
  builds a DB purely via `alembic upgrade head` and fails on any model/migration mismatch outside a
  reviewed allowlist that can only shrink (11 known missing FKs, 1 VARCHAR≡TEXT). Also asserts a fired
  rule can be deleted on the migrated schema, and page_size validation. Mutation-tested: fails on the
  pre-fix schema, reproduces the original FK error, catches a planted column, flags a stale allowlist.
- **`scripts/check_fk_orphans.py`:** read-only audit of all 418 declared FKs (orphan counts + whether the
  DB enforces each). SELECT-only, Postgres READ ONLY txn, always rolled back — DB file byte-identical
  after a run. Prerequisite for adding the 11 missing FKs (procedure in TESTING.md).
- **Verified:** migrations from scratch (single head); populated data at the previous head → upgrade →
  downgrade → upgrade with every row preserved; a copy of the real dev DB (create_all-built, mixed
  state) upgrades cleanly with identical row counts and a real fired rule deletes; failed-run recovery;
  backend suite **1,147 passed / 0 failed** on CI's pinned stack; ruff clean; workflow schema valid.
- **Dev-data notes (not production):** the dev DB still has a legacy `bookings` table holding 3
  appointments invisible to the app (the rename migration never ran on a create_all DB), and 3
  pre-existing orphan `checklist_sections.step_id` rows. Left untouched.

## build 144en — Best-in-class test build (5 commits: 144en-1 … 144en-5 + docs)

Planned with the testing-strategy skill; every change verified in an exact replica of CI's pinned
stack (pytest 8.2.0 / pytest-asyncio 0.23.7 / xdist 3.6.1 / ruff 0.6.9), not the sandbox defaults.

- **Lint suppression removed (the "both problems"):** 47 semicolon lines (E702) split and 13 ambiguous
  `l` comprehension vars (E741) renamed — token-based, never touching strings. **Proof: the AST of all
  31 changed files is identical to before** (the check itself caught and was corrected for a file that
  legitimately used `ln`). E702/E741 now enforced in tests.
- **Parallel CI + parity:** pinned `pytest-xdist`; CI runs `-n auto`. `make ci` runs every CI gate in CI
  order; `make test-pg` runs the suite on Postgres. Found & closed a parity gap: local runs had used
  pytest 9.1 / pytest-asyncio 1.4 vs CI's 8.2 / 0.23 — now verified on CI's versions.
- **Postgres production parity:** new always-on test compiles all 154 tables + 272 indexes to Postgres DDL
  and enforces partial-index `WHERE` parity across dialects (mutation-tested; 1.8s). Fixed app engine to
  use NullPool only for `test`+Postgres (asyncpg is loop-bound) — prod/staging/dev/SQLite pool choice
  proven unchanged. New `backend-postgres` CI job (Postgres 16: migrations from scratch + full suite),
  advisory until first green run.
- **Coverage ratchet:** backend branch coverage (pytest-cov 5.0.0, coverage 7.16.2 pinned) measured
  50.2% → floor 49.5; frontend v8 (`all: true`) 2.8% lines / 25% funcs → floors 2.5 / 24. Both gates
  proven to fail the run; reports uploaded as CI artifacts. Clean `npm ci` from lockfile verified.
- **Pre-commit:** pinned `pre-commit` + `pre-commit-hooks`; hooks run from the venv (one place for every
  version). Config delivered as a template (hook files are protected for the assistant). Each guard proven
  to block: real `.env`, SQLite DB even via `git add -f`, private key, lint error, raw key as UI label,
  broken YAML.
- **TESTING.md:** layers, how to run each, coverage targets, and gaps ranked by risk from measured data —
  P0: API keys + public v1 API (0% branch), public pages (9.6%), cash/finance/sales (7–13%) — with the
  first test cases to write.
- **Verified (final, committed state):** backend **1,144 passed / 0 failed**, coverage 50.2% ≥ floor;
  ruff clean; migrations from scratch clean; workflows valid against GitHub's schema; frontend tsc +
  ui-text lint + 85 tests + coverage floor + build all green.

## build 144em — Pre-launch full regression + make CI green (lint + 2 latent test bugs)

Full regression of the committed state before launch. Found that **CI could never have passed**:
the backend lint step was red and two tests errored under CI's environment.

- **Latent test bug (inventory integrity):** `test_convert_estimate_stock_movement` and
  `test_transfer_reverse_location` read `os.environ["TEST_DATABASE_URL"]` directly, but the
  conftest contract makes that var optional (falls back to `DATABASE_URL`) and CI only sets
  `DATABASE_URL` → KeyError *before* their key assertion (estimate-conversion logs a NEGATIVE stock
  movement), so that guarantee never ran in CI. Now honor the fallback; pass serially + under xdist.
- **Lint (CI-exact, pinned `ruff==0.6.9`): 140 errors → 0.** 129/140 were pre-existing in untouched
  test files. Fixed by risk class:
  - 75× I001 import order — auto-fixed with *safe* fixes only (no `--unsafe-fixes`).
  - 5 real-signal fixes by hand: **E711** `Part.org_id != None` → `.isnot(None)` (ruff's suggested
    `is not None` would have silently made the SQLAlchemy filter a constant `True`); **B017**
    `pytest.raises(Exception)` → `IntegrityError` (the old form passed on *any* error); **3× F841**
    kept the side-effecting POST calls and renamed the names to intentional `_`/`_oid`/`_tid`.
  - 60× E702/E741 (semicolons, a loop var named `l`) — cosmetic, test-only, can't hide a bug →
    added to the existing `backend/tests/*` per-file-ignores (alongside F401/F811). Real-signal
    rules stay enforced.
- **Proof of no behavior change:** OpenAPI surface compared before/after — **802 operations,
  identical**; app boots; `main.py` diff is only alphabetizing names inside one import statement
  (route registration order lives in the separate `include_router` list).
- **Verified (CI-equivalent):** backend **1,141 passed / 0 failed** (4 parallel chunks); ruff 0.6.9
  "All checks passed"; `alembic upgrade head` clean from scratch, single head `c8e0g2i4k6m8`;
  frontend `lint:uitext` clean, `tsc` clean, Vitest 85/85.

## build 144el — UI-quality test harness: content lint + status-name guard + Playwright E2E

Added the test layers that catch *appearance* and *rendered text* — things Vitest/pytest (which
check logic + data) fundamentally can't see. Motivated by the "underscore in a label?" question.

- **Content lint** `scripts/check-ui-text.mjs` (`npm run lint:uitext`, wired into CI): scans
  `src/**/*.tsx` for raw `snake_case` sitting in literal JSX text (a key leaking as a label, e.g.
  `>waiting_parts<`). Dependency-free, deterministic, near-zero false positives (ignores
  `className`/attrs/imports/`{expr}`). Self-tested to fire on a planted leak; repo is clean (0).
- **Status-name guard** `test_seeded_status_names_human.py`: every seeded status NAME across all
  six pipelines (38) must be a human label — no underscore, not equal to its key, Title-cased.
  Locks the "Dropped off / key-as-label" class forever.
- **Playwright E2E scaffold** (`e2e/`, `npm run e2e`): per main page it takes a screenshot for a
  **visual-regression baseline** (catches misaligned boxes / spacing drift) AND asserts no visible
  non-mono label renders an underscore. Runs against a live app (`BASE_URL`, `E2E_EMAIL`/
  `E2E_PASSWORD`); intentionally NOT in `package.json`/CI so `npm ci` stays lean — `e2e/README.md`
  covers install + first-run `--update-snapshots`.
- **Note:** the gray mono status-key badge in Settings (`waiting_parts`, etc.) is intentional — it's
  the slug you reference in automation triggers, deliberately styled as a key, and excluded from
  both the lint and the E2E underscore check.
- **Verified:** `lint:uitext` clean; backend status tests 40 passed; `npm test` 85 passed; `tsc` +
  build clean.
- **Live sweep (Chrome, 127.0.0.1:8000):** ran the E2E underscore scan by hand across all 9 main
  pages (home, work orders, appointments, inventory, invoices, clients, reports, timesheets,
  settings) — **0 underscored labels**. The one hit, `calendar_month`, was a Material Symbols
  icon ligature (renders as a glyph), so the E2E spec's scan was hardened to exclude the icon
  font and mono key badges — otherwise it would false-positive on every icon.

## build 144ek — Fix misleading default WO status "Dropped off" → "Not collected"

The default Work Order status named **"Dropped off"** sits in the terminal `dropped` group —
the cancelled/declined/abandoned bucket (declining an estimate moves there; it opens the
payment window, counts as closed in reports/insights, is excluded from overdue, and releases
reserved stock). But "Dropped off" reads like *intake* (a customer dropping a device off to
start a repair), so an owner could set a fresh device to it and silently mark the order
terminal — paid-window opened, hidden from active work, parts released, counted as closed.

- **Seed:** renamed the default to **"Not collected"** (`statuses.py` DEFAULT_STATUSES) — same
  key `dropped` and terminal group, just a name that can't be mistaken for intake.
- **Migration `c8e0g2i4k6m8`** (chains off `b7d9f1h3j5l7`, single head): data-only rename,
  deliberately narrow — only rows still at the exact old default (`key='dropped' AND
  name='Dropped off'`), so any org that customized the name is untouched. Reversible.
- **Test:** `test_144ek_dropped_status_name` asserts the terminal `dropped` default is
  "Not collected" (not "Dropped off") and stays in `TERMINAL_GROUPS`; the only intake default
  is `new`.
- **Verified:** `alembic upgrade head` clean on a fresh DB, single head; status tests pass (8).

## build 144ej — Markup/styling audit (app-wide)

Swept the frontend for common markup mistakes. The app came back clean; one real a11y gap fixed.

- **Fixed:** the only truly icon-only button without an accessible name — the "Mark up photo"
  close button in `PhotoAnnotator` (`<M n="close"/>` with no text) now has `aria-label="Close"`.
- **Checked clean:** no `class=` in JSX (the `class=` hits are all inside HTML template strings
  for print output / contenteditable token pills — correct); no `<img>` missing `alt`; no `.map`
  rendering elements without a `key`; no conflicting Tailwind size/rounded classes; no
  `target="_blank"` missing `rel="noopener"`; no duplicate literal DOM `id`s.
- **Not violations (left as-is):** 84 `onClick` on `<div>/<span>` are all modal backdrops
  (`fixed inset-0 onClick={onClose}`) and `stopPropagation` wrappers — these shouldn't be
  keyboard-focusable; the actual clickable controls (KPI cards, checklist rows, sortable table
  headers) already carry `role`+`tabIndex`+`onKeyDown`.
- `tsc` + `vite build` clean.

## build 144ei — App-wide bug sweep: fix timezone date defaults

Deep app-wide audit for several mistake classes. Grammar ("a/an") and API wiring came back
clean; the real class was **timezone-shifted date/time defaults**.

- **The bug:** several screens built "today" / "now" with `new Date().toISOString().slice(...)`,
  which is **UTC**. For a user in any zone ahead of/behind UTC (e.g. IST), the default date could
  be off by a day (late evening → next/previous day) and a new time entry's clock defaulted hours
  off. The reports module already had a local `iso()` helper for exactly this reason.
- **Fix:** added shared `lib/localDate.ts` (`isoDate` / `isoMonth` / `isoDateTimeLocal`, built from
  LOCAL date components) with tests, and replaced every UTC-based default:
  - Timesheets — `weekAgo()`, `today()`, new time-entry `started_at` (datetime-local), new
    shift `day`.
  - Finance — current-month default.
  - Clients / Inventory — CSV export filename dates (cosmetic, fixed for consistency).
- **Verified not bugs (left as-is):** `new Date(localValue).toISOString()` when *sending an
  instant* to the backend (Timesheets entry, Appointments reschedule, gift-card expiry) is correct;
  `.toISOString()` used as a React `key` in the WO calendar is fine. API endpoint diff found no
  dead/404 calls (12 initial flags were all dynamic `{id}` routes that exist). No new grammar
  artifacts. The main-view chart isn't affected by the numeric-group-key bug (catalog reports
  always have a text label column).
- **Tests/gates:** `localDate.test.ts` (4); `npm test` **85 passed / 14 files**; `tsc` + build clean.

## build 144eh — Custom report builder: tidy the Group by / Aggregates / Filters row

The controls row looked off — ragged select widths (Group by stretched wide while Count was
tiny), the two sections floating at different baselines, a bare × remove, and a harsh focus ring.

- **Group by + Aggregates** now live in one light "shape the output" panel (rounded slate card),
  with the two labels aligned on a shared baseline, "+ Add" aligned to the Aggregates header, and
  consistent control sizing (`py-1.5`, `min-w` on every select so they don't jump around).
- **Remove buttons** (filters + aggregates) are proper rounded hit-targets with a rose hover,
  not a bare glyph.
- **Filters row** got the same consistent select widths/padding for visual rhythm.
- No logic changed — markup/styling only. `tsc` + `vite build` clean; verified live in Chrome.

## build 144eg — Fix chart regression when grouping by a numeric field

Caught from a screenshot: Group by **Discount** (a money field) + Count. Valid config, but it
exposed a regression from the 144ed #7 change — the custom-report chart picked the *first*
numeric column as its value, which for a numeric group key is the group key itself, so the
chart plotted Discount against Discount instead of the count.

- **Fix:** extracted `pickChartColumns(columns, rows)` — chooses the label column first, then a
  numeric value column that is **not** the label, so the value is always the aggregate even when
  the group key is numeric. `CustomView` now uses it.
- **Tests:** 5 cases in `reportConfig.test.ts`, including the exact Discount+count scenario
  (value must be count, not the group key), text group key, flat mode, no-numeric, and empty rows.
- **Scope:** only the custom-report chart was reachable with a numeric group key; the catalog-report
  chart (always a text label + explicit column picker) is unaffected and unchanged.
- **Verified:** `npm test` **81 passed / 13 files**; `tsc` + `vite build` clean.

## build 144ef — Custom report builder: close QA review findings A/B/C/D

Follow-up to 144ee after the senior-architect-QA pass; closes the remaining findings, TDD.

- **A — backward-compat regression test:** a pre-144ee saved *grouped* report still carries a
  stale `columns` list in its config. Added `test_grouped_run_ignores_stale_columns` proving the
  grouped run returns exactly `[group_key, agg]` (not the 6 stale columns) — locks the contract
  against a future refactor re-honoring columns.
- **B — UI render test:** `CustomView.test.tsx` renders the builder (api mocked), picks a source
  (Columns picker + chips show), then sets a Group by and asserts the Columns picker disappears and
  the hint appears. Guards the UI half of the fix that the config unit test can't see.
- **C — date filter validation:** `_coerce` now parses `date`-type filter values, so a malformed
  date (`13/09/2026`) is caught and returns the friendly field-named message instead of leaking an
  unparsed string into the SQL comparison. Good dates run normally. Test added.
- **D — empty-columns hint:** in flat mode with no columns selected, the builder now shows
  "No columns selected — the report will show all of them." (matches the backend's all-fields
  fallback), removing the "cleared everything but got everything" surprise.
- **Verified:** backend custom-report suite **10 passed**; `npm test` **76 passed / 13 files**;
  `tsc` + `vite build` clean.

## build 144ee — Custom report builder: fix columns-vs-group-by contradiction (code-review #1–3, 5, 7)

TDD fixes for the issues the code review surfaced in the custom report builder.

- **#1–3 Columns vs Group by (the real bug):** when a Group by was set, the COLUMNS picker
  still showed all fields selected, but the backend ignores `columns` in grouped mode — so the
  chips were dead UI and saved grouped specs carried a meaningless column list. Extracted a pure
  `reportConfig.ts#buildReportConfig` (unit-tested) that **omits `columns` entirely when grouping**,
  and the builder now **hides the COLUMNS section under an active Group by**, replacing it with a
  hint: "Columns are set by Group by + Aggregates below." Flat (ungrouped) mode is unchanged.
- **#5 Friendly validation errors** (`services/custom_reports.py`): a non-numeric filter value no
  longer surfaces a raw "could not convert string to float" — it's now "'abc' is not a valid value
  for Total.", and a malformed date range gives "Invalid date in the date range (expected YYYY-MM-DD)."
  Both still map to 422, never a 500.
- **#7 Chart column detection** (CustomView): the chart picks its numeric/label columns by actual
  cell value type instead of the `align` styling flag.
- **Tests (RED→GREEN):** `reportConfig.test.ts` (5 — flat keeps columns, grouped omits them,
  aggregate mapping, filter pruning) and 3 new backend tests (friendly numeric/date messages +
  422-not-500). Backend isolation/whitelist suite still green.
- **Verified:** `npm test` **75 passed / 12 files**; backend custom-report + daterange **10 passed**;
  `tsc` + `vite build` clean; live in Chrome — the saved "Invoices by status" grouped report now
  shows the hint, no dead column chips.

## build 144ed — Reports UI/UX polish (richer, no data changes)

Visual polish pass on the Reports area — same real data and behavior, richer presentation.

- **KPI cards** (Reports + Business & analytics): larger `text-2xl` values, icon in a rounded
  tinted tile, a gradient accent strip along the bottom (per-metric color), hover lift +
  reveal arrow on the clickable ones. Fleet cards gained icons and skeleton placeholders while
  insights load.
- **BarChart**: gradient fills (primary ramp up, rose for negatives), rounded animated bars
  (`transition-[width]` on mount), inset track ring, leading rank number, signed value chips.
- **Tabs**: a Material icon per tab (summarize/tune/insights/payments/lightbulb/reviews),
  hover affordance on inactive tabs.
- **Tables**: zebra striping + smoother hover across all report tables, negative numbers in
  rose, active sort column highlighted, a skeleton table while a report runs (replacing the
  plain "Running report…" line), and iconed empty states (bar_chart / search_off / insert_chart).
- **Catalog sidebar**: active report gets a left accent border + colored icon.
- **Verified:** `tsc --noEmit` clean; `vite build` clean; live in Chrome (127.0.0.1:8000) —
  Reports and Business & analytics both render the richer cards/tabs/tables correctly on real data.

## build 144ec — Reports tech-debt audit (authenticity + connectivity) + spelling fix

Audited the whole Reports area (frontend `ReportsHub` + backend `reports.py`, `analytics.py`,
`insights.py`, `custom_reports.py`) for data authenticity, dead wiring, and text leaks.

- **Authenticity (the QuickBooks bar): PASS.** All 23 catalog reports + dashboard,
  job-profitability, cash-flow, total-money, and fleet/analytics are backed by **real,
  org-scoped DB queries** (orders, invoices, transactions, time entries, stock movements,
  employee ledgers). No stubs, mock/sample/demo data, hardcoded figures, or `random`
  anywhere; every query filters `org_id` (tenant isolation holds); money/profit computed
  from actual ledger rows.
- **Connectivity: PASS.** Verified the two endpoints that looked unwired are registered —
  `/api/reports/insights/*` (insights router) and `/api/feedback/list` (feedback router).
  Nothing silently 404s to an empty view.
- **Fix — spelling leak:** the Reports area mixed American "Labor" (tab + 3 of 4 titles +
  "Labor cost" columns) with British "Labour efficiency" (title) and "Billed labour"
  (column). Aligned the two outliers to American to match the local Reports convention:
  title → "Labor efficiency (booked vs actual)", column → "Billed labor". Left code
  identifiers, JSON response keys (`labour_cost`), DB enum values (`line_type="labour"`),
  and the `services/labour.py` module untouched — those are contract/internal, not display.
  (App-wide British-vs-American standardization is a separate house-style decision, deferred.)
- **Verified:** reports/labour/insights/custom-report/payroll/inventory test suites pass
  (43/43); no test asserted the changed display strings.

## build 144eb — Grammar + API-consistency sweep after the Appointments rename

App-wide pass for artifacts left by the blanket Bookings → Appointments rename.

- **Grammar ("a appointment" → "an appointment"):** fixed everywhere — including two
  **user-facing API error strings** in `appointments.py` ("That employee already has an
  appointment …", "That slot clashes with an appointment …"), plus comments/docstrings in
  `appointments.py`, `orders.py`, and three tests. Zero "a appointment" left in the repo.
- **API consistency:** confirmed the only remaining `booking` route in backend code is the
  intentional public 308 back-compat alias (`include_in_schema=False`); no admin endpoint,
  schema, or router prefix still says `booking`. No reverse "an booking" artifacts.
- **Stale docstring fixed:** `test_144dx_appointments_rename.py` header still claimed "no
  backward-compat alias" though the public 308 redirect was added later — rewritten to state
  the real split (admin API hard-404s, public path redirects).
- **Test filenames renamed** for consistency (no cross-imports, pytest discovers by glob):
  `test_144ek_service_booking_rules` → `…_service_appointment_rules`, `test_144p2_booking_convert`
  → `…_appointment_convert`, `test_144dr_invoice_booking` → `…_invoice_appointment`,
  `test_144iy_booking2` → `…_appointment2`, `test_build144bs_booking_notify` →
  `…_appointment_notify`. No `*booking*.py` test files remain.
- **Verified:** affected backend tests pass (17/17 grammar set, 17/17 renamed set); no test
  asserts the changed wording; `vite build` clean.

## build 144ea — Expand frontend test coverage to core logic + wire frontend into CI (34 → 70 tests)

Built on the 144dz harness: added unit + render tests for the highest-value logic across the
app, prioritising the regression class that bit us (permission gating) and the shared
helpers many screens depend on — then wired the whole frontend gate into CI so it runs on
every push/PR, not just locally.

- **CI:** new `frontend` job in `.github/workflows/ci.yml` (Node 20, `npm ci`) running
  `tsc --noEmit` → `npm test` → `npm run build`. The React side now has the same
  can't-merge-red gate the Python side has had.
- **`Gate.test.tsx`** (3) — integration of the real `MeProvider → MeContext → Gate` wiring
  with `/auth/me` mocked: owner sees gated content, an explicitly-denied non-owner has it
  removed after boot, a non-owner without the key still sees it (the 144dy default-allow guard,
  now proven end-to-end at the component level, not just in `canPerm`).

- **`canPerm` extracted** from `Me.tsx` into a pure, exported function (the provider's
  `can()` now just calls it), so the deny-wins / default-allow / owner rule is testable
  without mounting the provider. `Me.permissions.test.ts` (6) pins it: no code → allow,
  pre-boot (me null) → allow, owner always → allow (even on an explicit `false`),
  **default-allow for a non-owner with a missing/true perm** (the exact 144dy bug),
  deny only on explicit `false`, absent permissions object → still allowed.
- **`humanize.test.ts`** (5) — enum→label (`in_use`→"In Use"), mixed/repeated separators,
  trim, idempotence, nullish → "".
- **`assetIdentifiers.test.ts`** (8) — template-driven identifier fields: serial fallback
  when none/empty/all-unrenderable, template order preserved, non-Device kinds dropped
  (barcode/asset_tag/engine_serial), `primary` pulled to front only when allowed.
- **`assetNoun.test.ts`** (5) — a record stamped with a real template keeps its noun;
  `general`/default/unstamped records follow the org's current noun.
- **`userPref.test.ts`** (9, api mocked) — `cachedPref` (fallback, parse, corrupt-JSON
  safe), `savePref` write-through + swallows server errors, `loadPref` prefers non-empty
  server value / falls back to cache on empty or error.
- **Verified:** `npm test` → **70 passed / 11 files**; `tsc --noEmit` clean; `vite build` clean.
- **Live (Chrome, 127.0.0.1:8000):** Settings redesign shows all 19 sections grouped (no
  vanished sections — 144dy regression confirmed fixed), search filters ("invoice" →
  Statuses/Finance & numbering/Document templates). Appointments rename live: `/appointments`
  loads, `/api/bookings` → 404 "Not Found" while `/api/appointments` exists (auth-gated), and
  the old `/api/public/booking/{slug}/slots?…` 308-redirects to `/api/public/appointment/…`
  with path + query preserved (old public links still work). Fixed a grammar artifact from the
  blanket rename — "Create a appointment" → "Create an appointment" (3 strings in AppointmentsHub).

## build 144dz — Frontend test harness (Vitest + RTL) + Settings registry extracted

The frontend had **no test runner** — `tsc`/`build` only catch compile errors, not logic
bugs (which is how the 144dy `is_owner` regression slipped through). Added **Vitest +
React Testing Library** so the React side gets the safety net the Python side has (pytest).

- **Harness:** Vitest config (jsdom, globals, `src/test/setup.ts` with jest-dom matchers),
  `npm test` / `npm run test:watch` scripts. RTL + user-event for component tests.
- **Settings registry extracted** to `settingsRegistry.ts` (one source of truth: nav + search
  index + grouping) and `SetupChecklist` to its own self-contained file — both testable in
  isolation, and SettingsHub shrinks.
- **New tests:** `settingsRegistry.test.ts` (14) — the regression-class guard: every section
  has a category (so none vanish from the grouped nav) + keywords, no empty categories, and
  `searchSections` resolves invoice→finance, reminder→notifications, add-tech→team, tax→pricing,
  vin→industry, case-insensitive, [] on gibberish. `SetupChecklist.test.tsx` (4) — renders all
  items, initial progress, toggling updates the count, "Open →" calls goSection without toggling.
- **Legacy self-check scripts converted** (`pager`, `cashStats`, `statusTone`) from
  esbuild-run `console.log("OK")` scripts into proper Vitest suites — one convention now.
- **Verified:** `npm test` → **34 passed / 5 files**; `tsc --noEmit` clean; `vite build` clean.

## build 144dy — Settings redesign: grouped, searchable, role-gated (goodbye junk drawer)

Settings was 21 flat sections in one rail (the Orderry "settings junk drawer"). Now it's
**grouped into 6 scannable categories**, **searchable**, **role-gated**, and opens on an
**Overview** with a setup checklist — simpler than Jobber/Orderry/Shopmonkey, on the real
app tokens. The 21 existing section editors are unchanged; this wraps them in a better shell.

- **One registry drives everything** (`CATS`, `SEC_CAT`, `SEC_KW`, `VISIBLE_ALL`): the
  grouped nav, the search index, *and* role visibility come from one source — no duplication,
  and it's the seam for later splitting the 3,600-line component into per-section files.
- **Grouped collapsible nav** — Workspace · Work orders · Money · Inventory · Customers & comms
  · Connections. Six headers instead of twenty-one labels.
- **Search** in the rail (command-palette style): "invoice number", "reminder", "add tech",
  "tax", "vin" jump straight to the right section; results tagged with their category.
- **No per-section role-trim** (corrected): anyone who reaches Settings already passed
  `can_manage_settings`, so **all 19 sections are shown**. An earlier build gated by
  `me.is_owner`, which hid 16 sections from the admin login (admin has blanket perms but
  `is_owner=false`) — reverted. Per-area role-trim is deferred until the RBAC catalog exposes
  per-section permission codes; the backend stays authoritative regardless.
- **Settings Overview** (new default) — intro + a "Finish setting up your shop"
  checklist (progress + per-item Open →) + category cards. Activation guidance instead of a
  cold form.
- **Verified:** `tsc --noEmit` clean; `vite build` clean; registry-integrity check PASS
  (19/19 sections categorized + keyworded, 6 categories, search smoke tests resolve
  invoice→finance, reminder→notifications, tech→team, tax→pricing, vin→industry). `co` still
  loads on mount, so the new Overview default doesn't regress General. **Live Chrome
  click-through pending** (dev server not running in this environment).

## build 144dx — Rename "Bookings" → "Appointments" (app-wide: UI, API, schema, DB)

"Bookings" is renamed to **Appointments** everywhere — clearer for US/EU/ME repair shops
(US/service SaaS standard), and it fixes the Spanish label (Reservas → **Citas**, which read
as a hospitality *reservation*; Arabic → **مواعيد**).

**Customer links are safe.** The public page URL is `/book/{slug}` (served by `main.py`,
rendered by `main.tsx`'s `startsWith("/book/")`) — "book" has no "booking", so the rename
never touched it; saved links, QR codes and email links keep working. The internal API path
`/api/public/booking/*` → `/api/public/appointment/*` changed, and a **308 back-compat alias**
(`_booking_api_compat`, path + query + method/body preserved) keeps any direct API consumer or
stale cached bundle working. The admin API (`/api/bookings` → `/api/appointments`) is
internal-only and intentionally has **no alias**.

- **Backend:** `Booking` model → `Appointment` (table `bookings` → `appointments`); router
  `bookings.py` → `appointments.py` (`/api/bookings` → `/api/appointments`,
  `/api/public/booking` → `/api/public/appointment`); 7 `Org.booking_*` columns →
  `appointment_*`; partial-unique index `uq_booking_emp_slot` → `uq_appointment_emp_slot`;
  automation/notification-rule module/event values (`module='booking'`, `event` containing
  `booking`) renamed. 30 source files updated; historical migrations untouched.
- **Migration `b7d9f1h3j5l7`** (chains off `hr144dv01`, single head): `rename_table`, Org
  column renames via `batch_alter_table`, index recreate, and data UPDATEs for stored rule
  values — reversible. No data loss (pure rename).
- **Frontend:** `BookingsHub` → `AppointmentsHub` (page dir renamed), `PublicBooking` →
  `PublicAppointment`, route `/bookings` → `/appointments`, nav label + i18n (en
  "Appointments", es "Citas", ar "مواعيد"), Settings landing/automation labels, public page
  copy ("Confirm appointment", "No appointments yet", etc.). 8 files updated.
- **Verified:** **full backend suite 1,094 passed / 0 failed** (6 chunks, `-n 4`);
  `tsc --noEmit` clean; `vite build` clean; **single Alembic head**; **migration applied
  end-to-end on a fresh DB** (`alembic upgrade head` → table/columns/index confirmed
  renamed). New regression test `test_144dx_appointments_rename` proves `/api/appointments`
  + `/api/public/appointment` work while `/api/bookings` + `/api/public/booking` 404 as
  unknown routes. (The `status` enum value `'booked'` is unchanged — it is not the term
  "booking".) Migration data-step was corrected: `notification_rules` has `module`,
  `automation_rules` has only `event` — each updated on the right column.

## build 144dw — Spanish nav section headers + full-suite verification

The redesigned shell's five section headers and chrome strings were English-only under Español. Added
Spanish (and Arabic where missing) translations so the whole sidebar localises:

- **i18n:** `Front desk → Recepción`, `Workshop → Taller`, `Commerce → Comercio`, `Grow → Crecimiento`,
  `Admin → Administración`, plus `Home → Inicio`, `My work → Mi trabajo`, `Catalog → Catálogo`,
  `Collapse → Contraer`, light/dark mode, `Clock in → Fichar entrada`, `Security & two-factor`.
  Shell.tsx already renders `{t(group.name)}`, so no component change was needed.
- **Verified live** in Chrome (es): sidebar renders RECEPCIÓN / TALLER / COMERCIO / CRECIMIENTO /
  ADMINISTRACIÓN with items Inicio, Consultas, Reservas, Chats, Contactos, Órdenes de trabajo, Mi
  trabajo, Timesheets, Tareas, Ventas, Facturas, Catálogo, Inventario, Finanzas, Marketing, Informes,
  Ajustes, Contraer. Browser restored to English afterward.
- **Connectivity sweep:** full backend suite green — **1091 passed, 0 failures** (5 chunks, `-n 4`);
  single alembic head `hr144dv01`; pager logic test green; region-hint (`en`/`es` default, no IP
  tracking) green; no `en-IN` regressions; `tsc --noEmit` + `vite build` clean. Everything built this
  cycle (booking cache, reports pagination, qty guard, terminology sweep, shell redesign, Home+Catalog,
  clickable KPIs, home-feed endpoint + role-aware filtering, Timesheets fold, collapsible feed, feed
  action buttons, role-redirect) verified wired end to end.

## build 144dv — Role-redirect: each role lands on its own page

Opening the app no longer drops everyone on Home — the owner sets a landing page per role, and `/`
redirects there. A technician can start in My work, front desk in Inquiries; the owner stays on Home.

- **Model:** `Role.home_route` (nullable string route; null/blank = Home). Migration `hr144dv01`
  (`op.batch_alter_table` add column). Applied to dev DB; single head.
- **Wiring:** `/auth/me` surfaces the role's `home_route`; `rbac` RoleIn/_ser_role carry it (persisted in
  `_apply_scope`); the Home page redirects `/` → `home_route` on mount — loop-safe (only redirects to a
  non-"/" route, once per mount, with `replace`). Owners (no role) are unaffected.
- **Role editor UI:** a "Landing page — where this role starts" picker in the role drawer's Data access
  tab (Home / My work / Work orders / Inquiries / Bookings / Invoices / Inventory / Reports).

Tests `test_144dv_role_home_route` (2: round-trips through rbac + shows on `/me`; blank = Home). 12-test
batch (redirect wiring + roles + rbac) green; `tsc` + build clean; the picker live-verified in Chrome.

## build 144dt — Home "needs you" feed is collapsible (count never hides)

The feed can now be collapsed, but it never hides the fact that something's waiting:

- Header is a toggle — `⌄ Needs you  [N]` with a red count badge that stays visible collapsed or
  expanded, so you always know there are N things even when the rows are tucked away.
- **Defaults to open when there are items**, and to the "all caught up" line when there aren't; the
  user's manual choice persists in `localStorage` (`ros_feed_open`).
- Long feeds are **capped at 5** with a "Show N more" / "Show less" toggle, so a busy shop doesn't get
  a wall of rows.

Keyboard/ARIA: the header carries `aria-expanded`. `tsc` + `vite build` clean; live-verified expand ⇄
collapse with the count persisting.

## build 144du — Feed rows get an action button

Each "needs you" row now carries a labelled, colour-matched action on the right that taps straight to
where you complete it: Overdue → Reassign, Messages → Reply, Ready → Notify, Urgent → Review,
Unpaid → Remind, Low stock → Reorder, Tasks → Open, Inquiries → Triage. The button and the row title
both navigate to that signal's destination (the filtered list or the record), reusing the same
`navigate(to)` wiring. Live-verified: Reorder → Inventory stock list.

Honest scope: today the button *routes you to the place to act* (one tap to the exact screen), not an
inline mutate — true one-tap-complete is only unambiguous for single-record signals, and that refinement
(single-count acts directly, multi-count routes) can layer on next. No backend change; `tsc` + build clean.

## build 144ds — Home feed is role-shaped (owner decides in the roles section)

The owner now controls, per role, what the Home "needs you" feed shows — and the feed scopes to a
person's own work when their role is own-scoped.

- **Model:** `Role.home_feed_sources` (JSON list of signal keys; null/empty = all). Migration
  `hf144ds01` (`op.batch_alter_table` add column). Applied to dev DB; single head.
- **Feed endpoint** (`/reports/home-feed`) is now role-aware: it loads the caller's role and
  (1) if `order_scope == "own"`, limits the order/task signals (overdue, messages, ready, urgent,
  tasks) to records assigned to that person; (2) if `home_feed_sources` is set, only those signals
  appear. Owner (all-scope, no source list) still sees everything.
- **rbac:** `RoleIn` / `_ser_role` carry `home_feed_sources`; persisted in `_apply_scope` (create + edit).
- **Role editor UI:** a "Home feed — what this role sees" section in the role drawer's Data access tab,
  with a checkbox per signal (unticked all = show everything). Live-verified the toggles render + save.

**QA / bug fix:** swept for the `OrderStatus.status` vs `.key` bug class — none elsewhere; the feed fix
from 144dr holds. Tests `test_144ds_home_feed_roles` (2: source filter limits items; own-scope hides an
unassigned overdue). 31-test gate (feed + roles + rbac + authz + work orders) green; `tsc` + build clean.

This completes the role-aware feed (Layer 1 + owner control). The optional AI ranking/phrasing (Layer 2)
can still layer on top later.

## build 144dr — Home "needs you" feed (real signals) + Employee hours → Timesheets

**Home feed (Layer 1, no AI, fully wired).** New `GET /api/reports/home-feed` assembles a ranked,
org-scoped "needs you" list from real queries — overdue work orders, waiting customer messages,
ready-for-pickup, open urgent orders, unpaid invoices, low/out-of-stock, open tasks, and inquiries
awaiting response. Each item carries a count and a deep link (reusing the drill-through pattern), so
the item navigates to the filtered list. Returns `ok:true` when the shop is caught up (friendly empty
state). Deterministic; the AI-ranked/phrased layer can sit on top later. Rendered on Home above the KPI
grid. Tests `test_144dr_home_feed` (3: empty→ok, overdue surfaces, overdue count accuracy incl. a
seeded status to exercise the status-group path). Live-verified: feed showed "1 urgent order open · 11
items low/out of stock · 1 open task", and clicking an item navigated correctly.

Fixed before ship: the status-group mapping used `OrderStatus.status`; the field is `.key`. Caught in
live verify (the test tenant had no custom statuses — a blind spot now covered by the test).

**Employee hours → Timesheets.** Moved the Employee-hours view out of Home into Timesheets as a tab
(`Timesheet | Employee hours`), reusing the exact `EmployeeHours` component. Home is now a single
focused overview (no sub-tabs). Live-verified both tabs render.

`tsc` + `vite build` clean; 31-test regression batch (feed, inventory reports, bay, work orders, qty
guard, cache headers) green.

## build 144dq — Home KPI cards drill through to their source

The Home "Overview" KPI cards were dead numbers. Now every card is a link to the filtered list
behind it — "numbers are navigable," a pattern worth spreading app-wide.

- Work orders today → Work orders · Revenue/Refunds/Money in accounts → Finance ·
  New → Work orders filtered to **New** · Urgent → Work orders filtered to **priority: urgent** ·
  Overdue → Work orders · Receivables → Invoices.
- Work orders now honours `?status=` and `?priority=` deep-link params (applied once on load, then
  cleared so the URL stays clean and the user can re-filter freely) — reusing the existing `?open=`
  pattern. No new endpoint, no backend change.
- Cards are keyboard-accessible (role=button, Enter/Space) with hover affordance; non-linked cards
  are unchanged.

Live-verified in Chrome: "New" → New(8) filter, "Urgent" → priority filter (1 order). `tsc` + build clean.

**Reports KPIs too (same pattern):** the five Reports summary cards now drill through — Total/Open
orders → Work orders, Clients → Contacts, Revenue → Finance, Outstanding → Invoices. Same
clickable-card treatment (keyboard-accessible, hover). Live-verified: Clients → Contacts, orders →
Work orders. `tsc` + build clean.

**Finance KPIs too:** Finance's five Overview cards drill into the tab that explains each — Total cash →
Cashboxes (or Client balances when cashbox is hidden by permission), Revenue/Expenses/Gross profit →
P&L, Outstanding → Invoices. Here the "drill-through" is an in-page tab switch (`setTab`) rather than a
route, since Finance's detail lives in tabs. Live-verified: Outstanding → Invoices tab. `tsc` + build clean.

## build 144dp — Retire the "My company" junk-drawer: split into Home + Catalog

"My company" bundled five unrelated things under one nav item (Dashboard, Employee hours,
Services, Products, Job Kits) — the same hub-of-everything shape as Orderry. Split it by domain;
no backend, schema, or endpoint changed, so every feature is byte-identical — the exact same
components just render in cleaner homes.

- **Home** (`/`, renamed from "My company") now shows only **Dashboard + Employee hours** — a focused
  company overview, not a hub. Inner heading "My company" → "Overview".
- **Catalog** (new `/catalog`, nav item under Commerce) hosts **Services / Products / Job Kits**,
  reusing the exact `Services`/`Products`/`Bundles` components (now exported from CompanyHub) and the
  same `InventoryDrawer` edit flow. The catalog editors finally have their own home instead of being
  buried as sub-tabs.

No routes break: `/` still resolves (Home); the catalog tabs were never separate routes, so there are
no stale deep links. ⌘K picks up `/catalog` automatically (it iterates the nav). Permissions unchanged
(both were open).

**QA (senior-architect-qa self-review):** no API/data change → zero backend risk; components reused
verbatim → features preserved (live-verified in Chrome: Catalog list, pagination, categories, and the
Services edit drawer all work, drawer breadcrumb now reads "Catalog › Services"); `tsc` + `vite build`
clean; no orphaned imports.

Note: Employee hours stays on Home for now rather than folding into Timesheets — that move would mean
restructuring the Timesheets page into tabs, which is a larger, separate change; kept out to keep this
low-risk. Say the word and I'll do it next.

## build 144do — Shell redesign D: region auto-suggest at sign-up + language picker (E already shipped)

**D — region hint + per-user language.**
- `GET /auth/signup-options` now returns a `region_hint` derived from the CDN country header
  (`CF-IPCountry` / `X-Vercel-IP-Country` / `X-Country`) — a convenience default only. No raw IP
  is read or stored, and no third-party geo call is made. Country→region buckets: US→us, CA→ca,
  EU/EEA/UK/CH→eu, Gulf/MENA→mena, else other; absent/unknown → no hint (never guesses blindly).
  **Clients are not IP-tracked** — their region comes from the address/phone they provide.
- Sign-up pre-selects the region (and its currency) from the hint when present; the user can
  override. EU and Middle East still default to English per the region table.
- Settings → General gains a **"My display language"** picker (English / Español / العربية) —
  personal, per-device; Arabic flips the whole app to right-to-left.
- Tests `test_144do_region_hint` (2). 24-test auth/signup/rbac/role batch green.

**E — role × capability matrix: already present.** The Team → Roles editor is already a grouped,
searchable permission matrix with "Full access / No access" presets and role templates over
`Role.permissions`, enforced server-side by `require_perm`. The requested "every feature available,
toggled on/off per role, with presets" model was already built end-to-end — no change needed.

This completes the shell refresh (A grouped nav · B top-bar/clock · C locale+RTL · D region/language · E roles).

## build 144dn — Shell redesign C: locale layer (kills en-IN) + RTL + Arabic

Replaced the hardcoded `toLocaleString("en-IN")` (Indian digit grouping — the source of the
`21,00,00,00,00,098` display) everywhere with a locale-aware formatter driven by the user's
language: US English `2,100,000.5`, EU `2.100.000,5`, Arabic + Western fallbacks.

- `i18n.ts` now owns `getLocale()` (BCP-47 per language), `getDir()`, `applyDir()`, a `LANGS`
  list, and a third language — **Arabic (ar)** with an RTL layout. `applyDir()` sets
  `document.dir`/`lang` on load and before a language switch, so the whole shell mirrors for RTL.
- `money()` and all four Reports number formats now use the active locale, not a fixed region.
  `MeProvider.fmt()` uses the shared `getLocale()` (removed its private copy).
- The avatar Language control cycles EN → ES → AR for now; a full per-user picker lands with the
  Settings work (part D). EU/US default to English per the region table.

Verified: zero `en-IN` left; `tsc` + `vite build` clean; locale formatting spot-checked for
en-US / es-ES / ar. Part C of the shell refresh.

## build 144dm — Shell redesign B: top-bar declutter + honest clock

- **Time clock** no longer shows a dead `00:00:00`. Idle → a "▶ Clock in" pill (reads as an
  action); running → a green status chip with what's being timed + live elapsed. Same popover,
  same clock-in/out logic underneath.
- **Avatar menu** — language, light/dark, and Security & two-factor moved off the bar into a
  dropdown under the avatar (where people expect preferences). The bar now carries only search,
  the context tools (location, scan, clock per the existing flags), notifications, and the
  avatar — fewer equal-weight chips, less "cockpit."
- The amber "set up 2FA" dot now rides the avatar and its menu item; no behaviour change to the
  Security modal.

`tsc` + `vite build` clean. Part B of the shell refresh.

## build 144dl — Shell redesign A: grouped, collapsible sidebar (de-clone the nav)

The flat 16-item left rail was the clearest "Orderry clone" tell. Regrouped it into five
labeled sections — Front desk, Workshop, Commerce, Grow, Admin — with My company as the home
anchor above them. Added an icon-rail **collapse** toggle (persisted in `localStorage`) that
drops the rail to 64px with icon-only items and tooltips.

Routes, permissions, active states, and ⌘K are all unchanged: `NAV_GROUPS` is the new source
of truth and the flat `NAV` (used by the command palette and `NAV_PERM` filtering) is derived
from it, so every item still gates on its permission and an empty group hides its header.
Verified all 16 routes preserved with no duplicates. `tsc` + `vite build` clean.

Part A of the shell refresh (grouped nav → top-bar declutter → locale layer → region
auto-suggest → role matrix).

## build 144dk — Qty-input guard + template-aware terminology (no "Fleet" on a device shop)

**Bug — barcode became a quantity.** The Dead & slow stock report showed on-hand values like
`21,00,00,00,00,098` — a 13-digit EAN barcode scanned/typed into the quantity field and stored as
`qty_available`. The adjust endpoints took `qty: float` with no bound. Added `_guard_qty()` (reject
non-finite or `abs > 1,000,000`) at `/parts/{id}/adjust` (which also covers `/scan-adjust` and
`/parts/bulk-adjust`, since both route through it). A barcode-sized count now 422s with a clear message
instead of poisoning the inventory reports. Tests `test_144dk_qty_guard` (3). *Existing bad rows are
pre-existing data — correct them via the adjust UI; the guard prevents recurrence.*

**Terminology — follow the active industry template, not "Fleet/vehicle".** On a Device/Electronics
template the Reports tab still said "Fleet & analytics", "Fleet accounts", and "N assets". Wired the
existing `useTerminology()` hook (which reads the org's `industry-template` → asset noun) into the
screens that still hardcoded auto-industry words:

- Reports tab "Fleet & analytics" → "Business & analytics"; "Fleet accounts" → "Business accounts";
  "No company (fleet) accounts yet." → "No business accounts yet."; "{n} assets" → "{n} {assets}"
  (vehicles / devices / vessels … per template).
- Inventory assets empty state and the conversion picker: "No devices or assets registered." /
  "Select asset…" / "Device" fallback → the template's asset noun.
- Client drawer: "Registered assets / devices on file", "No devices on file.", the Assets tab empty
  state, and brand/model fallbacks → the template's asset noun.

Record-level labels were already template-aware (each asset keeps the noun it was created under via
`assetNoun()`); this pass fixes the remaining hardcoded app *chrome*. Legitimate industry-list and
custom-field-definition labels in Settings (which must name each industry explicitly) were left as-is.

Gates: `tsc --noEmit` clean; `vite build` clean; inventory/qty tests green.

## build 144dj — Reports stop doom-scrolling (pagination + sticky header)

Every report used one shared renderer that stacked a chart and then the **entire unbounded
table** in a single scroll — a 300-row report was a 300-row scroll, in every report type.
Brainstormed + council-reviewed; shipped the systemic fix (Option A: paginate, don't virtualize).

- **Main Reports table** (all preset reports — Stock, Dead & slow stock, Labour, etc.): paginated
  at 50/page with the shared `Pager`, and a **sticky column header** inside a `max-h-[62vh]` scroll
  box. Totals row and the chart still compute from the full filtered set — sums and the chart never
  change by page. Page resets to 1 on report switch, search, and re-sort.
- **Job profitability** and **Custom report** tables: same `Pager` at 50/page + sticky header, so the
  fix is consistent across report types, not just one screen.
- Excel/CSV export is untouched — it still exports the full filtered set, not the visible page.

Why pagination over virtualization: it reuses the already-tested `Pager`/`pageNumbers()` (builds
144t–144v), matches every other table in the app, and bounds each report to one screen with zero new
deps. Virtualization only matters if a single page needs thousands of rows, which pagination removes.

Gates: `tsc --noEmit` clean; `vite build` clean; `pager.ts` logic test green. (Chart already had a
Hide/Show toggle, so "collapse chart" is user-controlled; default unchanged to avoid surprising edits.)

Noted separately (not fixed here): On-hand values rendering as `21,00,00,00,00,098` is a data/format
issue in the stock numbers, not layout.

## build 144di — Booking caching policy, multi-tenant safe (framework-native, $0)

Caches only what is low-churn and tenant-isolated, using what the stack already gives us (HTTP
`Cache-Control`) — no new infrastructure. Decided with a council and brainstorm: because the
booking *availability* reflects the **whole shop** (any booking anywhere changes it) it must be
live, not served stale to other visitors.

**Cached — low-churn, URL-keyed, tenant-unique:**
- `GET /api/public/booking/{slug}` — page bootstrap runs **4 DB queries** (services, categories,
  staff, locations) per open; branding/services change rarely. → `Cache-Control: public, max-age=60`.

**Never cached — whole-shop live availability:**
- `GET /api/public/booking/{slug}/slots` and `/days` → `Cache-Control: no-store`. A booking made
  anywhere in the shop must show up immediately for every visitor; stale would hide a freed/taken
  slot. Cheap indexed reads after the 144dh indexes, so live is affordable.

**Tenant isolation (why nothing can clash or leak):** the booking URL carries the shop's `slug`
(`Organisation.subdomain`, `unique=True`; falls back to the unique org UUID). HTTP caches key on the
full URL, so the cache is partitioned by slug automatically — one shop's page can never be served for
another. A shop may rename its slug; it stays unique (DB constraint), and the URL-keyed cache needs no
cleanup (old URL simply stops being requested; the 60s TTL also bounds any slug-reuse window).

**Still not cached:** `track/{token}` (live, secret-token keyed); AI calls (inputs unique per request);
FX/currency (rates in DB, no per-request external call).

**No Redis / no added cost.** Nothing needs a cross-instance shared cache. If a high-volume shop's
`/days` ever gets slow, the upgrade path is a Redis key **prefixed with org_id** plus an isolation test
— not before. Per the no-shared-key rule, nothing user-specific or authenticated is cached.

Tests `test_144di_public_cache_headers` (4: config 60s; slots+days no-store; track uncached; two shops
get distinct unique slugs that never cross, and a duplicate slug is rejected). 26-test booking/public
batch green.

## build 144dh — Composite indexes for the hot list queries (scale-out, behavior-identical)

Readied the database for a lot more data and users without changing what any screen shows. The DB is
SQLite in dev (so `pg_stat_statements` isn't available — the hot queries were found by reading the
routers) and Postgres in prod; the migration is dialect-aware.

**Method.** Benchmarked the main-screen read queries on a 60k-order / 30k-client / 30k-part / 60k-txn
seed on the real schema. For each read query, `EXPLAIN QUERY PLAN` showed whether it scanned the whole
table (read-only — no `EXPLAIN` on any write query). Added indexes **only** on the columns those queries
use in WHERE / ORDER BY, as composites where a query filters+sorts on several columns together. The
single-column FK indexes these queries also touch (`order_id` on lines/comments/status_logs/invoices,
`account_id` on transactions) already existed, so they were **not** re-added — no "just in case" indexes.

**Six indexes added** (migration `ix144dh01`, also declared on the models so `create_all`/tests match):

| index | table(cols) | query it speeds up |
|---|---|---|
| `ix_orders_org_created` | orders(org_id, created_at) | WO list: filter org, newest-first |
| `ix_orders_org_status` | orders(org_id, status) | status tabs (New/In-repair/…) |
| `ix_clients_org_name` | clients(org_id, name) | clients list (clients had **no** org index — was a full scan) |
| `ix_parts_org_name` | parts(org_id, name) | inventory list (parts had **no** org index — was a full scan) |
| `ix_invoices_org_created` | invoices(org_id, created_at) | invoices list, newest-first |
| `ix_transactions_org_type` | transactions(org_id, txn_type) | finance P&L / fee aggregates |

**Before / after** (median of 7 runs, 60k-order seed; rows byte-identical before vs after):

| query | before | after | speedup | plan |
|---|--:|--:|--:|---|
| orders list (org, newest) | 10.92 ms | 0.04 ms | ~286× | scan→index |
| invoices list (org, newest) | 4.45 ms | 0.03 ms | ~130× | scan→index |
| inventory parts (org, name) | 2.98 ms | 0.03 ms | ~89× | **full scan→index** |
| clients list (org, name) | 1.54 ms | 0.03 ms | ~50× | **full scan→index** |
| orders status tab | 9.35 ms | 2.80 ms | ~3.3× | scan→index |
| finance txn aggregate | 8.22 ms | 2.99 ms | ~2.8× | scan→index |

Order-detail reads (lines/comments/status-logs by `order_id`, invoice-by-`order_id` batch, payment-due
balance) were already sub-0.1 ms on their existing FK indexes — left untouched.

**Write cost / unused indexes.** Every index slows inserts, so the set was kept minimal (6, each tied to
a named query). On large live Postgres tables they're built with `CREATE INDEX CONCURRENTLY` (in an
autocommit block) so saving data isn't blocked. Candidate never-used existing index: `ix_clients_discount_card`
(0 query sites in routers/services) — flagged for review, not dropped in this build.

**Verified.** Benchmark asserts identical result signatures before/after. 39-test gate across
orders / bay / clients / purchases / balances / invoice-ledger / group-invoice / inventory-reports /
public-pages / intake all green — screens show exactly the same data.

## build 144dg — Photo markup keeps the untouched original (dispute protection)

The council's one real gap: marking up an intake photo *replaced* it, destroying the clean check-in
shot that intake photos exist to protect. Now a markup is a linked derivative, not a replacement.

- `orders.intake_markups` ({original_blob_id: marked_blob_id}) — migration `im144dg01`. The original
  blob in `intake_photos` is never mutated.
- New `PATCH /orders/{id}/intake-markups` attaches a marked copy (validates the original is on the
  order and the marked blob belongs to the org). Removing a photo prunes its markup (no orphan).
- The detail payload serializes each intake photo as `{id, url, marked_id?, marked_url?}`.
- WO drawer gallery: shows the marked version with a "marked" badge and an **original ⇄ marked** toggle;
  the mark-up editor always opens the ORIGINAL, so re-marking stays non-destructive. The Create Order
  (check-in) flow keeps the original too — it adds the marked copy alongside, since there's no order yet
  to link to.

Tests `test_144dg_intake_markup` (3: original preserved + marked linked; foreign/unknown rejected;
markup pruned when the photo is removed). 120-test orders/public/checklist/bundle/catalog batch green;
tsc + vite build clean; live-verified in Chrome (marked badge + toggle, original kept).

## build 144df — Remove the dead legacy JobKit backend

After the Job Kit merge (144cx), the old JSON `JobKit` was unused by the UI (everything points at the
Bundle engine). Deleted the dead code: the `/api/job-kits` router, the `/orders/{id}/apply-kit`
endpoint, the `JobKit` model (table dropped by migration `jk144df01`), and the kit tests; `catalog_links`
now scans only bundles for "where used" and the delete-guard messages read "job kit" instead of
"bundle/kit". App imports clean with `/job-kits` and `/apply-kit` gone; 10-test catalog/bundle/deposit
batch green; single alembic head.

## build 144de — Checklist-incomplete amber dot on work-order list rows

Follow-up to 144dd (the senior-arch-qa "future enhancement"): the "required checklist unfinished"
signal now shows on the work-order **list rows** too, next to the order number — so you see which jobs
have unverified work before opening anything, same as the unread-message badge already does. Computed
in a single batched query over the page's order ids (`OrderChecklistItem` where required + undone),
not per-row — no N+1, mirroring the existing invoice-status batch. `checklists_pending` added to the
list payload; the row renders the same amber dot (with `title`/`aria-label` "Checklist incomplete").

Test `test_144dd_tab_badges::test_list_rows_carry_checklists_pending_batched` (the order with a required
undone item has the flag, the plain order doesn't). tsc + vite build clean; live-verified in Chrome
(amber dot on the one WO with a pending checklist, none on the rest).

## build 144dd — WO drawer tab badges reflect real state (kill the always-on dots)

The Checklists amber dot and Activity red dot were hardcoded in the tab array — always on, every order,
meaning nothing. A badge that's always lit trains everyone to ignore it. Now all three tab badges are
earned, driven from state the drawer already loads (no new fetches), each reading the exact signal its
tab renders (one source of truth — the dot can't disagree with the thing it points to):

- **Activity → red count**, shown only when `client_msg_unread > 0`, and hidden while you're on the
  Activity tab (you're reading it; the thread read already zeroes unread server-side — no reload, no race).
- **Checklists → amber dot**, only when a REQUIRED checklist item is still undone (same definition as
  the completion gate). New `checklists_pending` on the order-detail payload.
- **Invoices → "Due" badge**, only when an ISSUED (non-draft/cancelled) invoice on the order still has a
  balance. New `payment_due` on the detail payload (was also an always-on zombie badge). [senior-arch-qa fix]
- a11y: the amber dot gets `role="img"` + `title`/`aria-label` "Checklist incomplete" so it's not
  color-only. [senior-arch-qa fix]

Tests `test_144dd_tab_badges` (3: checklists_pending toggles with a required item; payment_due only for
an issued unpaid invoice; detail carries client_msg_unread). 157-test orders/invoices/checklists/public/
finance batch green; backend detail adds two cheap indexed queries, no schema change; tsc + vite build
clean; live-verified in Chrome (amber dot earned, Due badge gone, unread count correct).

## build 144dc — Mark up intake photos (circle the damage, drop a spot, draw)

Techs needed to point right at the problem on a photo — the tear for a seamstress, the dent/scratch for
auto, the cracked corner for device repair. New `PhotoAnnotator`: click an intake photo and a canvas
editor opens with **Circle**, **Spot**, and freehand **Draw** tools, five marker colours, an adjustable
line width, undo and clear. Works with mouse or touch (tablets on the shop floor). On save it flattens
the image + marks to a single JPEG, uploads it via the existing `/api/media`, and swaps the photo's id
in the order's intake list — so the annotated version is what shows everywhere the photo does (the WO
drawer, the customer's track page, dispute records). Export is capped at 1600px so uploads stay small.

Frontend-only: reuses `/api/media` upload and the `/orders/{id}/intake-photos` PATCH — no backend
change, no migration. New component `PhotoAnnotator.tsx`; the intake-photo gallery gains a mark-up
button (and clicking a thumbnail now opens the editor), wired in **both** the Create Order drawer (mark
up at check-in, before the order is saved) and the work-order drawer. tsc + vite build clean; live-verified in Chrome
(drew a red circle on an intake photo, saved, confirmed the photo id swapped to the marked-up version).

## build 144db — Device-aware job kits: a part slot auto-resolves to the part that fits the device

The differentiator from the Job Kit brainstorm. A job kit was one generic list — "Screen Repair Combo"
carried one fixed screen part, so a multi-model shop hand-swapped the right screen every time. Now a
product component can be flagged **"Auto-pick the part that fits the device"**: when the kit is applied
to a work order, that slot resolves to the in-category part whose compatibility (`compatibility_json`
"fits") matches the order's device — preferring one with stock at the order's location — and deducts
**that** part's inventory. No device, no category, or no matching part → the configured part is used
as the safe default. Reuses the existing `part_fits` engine; the configured part is always the fallback.

- **Model + migration**: `bundle_components.device_variant` (bool, `dv144db01`, single head).
- **Resolution**: `_resolve_variant_part()` in the kit→WO expansion picks the fitting in-category part
  (stock-preferred, stable by name); the order line and stock deduction use the resolved part.
- **API**: component serialization + `CompIn` carry `device_variant`; create/update persist it.
- **Editor**: a per-product-component "Auto-pick the part that fits the device" checkbox.

Tests `test_144db_device_variant_kit` (variant resolves to the iPhone-13 screen and deducts its stock,
generic untouched; no device → falls back to the configured part). 10-test kit/catalog/deduction batch
green; migration applies cleanly; tsc + vite build clean; editor toggle live-verified in Chrome.

_Deferred from the same brainstorm: stock pre-warning before a kit is applied, kits as customer-facing
quote options, per-kit margin analytics._

## build 144da — Job kit picker: click-to-browse dropdown (no typing required)

The component picker only showed results after typing ≥2 chars. Added an `openOnFocus` option to the
shared `CatalogPicker`: focusing the field fetches and shows the full list for the selected type right
away (services or products, A–Z, with stock counts), so items can be chosen from a dropdown instead of
typed; typing still filters live. Enabled on the job-kit component picker only, so the WO line picker
and other usages are unchanged.

`/catalog/search` also gained a `limit` param (default 8, capped at 200) because the browse dropdown
was only showing the first 8 items — the picker now requests `limit=100` in browse mode so the whole
catalog is listed (scrollable). Test `test_144da_catalog_limit` (default caps at 8; `limit=100` returns
all). tsc + vite build clean.

## build 144cz — Job kit: Services/Products tabs in the picker + verified WO stock deduction

Two follow-ups on the job-kit editor:

**Services / Products tabs.** The component picker now has a **Services | Products** toggle, and the
dropdown is scoped to the chosen type ("Choose a service…" / "Choose a product…") instead of one mixed
"all" search — so it's obvious which kind of item you're adding. (CatalogPicker already supported a
`type` prop; the editor now drives it from the tab.)

**Verified: a job kit deducts inventory on a work order.** The code path already existed (the bundle
expansion calls `_maybe_deduct` for product components on a non-estimate order), but had no test.
Added `test_144cz_jobkit_wo_deduct`: applying a job kit with a product component to a real WO deducts
that product's stock (2 units), a service component deducts nothing, and applying to an **estimate**
deducts nothing. All green — stock handling confirmed, no code change needed there.

tsc + vite build clean. Live-verified in Chrome: the Services/Products tabs switch the dropdown scope.

## build 144cy — Job kit component editor: add/remove services & products (create AND edit)

The job-kit (bundle) editor could not actually edit components — the list was display-only and
`save()` no-op'd for `kind="bundle"` ("Saved (view only for this type)"), so a job kit could be
neither created nor changed from the UI, even though the backend fully supported it.

Built the real editor in `InventoryDrawer`:
- A **Services & products** section with a `CatalogPicker` ("Add a service or product…") that adds a
  component on pick, a per-row **qty** input, a **remove** (×), a live components subtotal, and a
  "missing" flag for a component whose catalog item is gone. A job kit can't contain another job kit
  (guarded), and duplicates are rejected.
- A **Pricing** control: "Sum of items" (price auto-computed from components) or "Fixed kit price"
  (manual) — Bundle's `price_mode` surfaced properly for the first time in this editor.
- A `kind="bundle"` **save branch**: `POST /bundles` on create, `PUT /bundles/{id}` on edit, sending
  `{name, price_mode, price, category_id, components:[{ref_type, ref_id, qty}]}`. The same editor and
  its populated `components` drive edit, so adding/removing items works after creation too.
- Header/title now reads "New job kit" (was "New bundle").

Test `test_144cy_jobkit_components` (create with 2 components → edit to a different set + qty + fixed
price → GET reflects it). Backend unchanged (already supported it); tsc + vite build clean.
Live-verified in Chrome: created "Screen Repair Combo" with a service component (persisted), reopened
it and confirmed components are editable with the picker to add more.

## build 144cx — Merge Bundle + Job Kit into one concept, named "Job Kit"

Two overlapping features — Bundle (priced, sellable, catalog-linked package) and Job Kit (snapshot
line template) — confused users who saw two menu items that both "add a group of parts + labour to a
work order". After a council pass, the decision was to keep the richer **Bundle engine** (it can be
sold on a WO, has set/summed pricing, follows the catalog, has categories + where-used + broken-flag)
and retire the thinner JSON Job-Kit — then rename Bundle to "Job Kit", the name the owner wanted.

This build does the rename + de-dup in the **UI only** (zero backend/billing/migration risk):
- My company's "Bundles" tab → **"Job Kits"**; "New bundle" → "New job kit"; drawer title/labels and
  breadcrumb relabelled; empty/loading copy updated.
- Work-order line filter "Bundles" → "Job Kits"; the catalog picker now tags a kit result as **kit**
  (violet) and the backend search labels its category "Job Kit". Adding a job kit to a WO is the same
  one-pick-explodes-into-lines flow as before (`kind="bundle"` under the hood).
- Removed the duplicate surface: Inventory's old "Job kits" tab + `JobKitsPanel` (deleted), and the
  WO drawer's separate "Apply a job kit" button (the catalog picker already covers it).

Internal identifiers (`bundles` tab id, `kind="bundle"`, `/api/bundles`, the `Bundle` model) are
unchanged — only what the user reads. The legacy JSON Job-Kit backend (`/api/job-kits`,
`/orders/{id}/apply-kit`, `JobKit` model) is now unused by the UI; left in place as dead code (its
tests still pass) and flagged for removal in a later cleanup. tsc + vite build clean; catalog/bundle/
job-kit backend batch (9 tests) green; live-verified in Chrome (one "Job Kits" home, old tab gone).

## build 144cw — Payroll tenant-isolation fix (manual time entry) + senior-eng leak sweep

Full-app leak/isolation sweep (payroll, inventory, finance, timeclock, returns, transfers,
stock docs, bundles, assets). One real hole found and closed; everything else verified scoped.

**The leak (fixed).** `POST /timeclock/entries` (manual time entry) took `employee_id` from the
request and only checked the caller was a manager (`_can_manage`) — it then fetched the `Employee`
**without an org filter**. A manager could log time against another org's employee, which read that
employee's pay rates (`cost_rate`/`billable_rate`) into the caller's org and polluted this org's
payroll. The employee fetch is now scoped to `current.org_id` (foreign id → 404).

**Swept clean.** Every other subsystem's primary-record fetch is org-scoped: all timeclock entry-id
ops (stop/edit/delete/approve/reopen/dispute/resolve) filter `TimeEntry.org_id`; the `/start` order
+ order-line linkage validates org ownership; payroll rules/runs/balances all filter `org_id` and
take `base_salary` from the request (not a foreign read); returns, transfers, stock docs, bundles,
job kits and assets all scope their main record. Frontend API call paths all map to a live backend
router — no unwired top-level features.

Test `test_144cw_payroll_isolation` (a manager cannot log time for a foreign-org employee → 403/404).
30-test timeclock/payroll batch green; no schema change.

## build 144cv — Once a work order is closed, the customer can't message

A customer could keep messaging the shop from the track page on an order that was already done and
closed. Now `_post_client_message` (the single chokepoint for both the track and portal message
endpoints) rejects with 403 when the order's current status is terminal. "Closed" is read from the
order's CURRENT status via the org's configurable status table (`is_terminal`, built-in terminal keys
as fallback) — so reopening the order restores messaging, and `closed_at` staleness after a reopen
never wrongly blocks it. The track page's `allow_messages` flag now also factors this in, so the
"Message the shop" box hides itself client-side the moment the order closes.

Test `test_144cv_closed_no_message` (open → send OK; close → 403; reopen → send OK again). Public/
track batch green; no schema change.

## build 144cu — The customer track link is unique and can never repeat

Hardening the public link after the comms work. The track link is keyed on `orders.public_token`,
but that column was only `index=True`, not unique, and order-create minted it from a truncated UUID
(`_uuid()[:16]`, ~64 bits) while rotate used the stronger `secrets.token_urlsafe`. So two orders could
in principle share a link, and `_load` (which resolves the token with `scalar_one_or_none`) would have
500'd on the collision. Closed three ways:

- **Hard guarantee** — `public_token` is now `unique=True`; a new migration (`pt144cu01`) dedupes any
  pre-existing collisions and swaps the plain index for a unique one. The DB now refuses a duplicate
  outright. (Revoked links are NULL; multiple NULLs are fine under a unique index.)
- **Collision-checked generation** — new `unique_public_token(db)` helper (mirrors the existing
  `unique_order_code`): `secrets.token_urlsafe(16)` (~128 bits) checked DB-wide and retried on the
  practically-impossible clash, so the app never hands out a taken token in the first place.
- **Every mint path uses it** — order create, order duplicate (fresh token, never copies the source's),
  "Copy public link", and rotate all go through the helper. The model default is also upgraded to
  `secrets.token_urlsafe`.

Tests `test_144cu_token_unique` (3: every order gets a distinct non-null token; rotate mints a fresh
one; a forced duplicate is rejected by the DB with IntegrityError). 87-test orders/public/track/dup/
share batch green; migration applies cleanly on a fresh DB; single alembic head (`pt144cu01`).

## build 144ct — Close the comms loop on the customer side (staff replies reach the track page)

Audit of the messaging loop surfaced a real half-open link. The customer→shop direction worked: the
public track page's "Message the shop" box posts a `client_message` feed item that shows in the
customer's Updates and in the staff ConversationPanel (144cq). But shop→customer did **not** reach the
customer: a staff reply is stored as a client-visible `OrderComment`, and the track page's Updates
only read `EventFeedItem` — so the customer never saw the reply, only their own outgoing messages.

**Fix.** The track GET now also loads `OrderComment` where `visible_to_client=True` and merges them
into `updates` (as `shop_message`), sorted newest-first with the rest of the feed. No new write path,
no duplication — the staff ConversationPanel still posts one `OrderComment`; the public page now reads
it. The customer's own messages keep showing as before.

**Public UX.** The two message kinds are now labelled and iconed on the track page: the customer's own
messages read "YOU" (user icon), shop replies read "SHOP" (store icon), so the thread is legible as a
real back-and-forth.

Test `test_144ct_public_thread_loop` (customer posts → staff replies via `/orders/{id}/thread` →
track page `updates` contains BOTH). 62-test public/track/thread batch green; tsc + vite build clean;
no schema change.

## build 144cs — Technician bay: tick checklist items inline (no drawer)

Completes the bay. 144cr showed checklist progress per job; now the tech can act on it from the
same card. The `done`/`total` progress bar is a toggle — expanding it reveals the job's checklists
inline. Each `check_result` item gets one-tap **Pass / Fail / Caution** pills (active state
highlighted, a ✓ on done items); ticking one PATCHes `/orders/{id}/checklists/{cid}/items/{iid}` and
refreshes the card's progress bar in place. Non-check fields (text, number, file) are shown read-only
with "open job to fill", since a single tap can't meaningfully answer them.

No backend change — reuses the existing `GET /orders/{id}/checklists`, the per-list items endpoint,
and the `check_result`→`done` derivation (Pass/Fail/Caution all mark the item done, same as the
drawer). The Fail path still auto-drafts its upsell recommendation (144go), unchanged, because it
runs through the same PATCH. New component `BayChecklist.tsx`.

Test `test_144cs_bay_checklist` (attach a 2-check template, PATCH one to pass, assert the bay card's
`checklist_done` goes 0→1). 12-test checklist/bay batch green (incl. v2 + upsell regression);
tsc + vite build clean.

## build 144cr — Technician bay: job cards with one-tap status advance + checklist progress

The deferred "bigger half" of throughput. `MyWorkHub` already had the timer half (live timer,
clock-in per line, today/week hours, dispute). What a tech still couldn't do from here was move a
job forward — that meant opening the full drawer per job. Now the bay is job-first.

**Per-order bay cards.** `/timeclock/my-work` gains an `orders` array: one card per job the tech has
open lines on, carrying `status` + `status_label`/`status_color` (from the org's configurable
`work_order` workflow), the forward `next_status`/`next_label`, checklist `done`/`total`, client,
device, and the tech's lines. The flat `lines` array is untouched for back-compat.

**One-tap advance.** Each card shows a status pill and an "→ {next}" button that PATCHes
`/orders/{id}/status`. The existing Orderry-style entry gates still bite: a 409 ("requires all N
checklist item(s) done", comment-required, payment-required) surfaces as the flash message instead of
silently advancing — the tech learns exactly what's blocking the move. A terminal status shows "Final".

**Checklist at a glance.** A progress bar + `done/total` per job, so the tech sees how close the job
is to its completion gate without opening it.

Tests `test_144cr_bay_orders` (3: order cards with next-status + checklist + client; suggested
next_status is a reachable transition; org-scoped — another tenant sees none). 75-test
timeclock/status/board/mywork/comms batch green; no schema change (reuses OrderStatus/OrderChecklistItem);
tsc + vite build clean.

_Deferred next: ticking checklist items inline from the bay (today it shows progress + gate messages,
the full checklist still lives in the drawer's Checklists tab)._

## build 144cq — Work-order customer conversation (the comms loop) + unread triage

The highest-leverage Work-Order gap wasn't missing backend — it was that the pieces of customer
communication (public status link, `notify`, client-visible comments, inbound `/track/{token}/message`)
were never woven into one thread. Now they are.

**Conversation loop.** `GET /orders/{id}/thread` merges staff client-visible messages (`OrderComment`
`visible_to_client`) with the customer's replies (`EventFeedItem` `client_message`) into one
chronological conversation, and reading it clears the order's `client_msg_unread` badge.
`POST /orders/{id}/thread` posts a staff message the customer sees on their status page. The WO drawer
gets a `ConversationPanel` — chat bubbles (customer left, staff right), composer, Enter-to-send —
living at the top of the **Activity tab** (not on every tab). A customer messaging from their status
link now lands here, and a staff reply shows up on their status page: a real two-way loop out of
endpoints that already existed.

**Unread triage (throughput slice).** Work-order list rows now badge `client_msg_unread`, so staff see
at a glance which jobs have a customer waiting on a reply.

Tests `test_144cq_wo_thread` (thread merges + orders both sides; reading clears unread; empty rejected).
28-test order batch green; tsc + vite build clean; no schema change (reuses existing models/columns).

_Deferred (brainstorm bet #2, the bigger build): a dedicated technician bay / my-work view — one-tap
status, timer-forward, checklist-driven work — to cut clicks per job. Scoped as its own phase._

## build 144co — Product / service / bundle integrity + a tenant-isolation fix

The product→service→bundle→order core was correctly wired (org-scoped bundle, proportional fixed
pricing, stock deduction), but the catalog layer had two real gaps. Both closed, TDD-first.

**Delete guards (no more silent orphans).** `BundleComponent.ref_id` has no FK, so deleting a product
or service used in a bundle/job kit silently left the bundle showing "?" / ₹0. Now deleting a service
is blocked with 409 if it's in any bundle/kit ("used in N bundle/kit(s): … — remove it there first"),
and the product bulk-delete skips in-use parts and returns them in a `blocked` list (the Products UI
surfaces "N item(s) kept because they're in a bundle/kit"). New `services/catalog_links.py` powers it.

**Tenant-isolation fix.** `create_bundle`/`update_bundle` accepted any `ref_id` and `_ser` + the order
expansion resolved `Part`/`Service` **without an org filter** — a crafted component id could display
another tenant's item name + price. Components are now validated against the caller's own catalog on
create/update (400 otherwise), and every resolution (`_ser`, order expansion) is org-scoped.

**Broken-component visibility + where-used.** `_ser` now flags each component `missing: true` and the
bundle `broken: true` when an item is gone (UI shows a "Needs fixing" badge). New
`GET /api/bundles/using?ref_type=&ref_id=` lists the bundles/kits a part or service belongs to.

Tests `test_144co_bundle_integrity` (5: service-delete guard, part-bulk skip-in-use, foreign-component
reject, missing-component flag, where-used). 44-test security/isolation/catalog/orders batch green;
no schema change (logic + endpoint only); tsc + vite build clean.

_Deferred (brainstorm item 3, a design call): consolidating the overlapping JobKit vs Bundle concepts,
and a stock pre-warning when a bundle with an out-of-stock part is picked onto a job._

## build 144bn — Loose-ends cleanup: dead tab removed, export date range, role toggles verified

Closing the open items from the production audit:
- **Dead code gone.** Removed the now-unreachable `transactions` tab from FinanceHub (render block,
  `txns` state, `Txn` type, pagination vars, and its fetch) — the Cashboxes tab covers per-box
  transactions. tsc confirms no dangling refs.
- **Export date range.** The accountant export gained two date inputs (from → to); blank still means
  the current month. Backend treats a date-only `date_to` as inclusive to end-of-day. Test
  `test_export_date_range_inclusive` (same-day range includes today's rows; a past window excludes
  them). 4 export tests green.
- **Role toggles verified.** Confirmed `can_view_cashbox`, `can_export_accountant` (Finance) and
  `can_view_comeback_alerts` (Workflows) are in `CATALOG`/`ALL_CODES`, served by the rbac
  permission-catalog endpoint — so all three render as on/off switches in the Role editor.

tsc + vite build clean.

## build 144bm — Wiring fix: accountant export was missing business expenses

Audit of the new features for "should be linked but isn't" found a real gap: the 144bk accountant
export read only cashbox `Transaction` rows, so business expenses (rent/utilities recorded via
`/finance/expenses` into the `Expense` model — which never post to a cashbox) were omitted. The CPA
pack was incomplete. The export now merges `Expense` rows (as `(business expense)` / type `expense`)
with cashbox transactions, sorted by date. Test `test_export_includes_business_expenses` (a recorded
rent expense now appears in the CSV); full export + P&L bundle green.

_Other audit notes (acceptable for v1, documented not fixed): comeback detection keys on `device_id`,
so orders with no device captured aren't flagged; the comeback alert shows in the WO drawer, not the
WO list; the global command palette no longer jumps to Cash directly (it's a Finance tab now)._

## build 144bl — Comeback Shield (first slice): repeat-visit alert on work orders

The first buildable slice of the comeback-prevention wedge. `GET /orders/{id}/comeback` checks whether
this device has a prior order within the last 45 days — a likely comeback — and returns the count,
days since, and the prior WO number. The work-order drawer shows an amber banner: "Possible comeback.
This device was here N days ago (WO ####). Check the earlier fix before promising."

**Role toggle.** New `can_view_comeback_alerts` permission (Workflows group); endpoint gated by
`require_perm` and the banner by `can(...)`. Tests `test_144bl_comeback` (repeat device flagged with the
prior WO number; a brand-new device is not; a role with the perm denied gets 403).

_Deferred (the fuller Comeback Shield): a structured intake-completeness gate, and a close-time check
against this shop's own symptom+part comeback history ("last 3 times this came back — did you test X").
This slice ships the repeat-device detection those build on. tsc + build clean; 3 tests green._

## build 144bk — Accountant export ("your books are done when the day is done")

The concrete first slice of "finance that closes itself": `GET /finance/export/accountant.csv?from=&to=`
streams a clean CSV of the period's active cash transactions (Date, Account, Type, Category, Comment,
Income, Expense, Balance after; default period = current month to today) so the owner hands the CPA
one file instead of re-keying into QuickBooks. Finance overview gains a "This month at a glance"
header and an **Export for accountant** button.

**Role toggle.** New `can_export_accountant` permission (Finance group); the endpoint is gated by
`require_perm("can_export_accountant")` and the button by `can(...)`. Tests `test_144bk_accountant_export`
(CSV has the header + the recorded income/expense rows; a role with the perm denied gets 403).

_Deferred: the fuller "Today / one-tap daily close" ritual — the drawer close already serves as the
daily cash close; this adds the export half. tsc + build clean; both export tests green._

## build 144bj — One finance menu: Cash & drawer folded into Finance as a tab

Unified the two money menus. The cash screen's content moved into a reusable `CashPanel` (no Shell
wrapper); `/cash` still works standalone (thin Shell wrapper, kept for deep links) and the same panel
now renders as the **Cashboxes** tab — the first tab in Finance. The separate "Cash & drawer" nav item
is gone (one door), and the redundant "Transactions" tab was dropped (the Cashboxes tab shows per-box
transactions).

**Role toggle.** New `can_view_cashbox` permission in the catalog (Finance group); the Cashboxes tab
is gated by `can("can_view_cashbox")` (deny-wins, default-allow — existing roles keep access, and a
role can now switch the cash area off). Backend cash endpoints keep their existing finance perms.

Live-verified: Finance → Cashboxes renders the full panel (rail, drawer-first hero, activity) at full
height; nav shows a single Finance entry. tsc + vite build clean; role-permissions test green.

_Deferred: the now-unreachable `transactions` tab render block in FinanceHub is dead code to remove._

## build 144bi — Cash & drawer empty + non-cash states

Two presentational polish items on the redesigned `/cash`:
- **No cashboxes yet** — the hero used to render "— / ₹0.00". Now a proper empty state: a clay icon,
  "Add your first cashbox" with a one-line explanation, and a "New cashbox" button that opens the
  create drawer.
- **Non-cash box selected** — where the drawer card would be, a card/UPI/bank account now shows a
  quiet dashed note ("No physical drawer on a card/cashless account — record income, expenses, and
  transfers below") instead of nothing.

Conditionals only (`accounts.length`, `payment_option`); tsc + build clean.

## build 144bh — Manual cash income/expense feeds the drawer's expected cash

Live-testing the redesigned `/cash` surfaced a real disconnect: recording a manual cash **+Income /
−Expense** on the till during an open shift updated the cashbox balance but **not** the drawer's
expected cash, so a close read the whole till as a surplus ("Expected ₹0, counted ₹900, ₹900 OVER").
The hero ("in the till right now") and the drawer close disagreed.

**Fix (owner's call: count it).** Manual cash income/expense on a cash till now links to the open
shift (`Transaction.shift_id`, set at post time in `post_transaction`) whenever it's unambiguous: for
a location-scoped till, that location's open shift; for a **company-wide till** (the common single-box
shop), the one org-wide open shift. Two or more candidate drawers → left unlinked. Sales still flow
through `Payment.shift_id`. `_expected_cash` adds the signed sum of linked transactions, so expected =
opening float + cash sales − refunds + pay-ins − pay-outs **+ manual till cash**; close breakdown
gains a `manual_cash` line.

Migration `tx144bh01` adds `transactions.shift_id` (indexed, nullable FK). Tests
`test_144bh_drawer_manual_cash`: location-scoped till (₹1000 income − ₹200 expense → expected ₹800,
balanced), **company-wide till with one open shift** (₹500 income → expected ₹500, balanced), and a
**card** box whose income is ignored by the drawer. Finance bundle green; migration chain applies
clean; no frontend change (the close response gained one additive field).

## build 144bg — Cash & drawer redesign: cashbox rail + drawer-first hero + activity ledger

Reshaped `/cash` from the Orderry-style ledger-first layout into a drawer-first, owner-first screen —
same data and endpoints, new hierarchy, built from the app's own component kit.

**Cashbox rail (left).** Every cashbox (cash / card / cashless) as a compact gradient-circle row with
its balance; click to switch, kebab for income/expense/transfer/configure, "+ Cashbox" to add. The
multiple-cashboxes capability was always there — now it's the primary navigation.

**Drawer-first hero (center).** The selected box leads with its name, a big mono balance labelled "In
the till right now" for cash (else "Balance"), and a three-up glance strip — **In today / Out today /
Net today** — computed by a new tested helper `lib/cashStats.ts#todayTotals` (deleted rows excluded,
transfers ignored, signed/typed expenses both read as money-out). Test `cashStats.test.ts` (empty,
basic, yesterday-excluded, deleted-excluded, signed-abs) green.

**Drawer controls** (`CashDrawer`) now render only for cash boxes — a card/UPI box has no physical
till to open, count, or close (senior-architect-qa finding; previously shown for every box).

**Activity ledger.** The transaction table kept all its behaviour (corrections, delete/restore,
pagination, all-time totals) under a dark command bar carrying Income / Expense / Transfer and the
live count, scoped to the selected box.

Also fixed the page title (was "Finance", now "Cash & drawer" to match the nav). New logic is TDD'd;
the rest is presentational reuse — no backend change. tsc + vite build clean; todayTotals test green. Third reconciliation leg: card deposits vs. processor payouts

The piece that makes reconciliation whole. Shops take card and online payments all day, but the money
lands in the bank days later, net of processing fees — and nobody checks that what the processor
*said* it would deposit matches what it actually did. This adds that check.

**`ProcessorPayout` model + migration (`pp144bf01`).** A record of each bank/processor deposit:
provider, date, gross, fee, net, reference. `net` defaults to `gross − fee` when omitted. Manual
entry works today; a Stripe/Square/SumUp connector plugs into the same table later (we build the
foundation now, wire the connector later).

**`POST /finance/payouts` + `GET /finance/payouts`** (finance-permitted) to record and list payouts.

**Fourth reconciliation leg (`payout_gap`).** `GET /finance/reconciliation` now also compares expected
card deposits — card/cashless invoice-payment transactions *minus* recorded processing fees — against
the net of recorded payouts. A gap ≥ 1.0 surfaces as a "Card deposits don't match payouts" exception
(high ≥ 20, else medium). Only runs once payouts exist, so shops that don't take cards see nothing.

**"Record payout" in the Reconcile tab.** A button prompts for gross / fee / reference and refreshes
the queue, so an owner can reconcile a deposit against their statement by hand until the connector
lands.

Tests `test_144bf_payout_recon` (mismatched payout → high `payout_gap`; matching payout → clean;
payout list + net default). 11-test finance bundle (payout, reconciliation, drawer, blind count,
fees) green; tsc + vite build clean.

## build 144be — Drawer reconciliation history + "Needs your eyes" exception queue

Two steps that close the loop from "track cash" to "the books reconcile themselves."

**Reconciliation history (`ShiftHistoryModal.tsx`).** A "History" button on the drawer card opens a
list of recent drawers with a BALANCED / SHORT / OVER / OPEN status, who opened each, opening vs.
expected vs. counted, and the discrepancy — Shopmonkey's "Cash Tracking Report" equivalent, over the
`GET /finance/shifts` endpoint (finance-permitted).

**Exception-based reconciliation (`GET /finance/reconciliation` + FinanceHub "Reconcile" tab).** The
deep-research wedge: instead of making the owner reconcile by hand, we scan the books and surface
only the problems — **overpaid invoices** (amount_paid > total), **unbalanced closed drawers**, and
**supplier balances that drifted from their ledger** — as a ranked "Needs your eyes" queue
(high→medium). A clean shop sees "Your books are clean." Tests `test_144be_reconciliation` (flags a
double-pay + an unbalanced drawer; clean books → empty queue).

Verified live: the Reconcile tab renders under Finance with the clean-state card; backend engine
green. 26-test finance bundle (reconciliation, drawer, blind count, fees, cashbox, supplier) green;
tsc + vite build clean.

This completes the cash/finance feature set: reachable cashboxes + fees + income/expense, fees in
P&L, the full drawer (open/close, denomination count, pay-in/out, blind count, explained
discrepancy), reconciliation history, and the exception queue — the "accurate + effortless" finance
the SMB research said owners actually want.

## build 144bd — Guided denomination count on the drawer

Replaced the drawer's single-number open/close prompt with a proper **denomination grid**
(`DenomCountModal.tsx`): the operator enters how many of each note/coin are in the till, the total
tallies live, and the `{denom: count}` map is sent to the backend (which already stored it in
`open_denoms`/`close_denoms` and derives the total — 144bc). Denomination sets are currency-aware
(INR/USD/EUR/GBP/AED, with a sensible default). This is the Shopmonkey-grade physical-count ritual:
an untrained cashier counts bill-by-bill instead of fat-fingering one number, and the close stays
blind-aware (finance users see the explained variance; cashiers close blind). Pay-in/out keep their
quick amount+note prompts.

Verified live end-to-end: opened a drawer at ₹200, closed it via the grid (1×₹100 + 5×₹20 = ₹200),
and got "Drawer balanced ✓ — matches expected exactly." tsc + vite build clean; backend denom path
already covered by `test_144bc_cash_drawer` (open + close with denominations).

## build 144bc — Cash drawer made real: usable, reachable, reconciling (TDD)

The cash-shift/drawer system existed only in the backend (`/shifts/open`, `/shifts/close`) and was
**unreachable** — nothing in the UI called it, and the whole CashHub (add-cashbox, income/expense,
drawer) had **no nav link at all** (only `/finance` → FinanceHub was linked). That's why the owner
"couldn't find" add-cashbox / fees / income-expense: the page existed but wasn't on the menu.

Made it a real, end-to-end feature:
- **Reachable:** added a "Cash & drawer" nav item → `/cash` (CashHub). The existing add-cashbox,
  transaction-fee config, income/expense/transfer are now one click away.
- **Drawer UI (new `CashDrawer.tsx`):** open the drawer with an opening count; **Pay in / Pay out**
  (note required — the digital payout slip); **Close drawer** with a counted total; blind-aware
  result (a finance user sees expected vs. counted + the explained variance; a cashier closes
  blind). Mounted at the top of CashHub.
- **Backend completeness:** `CashShift` gains optional `open_denoms`/`close_denoms`; new
  `CashDrawerMovement` table for pay-ins/outs; `expected_cash = opening + cash payments − cash
  refunds + pay-ins − pay-outs`; one-open-drawer-per-location guard; `GET /shifts/current` and
  `GET /shifts` (reconciliation history with balanced/unbalanced + discrepancy); an **explained
  discrepancy** on close (direction, amount, the shift's movements to check). `close_shift` keeps
  the legacy `?closing_float=` query form working alongside the new denom/float body.
- Migration `dr144bc01` (new table + 2 columns, batch-mode, dialect-safe); `alembic upgrade head`
  clean.

Tests `test_144bc_cash_drawer` (full flow: open-with-denoms → pay-in/out → cash sale → blind close
with movements folded into expected + explained discrepancy; single-open guard; mandatory note;
history balanced flag; legacy query close). 99-test finance/shift/cash/invoice/supplier bundle green;
tsc + vite build clean; verified live in the browser (nav → open drawer → LIVE state).

## build 144ba/bb — Finance sprint: honest P&L (fees) + blind cash count (TDD)

Two finance improvements driven by competitor research (Shopmonkey/Orderry) and a real reporting
bug, delivered PM-style with TDD and a senior-architect-QA pass.

**144ba — Processing fees now count against profit (the real bug).** Card/online fees post as their
own `fee` transaction, but the owner P&L summed only `expense` transactions — so every card-taking
shop's reported profit was overstated by its processing fees. `compute_pnl` now reports `fees`
(Σ active `fee` txns in the period, org- + period-scoped, same shape as opex) as its own line and
subtracts it from net profit: `net = revenue − COGS − opex − fees`. Surfaced end-to-end — the
FinanceHub P&L shows a "Processing fees" card and a "− Processing fees (card/online)" row in the
breakdown; the Cash Flow report already included fee txns. Test `test_144ba_pnl_fees` (a 2.9% card
payment reduces net profit and shows the fee); the existing P&L test stays green (fees = 0 there).

**144bb — Blind cash count (trust/fraud control).** Industry best practice (and what Shopmonkey
enforces): the person counting the drawer must not see the expected total. `close_shift` still
computes and stores `expected_cash` for the admin, but only returns it (and the discrepancy) to a
user whose role allows `can_view_finance`; a cashier closes blind. Verified the gate is airtight —
`expected_cash` is exposed by no other endpoint. Test `test_144bb_blind_count` (finance user sees
expected/discrepancy; a `can_view_finance:false` role gets a blind close).

Gates: 35-test finance bundle green (pnl, fees, blind count, shift, cashbox posting, supplier pay/AP,
reports, RBAC); tsc + vite build clean; dist rebuilt.

Note (pre-existing, out of scope): `compute_pnl`'s `location_id` param is a no-op — revenue/COGS/
opex/fees are all org-wide, not location-filtered. Flagged for a future multi-location P&L story.

## build 144az — WO drawer showed fake mockup data; device card dead-click

The Work Order drawer rendered **hardcoded placeholder data** whenever a field was empty, via an
`or(real, "sample")` helper: an order with no device showed a fake "Samsung Galaxy S24 Ultra" (SN
`SM-S928B`, IMEI `358921109482104`), and a client with no contact info showed "Liam Vance /
+1 (555) 234-9182 / liam.vance@gmail.com". That's why clicking the "device" did nothing — there was
no real device (`dev.id` undefined), so the card was a dead element dressed up as a real one. Design
mockup slop that shipped. Fixed: the device card now renders the real device (clickable → opens the
asset drawer) only when one is linked, otherwise an honest empty state ("No device on this order").
Client name/phone/email and device title/SN/IMEI show real values or clear "No phone"/"No email"
placeholders — never fabricated data. The drawer header shows the device name when present, else the
client name. tsc + vite build clean; verified live in the browser (empty state renders, no dead
click). An order that *does* have a device still opens its asset details on click.

## build 144ay — Work Orders page fix: /orders limit cap vs the frontend's bulk-load

The Work Orders page rendered a raw API error instead of the table:
`Input should be less than or equal to 200 … input "1000"`. `WorkOrdersHub` bulk-loads
`/orders?limit=1000` and paginates client-side (same pattern as Clients, Cash, Assets, Invoices —
all of which use an uncapped `int` limit), but `list_orders` alone had a hard `Query(…, le=200)`
cap, so the request 422'd and the whole page broke. Raised the orders cap to `le=1000` to match the
page's real need while keeping a bound (absurd values still 422). Regression test
`test_144ay_orders_limit` covers both. (The cap had been tightened during the pagination/audit work;
no test exercised the `limit=1000` frontend contract, so the full-suite sweep didn't catch it —
that gap is now closed.)

## build 144ax — Supplier AP: one write-path, no drift (TDD)

`Supplier.balance` (a running column, + = we owe them) and the linked contact ledger (− = we owe
them) are the same debt seen twice. Previously eight sites — stock postings, PO receipt, PO freight
and shipment charges, supplier returns (create + delete), and credit postings (create + delete) —
each updated both by hand, which let them drift: some bumped the column with a raw float while the
ledger took a quantized value (sub-cent drift on every PO receipt), and deleting a **cash**-settled
return bumped the column by +total even though a cash return never touched it on create (it's paid
from the cashbox), with no matching ledger row.

New single write-path `ledger.adjust_supplier_ap(db, org, supplier, delta, …)`: the column moves by
+delta and the contact ledger by −delta, both quantized from the **same** value, in one function —
so they can't diverge. All eight sites now route through it; the cash-return-delete column bump is
gated on balance-settlement to match create. Tests (`test_144ax_supplier_ap_single_source`): a
balance return leaves column and ledger reconciled (discrepancy 0), and a create→delete round-trip
returns both to zero. Full suite (1023 tests) green. (This is the single-source follow-through on
144aw's reconciliation-visibility flags — drift is now structurally impossible, not just visible.)

## build 144av/aw — Senior-QA remaining items: cashbox correctness, atomic stock, real shift cash, safe contact delete (TDD)

The remaining critical/high items from the senior-architect-qa "what's left" assessment, each
RED→GREEN with a regression test and no change to callers that don't opt in.

**C2 — money that moves now hits the cashbox.** Three payment paths previously moved a contact or
supplier balance but never posted a `Transaction`, so the account/cashbox balance and finance
reports silently drifted: (a) `finance.record_payment` (invoice pay), (b) `clients.move_balance`
(advance/payout), and (c) `stock_docs` supplier returns settled in cash (the `account_id` was
accepted and dropped). All three now post through the shared `post_transaction` when a cash
`account_id` is supplied — invoice/advance as income **in**, payout as expense **out**, supplier
cash refund as income **in** (and the return is marked paid). `account_id` is optional, so existing
callers are unaffected. `balance`/`adjustment` settlements move no cash → no Transaction, correctly.
Test: `test_144av_cashbox_posting` (3 cases).

**C3 — atomic work-order stock deductions.** The three WO stock-deduction sites (add-line, convert
estimate, receive-pending) did a non-atomic read-modify-write on `StockLevel.qty_available`, racing
under concurrency. Each `SELECT` now takes `.with_for_update()` (row-lock serialization on Postgres,
no-op on SQLite). Verified against the stock-movement / reservation-lock / convert suites.

**H1 — shift close computes a real expected_cash.** `close_shift` wrote nothing to `expected_cash`,
so the discrepancy was always 0. It now computes `opening_float + cash payments on the shift −
cash refunds on the shift` (card/transfer/balance never touch the drawer) and returns a true
discrepancy. Test: `test_shift_expected_cash` (short-drawer case).

**H3 — contact delete can no longer orphan or crash.** `delete_client` ran a blind
`db.delete(client)`; with FK enforcement now on (144as) that **crashed with a 500** for any contact
that had history. It now walks every table that foreign-keys to `clients.id` (discovered from the
mapper, same source as the merge) and refuses with a 409 pointing to merge if anything references
the contact — financial history is never cascaded away. Clean contacts still delete. Test:
`test_144aw_client_delete_guard`. (Speculative `ondelete=CASCADE` on the order/invoice child trees
was deliberately **not** added: those parents are only ever archived, never hard-deleted — YAGNI.)

Gates: all four new test modules green; related regression suites (money hardening, returns/posting,
merge, reservations) green; imports clean.

**C1 — full-suite sweep under the new invariants (all 267 test files, FK enforced + Numeric money).**
Ran the entire backend suite in chunks (1021 tests) and triaged every failure. Two were REAL
production bugs the invariants/sweep uncovered:
  * `tags.delete_tag` deleted the tag before its `Taggable` rows (no ORM relationship → unordered
    unit-of-work); under enforced FKs this 500'd. Now bulk-deletes attachments + flushes first.
  * `catalog_products.update_product` had the custom-field `resolve_custom_data` call (required +
    lock_after_create enforcement) mis-nested under the `compatibility_json` branch (a 144jt merge
    slip), so required/locked product fields went unenforced on edit unless compatibility_json was
    also sent. Re-tied it to the `custom_data` branch.
The rest were test-harness issues the stricter invariants exposed, not product regressions:
fabricated-org / dangling-FK fixtures (`pos_scan`, `notif_dismiss`, `pnl`) now insert real parent
rows; B1 default-rule seeding interactions (`consent`, `gmail`, `inventory_notif_polish`) clear
seeded rules so each test isolates its own; a stale PO-receive flow (`144ai`) now marks the PO
ordered first; the mandatory-MFA default (`track_b`) is cleared before the password-gate assertion.
Test infrastructure hardened: conftest makes BOTH the app engine (DATABASE_URL) and test engine
worker-aware under pytest-xdist, and the schema fixture now disposes the pool each test so a
connection leaked by an erroring endpoint can't leave the next test's schema half-built (which had
stripped indexes off a table). Suite is green serially and under `-n 4`.

**H4 — observability + CI hardening.** `/health`, `/api/readyz` (DB-probe) and `/metrics` already
existed. The CI migration gate (`alembic upgrade head`) was **silently red**: a clean migrate from
scratch aborted on two real bugs, so production deploys (which migrate rather than create_all)
would have failed. Both fixed: (1) the `job_kits` migration declared `org_id` with `index=True`
*and* an explicit `create_index`, so create_table auto-made the index and the explicit call then
hit "index already exists" — dropped the redundant `index=True`; (2) `fk144as01` (the SET-NULL FK
swap) used a SQLite batch `drop_constraint` by a name SQLite doesn't assign, aborting at batch
flush — made it dialect-aware (real swap on Postgres, no-op on SQLite, which bakes the ondelete in
via create_all). `alembic upgrade head` now succeeds end to end. CI also gains an **advisory**
`alembic check` drift step — kept non-blocking because the dev create_all path has let the models
drift ahead of the migration chain and SQLite reflection yields false-positives; a Postgres-based
reconciliation migration is tracked separately before it can become a hard gate.

**Mediums.** Two real N+1s collapsed: `GET /clients/{id}/purchases` fetched each sale's lines in a
separate query (now one `IN` query grouped in memory), and `GET /sales/stocktakes` ran four queries
per stocktake — location name, bin name, and two line counts — up to ~400 round-trips for 100 takes
(now one query each for names and a single grouped count query). Supplier balance reconciliation:
`GET /suppliers/{id}/ledger` now returns a `discrepancy` and `reconciled` flag comparing the running
`Supplier.balance` column against the linked contact's ledger balance, so the two representations
drifting is detectable instead of silent (a single-source refactor is deferred with H2). The cash
`/balances` endpoint was already a single query, and the AI endpoints already tag every response
with `provider: anthropic|stub` plus a `/api/ai/status` probe, so AI-stub transparency needed no
change. Payroll `/run-all` remains a per-employee accrual (a write batch, not a collapsible read).

**H2 — money Decimal-compute, Phase 1 (scoped + first stage shipped).** Money is already *stored*
exactly (Numeric columns, 144aq); the live risk is *compute* — ~338 `round()` sites use Python's
banker's rounding on floats, which mis-pennies receipts (`round(2.675,2)==2.67`) and sums
inexactly. Shipped `backend/services/money.py` — the one place money quantizes: `q2`/`q4`
(round-half-up, float out for drop-in use), `d2` (exact Decimal), `msum` (exact sum), converting
through `Decimal(str(x))` so decimal text rounds, not binary noise. Tests in `test_money_helper.py`;
applied at the first boundary (invoice subtotal + discount/tax total).

**H2 Phase 2 — COMPLETE (all money `round()` sites converted).** Every `round(x, 2)` → `q2(x)` and
`round(x, 4)` → `q4(x)` across the 36 money-handling router/service modules — **322 sites** — now
goes through the half-up quantizer instead of Python's banker's rounding. Done with an AST-based
codemod (so nested-paren expressions were handled structurally, not by regex), each file's
`money` import added, then verified: every file compiles, no `round(…, 2/4)` remains outside
`money.py`, and the full backend suite (1021 tests across all 267 files) is green under FK + Numeric
+ the new quantizers. `round(…, 1)` (percentages/margins) and integer rounds were correctly left
alone — they're not money. Phase 3 (carrying `Decimal` unconverted through the hot compute chains)
remains optional and documented in `SPEC_money_decimal.md`; it's belt-and-suspenders now that both
storage and compute are exact, so it stays deferred until a real reconciliation gap justifies it.

## build 144aq — The final three hardening items (money precision, in-app dialogs, FK enforcement)

The three large scheduled items from the audit, done one at a time with gates.

**1. Money columns → exact NUMERIC.** 69 money columns (totals, amounts, balances, prices,
unit_price/unit_cost, deposit/subtotal/tax/discount across orders, invoices, payments, sales,
transactions, parts, suppliers, etc.) changed from `Float` to `Numeric(12,2, asdecimal=False)`
(`avg_cost` → `Numeric(12,4)`). Postgres now stores money exactly instead of binary-float REAL.
`asdecimal=False` means Python still gets floats, so **zero arithmetic ripple** — the whole money
suite passed unchanged. Migration `nm144ar01` (batch-mode, SQLite + Postgres). Only unambiguous
money columns were touched — quantities, rates, %, hours, dimensions, fx_rate stay Float.

**2. Native browser dialogs → in-app.** New promise-based `src/lib/dialog.tsx`
(`confirmDialog` / `alertDialog` / `promptDialog`) styled with the design tokens, and **~100
`window.confirm/alert/prompt` call sites across 20 files converted** (destructive actions get a red
danger button). Zero native dialogs remain outside the lib. tsc + build clean.

**3. Foreign-key enforcement.** SQLite now runs `PRAGMA foreign_keys=ON` (both the app engine for
dev and the test engine), so dev/tests finally match Postgres (which always enforces FKs) — a bad
delete or orphan now fails loudly instead of silently passing in dev. Verified safe across ~110
tests spanning every delete/FK-risky path (merge, GDPR client-erase, order archive, gift-card,
coupon, loyalty, booking, payroll, assets, write-off, invoices, reservations). Enforcement
**surfaced a real production bug**: deleting a notification rule that had already fired raised a FK
violation (its `NotificationLog` rows pointed at it). Fixed with `ondelete="SET NULL"` on
`NotificationLog.rule_id` (keeps the log history) — model + migration `fk144as01` + regression test.
Also fixed one test that broke because the B1 default-rule seeding now inflates match counts.

Gates: migration tree = one clean head (`fk144as01`), zero dup ids, all migrations parse; `tsc` +
`vite build` clean; ~110-test backend regression green under FK enforcement incl. the new
`test_notif_rule_delete` and the updated notif-workflow test.

With this, every item from the full-app audit (AUDIT_full_app.md) is addressed. The money paths are
exact-stored, concurrency-locked, and fully reversible on archive; the schema matches prod with FKs
enforced and hot-path indexed; the UI is consistent (no native dialogs), accessible, and currency-
correct; and every exploitable security hole is closed.

## build 144ap — Hardening pass 3 (archive fully reverses + a11y + honest tax)

- **Archiving an order now fully reverses it** — the money-and-stock cluster completed with tests:
  - *(144an)* on-account (balance) payments are no longer cash-refunded;
  - **the closing charge is reversed** (no phantom receivable left on the client) — mirrors the
    reopen path via `order_charge_net`, idempotent;
  - **consumed parts are restocked** (reuses `_reverse_fulfillment`, the same reversal
    delete-line/reopen use). *RED→GREEN: test_money_hardening (charge-reversal + restock).*
- **Accessibility:** 134 icon-only buttons across 35 components got `aria-label`s (close, delete,
  edit, the notification bell, drawer/dialog controls…) — screen readers announced bare "button"
  before. No layout/logic changed.
- **Honest order estimate:** the New Work Order drawer fabricated a flat **8% US tax** on a
  ₹/configurable-tax app; removed it — the drawer now shows an "Items subtotal (before tax)" and the
  real tax is applied server-side from the org's rules on create.

Gates: `tsc` + `vite build` clean; 14-test consolidated regression green (money-hardening,
deletion-chains, merge-completeness, status-gates, staff-authz, IDOR).

### Production-readiness status
Closed across passes 1–3: all exploitable security (cross-tenant cash IDOR, invite priv-esc,
`PUT /company`, public `/lookup` scope, bulk-status gate, Meta-webhook signature, Stripe replay,
prod webhook-secret enforcement); the broad 500 cluster + timezone crash; refund caps + ledger,
double-pay guard, and the **full archive reversal** (cash + charge + stock); migration safety
(23 hot indexes, 39 `server_default`s, prod-backfill script, clean single-head tree); currency
correctness, NaN, 134 a11y labels, dead-code purge, honest tax.

**Concurrency (done):** the three money-write paths now take row locks so concurrent requests
serialize on Postgres (no-op on SQLite) — `cash.pay_order` (locks the order, + the remaining≤0
guard), `finance.record_payment` (locks the invoice), and loyalty redemption (locks the client).
With the refund caps and double-pay guard, the money paths are hardened against double-post /
double-spend races.

Still **scheduled** (large, each needs its own tested pass — called out so they're not lost):
**`Float → Numeric`** for ~80 money columns (deliberate migration with full re-testing); SQLite
**`PRAGMA foreign_keys=ON`** in tests + the FK cascades it surfaces (could ripple across the suite);
and the frontend **native-dialog → Modal** cleanup (~70 sites, cosmetic).

## build 144ao — Production hardening pass 2 (P0 security + prod-migration safety)

Continued the full-app hardening (team on disjoint slices), closing the P0 security and the
remaining safely-fixable prod-migration items.

**Security (P0):**
- **Bulk status-change no longer bypasses the gates.** The bulk `set_status` action set the status
  directly, skipping transition RBAC, completion/pending-stock gates, `status_entered_at`, and
  notifications, and accepted arbitrary strings. Refactored the single-order `change_status` into a
  shared `_apply_status_change(...)` that both the single endpoint and the bulk loop now call;
  bulk validates the target is a real org status and processes each order through the full path
  (best-effort per order, blocked transitions reported as `skipped`). *(verified by the status/gates
  suites)*
- **Public `/lookup` is org-scoped.** It resolved an order by number with no org scope — and numbers
  are per-org, so it could surface another tenant's order and its `public_token`. Now resolves the
  org (shop slug like the sibling public routes, or pins the tenant via the client-phone match) and
  filters by `org_id`; response shape unchanged.
- **Meta inbound webhook is signature-verified** (`X-Hub-Signature-256` HMAC over the raw body,
  constant-time, fail-closed when `META_APP_SECRET` is set) — it was previously unauthenticated.
- **Stripe webhook replay** rejected (300s timestamp tolerance).
- **Prod boot now requires webhook secrets** for the configured provider (Stripe/Square hard-fail;
  Meta warns), via the existing prod fail-closed validator.

**Prod-migration safety:**
- **`server_default` added to all 39 `NOT NULL` add-columns** across 13 migrations, so `ALTER` on a
  populated Postgres table no longer aborts. Defaults match each model's intent (booleans
  `false/true`, ints `0`, strings their semantic default or `''`). Zero revision/chain changes.
- **The two init_db-only backfills now have a prod path:** `scripts/prod_backfills.py` — an
  idempotent one-off (`python -m scripts.prod_backfills`) that unifies orphan suppliers into
  Contacts and seeds default notification rules for orgs lacking them (reuses the tested
  `seed_default_rules`). Smoke-verified: runs clean and a second run is a 0/0 no-op.

**Test hygiene:** fixed a stale assertion in `test_status_record_types` (code has 6 record types incl.
`purchase_order`; the test still expected 5) — a pre-existing drift surfaced by the status re-run.

Gates: all touched modules import; migration tree = one clean head (`ix144an01`), zero dup ids, all
migrations parse; status/gates/archive/dupe-heal suites green (11); deletion-chains + money-hardening
+ authz/IDOR green; prod-backfill smoke idempotent.

**Still queued (big, scheduled — each needs its own tested pass):** pay/refund idempotency +
row-locking and loyalty-redemption race (Postgres-level testing); archive charge-ledger reversal +
restock; `Float→Numeric` money columns; SQLite `PRAGMA foreign_keys=ON` in tests + the FK cascades it
reveals; frontend native-dialog → Modal cleanup, a11y labels, and the hardcoded 8% order tax.

## build 144an — Production hardening pass 1 (money, 500-guards, currency, indexes, cruft)

Executed from the full-app audit (AUDIT_full_app.md) by a review-and-fix team working disjoint slices.

**Money integrity (TDD — test_money_hardening):**
- **Refunds can't exceed what was paid** — `returns.create_refund` now caps the amount at net paid
  (payments − prior refunds) for the order and **posts a `refund` client-ledger entry** so
  `Client.balance` reflects the cash going back (was silent before).
- **`pay_order` rejects double-pay / non-positive** — a second "pay the balance" tap (or retry) on a
  settled order, and negative amounts, now 400 instead of silently double-posting.
- **Archiving an on-account order no longer refunds cash** — payments settled on the client's own
  balance (`method="balance"`) are excluded from the cash refund, so deleting such an order can't
  hand the customer money that never entered the till.

**500 → clean 400 (the fromisoformat/timezone cluster):** every user-supplied date parse in
`timeclock`, `bookings`, `tasks`, `conversions`, `feedback` now goes through `parse_dt`/`parse_date`
(400 on bad input), and timeclock normalizes tz-aware input to naive UTC (was a `TypeError` 500).

**Frontend currency correctness:** the `money()` helper was duplicated in 6 hubs, each hardcoding `₹`
— a non-₹ shop saw ₹ on POS/Finance/Reports/Inventory/Cash/Company. Consolidated to the single
currency-aware helper (symbol from the MeProvider); fixed a `NaN` in the merge-preview balance.

**DB performance:** added `index=True` to 23 hot tenant-key/FK columns (orders, order_lines,
payments, invoices, invoice_lines, transactions, order_status_logs, order_comments, expenses, sales)
+ Alembic migration `ix144an01` (single clean head). Postgres doesn't auto-index FKs; these were
full-scanning at scale.

**Cruft removed:** deleted dead stubs `components/Drawer.tsx`, `pages/WorkOrdersPreview.tsx`, and
stray root files `WorkOrderControlCenter.tsx`, `bin_storage_reference.ts`, `procurement_reference.ts`.

Gates: all touched backend modules import; migration tree = one clean head, zero dup ids; backend
regressions green (deletion-chains, invoice-pay-ledger, money-hardening, merge-completeness);
`tsc` + `vite build` clean.

**Still queued (documented in AUDIT_full_app.md, each needs its own tested pass):** pay/refund
idempotency+row-locking (Postgres), loyalty-redemption race, archive charge-ledger reversal +
restock, `Float→Numeric` money columns; `server_default` on 39 NOT-NULL add-columns, SQLite
`PRAGMA foreign_keys=ON` + FK cascades, the two missing data migrations (supplier-unify, notif-seed);
P0 security (public `/lookup` org scope, bulk-status gate, Meta webhook signature, webhook
prod-enforcement + replay); native-dialog → Modal cleanup, a11y labels.

## build 144am — Full-app audit: exploitable-security + money + slop fixes

A six-reviewer full-app audit (see AUDIT_full_app.md) found the app broadly healthy (clean migration
graph, zero broken FE↔BE calls, solid auth) with risk concentrated in a few places. Fixed the
exploitable + cheap-and-clear items here; the rest are triaged in AUDIT_full_app.md.

- **SECURITY — cross-tenant balance corruption.** `cash.post_transaction` looked up the `Account` by
  id with no `org_id` scope, so a client-supplied `account_id` could skew another tenant's balance.
  Scoped the lookup to the org.
- **SECURITY — privilege escalation via invites.** `POST`/`DELETE /staff/invites` had no permission
  gate, so a restricted employee could invite a teammate as admin. Added `require_perm("can_manage_employees")`.
- **SECURITY — `PUT /staff/company`** (org-wide settings incl. payment provider) had no authz. Added
  `require_perm("can_manage_settings")`.
- **MONEY — supplier balance corruption.** Deleting a *cash-settled* stock posting subtracted
  `doc.total` from `Supplier.balance` even though cash postings never added to it (drove suppliers
  negative). Gated the reversal on `settlement == "balance"`.
- **500s → clean errors.** `list_orders` date filters now parse via `parse_dt` (400 not 500); bulk
  set-priority guards the int cast; `list_orders` and inventory list `limit` are capped (`le=200/500`)
  to stop an unbounded eager-load DoS.
- **FRONTEND slop (fake/decorative data shown as real).** WODrawer no longer shows fabricated
  checklist progress ("3/4 · 5/5") on orders with none — real loading + empty state + real names; the
  Type/Priority dropdowns (which never persisted) are now read-only fields; the always-checked "Client
  Approved" checkbox now reflects real `estimate_approved_at`, read-only.

Gates: backend imports clean; `test_staff_authz` + `test_idor_tenant_isolation` green; `tsc` + `vite
build` clean. Deeper items (pay/refund idempotency, archive reversal, FK enforcement, missing prod
data migrations, hot-path indexes, Float→Numeric money, native-dialog cleanup) are documented and
sequenced in **AUDIT_full_app.md** — they need dedicated tests before a prod push.

## build 144al — Deep-audit fixes: merge completeness, migration IDs, authed images

A 3-agent deep audit of this session's work (144ad–144ak) found two CRITICALs and several HIGHs.
Fixed here, with tests.

- **CRITICAL — contact merge orphaned 15+ client-linked tables.** The merge re-pointed only 6 of
  the **23** columns that foreign-key `clients.id`; the rest (coupons, gift cards, loyalty, POS sales,
  calls, chats, consent log, leads, tasks, the Supplier mirror, `Invoice.payer_client_id`, the
  `Client.payer_id` self-ref, …) were left pointing at a hard-deleted contact — **silent data loss on
  SQLite, an IntegrityError 500 on Postgres** for any real contact. The merge now discovers **every**
  client FK from the mapper (`_client_fk_targets`, can't drift as models are added), re-points them
  all, and records each in the undo map. *RED→GREEN: test_merge_completeness (coupon round-trips
  through merge+undo; metadata guard asserts full coverage).*
- **CRITICAL — the three new Alembic migrations couldn't deploy.** Their revision IDs collided with
  pre-existing migrations (`a7c9e1g3i5k7`, `c9e1g3i5k7m9`) and chained off the wrong head, so
  `alembic upgrade head` failed. Re-IDed to unique `wa144ag01 → ml144aj01 → ip144ak01`, chained off
  the real head `nd144bp01`; the tree now resolves to a single clean head.
- **HIGH — undo clobbered the target's balance.** Undo reset `balance` to the pre-merge snapshot,
  discarding any payment/advance taken after the merge, and broke under stacked/out-of-order undos.
  Now it **delta-reverses** (subtracts the restored sources' balances) and restores a filled field
  only if the merge set it AND it hasn't been edited since — order-independent and non-destructive.
  *Test: test_undo_preserves_post_merge_balance_activity.*
- **HIGH — intake photos (and checklist attachments) showed broken images.** `GET /api/media/{id}`
  needs the bearer header, which a plain `<img src>` can't send (the same reason `CodeSvg` fetches its
  SVG). New shared `AuthedImage` component fetches with auth and renders from an object URL; wired
  into the intake gallery, the create-order thumbnails, and the pre-existing checklist attachments.
- **MEDIUM — intake-photo blob ids are now validated** against the org (no cross-tenant reference) and
  the list is de-duped and capped at 40.
- **DRY — `readAsDataUrl`** extracted to `lib/files.ts` (was copy-pasted in 3 places this session).

Gates: `tsc` clean; `vite build` clean; 14 backend tests green (merge completeness/undo/preview/ledger,
intake photos, watch-out, notif seed); WO drawer live-verified with no console errors after the refactor.

### Known, deferred (pre-existing — not this session's features)
- Merge/preview/undo scope by `org_id` but not by per-user *record scope* (a scoped rep could merge
  contacts outside their scope); sibling `delete_client` is ungated the same way. Consider a
  contacts-manage permission + `can_view_client` check.
- Pre-existing frontend slop flagged by the audit (out of scope): decorative no-op controls
  (InventoryDrawer image dropzone, CreateOrderDrawer step-2 tabs, WODrawer "Actions"/"Client Approved"),
  WODrawer Type/Priority selects that don't persist, static demo KPIs + sample-PII fallbacks,
  `prompt()` still used for gift-card/coupon issue in the Loyalty tab, and `CodeSvg`/`QrSvg`
  duplication. Recommend a dedicated cleanup pass.

## build 144ak — Intake condition photos on work orders

The camera at check-in was decorative; now it captures real dispute-protection photos.

- **Capture at intake.** The New Work Order drawer's device photo tile is a working multi-file
  uploader ("Condition photos · browse") — each image uploads to `/api/media`, shows as a thumbnail,
  and the blob ids ride along on order create as `intake_photos`.
- **Gallery on the work order.** The WO drawer's General tab gains an "Intake photos — condition at
  check-in" card: view full-size, add more, or remove, with an empty-state nudge. Adds/removes write
  through `PATCH /orders/{id}/intake-photos`.
- **Backend.** New `Order.intake_photos` JSON column (list of media blob ids); accepted on create,
  resolved to `{id, url}` in the order detail, and replaceable via the new PATCH endpoint. Reuses the
  existing media storage (`POST /api/media` + `GET /api/media/{id}`) — no new storage surface. New
  column via create_all on dev; Alembic migration `c9e1g3i5k7m9` for production.
- Verified live: the capture tile renders in the create drawer and the gallery (with Add + empty
  state) renders on the work order; the upload→attach→serialize→patch path is covered by tests.

Gates: `tsc --noEmit` clean; `vite build` clean; new backend tests pass (photos attach on create and
surface as URLs; PATCH replaces/clears the set). *RED→GREEN: test_intake_photos.*

## build 144aj — Contact merge is now reversible (merge preview/undo, phase 2)

Phase 1 made the merge *informed*; this makes it *recoverable*. Merge is no longer a one-way door.

- **Reversible merge.** Before deleting the sources, the merge now snapshots each one's full row and
  records exactly which records moved (and from which source) plus the target's prior fields and
  balance, into a new `merge_logs` row. Everything the merge did is captured, so it can be reversed.
- **Undo endpoint.** `POST /clients/merge/{log_id}/undo` recreates the deleted source contacts with
  their original ids, moves each record back to the source it came from (only if it's still sitting
  on the target — safe against later edits), and restores the target's filled fields and balance.
  Undoing an already-undone merge is refused.
- **Undo in the UI.** After a merge the Contacts list shows a green "Merged N contact(s) into X · Undo"
  banner; one click restores everything. The preview dialog's note now says the merge can be undone.
- New table `merge_logs` (dev via create_all; Alembic migration `b8d0f2h4j6k8` for production).
- Verified live end-to-end: merged two contacts (12 → 11, orders combined), clicked Undo, and the
  list returned to 12 with both contacts and their orders exactly as before.

Gates: `tsc --noEmit` clean; `vite build` clean; new backend tests pass (merge→undo restores
sources, records, fields and balance; double-undo rejected); preview + merge-ledger suites still
green. *RED→GREEN: test_merge_undo.*

## build 144ai — Merge preview (contact merge, phase 1 of preview/undo)

Contact merge was one-click-and-gone behind a native `confirm()`. Now it shows exactly what will
happen first.

- **Dry-run endpoint.** `POST /clients/merge/preview` reports what a merge *would* do without
  changing anything: counts of records that move (devices, orders, invoices, payments, feedback,
  balance entries), which blank target fields would fill and with which source value, and the
  combined balance. Shared validation with the real merge (same guards), so the preview can't claim
  a merge the merge itself would reject.
- **Preview dialog.** The Contacts bulk "Merge" action now opens a dialog listing the sources being
  removed, a "what moves" breakdown, the blank fields that fill, the combined balance, and an honest
  "can't be undone yet" note — then Merge or Cancel. Replaces the old native confirm popup.
- Verified live: selecting two contacts and choosing Merge shows the breakdown ("2 work orders, 1
  invoices") pulled from the dry-run, and Cancel leaves everything untouched.

Phase 2 (reversible merge + undo via a source snapshot) is scoped next; this phase makes the merge
*informed*, the next makes it *recoverable*.

Gates: `tsc --noEmit` clean; `vite build` clean; new backend tests pass (preview reports moves/fills
and commits nothing; rejects target-as-source); the merge-ledger regression still green. *RED→GREEN:
test_merge_preview.*

## build 144ah — Starter customer notifications for every shop (B1)

New orgs (and existing ones) no longer start out silent on order updates.

- **The gap:** the notification engine and rule-builder were fully built and `status_changed`
  already fired rules — but a shop began with **zero rules**, so customers got nothing until staff
  configured notifications by hand.
- **The fix:** registration now seeds three template-less, client-facing order rules — *order
  received* (`created`), *status changed* (`status_changed`), and *completed* (`closed`) — on the
  email channel. They carry no template, so the engine renders its sensible per-event default
  message. Staff can edit, disable, or delete them in the existing rule-builder, and add SMS/WhatsApp
  or templates on top.
- **Backfill:** existing orgs with no client order rules get the same starter set on startup
  (dev via `init_db`; production via the matching Alembic step). Seeding is **idempotent** — an org
  that already has client order rules is never touched or duplicated.

Gates: `tsc` n/a (backend-only); new backend tests pass (starter rules present on a new org;
re-seeding a configured org is a no-op); balance + watch-out suites re-run green. *RED→GREEN:
test_seed_default_notif_rules.*

## build 144ag — Watch-out warnings on work orders + inline Add-Credit

Adds a prominent handling-warning system and removes the native popup from Add Credit.

- **Watch-outs (new).** A contact and an asset can each carry a free-text warning the front desk
  should see before touching a job — "battery swollen, handle with care" on the asset, "always
  disputes charges" on the customer. New nullable `watch_out` column on **both** `clients` and
  `devices` (dev auto-syncs the column; Alembic migration `a7c9e1g3i5k7` owns it for production).
  The order-detail payload now carries `client.watch_out` and `device.watch_out`, and the work-order
  drawer shows an amber banner (asset + customer lines) above every tab, with inline edit that writes
  back via `PATCH /clients/{id}` and `PUT /assets/{id}`. When none is set, a quiet "Add a watch-out
  warning" affordance sits in its place. *RED→GREEN: test_watch_out_banner (persist + surface on the
  order; absent → null).*
- **Add Credit no longer uses a browser `prompt()`.** The ClientDrawer header and the Loyalty-tab
  credit control now reveal a small inline amount field (Enter to submit, Esc to cancel) posting to
  `/clients/{id}/balance` — smoother, and no blocking native dialog.

Gates: `tsc --noEmit` clean; `vite build` clean; new backend tests pass; the merge + order-detail
suites re-run green (one unrelated single-file failure is the known `TEST_DATABASE_URL` harness quirk,
green under the full suite).

## build 144ae — Work-order Filter panel + requested-deposit control (Sprint A, batch 2)

Two more dead/absent controls wired over endpoints that already existed — no new backend surface.

- **Work orders "Filter" button was dead.** It now opens a multi-facet panel: **Assignee**
  (any / unassigned / each technician on the board), **Priority** (Normal / High / Urgent), and
  **Scheduling** (scheduled / unscheduled). Facets filter the same loaded set as the status KPIs and
  text search, the button shows a count badge when active with a one-click Clear, and the facets are
  **saved and restored with Saved Views** (added to the view's `params`; `applyView` rehydrates them).
  Page resets to 1 when a facet changes. Pure client-side over the already-fetched orders.
- **Requested deposit on a work order.** The Invoices & payments tab gains a "Requested deposit"
  card: 25 / 50 / 100 % quick-set or a custom amount, posting to `PATCH /orders/{id}/deposit`
  (amount **or** percent of total, validated server-side), showing the current requested figure with
  a Clear action. The customer pays it through the existing payment link / portal — this records what
  to ask for. Seeded from the order's `deposit_amount`.

Gates: `tsc --noEmit` clean; `vite build` clean. Frontend-only (both endpoints pre-existed and are
unchanged), so no new backend tests.

## build 144ad — Merge-ledger data fix + wired the dead action buttons (Sprint A)

Fixed one live data-integrity bug and connected buttons that rendered but did nothing — each over
an endpoint that already existed, so this is wiring, not new backend surface.

- **Contact merge orphaned store credit (data-integrity).** `POST /clients/merge` re-pointed
  devices, orders, invoices, payments and feedback onto the target and then **hard-deleted** each
  source — but it never moved the source's `ClientLedgerEntry` rows. The audited balance history
  (advances, payouts, adjustments = real store credit) was left pointing at a deleted contact:
  orphaned, and FK-breaking on a real DB. Merge now re-points the ledger too (added to the same
  re-point loop; `moved` gained a `ledger` count). *RED→GREEN: test_merge_moves_ledger.*
- **ClientDrawer header "+ Add Credit"** was dead (a working one was buried in the Loyalty tab).
  Now posts an advance to `/clients/{id}/balance` and reloads the header balance in place.
- **ClientDrawer header "+ New Work Order"** was dead. Now opens the real create-order drawer
  **prefilled with the contact** (new optional `prefillClient` prop on `CreateOrderDrawer`; it
  seeds the client step so the front desk skips the search).
- **InventoryDrawer "Barcode / Generate barcode / Generate QR"** were three dead buttons over a
  finished endpoint. "Generate barcode/QR" now call `POST /catalog/products/{id}/barcode/generate`
  (Code128 or QR), persist the assigned code, and the "Show code" toggle inlines the authed
  `/catalog/code.svg` preview (same generator the label/print engine uses). Generate is disabled
  until the product is saved (the endpoint writes to the row), with a "Save first" hint.

Gates: `tsc --noEmit` clean; `vite build` clean; new + adjacent backend tests green
(test_merge_moves_ledger, test_client_balance, test_client_purchases). No DB migration.

## build 144ac — Deferred audit items: hardening, atomicity, and N+1 (batch 3)

The lower-severity backend findings from the deep audit.

- **Coupon redemption was a non-atomic read-modify-write** — two concurrent redemptions of a
  single-use coupon could both pass the check and exceed `max_uses`. Now a guarded atomic UPDATE
  (`uses = uses + 1 WHERE max_uses IS NULL OR uses < max_uses`, 400 if it didn't take), mirroring the
  gift-card path. (POS/coupon suite still green.)
- **`simulate-pay` payment bypass** — the unauthenticated simulated-gateway success hook is now refused
  when `settings.is_production`, so a guessed intent id can't mark an invoice paid in prod.
  *(test_simulate_pay_prod_gate)*
- **Malformed dates returned 500 instead of 400** — added `services/dates.py` (`parse_dt`/`parse_date`
  → clean 400) and applied it at 20 request-body date parses across bookings, cash, schedules, payroll,
  v1 and orders. *(test_bad_date_returns_400)*
- **N+1 queries** — `chats.list_threads` did 1+2N queries with **no limit**; now 3 queries + a 200 cap
  (batched client names and per-thread last message). `analytics.fleet_overview` (2 queries/company)
  and `fleet_detail` (1 query/device) are now batched with grouped counts / a single `IN` fetch.
  Output verified unchanged — the existing `test_analytics_fleet` and chat-scoping suites pass.
- **Cross-org email enumeration — intentionally left as-is.** Login resolves employees by email
  *globally* (auth.py), so email must be globally unique; scoping the staff uniqueness check to the org
  would break login. The minor info-leak is inherent to global-email logins (register/reset leak the
  same way) and isn't fixable without redesigning auth to org-scoped sign-ins.

Gates: all touched routers compile; new tests pass; coupon/gift-card POS suite (54 tests) and
analytics/chat suites re-run green. Backend-only; dev server auto-reloads.

## build 144ab — Data-integrity + frontend fixes from the deep audit (batch 2)

The rest of the audit findings. The three money/stock fixes were done TDD (failing test → fix →
pass), one at a time.

Data integrity (backend, each with a pytest regression):
- **Invoice payment bypassed the contact ledger.** `POST /finance/invoices/{id}/pay` bumped
  `amount_paid` but never posted to the shared ledger, so `Client.balance` and the Balance-tab
  statement drifted vs what was actually paid (the cash.py paths post correctly). Now posts
  `income, -amount` keyed by the payment id, exactly like cash.py. *(test_invoice_pay_ledger)*
- **Estimate→WO conversion logged a corrupt stock movement.** It recorded `qty=+l.qty` (positive, full
  line qty) while actually deducting `deduct`; every other consumption logs `qty=-deducted`. Fixed to
  `-deduct` so movement-sum reports/valuations are correct. *(test_convert_estimate_stock_movement)*
- **Transferring a work order then reversing corrupted per-location stock.** `_reverse_fulfillment`
  returned stock to `order.location_id`, but after a transfer that differs from where the parts were
  deducted — so the original location stayed short and the new one gained phantom units. Now returns to
  the location recorded on the "use" movement (fallback to order.location_id). Root-cause fix at the
  shared reversal helper; no-op for non-transferred orders. *(test_transfer_reverse_location, RED/GREEN
  proven)*

Frontend:
- **Hardcoded identity** — the sidebar/header showed "Admin User / Senior Lead Tech / AD" for everyone;
  now driven by `useMe()` (real name, role, initials).
- **Scan deep-open** — scanning a product or asset landed on the list but never opened the drawer (only
  Work orders read `?open=`). InventoryHub (product) and AssetsHub (asset) now read it too.
- **Assets search** was a decorative input — now filters the list (brand/model/serial/IMEI/owner).
- **Bundles stale-after-save** — the Bundles tab now takes the `refresh` signal, so a saved bundle
  appears without a tab switch (matching Services/Products).
- **3 more blank-circle icons** mapped: `qr_code_2`, `sticky_note_2`, `receipt`.

Deliberately NOT changed (these are unbuilt features needing product intent, not one-line bugs, and are
reported as such): ClientDrawer "+ Add Credit" / "+ New Work Order" / "Delete Client", the Work Orders
"Filter" button, the InventoryDrawer barcode/QR-generate buttons, and various row kebab menus.

Also deferred (lower severity, from the audit): analytics/chats N+1, the `fromisoformat`-500 cluster,
coupon redemption atomicity, `payments.simulate-pay` prod-gating, cross-org email enumeration.

Gates: FE tsc clean + build green + deployed; all new backend tests pass; reversal/stock/finance suites
re-run green; identity fix live-verified (sidebar shows the real user + role).

## build 144aa — Security & integrity fixes from the deep audit (batch 1)

A parallel deep audit (frontend/backend/security/data-integrity) surfaced real bugs; this is the first
fix batch — the critical auth hole, the tenant-isolation leaks, and two integrity bugs — each with a
pytest regression.

- **CRITICAL — privilege escalation in `staff.py`.** The employee / role / password / MFA / delete
  endpoints were gated by `get_current_employee` only — no permission check — so any authenticated
  employee could `PATCH /employees/{self}` with `role_id: null` to become owner, reset a teammate's
  password, or rewrite roles. Added `require_perm("can_manage_employees")` / `can_manage_roles` to all
  nine. Test: a restricted tech now gets 403 on every one and cannot escalate; owner unaffected.
- **HIGH — cross-tenant IDOR (3).** `finance.close_shift` looked up CashShift by id with no org scope
  (it has no org_id — now scoped via its Location → org); `sales.set_count` mutated a StocktakeLine
  without checking the parent Stocktake's org; `suppliers.reserve/unreserve_po_line` touched PO lines
  without the Supplier→org join every other PO handler uses. All three now scoped. Also hardened
  `finance.open_shift` to reject a foreign location. Test: org A gets 404 on org B's shift, org B gets
  200.
- **MEDIUM — category delete could orphan services/bundles.** The shared category tree is used by
  products, services and bundles, but `delete_category`'s in-use guard only counted products — so
  deleting a service/bundle category orphaned those rows (exposed by the recent move of services onto
  `category_id`). Guard now counts Service and Bundle references too. Test: in-use service category →
  409, unused → 200.
- **HIGH — PO "cancelled" vs "canceled" reporting leak.** Writes use `cancelled`; the outstanding-PO
  and supplier-balance reads filtered `canceled`, so cancelled POs still counted as outstanding. The
  four read filters (reports.py ×2, suppliers.py ×2) now exclude both spellings.

Gates: all four routers compile; new tests pass (`test_staff_authz`, `test_idor_tenant_isolation`,
`test_category_delete_guard`); existing staff/stocktake/PO/supplier suites still green (72 pre-existing
tests re-run across the touched areas). Dev server auto-reloads.

Still open from the audit (deferred for per-fix review as requested): invoice-pay ledger bypass, order
transfer stock linkage, estimate→WO conversion stock movement; plus the frontend UX batch.

## build 144z — Loading skeletons across all list tables

Replaced the plain "Loading…" cell in every list table with a shimmer skeleton, so tables fade in with
their shape intact instead of flashing a bare line of text.

- **New `SkeletonRows`** (`components/SkeletonRows.tsx`) — renders N shimmer rows (default 6) matching
  the column count, using the shared `.sk` shimmer (already respects `prefers-reduced-motion`). Keeps a
  screen-reader `role="status"` "Loading…" announcement so a11y isn't regressed vs the old text.
- Swapped it into **26 table loaders across 11 hubs** — Assets, Cash, Clients (+Suppliers), Company
  (hours/services/products), Finance (txns/invoices/payroll/runs/balances), Inventory (devices/PO/audit
  /batches/bins/forecast), Job kits, Marketing (campaigns/calls), Reports, Tasks, Timesheets, Chats. The
  Stock tab's existing detailed skeleton (image + two lines) was left as-is.

senior-architect-qa pass: skeleton renders per-column (not a single colSpan cell) so widths stay put;
a11y announcement preserved; only list tables touched — drawer/dropdown/inline loaders left alone
(skeletons there would be over-engineering).

Gates: tsc clean, FE build green + deployed. Live-verified by briefly throttling the products fetch —
the Company → Products table showed 6 shimmer rows across all 7 columns with the header and widths
intact, then loaded normally.

## build 144y — App-wide icon sweep: 43 blank-circle icons fixed

Found via a systematic sweep (diff every `<M n="…">` ligature used against the Shell ICONS map): **43
distinct icons were unmapped and rendering as blank circles** — the same fallback bug caught twice
before (144bw, chevron_left), but this time hunted exhaustively instead of one at a time.

Highest-frequency offenders: `local_shipping` (×6), `download` (×6), `insights` (×4), `history` (×4),
plus `lock`, `cancel`, `target`, `archive`, `table_view`, `attach_file` and ~33 more singletons across
Finance, Inventory, Marketing, Sales, Settings and the audit tabs.

Mapped all 43 to semantically-matched lucide icons (e.g. local_shipping→Truck, download→Download,
insights→LineChart, target→Target, lock→Lock, cancel→CircleX, verified_user→ShieldCheck). tsc confirms
every lucide name resolves; a re-run of the sweep shows **zero** literal `<M>` icons still unmapped.

Gates: tsc clean, FE build green + deployed. Live-verified — the Inventory → Purchase orders "Cockpit"
button now shows a target/bullseye icon instead of the bare circle it rendered before.

## build 144x — Category Phase 2 audit: no migration needed; surface picker create errors

senior-architect-qa pass on the category feature, with the Phase-2 "migrate legacy free-text → tree"
question decided on evidence.

**Phase 2 migration — not built (YAGNI, evidence over claims).** Checked the live data via the app's
API: 276 products and 5 services, **zero** with a free-text `category` lacking a `category_id`. And no
code path creates orphans — the CSV import resolves its Category column to a `category_id` via
`_resolve_category_path`, the picker writes `category_id`, and the product update syncs the
denormalized name. A backfill migration would be dead code; dropping the legacy column is a
destructive schema change for no benefit. Nothing read free-text as a source of truth anymore, so the
field is already retired in practice.

**One real fix shipped.** `CategoryPicker.create()` swallowed errors silently — a user without
`can_manage_inventory` (or any backend error) clicked "Create X" and got no feedback. Now it shows an
inline error in the dropdown. (This also softens the known low-severity create-then-immediately-Save
race: a failed create is now visible instead of silently leaving the row Uncategorized.)

Audit verified sound: category_id persisted on create/update for both products and services; shared
picker used by both the Inventory and Company drawers; Company list refresh working; import clean.
Gates: tsc clean, FE build green + deployed.

## build 144w — Categories for products & services: one tree, assignable from a picker

"I don't see category for products and services" was really a fragmentation bug: two category systems
were fighting, and the edit drawers were wired to the wrong one.

Root cause:
- A shared hierarchical **category tree** (`category_id`) exists, with a manager, and backs the
  Inventory column + filter. But the **product drawer's Category box was free-text bound to the legacy
  `category` string while save sent `category_id`** — so typing a category did nothing and every
  product stayed "Uncategorized" (a silent no-op). The **service drawer** wrote legacy free-text,
  ignoring the tree entirely. And **Company → Products had no Category column at all.**

Fix (pitched as Option A, phased P0+P1; brainstorm → council → build):
- **New `CategoryPicker`** (`components/CategoryPicker.tsx`) — a searchable, hierarchical dropdown
  bound to `category_id`, with inline **"+ Create"** (POST /catalog/categories) and an Uncategorized
  option. One reusable component.
- **Both drawers** (InventoryDrawer product + service) now use the picker against `category_id`,
  replacing the free-text boxes. Fixes the product no-op; moves services onto the shared tree. Service
  save now sends `category_id` (keeps `category` untouched as a fallback).
- **Company → Products** gained a Category column, a category filter, and a Categories manager button;
  **Services** gained a category filter for parity. Both resolve `category_id` → name against the tree.
- **Bug found during live verify:** Company's drawer `onSaved` only closed the drawer, never reloading
  the list — so edits (including the new category) didn't show. Added a refresh signal so the
  Products/Services tabs re-fetch on save. (Pre-existing; surfaced by this feature.)

No DB migration — `category_id` columns and serializers already existed; the product update endpoint
already syncs the denormalized name via `_sync_part_category_name`.

Gates: tsc clean, FE build green + deployed. Live-verified end-to-end in Chrome — created "Batteries"
inline from the product picker, saved, and it showed in the Company Products column (list
auto-refreshed); then reverted the product and deleted the test category (test data cleaned up).
Reversible build.

## build 144v — Pagination app-wide: every record list now pages at 50

Took the shared `Pager` to the remaining record lists across the app. Ten hubs now import the one
component.

Paginated this build:
- **Assets** (`AssetsHub`) — registry table, cap 100 → 1000.
- **Cash** (`CashHub`) — per-account transactions, cap 100 → 1000; account totals row stays full-set;
  resets on account switch.
- **Finance** — Transactions (cap 100 → 1000) and Client balances tabs, reset on tab.
- **My company** — Employee hours (time entries), Services, Products (276 → 6 pages), reset on search.
- **Inquiries** — **list view only** (board stays a kanban), reset on view switch.
- **Tasks** — list, reset on status filter.
- **Marketing** — Campaigns and Call log tabs, reset on tab.

Deliberately **not** paginated, with reason: Bookings (agenda/scheduler = calendar), My Work (card
view), Chats (messaging pane), Reports (analytical tables meant to be scanned/exported whole),
Timesheets (per-employee, date-range-scoped sheet with totals — a report, not an unbounded list),
Settings (config), Sales POS (search grid), and all drawers/pickers/autocompletes. Paginating an
analytical or bounded view is an anti-pattern, not consistency.

Senior-architect-qa pass caught one real bug: **`GET /timeclock/entries` hard-coded `.limit(500)` and
ignored its `limit` query param** — so the Employee-hours pager would have silently capped at 500.
Fixed: the endpoint now takes `limit: int = 500` and honours it (frontend asks for 1000). Every other
raised-cap endpoint (`/cash/transactions`, `/orders`, `/clients`, `/finance/invoices`, `/assets`)
applies `.limit(limit)` directly with no cap — verified. Catalog products/services/bundles and
`/tasks`, `/comms/*`, `/leads/board` take no limit (return all), so client-side paging sees everything.

Gates: `pager.test.ts` passes, tsc clean, FE build green + deployed; `timeclock.py` compiles (dev server
auto-reloads). Live-verified My company → Products "Showing 1–50 of 276" with `‹ 1 2 … 6 ›`. Reversible
build.

## build 144u — Pager extracted to a shared component; Work orders, Clients, Invoices paginated

Took the pager app-wide. First a refactor so there's one pager, not four copies; then wired it into
the big lists outside Inventory.

- **Shared component**: `pageNumbers()` moved to `lib/pager.ts` (+ `lib/pager.test.ts`), and the pager
  UI moved to `components/Pager.tsx`. Inventory now imports it instead of defining its own — no
  behaviour change there, just DRY. One pager, now used by Inventory (Stock, Devices, PO, 7 audit
  tabs), Work orders, Clients, Suppliers and Invoices.
- **Work orders**: table view paginates at 50/page; **board and scheduler are deliberately untouched**
  (kanban columns and the calendar aren't paged lists). Page resets on status filter, search, or view
  change. `/orders` fetch cap 100 → 1000.
- **Clients + Suppliers**: both tabs 50/page off one shared page counter that resets on tab/search
  change. `/clients` cap 200 → 1000.
- **Invoices**: 50/page; `/finance/invoices` cap 50 → 1000 (it previously fetched only the first 50
  with no way to see the rest).

Senior-architect-qa pass: confirmed the three list endpoints apply `.limit(limit)` directly with **no
server-side cap**, so `limit=1000` is honoured and the "of N" total is truthful (the main risk of a
client-side pager over a capped fetch). Select-all means all *filtered* rows and survives paging
(id-keyed); the pager clamps when a filter shrinks the set. Named upgrade path unchanged: any list
past ~2k rows flips that one fetch to server-side `limit/offset`.

Gates: `pager.test.ts` passes, tsc clean, FE build green + deployed. Live-verified in Chrome — Clients
"1–12 of 12", Work orders "1–16 of 16" (table view), Invoices "1–4 of 4"; multi-page behaviour already
proven on Stock (276) and Postings (285) using the same component. Reversible build.

## build 144t — Pager everywhere in Inventory + a TDD test for its page-number logic

Extended the 144s pager to the rest of the Inventory tables, and backfilled the unit test the
page-number logic should have had from the start (superpowers / TDD pass).

- **New `pager.ts`** — the page-number/ellipsis sequence (`‹ 1 2 … N ›`) extracted into a pure
  `pageNumbers(current, pages)` and covered by `pager.test.ts` (written RED → GREEN before wiring):
  single page, all-shown-no-ellipsis, leading/trailing/both-side gaps, and the 0-pages guard. `Pager`
  now calls it instead of an inline loop — no behaviour change, just a tested seam.
- **Purchase orders** and the **shared audit renderer** (Postings, Reservations, Conversions,
  Transfers, Stock-takes, Write-offs, Returns) now paginate at 50/page via one `secPage` that resets
  to 1 on tab change (only one of those tabs is visible at a time, so one counter is enough).
- Bundles was *not* touched — it moved out of Inventory to CompanyHub, so there's nothing to page here.

Gates: `pager.test.ts` passes, tsc clean, FE build green + deployed. Live-verified in Chrome — Purchase
orders shows "Showing 1–5 of 5" (single page, no numbers); Postings shows "Showing 1–50 of 285" with
`‹ 1 2 … 6 ›`. Reversible: self-contained build.

## build 144s — Inventory pagination: 50/page + numbered pager (Stock + Equipment)

The Stock and Devices/Equipment lists used to dump every row into one long scroll — the
"Previous · 1 · Next" footer was a dead stub wired to nothing. Both now paginate at **50 rows/page**
with a real numbered pager.

Research → brainstorm → council (tech-PM) first, per request. Outcome: **client-side** pagination,
not server-side offset. The sets are small (hundreds), and search + availability filtering already run
in the browser instant across the *whole* set — server paging would have quietly broken that unless
search also moved server-side (a much bigger change for zero benefit at this scale). Named upgrade
path if a catalog ever crosses ~2k rows: flip that fetch to `limit=50&offset=` + server-side search.

- **New `Pager`** (replaces the `Footer` stub in `InventoryHub`): shows "Showing X–Y of N {noun}" and
  `‹ 1 2 … N ›` with ellipsis truncation (first + last + window of ±1 around current), current page as
  a solid pill, chevrons disable at the ends. Page numbers hide entirely when there's only one page.
  `chevron_left` was missing from the Shell icon map (would've rendered as a circle — the known
  fallback bug); added `ChevronLeft`.
- **Stock**: `pageProducts` slices the already-filtered `shownProducts` to 50/page; page resets to 1
  on any search/filter change and clamps if it falls off the end (e.g. after an archive). Select-all
  now means **all filtered rows** (was: the unfiltered catalog), and selection survives paging since
  it's keyed by id.
- **Devices/Equipment**: `pageAssets` slices at 50/page; `/assets` fetch cap raised 100 → 1000 so the
  pager sees the full set.

Gates: tsc clean, FE build green + deployed to `dist`. Live-verified in Chrome on localhost — Stock
shows "Showing 1–50 of 276" with `‹ 1 2 … 6 ›`, page 2 → "Showing 51–100 of 276"; Equipment shows
"Showing 1–4 of 4" with no page numbers (single page). Reversible: self-contained build, revert if
not wanted.

## build 144r-3 — Job kits: catalog-linked lines that deduct stock

The job-kit enhancement flagged earlier. Kit lines can now reference catalog items, so applying a kit
behaves like adding those items by hand — including stock deduction for products.

- **Kit editor** (`JobKitsPanel`) gained a catalog picker ("Add from catalog") alongside the custom
  line builder; picked items store `kind` + `ref_id` + price and show a "linked" badge. Custom
  (free-form) lines still work for one-offs.
- **Apply path** (`orders.apply_job_kit`) now runs catalog-linked **product** lines through the same
  reservation-aware `_maybe_deduct` the normal add uses — so applying a kit draws the parts from
  stock (and 409s if they're held for another job). Free-form lines and estimates never deduct
  (unchanged). `ref_id`/`kind` were already carried onto the order line.

TDD: new `test_job_kits_apply_stock.py` — applying a catalog-linked kit deducts the right qty and the
order line carries the link; a free-form kit line leaves stock untouched. Both pass, plus the existing
perm test and the reserve-stock regression. Gates: tsc clean, FE build green + deployed; backend
compiles (dev server auto-reloads).

## build 144r-2 — Job kit follow-up: gate writes behind can_manage_inventory

Follow-up flagged in the job-kits QA. Creating / editing / deleting a job kit now requires the
`can_manage_inventory` permission (same gate as the rest of inventory writes); **listing stays open**
so any technician can still see kits to apply to a work order. Covered by a new test
(`test_job_kits_perm.py`): owner can write, a tech with the permission denied gets 403 on
create/patch/delete but 200 on the list. Backend-only change; pytest passes.

Connectivity audit, closed: regenerated the 3D app-connection map
(`app-connection-3d.html`) — with prefs, job-kits and schedules now wired and admin marked as the
intentional SaaS-operator surface, the map shows **zero genuine disconnected back-ends** (the former
`/api/feed` and `/api/api-keys` flags were static-analysis false negatives on dynamic paths).

## build 73-FE — Connect orphaned /api/schedules: planned shifts + plan-vs-actual

Final connectivity fix from the app-graph audit. `/api/schedules` (planned shifts/absences,
Build 73) had no front-end caller, so the plan-vs-actual variance — the whole point of the feature —
couldn't function (there was no way to enter the plan). Now wired into Timesheets:

- **Planned shifts panel** on the Timesheets hub (reuses the existing employee + date-range
  selectors): lists planned shifts/absences for the selected employee in range, with a
  **Planned / Actual / Variance** readout from `/schedules/compare` (variance coloured — red under,
  amber over, green on-plan).
- **Add shift / absence drawer** — employee, day, type (shift / day off / vacation / sick), start/end
  (shift only), note → `POST /schedules`. Delete per entry. Server enforces `can_manage_staff` for
  writes and `can_manage_payroll` for the compare (the panel degrades gracefully if denied).

Verified live end-to-end (API + UI): create shift (201, 8h planned) → lists → compare returns
planned/actual/variance (200) → delete (200), cleaned up. The panel renders on the page with the
compare row. Gates: tsc clean, build green, deployed.

This closes the connectivity audit: of 4 orphaned back-ends, **prefs, job-kits and schedules are now
wired**, and admin was correctly left as the separate SaaS-operator console (role-based shop access is
already covered by the existing RBAC).

## build 144r-FE — Connect orphaned /api/job-kits: Job kits manager + one-click apply

Second connectivity fix from the app-graph audit. `/api/job-kits` (reusable parts+labour bundles,
built in 144r) had full CRUD on the backend and an apply endpoint on the orders router, but **no UI
called either** — the feature had no door. Now wired on both ends:

- **Job kits manager** — new `JobKitsPanel`, mounted as an Inventory "More" view (Inventory → Job
  kits). List / create / edit / delete kits; per-kit line builder (type · description · qty · unit
  price) with a running total, active toggle, empty state with a primary CTA.
- **Apply a job kit** on the work order — a picker in the WO drawer's Services & products tab that
  lists active kits and calls the existing `POST /orders/{id}/apply-kit/{kit_id}`, then reloads the
  order and toasts.

Verified live end-to-end (API flow in Chrome): create kit (201, total computed) → appears in list →
apply to a work order (200, order went 0 → 2 lines) → test lines + test kit cleaned up (deletes 200 /
204, order restored). Gates: tsc clean, build green, deployed.

Known limitation (follow-up, not a blocker): kit lines are free-form (description + price), so applied
part lines are custom lines and don't auto-link to the catalog / deduct stock. Catalog-linked kit lines
are a future enhancement.

## build 144ca-FE — Connect orphaned /api/prefs: cross-device saved views + density

Connectivity fix from the app-graph audit: `/api/prefs` (per-user UI prefs, built in 144ca) had no
front-end caller — the Inventory saved views and density toggle lived only in `localStorage`
(per-browser). They now sync through `/api/prefs` so a user's saved views and density follow them
across devices, with `localStorage` kept as an instant cache (no paint flash) and offline fallback.

- New `lib/userPref.ts` — `cachedPref` (sync, for first paint), `loadPref` (server reconcile on
  mount), `savePref` (write-through: cache + best-effort server PUT).
- InventoryHub saved views + density migrated to keys `listview:inventory-views` /
  `listview:inventory-density` (within the server's `listview:*` allowlist).

Verified live in Chrome: PUT → GET round-trips through `/api/prefs` (a saved view persists
server-side). Gates: tsc clean, build green, deployed. Reversible — revert to drop server sync; the
localStorage cache keeps everything working per-browser.

## build 144bz — Deep app-wide craft sweep: final enum + symbol cleanup (reversible)

Follow-up deep sweep across the whole frontend for the remaining instances of the issue classes:
raw enum leaks, stray symbols/emoji, debug leftovers, slop copy, raw IDs.

- **My work** — the timesheet entry line rendered a raw `e.state` enum; now humanised. Replaced the
  `⚑` flag glyph on disputed entries with plain "Disputed" (house rule: no emoji/symbols as icons).
- **Template editor** — the default-template marker in the `<select>` changed from a `★` symbol to
  " (default)" text (options can't hold an SVG icon, so a word is the clean choice).

Swept and confirmed clean (no change needed): no raw UUIDs rendered to users; no `capitalize`-only
enum pills left (all underscored statuses now go through `humanize()`); no `console.log`/`debugger`
in app code (only the statusTone test's intentional log); no "click here"/lorem/TODO slop copy; the
`→` arrows found are typographic (mostly code comments), not emoji; the only remaining symbol is the
Reports star-rating `★` ("5 ★"), which is conventional and correct.

Gates: statusTone test passes, tsc clean, build green, deployed. Reversible.

## build 144by — App-wide: humanise raw enum labels on customer-facing screens (reversible)

Systematic sweep (not one-off) for the same issue the asset drawer had: raw enum/key strings with
underscores leaking into the UI ("in_use", "partially_paid", "write_off"). Added one shared
`humanize()` helper (replace underscores, Title Case) and applied it only at genuinely user-facing
render sites:

- Finance — transaction type, invoice status, e-invoice status pills.
- Marketing — campaign status (list + detail).
- Chats — message delivery status.
- Inventory — PO status toast, stock-movement reference type.
- Work orders — parts-hold status, invoice status pill, notification/log status.

Deliberately **not** touched: Settings (admin configuration screens where raw keys like `field_type`,
`doc_type`, `trigger_status_key` are meaningful to the power user and intentionally shown verbatim),
and real `<select>` controls that already carry proper option labels. The WO/estimate pipeline status
keeps using the dedicated `StatusControl` component (already humanised), so it was left alone.

Gates: tsc clean, build green, deployed. Reversible — revert 144by; `humanize()` is additive and
guarded (returns "" for empty), so callers that already guarded their status keep working.

## build 144bx — Asset detail drawer: craft cleanup (reversible)

Review caught the asset/equipment detail drawer still carried the pre-refinement rough edges the
Inventory list had already fixed. Brought it up to the same standard — copy and the status chip only,
no behaviour change.

- **Raw `in_use` status → a tone chip.** The status rendered the raw enum string ("in_use"); it now
  reuses the shared `assetStatusTone` helper to show a humanised, coloured chip ("In Use", amber for
  attention states, etc.) exactly like the list — fixing the same raw-enum issue there.
- **Plain, human labels** (impeccable anti-slop — the labels read like generated enterprise jargon):
  "Hardware Ownership & Association" → "Ownership"; "We (company)" → "Company"; "Associated client" →
  "Client"; "Client current balance" → "Client balance"; "Physical & Serial Identifiers" → "Device
  details"; "Device type" → "Type"; "Cosmetic condition" → "Condition"; "Brand / Manufacturer" →
  "Brand"; "Model designation" → "Model"; "Color / Finish" → "Color".
- **Tabs** "General Specs" / "Repair History & Logs" → "General" / "History".
- **Subtitle** "Associated with {name}" → "Owned by {name}" (company assets → "Company-owned").

Gates: tsc clean, build green, deployed. Verified live in Chrome. Reversible — revert 144bx.

## build 144bw — Fix: inventory craft icons fell back to a circle

Live verification caught that the new icon names introduced across 144bs–144bv
(`density_medium`, `density_small`, `search_off`, `content_paste`, `filter_alt_off`, `bookmark_add`)
weren't in the `<M>` component's lucide icon map, so they rendered as the `Circle` fallback — the
density toggle and the empty-state/clear-filter/save-view icons all showed as plain circles. Added the
six mappings (Rows2 / Rows4 / SearchX / ClipboardPaste / FilterX / Bookmark). Verified live in Chrome:
density toggle shows proper row icons, the no-results empty state shows a search-off icon, and the
Clear-filters button shows a filter icon. Gates: tsc clean, build green, deployed.

## build 144bv — Premium visual-craft batch 3: Stock density toggle (reversible)

Third craft slice — a persisted row-density control on the Stock table, the one commoditized
data-table pattern the research flagged as missing. Serves the real split (an auditor wants roomy
scanning; a bulk-reviewer wants maximum rows) without adding any new surface.

- **Density toggle** (Comfortable / Compact) in the saved-views bar, driven by one CSS override that
  tightens only row vertical padding in compact mode — horizontal padding and type are unchanged.
- **Persisted per browser** (`ros_inv_density`), so a user's choice sticks across sessions — the
  key rule from the research (persist per account/user, not per session).

Gates: tsc clean, vite build green, deployed. Reversible — revert 144bv to drop the toggle.

## build 144bu — Premium visual-craft batch 2: Inventory state craft (reversible)

Second craft slice — the supporting states that separate "designed" from "defaulted", applied to the
Stock table. Backed by IBM Carbon, Shopify Polaris and NN/g.

- **Differentiated empty states.** The bare "No stock items yet." / "No products match this filter."
  text becomes two proper empty states: a first-run state (icon, positive title "No stock yet", helper
  copy, and two primary pathways — Add product/stock and New posting) and a no-results state (icon,
  corrective copy, and a one-click "Clear filters"). Positive, instructional framing per Carbon/NN/g.
- **Table-body skeleton on load.** The "Loading stock…" line becomes six shimmer rows whose geometry
  matches real rows, so loading prevents layout shift instead of causing it. Page chrome (tabs, toolbar,
  column headers) stays real — only the body skeletonises (Polaris "mix real + skeleton"). Shimmer
  respects `prefers-reduced-motion`.

Deliberately deferred: optimistic inline status/quantity editing. In Inventory, stock status is derived
from quantity (not directly editable), and inline qty editing needs a dedicated PATCH path — that's a
feature with its own TDD, not craft, so it's split out rather than rushed here (keeps "not Orderry 2.0").

Gates: tsc clean, vite build green, deployed. Reversible — revert 144bu to restore the plain states.

## build 144bt — Premium visual-craft batch 1 (app-wide, reversible)

First slice of the "make it look best-in-class" craft work from the deep-research pass. App-wide,
CSS-only, no component or data changes — the highest craft-per-line, lowest-risk moves, all backed
by strong cross-source consensus (Rauno/Vercel, Emil Kowalski, IBM Carbon, WCAG).

- **Keyboard-only focus ring.** A box-shadow focus ring (so it follows each element's border-radius,
  unlike `outline`) on buttons/links/controls, shown only on `:focus-visible` — so it appears for
  keyboard users and never flashes on mouse clicks. Elements with their own focus style keep it.
- **`prefers-reduced-motion` honored.** Animations and transitions collapse to near-instant for users
  who ask for reduced motion — a WCAG-aligned accessibility gap now closed.
- **Dense-table hover is instant.** Table rows drop their hover transition — the strongest rule in the
  research is "don't animate high-frequency actions"; row hover is one of the most frequent.
- **Dark-mode light-catching borders.** Dark edges switch from a hard solid `#2a3550` line to a
  low-opacity white hairline (`rgba(255,255,255,.06–.08)`) so they catch light like Linear's, rather
  than reading as hard lines.

Already-good (verified, left as-is): shadows are navy-matched + layered (not pure black), the micro
motion curve is already a strong ease-out, and numbers already use tabular figures. Gates: tsc clean,
vite build green, deployed. Reversible — revert 144bt to restore the prior focus/border/motion CSS.

## build 144bs — Inventory grounded refinement, Phase 3: identity fold + filter clear (reversible)

Final polish slice. No data or action changes — layout only.

- **SKU folded into the Product cell.** The dedicated "SKU / Barcode" column is removed; SKU, barcode
  and brand now sit as one muted monospaced line under the product name (the grounded identity-column
  pattern — name primary, identifier secondary). Reclaims a column; search still covers SKU/barcode, so
  nothing is lost. Stock column count adjusted (9, or 10 with reservations). This is the most visible
  structural change of the three phases — revert 144bs alone to bring the SKU column back.
- **Active-filter clear.** When Availability / Category / Warehouse are set away from their defaults, a
  "Clear (N)" control appears next to the filters to reset them in one click, with a count of how many
  are active (Carbon/Baymard filtering guidance).

Gates: tsc clean, vite build green, deployed to dist.

## build 144br — Inventory grounded refinement, Phase 2: nav overflow + saved views (reversible)

Second slice of the grounded Inventory redesign — the structural moves — again using the app's own
design system, with every feature preserved (nothing removed, only relocated or added).

- **Tab overflow ("More" menu).** The eleven-item tab bar now shows the three daily tabs first-class
  — Stock, Devices & Assets, Purchase orders — and folds the long tail (Postings, Reservations, Bins,
  Conversions, Transfers, Stock takes, Write-offs, Returns) into a single labelled **More** dropdown
  (NN/g "fewer tabs is better"; one row only). Every tab is still one click away; the settings gates
  are honored (Reservations/Bins only appear when enabled); terminology still drives the Devices label.
  When the active view lives in the overflow, the More button shows its name and the active underline.
- **Saved views on Stock.** A quick-switch bar above the table: "All stock" plus any number of named
  views the user saves. "Save view" captures the current Availability + Category + Warehouse filters;
  clicking a view restores them; each view has an inline delete. Persisted per-browser in localStorage
  (`ros_inv_views`) — no backend change. The existing availability/category/warehouse dropdowns are
  untouched, so Low/Out remain available there too.

No columns, actions, drawers or data wiring changed. Gates: tsc clean, vite build green, deployed to
dist. Fully reversible — revert build 144br to restore the single-row tab bar and drop saved views.

## build 144bq — Inventory grounded refinement, Phase 1 (reversible)

First slice of the research-grounded Inventory redesign, applied to the real Stock and
Devices & Assets tables using the app's own design system (no new visual language). Scope is
deliberately in-cell only — no tab, column, or navigation changes — so every existing feature,
filter, bulk action and drawer is untouched.

- **Bug fix — category name never rendered.** The Stock table showed "—" for a product that
  *had* a category and "Uncategorized" for one that didn't (backwards, and the real name never
  appeared). It now resolves and shows the actual category name from the loaded category tree;
  genuinely uncategorised rows read a muted "Uncategorized".
- **Bug fix — asset status always amber.** Every Devices & Assets row rendered its `asset_status`
  as one loud amber pill, so "With customer" shouted as loudly as "Awaiting parts". Status now
  maps through a new pure `statusTone` helper: normal in-progress states (in use, with customer,
  in repair) are quiet neutral; only attention states (awaiting parts, overdue, on hold) go amber,
  critical states (scrapped, lost, out of service) go rose, and ready-for-pickup goes green.
- **Exception-only stock status.** Healthy stock now shows a plain right-aligned number instead of
  a green "N in stock" pill; only Low (amber "N left") and Out (rose) keep a pill — the eye lands
  on what needs action. Reuses the existing `low_stock` flag.
- **Tabular figures** on Cost / Sell / Margin / In stock / Reserved so columns align on the digit.

`statusTone.ts` is covered by a node/esbuild assertion test. Gates: tsc clean, vite build green,
deployed to dist. Fully reversible — revert this commit to restore the prior rendering.

## build 144bp — Dismiss notifications from the bell (soft-dismiss)

The staff notification bell could mark-read but never clear anything — notifications piled up with
no way to remove them. Added soft-dismiss: a dismissed notification drops out of the bell and the
unread count but the row is kept (audit/history).

- **`StaffNotification.dismissed_at`** (new, nullable) is the soft-dismiss marker. The bell list and
  the unread count both filter `dismissed_at IS NULL`, so dismissed items vanish everywhere at once.
- **`POST /api/tech/notifications/{id}/dismiss`** (one) and **`/dismiss-all`** (clear the bell) — both
  org/user-scoped (a foreign notification returns 404) and also mark the row read so it never lingers
  in the count. Mirrors the existing read / read-all pair.
- **Bell UI**: a per-row **×** (hover-revealed, optimistic remove) and a **Clear all** control beside
  Mark-all-read. Applies to every notification kind, not just inventory alerts.
- Gates: 3 new pytest (dismiss-one hides + keeps row, dismiss-all clears bell + unread, cross-user
  404), tsc clean, migration head `nd144bp01`, and verified live in Chrome — dismiss-one removed just
  that row (4→3), Clear all emptied the bell and zeroed the unread badge.

## build 144bo — Inventory alerts: low-stock + job-unblocked (in-app bell)

Two production inventory alerts, both delivered through the existing staff notification feed
(bell + unread count + web-push), gated by a new role permission.

- **Low-stock alert** (`inventory.low_stock`): fires once when a sale or work-order consume drops a
  part's global on-hand to/at or below its **min-stock** line. Deduped "once until restocked" via a new
  `Part.low_stock_alerted` flag — it won't nag again until a receipt lifts stock back above the line.
  Wired at the four `use`-consume sites (POS sale + the three WO deduct paths).
- **Job-unblocked alert** (`inventory.job_unblocked`): when a part is received (bulk posting or PO
  receive), any open work order that was short of it and is now coverable triggers a bell notification
  to recipients **plus the job's assigned technician**. FIFO allocation (oldest waiting job first) so
  the alert is honest about which jobs the new stock actually covers.
- **Role permission** `can_receive_inventory_alerts` (Inventory group, auto-rendered in Settings →
  Roles). Recipients = active staff whose role allows both `can_manage_inventory` and this toggle, so
  an owner can mute a role. New shared service `services/inventory_alerts.py` with pure, unit-tested
  decision helpers (`crossed_low`, `newly_covered`); notifications ride the same transaction as the
  stock movement (`commit=False`).
- Also fixed a pre-existing latent crash: the Work-Orders **board** endpoint referenced `scoping`
  without importing it in-scope (500 on `/api/orders/board`). One-line local import, matching `list`.
- Gates: 5 new pytest (pure helpers + low-stock dedupe/reset + job-unblocked), full inventory/sales/
  orders suite green (104), tsc clean, single migration head `lsa144bo01`, and both alerts verified
  live in Chrome end-to-end (sale → low-stock bell; receive → WO-00020 "job can resume" bell).

## build 144bn — Smart paste for bulk posting (blended header-detect + column-mapper)

Paste from a spreadsheet no longer has to be in a fixed column order. The posting grid now reads
whatever you paste — with or without a header row — figures out which column is which, and shows a
one-row **column-mapping bar** you can correct before anything is posted. Your correction is
remembered for next time.

- **`pasteMap.ts`** (new, pure/testable): `analyzePaste()` runs a three-step pipeline — detect a
  header row (synonym match: "Part Number"→code, "Description"→name, "Quantity"→qty, "Unit Price"→cost,
  etc.) → fall back to a remembered mapping if the column count matches → otherwise infer each column
  from its content (wordiest text = name, code-like text = code, most-decimal numeric = cost, other
  numeric = qty). 13 node assertions cover header detect, reordered headers, bare-row heuristics,
  data-row-not-header, remembered-map, and blank-row drop.
- **Posting drawer**: `applyPaste` now uses the pipeline; a correctable mapping bar (one dropdown per
  column, pre-set to the detected field, with a sample value) appears after a paste. Changing a mapping
  re-derives the rows live and saves the mapping to `localStorage` for reuse. A field can only map to
  one column (picking it elsewhere clears the old one). File/photo extract path clears the bar.
- Verified live in Chrome on both a headered sheet with reordered synonym columns and a bare
  no-header sheet — both mapped to the right fields and populated the grid correctly.
- **Hardening (senior-architect audit fixes):** per-line delimiter detection — a tab-delimited paste
  now splits on tabs only, so commas inside a product name ("Screen, Black Edition") and comma-grouped
  numbers ("1,250") stay in one cell; CSV lines still split on comma. Number parsing is locale-aware:
  European decimals ("60,50" → 60.5) and grouped numbers ("1.234,56"/"1,234.56" → 1234.56) parse
  correctly, and the cost-column heuristic recognises comma decimals. The column-mapping bar now clears
  when a row is added or deleted. And a hand-edited cell now survives a later column-remap: `mergeRemap`
  re-derives rows for the new mapping but keeps any field whose value differs from the old auto-derived
  one (verified live — an edited name stayed put while the Cost column was moved to Markup). Test suite
  grew to cover all of this; both paths re-verified live.

## build 144bm — Job-driven "Parts to order" (procurement loop, step 1)

The flagship from the strategy pitch, built as its safe read-only core: one place that shows every
part your OPEN work orders need, netted against stock on hand and what's already on order, grouped by
supplier — so you batch a PO instead of discovering stock-outs job by job. No schema change.

- **`GET /inventory/to-order`** (read-only, `can_manage_inventory`): aggregates `part` lines across
  open orders (excludes closed/dropped/archived), computes needed − on-hand − on-order = shortfall,
  attaches the waiting job numbers, a stalled-days figure (from each job's status-entered time), and
  the default supplier. Returns a summary (parts short, jobs waiting, shortfall units). Org-scoped.
- **Inventory → Actions → "Parts to order (jobs)"** opens a drawer grouped by supplier; each group
  shows need / have / on-order / order-qty per part + which jobs are waiting, and a **Create PO**
  button that drafts a PO through the existing `/inventory/po` endpoint (rounds shortfall up, uses the
  part's avg cost). Empty state when every open job is covered.
- TDD: `test_144bm_to_order.py` (3) — shortfall from an open job appears; a fully-stocked part is
  excluded; a closed order is excluded. Live-verified (endpoint 200, menu + drawer render).
- This is the visibility half of the loop; auto-allocate-on-receive already exists (reserve-on-receive),
  so receiving a PO raised here flows back to the waiting job.

## build 144bl — AI feeder: posting from a supplier file / photo

The third front door for bulk posting (from the council pitch). Drop a supplier invoice/packing-slip
photo or PDF (or paste a messy list) and it becomes draft posting rows — which then flow through the
**same preview → review → commit gate** as every other posting, so the AI output never writes stock
directly. No schema change.

- **`services/ai.py`** gains `_complete_vision` (Anthropic image+text) and `extract_posting_rows`,
  following the existing live-model-or-deterministic-stub pattern (never raises). With no AI key, a
  pasted list is parsed deterministically; a photo with no provider returns an honest "configure AI"
  note rather than guessing.
- **`POST /inventory/postings/extract`** (multipart: `file` image/PDF up to 8 MB, or `text`) →
  `{provider, rows[]}`. Permission-gated (`can_manage_inventory`), read-only (writes nothing).
- **Posting drawer** gets a **"Scan invoice / photo"** button beside Paste/Add-row: uploads the
  file, fills the grid with the extracted rows, and shows how many were read — the user still
  confirms every row on the review screen before posting.
- TDD: `test_144bl_extract_posting.py` (4) — text extraction shape, extracted rows flow into
  preview+commit, 422 with no input, photo-without-provider returns the gated note. Live-verified.
- Honest limit: live photo extraction only runs when `ANTHROPIC_API_KEY` is configured; everything
  degrades safely without it.

## build 144bk — Bulk posting overhaul (paste-grid + review/confirm + bulk adjust)

Getting a lot of stock in at once was slow (one dropdown per line) and had no way to post from a
supplier file. Added three front doors to the existing posting engine — the WAC/movement core is
unchanged, so this is additive and low-risk. No schema change, no migration.

- **Paste-from-Excel grid.** The New-posting drawer is now a grid (Code/SKU · Name · Qty · Cost ·
  Markup%). Paste a tab/comma block from a supplier sheet and it fills the rows; type or add rows
  manually too.
- **Review & confirm (safety gate).** `POST /inventory/postings/preview` classifies every row as
  **matched** (exact SKU/barcode/code or name hit), **unsure** (fuzzy candidates — pick one), or
  **new** (will be created). Nothing writes until the user confirms on the review screen — a parsed
  file can never silently invent SKUs or wrong costs.
- **Commit creates-and-posts in one transaction.** `POST /inventory/postings/commit` creates any
  confirmed `new` products, then posts all rows through the shared `_do_posting` engine (extracted
  from `create_posting`, so both paths share one WAC + movement + supplier-balance code path).
- **Bulk quantity adjust** (ShopMonkey-style). `POST /inventory/parts/bulk-adjust` + an "Adjust qty"
  action on the Stock bulk-select bar: set an absolute on-hand or apply a ± delta across many parts,
  one `adjust` movement each.
- Permissions: all three gated by `can_manage_inventory`, all queries org-scoped. TDD:
  `test_144bk_bulk_posting.py` (4) — preview classification, commit creates-new + posts, commit
  rejects empty rows (422), bulk adjust set/delta. Live-verified end to end.
- Deferred (needs a vision model, not shipped): AI photo/packing-slip extraction. The review screen
  built here is exactly the surface it would feed.

## build 144bi — Inventory Stock tab: working Warehouse + Category filters

The Warehouse and Category controls on the Stock tab were static `<Chip>` placeholders (a
leftover `// warehouse/category left static` shim) — they rendered but filtered nothing. Only
Availability was live.

- **Category filter.** Real dropdown populated from `/catalog/categories?entity_type=product`
  (tree flattened with indentation). Selecting one refetches products with `?category_id=`,
  which the products endpoint already supported.
- **Warehouse filter.** New `warehouse_id` param on `GET /catalog/products`. Stock was summed
  across every warehouse, so a true per-warehouse view needed backend scoping: `_stock_for` now
  filters `StockLevel` to the chosen warehouse **and** its location-level rows (warehouse_id
  NULL, same location) — the same postings-write-location-keyed split handled in the stocktake
  engine — so the filter shows real on-hand instead of hiding it. Parts with zero there drop out.
- Verified live: category filter returns exactly the assigned product; warehouse "Main stock"
  keeps location-keyed stock visible; all three selects render.

## build 144bh — Stock takes run-flow (New → scan-to-count → post variance)

The Stock takes tab listed past counts but had no way to start or run one — the backend count
engine (start/scan/set-count/post) was complete and unreachable. Wired the whole flow, and fixed
the same location-vs-warehouse StockLevel split that broke Write-offs.

- **New stock take drawer.** "New stock take" button + click any row to reopen. Pick a location and
  Full (seeds every part on hand) or Partial (starts empty — only what you scan/edit is counted).
- **Scan-to-count.** A scan/type box increments the counted qty per scan (how a scanner actually
  works), with a "register unknown codes" toggle so a typo can't silently invent stock. Each line
  also has a manual counted-qty field; variance and a running net-variance update live.
- **Post variance (non-destructive default).** Posting adjusts only counted lines with a non-zero
  variance; uncounted lines are left untouched. Confirmation states exactly how many lines move.
- **Location-vs-warehouse fix (root cause).** Postings/sales write `StockLevel` keyed by
  `location_id` (warehouse_id NULL); the stocktake engine read warehouse-keyed rows — a separate,
  empty set. Added `_st_stock_row` (warehouse row → location fallback) used by full-count seeding,
  scan, and post, so a count seeds real on-hand and posting adjusts the existing row instead of
  creating a duplicate warehouse-keyed row. TDD: `test_144bh_stocktake_flow.py` (3).

## build 144bg — fix the broken Write-offs tab (list 404, create 400, empty stock)

The Write-offs tab was dead three ways; live audit + TDD traced each to its root and fixed it.

- **List 404 → correct endpoint.** The FE audit list read `GET /inventory/write-offs`, which never
  existed (hard 404, swallowed into a permanently-empty table). Repointed to `/stock-docs/write-offs`.
- **Create 400 "No warehouse resolved" → graceful fallback.** `default_for_location` only returned a
  warehouse explicitly flagged `is_default` + `kind="product"`; a location without that flag resolved
  to nothing and every stock document 400'd. It now falls back: default product WH → any product WH →
  any WH at the location. Only the previously-None case changes, so existing callers are unaffected —
  and this also unblocks transfers, stocktakes and postings that share the resolver.
- **Create 409 "0 in this warehouse" → read stock where it actually lives.** Root cause: postings,
  sales and PO receipts write `StockLevel` keyed by `location_id` (warehouse_id NULL), but the
  stock-docs family read `StockLevel.warehouse_id` — a separate row set nothing populated, so
  write-offs/transfers always saw zero. `_stock_row` now falls back to the location-level row, so a
  write-off depletes the same on-hand the rest of the app uses.
- **Readable list.** `GET /stock-docs/write-offs` now returns `part_name` + summary `qty` per row so
  the audit table's Part/Qty columns populate.
- **Verify:** `test_144bg_writeoff_wiring.py` — create resolves a warehouse from location only (201),
  list shows part_name + qty, old `/inventory/write-offs` path stays 404. 3/3 green; 46/46 across the
  inventory/stock-doc regression (postings, stocktake, conversions, freeze, stock-value, reserve);
  tsc clean; built + deployed (index-Y-b0wsDH.js). Backend auto-reloads; no migration.

## build 144bf — Phase1-E: supplier payment (settle AP)

Completes the supplier-ledger slice of Phase 1. The read-only ledger, balance, performance
scorecard and price-list import already existed; the missing piece was **paying the supplier**
to settle what we owe — the ledger had no way to go down except via returns.

- **`POST /api/suppliers/{id}/pay`** `{amount, account_id, note?}` — one transaction, three linked
  audited movements: money leaves the chosen cashbox (an `expense`), `Supplier.balance` drops, and
  the mirrored contact ledger records a settlement row. Built to the hardened standard: the balance
  is decremented with a single atomic `UPDATE` (no lost-update under a concurrent double-pay), and
  every leg is a reversible, referenced entry (`ref_type="supplier_payment"`). Overpaying is allowed
  and simply tips the balance to a credit (supplier owes us). Gated by `can_manage_inventory`.
- **Sign integrity:** `Supplier.balance` positive = we owe; the contact ledger negative = we owe
  (single source in `services/ledger`). A payment subtracts from the former and posts a positive
  `payout` row on the latter, moving both toward zero in lockstep.
- **Frontend:** the supplier Balance tab gains a **Record payment** button → inline amount + cashbox
  picker; on success the balance and statement refresh.
- **Verify:** `test_144bf_supplier_pay.py` — payment drops supplier balance + cash + posts the
  ledger row; rejects non-positive; 404 on a foreign account; overpay tips to credit. 4/4 green;
  tsc clean; built + deployed (index-CL0yA6jT.js). Backend auto-reloads; no migration.

## build 144be — POS sale crash fix (seller without a home location)

Live-verification of 144bb/bd surfaced a **pre-existing** crash: `create_sale` wrote
`sales.location_id = current.location_id`, but a seller with no home location (the org owner is
the common case) made that NULL — and the column is NOT NULL, so **every** POS sale by such a
user 500'd (plain or with rewards). The reward code never even ran; this predated 144bb.

- **Fix:** `create_sale` now resolves `loc_id = current.location_id or <org's first location>` and
  uses it for the sale row and all three stock lookups (StockLevel, reservation guard,
  StockMovement); 400s only if the org genuinely has no location. Found by running the real
  endpoint against a copy of the live DB during the Chrome verification pass.
- **Verify:** new `test_pos_sale_when_seller_has_no_location` (nulls the seller's location, expects
  201); reproduced the 500 and then the fix against a live-DB copy. 6/6 POS-rewards tests green,
  19/19 across the rewards+archive arc. Backend-only, auto-reloaded live; coupon and gift-card POS
  sales confirmed 201 end-to-end.

## build 144bd — Phase1-G: archived orders restore + bulk archive/restore

Completes the "archived / bulk orders" slice of the Phase-1 epic. The archive column, single
archive, archived list and a `POST /orders/bulk` endpoint already existed — this fills the gaps.

- **Restore (un-delete).** New `POST /orders/{id}/restore` brings a soft-deleted order back into
  the active lists (clears the archive stamp, audited). It intentionally does **not** re-accrue the
  commission reversed at archive time — a manager re-closes the order to re-earn it, so un-deleting
  never silently double-pays (marked as a known ceiling).
- **Bulk archive / restore.** `POST /orders/bulk` gains `archive` and `restore` actions. Archive
  runs each order through the existing single-archive helper, so refunds, commission reversal and
  audit all happen exactly as for a one-off — and an order carrying unrefunded payments is **skipped
  and reported**, never a fatal 500 for the whole batch.
- **Permission gate.** `archive`, `restore`, bulk archive/restore, and `DELETE /orders/{id}` are now
  gated by `can_delete_orders` (default-allow — only roles that explicitly deny it are blocked), per
  the architect standard that destructive bulk actions must be permission-gated.
- **Frontend.** WO hub bulk bar gains an **Archive** action (prompts for a reason, surfaces skipped
  orders); the archived overlay gains a per-row **Restore** button.
- **Verify:** `test_144g_archive_bulk.py` — archive↔restore round-trip, restore rejects non-archived,
  bulk archive+restore, bulk archive skips an order with an unrefunded payment. 4/4 green; tsc clean;
  built + deployed (index-CPaZA9Yc.js). Restart to load the router, then hard-refresh.

## build 144bc — reward-ledger hardening (money-safety, from the 144bb architect review)

Two money-safety guards applied to **both** the POS (`sales.py`) and work-order (`engage.py`)
redeem paths, closing the MEDIUM findings from the 144bb senior-architect review.

- **Atomic gift-card decrement.** The balance is now drawn with a guarded
  `UPDATE gift_cards SET balance = balance - :amt WHERE id = :id AND balance >= :amt`. If a
  concurrent redemption drained the card between the read and the write, `rowcount == 0` and the
  call returns `409` instead of driving the balance negative. Same pattern the stock draw already
  uses. (Fixed a subtle double-subtraction: the ORM attribute is synced by the UPDATE, so the
  returned `card_balance` is now captured *before* the statement.)
- **Loyalty over-redemption cap.** Redeeming more points than the bill is worth now only consumes
  points up to what's owed — the surplus stays on the client's balance instead of being burned.
  Applies to POS (`remaining` after coupon + gift card) and WO (order balance).
- **Concurrency ceiling noted.** Loyalty balance is a ledger SUM, so it has no single row to lock;
  strict double-spend protection there needs a per-client lock on Postgres (prod). Marked as a
  `ponytail:`-style known ceiling; the value-loss bug (the user-visible one) is fixed now.
- **Verify:** `test_144bc_reward_hardening.py` — POS + WO gift-card never goes negative under a
  racing drain; POS + WO loyalty caps at the bill and leaves the surplus. 15/15 green across
  144bc + 144bb + engage. Backend-only — no FE rebuild, no migration; restart to load the router.

## build 144bb — POS rewards redemption (Phase1-D, loyalty epic)

Over-the-counter sales can now redeem the same three reward types the work-order side already
supports, closing the POS half of Phase1-D (loyalty). The WO side shipped earlier via the
"Rewards & credits" panel in the WO drawer; POS was the remaining gap.

- **Backend** — `POST /api/sales` (`SaleIn`) accepts optional `coupon_code`, `gift_card_code`,
  and `redeem_points`. A new `_pos_redemptions` helper resolves all three into a single extra
  discount folded into the sale total, and decrements the gift-card balance (`GiftCardLedger`)
  and loyalty balance (`LoyaltyLedger`) with `ref_type='sale'`. Mirrors the order-based
  `engage.py` validation (coupon window/uses/min-total/client scope, gift-card active/expiry,
  loyalty configured + sufficient balance). No-op and byte-identical for plain sales. The
  response now carries a `redeemed` detail map.
- **Frontend** — SalesPOS cart gains a collapsible **"Rewards & credits"** panel (coupon code,
  gift-card code, loyalty points; points disabled until a client is attached). The charge call
  passes the reward fields; the confirmation toast reports what was redeemed.
- **Why POS needed its own path:** POS is a one-shot `POST /sales` with no intermediate order,
  so the order-scoped engage redeem endpoints didn't fit — the redemption had to be folded into
  `create_sale` itself.
- **Verify:** `test_144bb_pos_rewards.py` — coupon reduces total; gift card decrements to zero;
  loyalty redeems + leaves correct remaining balance; loyalty needs a client (400); plain sale
  unchanged (`redeemed == {}`). 5/5 green; tsc clean; built + deployed (index-D7_DL73w.js).
  No new columns — restart only to load the router change, then hard-refresh.

## build 144az — Technician "My Work" view + time dispute flow (labour arc finished)

The shop-floor surface that makes all the per-line labour attribution actually used by techs,
plus the dispute surface the ADR named as the last thing before the clock is a pay source of record.

- **"My Work" page** (`/my-work`, new sidebar item) — a technician's own cockpit: live job timer
  with one-tap stop, today's + this week's clocked job hours, their **assigned open service lines**
  with booked-vs-actual and one-tap clock-in, and their **recent time entries** with an inline
  edit-history viewer. Backend `GET /timeclock/my-work` composes it (self-scoped).
- **Dispute flow** — a tech flags their own entry (`POST /entries/{id}/dispute {note}`); a manager
  clears it (`POST /entries/{id}/resolve-dispute`, payroll perm). Both write to the 144ay audit
  trail, so a contested paycheck has a full who/what/when record. New `disputed` + `dispute_note`
  on `TimeEntry`; migration `mtl144az001`. ADR 144ax's last open item (tech dispute surface) closed.
- **Drive-by fix** — a second pre-existing `scoping` NameError in the single-order **timing**
  endpoint (companion to the 144ay one); cycle-time card no longer 500s.
- **Verify:** `test_144az_mywork_dispute.py` — my-work lists my assigned lines; dispute→resolve
  both audited. 28 labour/clock/payroll tests green; tsc clean; built + deployed (index-z5IUtMyu.js).
  Restart to load the new columns, then hard-refresh.

## build 144ay — Time-entry audit log: job clock becomes payable-hours system of record

Implements the one item ADR 144ax deferred — the audit trail that lets the job clock serve as the
compliance source for payable hours (Option B).

- **Append-only `TimeEntryAudit`.** Every manual create, field edit, approve, reopen, and void on a
  time entry writes an immutable row: action, field, old→new, actor, timestamp. Rows are never
  updated or deleted. Edits that don't actually change a value write nothing (no trail spam).
- **Read API** — `GET /api/timeclock/entries/{id}/audit` returns the chronological history
  (manager/owner, or the entry's own employee).
- **Schema:** `time_entry_audits` table; migration `mtl144ay001`. Rate pinning already on
  `TimeEntry` made this reachable with no data migration.
- **ADR updated** — 144ax's "efficiency clock only" stance is superseded; the clock is now
  defensible as the payable-hours system of record. Remaining before full reliance: a tech-facing
  dispute surface (noted in the ADR).
- **Drive-by fix.** Found and fixed a pre-existing `NameError: scoping` in the order **timing**
  and **timing/flags** endpoints (the `scoping` module was used but never imported in those two
  functions — the stall-badge/cycle-time endpoints 500'd). Added the local import; cycle-time
  tests now green.
- **Verify:** `test_144ay_timeentry_audit.py` — create/edit/approve/reopen/void all audited,
  old→new captured, chronological + append-only, and a no-op edit writes no row. 32 labour /
  timeclock / cycle-time / payroll tests green. Backend-only — restart to load the new table.

## build 144ax — Labour gap-closure (PM + architect pass): prefill, efficiency report, flat-rate

Closed the full gap list from the tech-PM + senior-architect review of the per-service labour work.

- **G1 — booked hours prefill.** Adding a catalog service now snapshots its standard duration as
  the line's booked hours (minutes→hours, ×qty), so booked-vs-actual has a baseline without the
  tech typing it. Snapshot, not a live link — editing the catalog later won't rewrite the line.
- **G2 — Labour-efficiency report.** New Reports → Labor → "Labour efficiency (booked vs actual)":
  per-technician booked vs measured actual hours + efficiency %. Both the report and the inline
  per-line delta now read actual time through one shared helper (`services/labour.py`), so they
  can't drift — covered by a consistency test.
- **G3 — flat-rate labour billing.** A new per-service **Flat-rate** flag bills booked hours × fee
  (fee acting as the hourly rate) by syncing the line qty to the booked hours. The money engine
  (`_calc_line`) is untouched, so non-flat-rate lines are byte-identical to before (golden-master
  test). Column + migration `mtl144ax001`.
- **G4 — split time is measured, not fabricated.** Per the architect: a split line credits
  commission by share but **actual hours come from who really clocked in** (real `TimeEntry` rows),
  not from dividing one person's timer. The efficiency report uses measured per-tech time.
- **G7 — print shows co-techs.** A split line now prints "James D. + Rosa M." instead of one name.
- **G8 — custom split ratios.** The split picker offers 50/50, 60/40, 70/30 (not 50/50-only).
- **G6 — ADR.** `docs/ADR-144ax-labour-clock-role.md` records the decision: the job clock is an
  efficiency/commission instrument, **not** the compliance system of record for payable hours
  (so no audit-log epic is pulled in now; the trigger to revisit is documented).
- **Verify:** 29 payroll/commission/analytics/labour tests green (unrelated pre-existing gift-card
  test excluded); tsc clean; built + deployed (index-f5TwtVGv.js). Two new columns
  (`order_lines` untouched; `services.flat_rate`) — restart to load, then hard-refresh.

## build 144aw — Per-service labour hardening (senior-architect audit fixes)

Follow-up to 144av, closing the findings from the architecture/QA review.

- **Split-share validation (was: silent over/under-pay).** Writing a line now rejects (422) when
  `co_technicians` shares don't add up to 100%, and when any technician id (primary or split)
  doesn't belong to the org. New `_validate_line_assignment` guards both the add-line and
  edit-line paths, so a bad split can't reach the commission engine.
- **Reports & analytics now split-aware.** The per-technician profitability report and the
  top-technicians analytics both attribute revenue through the *same* `_line_splits` rule payroll
  uses, so a split line's revenue reconciles with its commission ledger instead of landing
  entirely on the primary tech.
- **`/labor` `by_line` scoped to job time** — break/admin entries can no longer inflate a line's
  "Actual" hours.
- **Verify:** new tests for 100%-sum + foreign-tech rejection; 25 payroll/commission/analytics
  tests green (unrelated pre-existing gift-card test excluded). Backend-only — restart to load.

## build 144av — Per-service labour: employee attribution, booked-vs-actual, fractional split

Deep-research pass (Orderry / ShopMonkey / Jobber) confirmed the gap: our per-line clock-in didn't
record *whose* time it was, and nobody in the market shows booked-vs-actual inline. All four
council-ranked options shipped, on the existing WO Services & products design.

- **#1 Per-line employee attribution.** Each service/labour line now carries an assignee chip
  (avatar + name) you tap to set the technician; the clock-in already attributes time to that line,
  and the assignee flows straight into the payroll + commission engines. Picker reuses the roster.
- **#2 Booked vs Actual, inline.** New `booked_hours` on the line (flat-rate hrs billed) shown
  beside **Actual** (clocked, from `/labor` `by_line`) with a live **efficiency %** — green ≥100%,
  amber below. ShopMonkey only shows this in reports; this is on the line.
- **#3 Fractional split.** A service can be split 50/50 across two techs (`co_technicians`
  `[{employee_id, share}]`); payroll pays each their share. Orderry splits pay but not time — ours
  does both. Idempotency key now includes the employee so two techs on one line both post.
- **#4 Technician swimlanes.** A "By technician" toggle groups the lines into per-tech lanes
  (flat list stays the default, per council — a lens, not the home screen).
- **Schema:** `order_lines.booked_hours` (Float) + `order_lines.co_technicians` (JSON); migration
  `mtl144av001`. Serialized + settable on create and PATCH. `/labor` now returns a `by_line` rollup.
- **Verify:** `test_144av_line_labour.py` (split resolution unit; booked round-trip + by_line;
  split commission pays each share). 27 payroll/commission/RBAC tests green; tsc clean; built +
  deployed (index-DsoCXYen.js). Restart to pick up the new columns, then hard-refresh.

## build 144au — Work-order timer relocated + procurement moved to the job tab

Design-council + PM pass on the work-order timer (it was wedged into the Order Parameters form).
Followed ShopMonkey (clock into a specific service line, per-tech rollup) and Jobber (one prominent
job timer). Shipped the council's A+B recommendation.

- **Header timer pill (A).** A single job timer now lives top-right of the WO drawer header: a green
  running pill with elapsed time + stop when any tech is on the clock, else a one-tap *Timer* start.
  Hidden on estimates.
- **Per-line clock-in (B).** Each service/labour line on the *Services & products* tab has its own
  *clock in* control, and a *Time on job* rollup bar sits above the lines — matching ShopMonkey's
  "clock into the work you're doing" model.
- **Parts procurement moved onto the job tab.** The Order parts (supplier PO) + Reserve in-stock
  parts card now renders inside *Services & products* (where the parts are), not General info. The
  *General info → Time & people* block is slimmed back to the roster only.
- **Reserve by hold-preset chips.** Reserve now uses 7d / 14d / 30d chips instead of a raw number
  input, with the holds list showing "until <date>".
- **Verify:** tsc clean, built + deployed; live-verified in Chrome (header pill, per-line clock-in,
  relocated procurement card, hold chips). FE-only — hard-refresh to load.

## build 144at — Reservation lock + inventory role rules (connection audit + fixes)

Audited the reserve → stock → consumption → PO-receive → balance → roles chain and closed the gaps.

- **Reservation lock (the core fix).** Consumption used to read `qty_available` and ignore
  `qty_reserved`, so any work order — or POS sale — could eat stock reserved for another job. New
  `services/reservations.py` centralises the rule: a job may take only FREE stock (on-hand minus
  all active holds) plus its OWN reservation. Taking units reserved for a *different* order returns
  409 unless the actor can override. Wired into every consumption path: work-order create, line
  add/merge, line qty increase, and POS sales.
- **Admin override, audited.** `can_override_reservation` (opt-in / default-deny — owner or explicit
  grant) lets a manager consume another job's reserved units; each override draws from those holds
  FIFO, decrements `qty_reserved`, releases emptied holds, and writes a `reserve_override`
  StockMovement (who + which job) for the trail.
- **Role rules.** New Inventory permission group surfacing `can_manage_inventory` +
  `can_create_purchase_orders` (previously orphaned — advertised but unenforced) and new
  `can_reserve_stock` + `can_override_reservation`. Reserve-stock now gates on `can_reserve_stock`.
  These appear in the role editor automatically (it renders the catalog).
- **Self-healing expiry.** Free-stock is computed from the sum of *active* holds, so an expired hold
  stops blocking immediately — even before the release sweep touches `qty_reserved`.
- **Receive → stock + balances** was already correct (moving-avg cost + StockMovement + supplier
  balance/ledger on credit) and is confirmed connected.
- **Verify:** `test_144at_reservation_lock.py` (block → override → audit; free stock still
  consumable; own reservation consumable). 60 tests green across orders/sales/postings/reserve
  consumption paths; no regressions. No migration (reuses the reservation model). Restart to load.

## build 144as — Work order → procurement: raise PO + reserve stock for N days

The work order is now wired to inventory procurement, gated to staff with inventory access
(`can_manage_inventory`) — the UI hides the whole card for everyone else, and the API enforces it.

- **Raise PO from the WO** (existing endpoint, upgraded UI): pick a supplier and raise a purchase
  order for the parts the job needs, linked back to the WO (`source_order_id`). New options on the
  card: *Only what's short of stock* and *Mark ordered & notify supplier*; the "Waiting on PO" strip
  refreshes on success.
- **Reserve stock for N days** (new): `POST /orders/{id}/reserve-stock {hold_days}` earmarks the
  in-stock parts the job needs as a timed hold (auto-expiring), capped at what's free and never
  double-holding what's already reserved for that job; returns what was held and what's still short
  (→ raise a PO for the rest). `GET /orders/{id}/reservations` lists active holds; release reuses
  `/inventory/reservations/{id}/release`. A live list on the WO shows each hold with a days-left
  countdown and a one-click release. Complements the PO: reserve covers what's *in stock*, the PO
  covers the *shortfall*.
- Design: one "Parts procurement" card on the WO General tab, glass/clay rows, Material icons, no
  emoji — consistent with the drawer system.
- **Verify:** `test_144as_wo_reserve_stock.py` (hold + idempotency + release + shortfall reporting).
  Backend endpoints are org-scoped with access-guarded; tsc + build clean; 7-test focused regression
  green. No migration (reuses the existing reservation model). Restart to load the new endpoints.

## build 144ar — Freight allocation basis + per-shipment freight + auto-advance workflow

Three purchasing power-ups, each something Orderry/Jobber/ShopMonkey don't do:

- **Allocation basis (value / qty / weight).** PO charges capitalised into landed cost can now be
  spread cost-weighted (default), per unit, or by weight (uses `Part.weight`, per kg — the right basis
  for real freight). One `_freight_per_unit` helper drives receive, recost and the cockpit gap so the
  math is identical everywhere; falls back value→qty when a basis yields nothing (e.g. weights unset).
  Settable on the PO editor and the recost call (`alloc_basis`). `Part` now accepts `weight`/`dimensions`
  via the products API.
- **Per-shipment freight.** `POST /po/{id}/receive` accepts `shipment_freight` (+`shipment_freight_to_cost`):
  freight billed *with a partial delivery* is spread across only the units received now and folded into
  their landed cost (or posted to the supplier balance when not capitalised). Tracked in
  `purchase_orders.shipment_freight`, separate from the PO-level charge recost. Surfaced as a
  "Shipment freight" input on the receive bar.
- **Auto-advance workflow.** A full receipt now auto-advances the manual workflow stage to *Received*,
  unless the buyer has parked it at a later/terminal stage (closed/cancelled) — the receipt state stays
  the source of truth; a partial receipt never touches the label.
- **Schema:** `purchase_orders.alloc_basis` (default `value`) + `shipment_freight` (default 0);
  migration `mtl144ar001`, dev auto-migrate adds both on restart.
- **Housekeeping:** `backend/scripts/purge_inventory_test_data.py` — a guarded, backed-up one-command
  purge of throwaway PO/product test data (run locally; contacts kept).
- **Verify:** `test_144ar_alloc_shipment_autoadvance.py` (5 tests — auto-advance + terminal-respect,
  per-shipment capitalisation, qty and weight basis) and updated `test_144an` split into partial-leaves
  / full-auto-advances. 17 PO/landed/cockpit/workflow tests + 12 posting/state/reorder regressions green;
  tsc + build clean. Restart to load the new columns/code.

## build 144aq — Purchasing Cockpit (action worklist, not a Kanban)

A Kanban only shows *where* a PO sits; the cockpit shows *what to do*. One drawer (Inventory →
Purchase orders → **Cockpit**) surfaces only what needs the buyer now, each row one click from a fix —
exception-driven, which beats Orderry/ShopMonkey's filterable list.

- **`GET /inventory/purchasing/cockpit`** aggregates six buckets, org-scoped, read-only (cheap, always
  truthful): **Overdue** (open + past ETA, with days-late), **Arriving this week** (by ETA),
  **Freight not recosted** (received POs with an un-applied landed-cost delta → surfaces the 144ap
  differentiator proactively), **Awaiting supplier invoice** (received, no bill → closes three-way
  match), **Below minimum & not on order** (reuses the reorder logic), and **Supplier balances owed**.
- FE `PurchasingCockpit` drawer: each row opens the PO drawer (chase/receive/recost/add-invoice) or
  the reorder flow; a clean "All clear" empty state when nothing needs attention.
- **Verify:** `test_144aq_cockpit.py` — overdue, awaiting-invoice and below-min POs/parts land in the
  right buckets with the right fields. tsc + build clean. No migration; restart to load the endpoint.

## build 144ap — Late-freight landed-cost recost + in-transit visibility (beyond Orderry/Jobber/ShopMonkey)

Competitor research (their own docs): **none** of Orderry, Jobber, or ShopMonkey spread freight/duty
into part cost, and none recost received stock — that's ERP territory (Sage/NetSuite/Finale). We
already capitalise freight at receive; this adds the two things that make our purchasing decisively
deeper, while staying honest.

- **Late-freight recost (P0).** The freight bill usually arrives *after* the goods. `POST
  /inventory/po/{id}/recost` lets you add/edit the PO's charges — even on a fully-received PO — and
  applies the freight attributable to already-received units as a **real cost adjustment** to on-hand
  stock: bumps the global moving-average and raises the remaining FIFO layers, with a zero-qty
  `recost` StockMovement for audit. Only the *delta* beyond what was already capitalised is applied
  (`PurchaseOrder.freight_capitalised` tracks it), so it's idempotent. Only the on-hand share is
  revalued — cost tied to units already sold is sunk COGS, matching how real inventory systems recost.
  New `services/stock_posting.apply_cost_adjustment` keeps this in the single source of truth.
- **In-transit strip (P1).** The PO drawer now shows an **"On the water"** line — units + value still
  outstanding (ordered − received) + ETA — and a **"Freight bill arrived late?"** box with one-click
  **Add & recost stock**.
- **Migration `mtl144ap001`** adds `freight_capitalised` (default 0). SQLite dev auto-adds on startup.
- **Verify:** `test_144ap_landed_recost.py` — receive 10 @ ₹2 (avg 2.0) → add ₹20 freight → avg 4.0,
  idempotent on a second call, and a no-op when "capitalise into cost" is off. PO/posting/status
  suites green; tsc + build clean. Deploy note: restart the server to load the new endpoint.

## build 144an — Configurable manual PO status pipeline (Orderry parity, decoupled from receipt)

Researched Orderry's PO model (help docs): a PO's status is a **manual, user-configurable
workflow pipeline** — the same status engine they use for work orders/estimates — while
**receiving is a separate, automatic per-line "posted vs ordered" progress**. We now do both, and
go one better than Orderry (who list this as a *future* capability): the PO status pipeline is
fully editable today.

- **Manual, configurable status** — POs get their own pipeline via the existing OrderStatus engine
  (`record_type="purchase_order"`, seeded Draft / Awaiting approval / Approved / Ordered / In
  transit / Delayed / Received / Closed / Cancelled). Edit, rename, recolour, reorder or add your
  own stages in **Settings → Statuses → Purchase orders**, exactly like the other pipelines. The PO
  drawer header is now a manual dropdown listing your stages; `PurchaseOrder.workflow_status` stores
  the choice. `POST /inventory/po/{id}/workflow` validates against your org's pipeline.
- **Decoupled from receipt truth (architect/QA)** — the new stage is a label and moves *nothing*.
  The automatic receipt state (`PurchaseOrder.status`: ordered/partial/received) is still the only
  thing receiving touches, so a workflow label can never fake what physically arrived — and every
  report, supplier-spend and on-order-stock calculation that keys off `status` is unchanged
  (zero downstream breakage). The header shows the receipt state as a separate read-only badge.
- **Migration `mtl144an001`** adds `workflow_status` (nullable) and backfills existing POs from
  their receipt state. On SQLite dev the column auto-adds and the backfill runs on startup.
- **Verify:** `test_144an_po_workflow.py` — pipeline seeded, manual set + 422 on unknown stage, and
  receiving leaves the manual stage untouched while the receipt state advances. 144am integrity
  tests still green (receipt state + guards unchanged); statuses/posting/PO suites green; tsc + build
  clean. Deploy note: restart the server to load the new endpoint + pipeline seeding.

## build 144am — One contact form (Client ⇄ Supplier role switch) + PO status trigger

Two things the council flagged as "half-built, finish it."

**Suppliers are contacts, edited in one form.** A supplier was already stored as a unified
`Client` (`is_supplier`) with a thin mirrored `Supplier` row for purchase orders — but the
Contacts→Suppliers list opened a separate, thin supplier sheet. Now it opens the **same contact
form**, which grows a **Roles** switch (Customer always on; toggle Supplier) at the top of General
info and, when Supplier is on, a **Supplier tab** carrying the PO-facing fields (contact person,
lead time, PO currency), the balance statement (what we owe), performance, and price-list import.
Edits on either side save to the one contact, so finance cashboxes, the ledger and reports stay
connected automatically. Turning the role on creates the mirrored supplier profile server-side and
reloads in place.

- `suppliers._ser` now returns `client_id`; `clients._ser_client` returns a `supplier` facet
  `{id, contact_name, lead_days, currency, balance}` when a mirror exists. The Contacts list routes
  supplier rows into `ClientDrawer(client_id)`.
- **Backfill:** legacy suppliers created before the mirror existed had no backing contact (so no
  ledger/reports). Startup now self-heals every orphan supplier → linked `Client(is_supplier)`,
  idempotent (only touches `client_id IS NULL`). No schema change — reuses existing columns.
- `SupplierPanel` was extracted so the standalone drawer and the contact tab share one component
  (no duplication).

**PO receiving is a real status trigger** (ShopMonkey/ServiceTitan model). A PO can now start as a
**Draft** (editable, not placed), be manually **marked Ordered**, and from there receipt *drives*
the status — receiving part of a line flips it to **Partially received**, receiving the rest to
**Received**. The drawer's status pill is now a dropdown (Mark as ordered / Back to draft while
nothing received / Cancel); a **Receive all** button one-clicks the outstanding remainder; each
line shows "N left"; receiving is hidden on a draft until it's placed.

- `POST /inventory/po/{id}/status` — manual draft⇄ordered only, guarded by the existing transition
  graph; un-ordering blocked once anything is received; receipt statuses stay receipt-driven so a PO
  can never read "received" without the stock (and cost layers) having landed. `POIn.draft` lets a
  PO be created as a draft; New PO drawer gains **Save as draft** vs **Create & order**.
- **Verify:** `test_144am_supplier_unify_po_status.py` — supplier↔contact link both ways, role
  toggle creates the mirror, and draft→ordered→partial→received with the un-order/terminal guards.
  All PO/posting/client suites green; FE tsc + build clean.
- **Architect-QA pass (same batch):** two issues found and fixed before ship — (1) the
  contact-match in `create_supplier` and the backfill compared NULL phone/email as `IS NULL`,
  which could mis-link a supplier to an unrelated contact missing that field; now only
  present identifiers are compared. (2) `receive_po` allowed posting stock against a Draft PO,
  bypassing "mark as ordered"; now blocked (409) so stock never lands on an unplaced order. Both
  covered by new tests (`test_cannot_receive_a_draft`, `test_supplier_with_only_email_does_not_mismatch`).
- **Deploy note:** no migration; a server restart loads the new endpoints and runs the one-time
  supplier backfill.

## build 144aj — Attach the supplier invoice to a PO (three-way match evidence)

The last purchasing item. In the PO drawer's three-way match panel you can now **Attach PDF**
(or image) — the supplier's actual bill — sitting right beside the invoice-total field, so the
match has its paper trail. Click the filename to open it; "Replace" swaps it.

- Stored via the shared media service (`/api/media`) with a blob pointer on the PO
  (`supplier_invoice_blob_id` + name), so files go through the same storage path as every other
  upload — no bespoke handling. Migration `mtl144aj001` (two nullable columns, single head).
- `POST /inventory/po/{id}/invoice` now accepts `invoice_file` (base64/data URL) + `invoice_name`;
  the serializer returns `supplier_invoice_file: {id, url, name}`; the file streams from
  `GET /api/media/{id}` (authed, org-scoped). 6 MB guard on the FE.
- **Verify:** `test_144aj_po_invoice_file.py` — attach a PDF → detail exposes the file with a
  media URL → the bytes stream back starting with `%PDF`. FE tsc + build clean.

**This completes the entire purchasing brainstorm list** (edit/charges/send, reorder CTA, receipt
timeline, barcode receiving, supplier scorecard, spend-approval gate, "waiting on PO" badge, and
now invoice attach).

## build 144ai — "Waiting on PO" badge on the work order

Closes the loop between a job and the parts it's waiting on. A WO now shows an amber banner
listing any open purchase orders raised for it (or with lines reserved for it) and the
outstanding quantities — so a tech opening the job instantly sees "these parts are on the way."

- `GET /orders/{id}/incoming-po` — open POs linked to the order via `source_order_id` or a line's
  `reserved_order_id`, with per-part outstanding qty. Org-scoped via supplier + the record-level
  `_guard_order_access` (a scoped tech only sees their own order's incoming stock).
- WO drawer shows the banner across all tabs; it disappears once the PO is fully received.
- **Verify:** `test_144ai_wo_incoming_po.py` — raise a PO from a WO → the WO reports 1 incoming
  PO with outstanding qty; receive it fully → the WO reports none. FE tsc + build clean. No
  migration.

_Last item from the list: supplier-invoice PDF attach (needs upload/storage plumbing)._

## build 144ah — PO spend-approval threshold (governance)

A configurable spend gate on purchase orders. Set **Settings → Inventory → PO approval limit**;
any PO whose total exceeds it can only be **sent** by a manager — a role that is not denied
`can_manage_settings`. Deny-wins, consistent with the rest of RBAC (0 disables the gate).

- Model: `Organisation.po_approval_threshold` (migration `mtl144ah001`, chained on
  `mtl144aac001`, single head).
- Enforced in `POST /inventory/po/{id}/send` (403 with a clear "a manager must send it" message).
- Exposed through the existing `/staff/inventory-settings` GET/PUT and a field in the Settings →
  Inventory card.
- **Verify:** `test_144ah_po_approval.py` — a restricted buyer is blocked on a ₹250 PO but sends
  a ₹40 one; the admin sends the big one; threshold round-trips through settings. 2 passing. FE
  tsc + build clean.

_Remaining from the brainstorm list: "waiting on PO" badge on the work order, and supplier-invoice
PDF attach._

## build 144ag — Receipt timeline, barcode receiving, supplier scorecard

Third purchasing batch (the NEXT/LATER brainstorm items), all reusing existing data — no
migration.

- **Receipt history** in the PO drawer — every partial delivery booked against the PO (date,
  product, qty, unit cost, who), from `GET /inventory/po/{id}/receipts` (reads StockMovement).
- **Barcode receiving** — a scan box in the PO receive view: scan/type a SKU or barcode and the
  matching line's "receive now" bumps by 1. Reuses the loaded product catalog; no server round
  trip per scan.
- **Supplier performance scorecard** (`GET /suppliers/{id}/performance`, shown in the supplier
  drawer) — total spend, on-time delivery %, open/total POs, last order, and a **price trend**
  (first vs latest unit cost per part). This is the buy-side analytics Orderry doesn't surface.
- **Verify:** `test_144ag_po_receipts_perf.py` (two partial receipts → 2 timeline rows; 2 POs at
  rising cost → spend 26, price trend 5→8) + prior PO suites; 4 passing in batch. FE tsc + build
  clean. No migration.

_Still open from the list (each needs schema/upload or touches the large WO drawer, so deferred
rather than rushed): supplier-invoice PDF attach, "waiting on PO" badge on the work order, and
the PO spend-approval threshold._

## build 144af — PO drawer: edit, charges/landed-cost, send + reorder CTA

Exposes backend capabilities that already existed but had no UI, completing the order → send →
edit → receive loop. Council-sequenced (tech-PM leverage-first, architect/QA guards, ponytail
reuse-don't-rebuild).

- **Edit PO** (pencil, only while `editable`) — change line qty/cost, add/remove lines from the
  product picker, edit supplier-invoice #/date, expected date, notes, on-credit. Saves via
  `PATCH /inventory/po/{id}`. QA guard honoured: a line that already has received stock is locked
  (can't edit product or delete it) — mirrors the backend 409.
- **Charges / landed cost editor** — add shipping/customs lines + the "capitalise into unit cost"
  toggle, so the landed-cost engine is finally reachable from the UI. Charges fold into the PO
  total; when capitalised they spread across received units' cost (verified: ₹20 shipping on 5
  units → avg cost 10 → 14).
- **Send to supplier** — email / SMS / WhatsApp menu → `POST /inventory/po/{id}/send`, logged to
  the supplier's contact thread.
- **Reorder suggestions** button on the Purchase Orders tab surfaces the existing velocity-based
  reorder→draft-PO flow (was built but had no entry point).
- **Verify:** `test_144af_po_edit_send.py` — edit lines+charges → totals recompute, send OK,
  receive → landed cost in avg; received-line edit blocked. 12 passing across PO edit/state/
  posting/scope suites. FE tsc + build clean. No migration.

## build 144aae — New PO drawer: inline supplier + product quick-create

The empty-system dead-end: with no suppliers (or products) yet, the New PO drawer had nothing
to select and no way forward. Council (tech-PM + architect-QA + ponytail) agreed on the smallest
unblock — inline quick-create, reusing the tested `POST /suppliers` and `POST /inventory/parts`
endpoints, no new backend.

- **Supplier "+ New"** next to the field: type a name → Add → it's created and auto-selected.
- **"New product"** by Add line: name + optional cost → Add → created, added to the picker, and
  dropped into the first empty line.
- Empty-state banner now points at those inline buttons ("no need to leave this screen") instead
  of sending the user to another hub.
- Drawer self-fetches the product catalog if the parent hadn't loaded it yet. FE-only, tsc +
  build clean, no migration.

## build 144aad — Fix: create a PO from the app (New PO drawer)

The Purchase Orders toolbar button was wired to open the **product** form, so a PO could
never be raised from the React app — which is why the tab always looked empty and "View
Details" had nothing to open. Added a real `NewPODrawer` (supplier, deliver-to location,
on-credit, expected date, product line rows with qty + unit cost and a live total) that POSTs
to `/inventory/po`, then refreshes the list. This closes the visible loop: create → View
Details → Receive → invoice → three-way match. FE-only, tsc + build clean, no migration.

## build 144aac — PO state machine + three-way match (P2, beyond Orderry)

The hardening pass on top of the 144aab accuracy fixes.

**State machine** — one place (`PO_TRANSITIONS` + `_guard_po_transition`) defines the legal
lifecycle: draft → ordered → partial → received, with received / cancelled / grouped terminal.
- **Cancel** (`POST /inventory/po/{id}/cancel`) — allowed only from a non-terminal state; a
  received PO (which moved real stock) can't be cancelled.
- Receiving now refuses a terminal PO up front (409) instead of silently no-op'ing.
- FE: a **Cancel** action in the PO drawer header (only while editable).

**Three-way match** — order vs receipt vs supplier invoice, the step past Orderry's post-and-done.
- New `purchase_orders.supplier_invoice_total` (migration `mtl144aac001`, single head).
- `POST /inventory/po/{id}/invoice` records what the supplier billed — allowed on a received PO
  too, since the invoice usually lands after the goods (but never on a cancelled one).
- Serializer returns a `match` block: ordered/received quantities, expected (landed) cost,
  invoiced total, qty & price variances, and a status — **pending → unbilled → matched /
  variance** (price tolerance ₹1 or 0.5%).
- FE: a match badge on the PO drawer header + a Three-way match panel with the invoice-total
  input; a red variance line calls out over/under-billing, green when all three agree.

**Verify:** `test_144aac_po_state_match.py` — match state progression (pending→unbilled→matched
→variance) and terminal guards (can't cancel or re-receive a received PO; can cancel an open
one) — 3 passing; PO/postings/reorder/conversions suites green (38 across runs). FE tsc + build
clean. Migration `mtl144aac001` chains on `mtl144v0001` (one head).

## build 144aab — Purchase orders & postings: accuracy overhaul (better than Orderry)

Fixes every defect from the PO/postings audit (`INVENTORY_PO_POSTINGS_AUDIT.md`). The core
problem: receiving a PO was **unreachable in the app**, and the three ways stock came in
disagreed on cost. Now there is one truthful path.

**P0 — the loop works again**
- **PO detail + Receive drawer (FE)** — "View Details" on the Purchase Orders tab now opens a
  real drawer: supplier/invoice/dates, line table with ordered vs received, charges + totals,
  and a **Receive** action (full or per-line quantity). Posts to the one real receive path.
  Before this, POs could be created but never received — stock never landed and POs stayed open
  forever.
- **One posting service** (`backend/services/stock_posting.py::apply_receipt_line`) — both
  `POST /inventory/postings` and `POST /inventory/po/{id}/receive` now route each line through
  it: quantity, `StockMovement`, FIFO `StockItemBatch` layer, serials, and moving-average cost,
  computed identically every time.
- **Killed the split-brain endpoint** — the dead `POST /suppliers/purchase-orders/{id}/receive`
  (marked a PO "received" while posting **no** stock/cost/balance) is gone.

**P1 — better than Orderry**
- **One WAC basis** — postings used to weight moving-average on *per-location* quantity while PO
  receipt used *global*; the same part got different costs by door. Both now use global on-hand.
  (Test: a 2nd-branch posting blends to ₹15 avg, not the old buggy ₹20.)
- **Cost layers everywhere** — manual postings now create `StockItemBatch` layers too, so exact
  COGS at fulfilment works for all stock, not just PO-sourced.
- **Reserve-on-receive** — goods a work order was waiting on (`POLine.reserved_order_id`) land
  already reserved (`qty_reserved` + `StockReservation`) instead of as free stock. Orderry
  reserves incoming goods; we do that *and* keep the exact cost layer.

**Verify:** new `test_144aab_posting_unified.py` (global WAC across two locations + cost-layer
parity); reservation-survives-receipt test repointed to the real endpoint and now also asserts
stock landed reserved; 75 passing across postings/stocktake/PO/ledger/conversions suites. FE tsc
+ build clean. No migration.

_Deferred to a P2 follow-up: unified PO state machine and three-way (PO/received/invoice) match._

## build 144z — Phase 1 E: supplier detail drawer (ledger + price-list import)

Suppliers become first-class: click any supplier row in Contacts → Suppliers to open a detail
drawer instead of a dead row.

- **Balance & details tab** — editable header (name, contact, phone, email, lead days → `PUT
  /suppliers/{id}`) plus a running **statement** from `GET /suppliers/{id}/ledger`: received POs
  and returns post here as signed rows against the supplier balance, so raised POs finally have
  somewhere to land.
- **Price list tab** — paste the supplier's CSV, auto-map columns to sku/barcode/name/cost/sell
  (best-effort by header name, editable), pick the match key, then **Preview** (dry-run) shows
  exactly how many products would change before **Apply** writes anything. Reuses the existing
  `POST /suppliers/{id}/import-price-list` mapping engine — no new cost-rewrite path.
- Both backends already existed and stay covered (ledger + import incl. the bad-mapping 422
  guard — 32 passing across the ledger/stocktake suites). FE-only: new `SupplierDrawer.tsx`,
  wired into `ClientsHub`. tsc + build clean. No migration.

## build 144y — Phase 1 F: special-order parts (raise PO from a work order)

Order a part a work order needs but doesn't stock, without leaving the job.

- **WO drawer → "Special-order parts"** — pick a supplier, hit **Raise PO**. Posts to the
  existing `POST /orders/{id}/create-po {supplier_id, only_shortfall:true}`, which turns any
  custom (non-catalog) product lines into real tracked parts, orders only what's short of
  stock, links the PO back to the work order, and shows `PO-xxxx raised — N part(s)`.
- **Leak closed:** `create-po` now runs `_guard_order_access` like the deposit/comment/status
  write paths, so a record-scoped tech (`order_scope="own"`) with inventory rights gets **403**
  raising a PO on someone else's order instead of a silent cross-order write.
- Tests: `test_144y_create_po_scope.py` — happy path (short line → 201, PO with 1 line) +
  scope-guard 403. Scope/timer suites still green (6). FE tsc + build clean. No migration.

## build 144x — Phase 2: scan-driven stock posting

Hands-free receive / write-off / count. Pick a location + action once, then scan parts
one after another — each posts to stock without a part-id lookup.

- **`POST /api/inventory/scan-adjust {code, location_id, qty, movement_type}`** — resolves the
  part by sku/barcode/code, then **delegates to the existing `adjust_stock`** so the permission
  (`can_manage_inventory`) + warehouse guards + StockMovement audit all apply unchanged. 404 on
  unknown code. No duplicated posting logic.
- **`ScanStockPanel`** (Inventory → "Scan to stock") — location + action + qty, then a scan
  input; each Enter posts and appends to a live success/fail log. Keyboard-wedge friendly.
- Tests: `test_144x_scan_stock.py` (receive by sku raises on-hand, write-off by barcode lowers
  it, unknown code 404) — 3 passing; scan-resolve suite still green (5 total). FE tsc + build
  clean. No migration.

## build 144w — Phase 2: universal scan (keyboard-wedge) + deep-open

Shop-floor "scan anything, jump to it." A USB keyboard-wedge scanner (or the camera, or a
typed code) resolves to the right record and opens it.

- **`POST /api/scan/resolve {code}`** (new `scan` router) — ordered lookup: work order
  (barcode / order number), then product/part (sku / barcode / internal code), then asset
  (serial / imei / vin / hin). Org-scoped; returns `{matched, type, id, route, label}` or
  `{matched:false}`.
- **Topbar ScanBox** (Shell) — a scan icon → focused input; Enter resolves and navigates to
  `{route}?open={id}`. Keyboard-wedge friendly (fast type + Enter). "No match" nudges to ⌘K.
- **WO deep-open** — WorkOrdersHub reads `?open=<id>` and opens that work order's drawer
  immediately (WODrawer fetches full detail by id), then clears the param.
- Tests: `test_144w_scan_resolve.py` (order by number + barcode, product by sku + barcode,
  unmatched) — 3 passing. App imports (76 routers). FE `tsc` + build clean. No migration.

## build 144v — Multi-tech per-line time attribution + WO "Time & people" panel

Several technicians can each time their own labour/service line on one work order
(concurrency already worked — the running-timer limit is per employee, not per order).

- **Line attribution.** New `TimeEntry.order_line_id` (migration `mtl144v0001`, single head).
  `/timeclock/start` accepts `order_line_id`, validates the line belongs to the order (400 if
  not), derives the order from the line when omitted, and stores it. Serializers expose it.
- **WO "Time & people" panel** (WODrawer): "Start timer on this job" plus a per-labour-line
  Start button, and a live roster of who's on the job with per-person totals + running dot.
- **Enriched `/orders/{id}/labor`**: employee names, `by_employee` per-person summary,
  per-entry `line_description` + `running` + live seconds (lines queried explicitly to avoid a
  lazy-load 500 under async).
- **Manager stop (#3)** — `POST /timeclock/entries/{id}/stop`: stop a specific running entry.
  Own timer anyone; another tech's requires manager rights (same `_can_manage` gate as the
  roster) → 403 otherwise, 400 if not running. WO "Time & people" panel shows a Stop button on
  each running person. No migration (uses existing TimeEntry).
- Verify bar (both requested): `test_144v_line_timer.py` (attribution + labor report +
  cross-order rejected + stop-by-id own + re-stop 400) — 4 passing; **full clock + payroll
  suites rerun green** so commission/job-cost split is unaffected. FE `tsc` + build clean.

## build 144u — Write-path IDOR guards (assignee scoping, follow-up #1)

Closed the theoretical write-path IDOR flagged in 144t's QA. Added one shared helper
`_guard_order_access(db, current, order)` (403 when a role scoped to `order_scope="own"`
acts on a non-owned order) and called it after the existing fetch in the sensitive
`/orders/{id}` mutation endpoints: **status-change (board drag), deposit, apply-kit, type
switch, comment, signature, payment-link, complete**. Additive one-liners — no behavior
change for full-access roles.

Verified transparent for admins: 11 existing order tests (people/status, deposit/kit,
multi-order invoice, scope/timer) still pass; app imports (75 routers). No migration.
Belt-and-suspenders restricted-user login integration test now DONE
(`test_144u_scope_integration.py`): seeds a real technician with `order_scope="own"`,
logs in via `/api/auth/token`, and proves list shows only their order, foreign detail = 403,
foreign comment (write) = 403, and admin still sees both. 1 passing.

## build 144t — Job-attributed timer + assignee scoping hardening

- **Timer shows which job.** The top-bar clock chip now labels the running timer with the
  work-order number, task title, or "General" (was always "Active"). `/timeclock/active` returns
  `task_title`; a null-guard was added so no running timer no longer risks a 500.
- **Start where you work.** WO drawer gets a "Start timer on this job" button
  (`/timeclock/start?switch=true {order_id}`); Tasks already had the task equivalent. Time is
  attributed to the order/task, feeding job-profitability + payroll-by-job.
- **Assignee visibility hardening.** "See only my assigned orders" already existed via
  `Role.order_scope = "own"` (enforced on list + detail). QA found the **board**, **timing/flags**
  and **{id}/timing** read paths were NOT scoped — a restricted tech would see everyone on the
  Board. All three now apply `scope_orders` / `can_view_order` (403), so no order read path leaks.
- Tests: `test_144t_scope_and_timer.py` — scope-query rule (own filters by assignee, all/none
  untouched) + timer attributes to a WO and labels it. 13 existing clock tests still green;
  order-people + gift-card + deposit/kit suites green. No migration (uses existing columns).

## build 144s — Camera barcode/IMEI scanner (completes the five-concept set)

- **`<ScanButton>`** (`frontend-react/src/components/ScanButton.tsx`) — glass camera modal using
  the browser-native `BarcodeDetector` (zero deps; works in Chrome). Decodes Code128/39, QR,
  EAN/UPC, ITF, Codabar; on read it calls `onScan(code)` and closes. Graceful fallback message
  where the API is missing (manual typing still works).
- Wired into the identifier fields: **CreateOrderDrawer** (replaced the dead scanner button next
  to the primary ID) and **AssetDrawer** (scan button on every identifier field). Scanned value
  autofills through the existing identifier engine. `tsc` clean; `vite build` OK.
- With this, all five council concepts are in: ① DatePicker, ② kanban board (was already built —
  drag-to-status fires notification rules), ③ deposits, ④ job kits, ⑤ scanner.

## build 144q/144r — Shared glass DatePicker + estimate deposits + job kits (backend-first)

Five-concept build. This batch lands the DatePicker (frontend) and the backends for
deposits + job kits; kanban board and camera scan are frontend-only and come next.

- **Shared glass `<DateField>`** (`frontend-react/src/components/DateField.tsx`) — our own
  glass/clay calendar + time popover, zero dependencies (native `Date`). Replaces **all 21**
  native `<input type=date|time|datetime-local>` across 9 files (Bookings, Timesheets, Tasks,
  Finance, Reports, WODrawer, ClientDrawer, CreateOrderDrawer, Settings) — no more grey OS
  picker chrome anywhere. `tsc` clean; `vite build` OK.
- **Estimate deposits** — `orders.deposit_amount` column (migration `dep144q0001`),
  `PATCH /api/orders/{id}/deposit` accepts `amount` or `percent` (of total); exposed on the
  order serializer alongside `prepayment_total`. Client pays it via the existing pay-link
  (`POST /api/payments/checkout`).
- **Job kits** — new `JobKit` model + `/api/job-kits` CRUD (`job_kits` router) and
  `POST /api/orders/{id}/apply-kit/{kit_id}` which appends the kit's parts+labour lines and
  recomputes totals. Migration `kit144r0002` (both new revisions chain onto real head
  `g2b4d6f8h0j2`; single head verified).
- Tests: `test_144qr_deposit_kits.py` (deposit percent/absolute + guards, kit CRUD + apply +
  404) — 3 passing; gift-card regression suite still green. App imports with 75 routers.
- NOT yet done this batch: **live deploy** (sandbox `dist/` is append-only — run
  `cd frontend-react && npm run build`) and **`alembic upgrade head`** (alembic absent from
  sandbox). Kanban board + camera scanner are the remaining frontend-only pieces.

## build 144p5 — Gift cards: always-unique code, client-linked, optional validity

Per request, gift cards are now issued with guaranteed integrity:
- **Code is always server-generated and unique** — the create endpoint no longer accepts a
  caller-supplied code; it allocates `GC-XXXXXX` and retries on the rare collision (no more 409s).
- **Always linked to a client** — `client_id` is now required and validated against the org.
- **Optional validity date** — `expires_at` stays optional (blank = never expires); redemption
  already blocks expired cards. The Client drawer's "issue card" now prompts for an optional
  expiry and passes it through.
- Backend `GiftCardIn` tightened (drop `code`, require `client_id`). Tests
  `test_144p5_giftcard.py` (unique code, required client, expiry round-trip) — 3 passing.
- `tsc` clean; deployed (index-sInXd8WG.js). Backend change needs a server restart to go live.

## build 144p4 — Phase 1-D: loyalty / coupon / gift-card redemption on the WO

The WO Invoices & payments tab now has a **Rewards & credits** panel (shown while a balance is
due): redeem loyalty points, apply a coupon code, or redeem a gift card — POST
`/orders/:id/redeem-loyalty|apply-coupon|redeem-giftcard`, then the invoice/payment ledger
reloads so the new balance shows. Backend endpoints already existed (engage router); this wires
the missing UI. `tsc` clean; deployed (index-CiCTcR_S.js). FE-only, works live.
- **Note:** the money math is the backend's; give it a live click-through on a real order before
  relying on it in production.

## build 144p3 — Phase 1-G: archived orders + efficient bulk actions

- **Archived work orders** — the dead "Archived" button in the Work-orders toolbar now opens a
  drawer listing archived orders (number, client, reason, status) from GET `/orders/archived`.
- **Bulk** — the existing multi-select bar now calls the single `/orders/bulk` endpoint
  (action=set_status) instead of N per-row PATCHes, with a per-row fallback if the bulk call fails.
- Verified: `tsc` clean; build + deploy (index-CyBlYR-E.js). FE-only, works live.
- Remaining Phase-1: D (loyalty/coupon/gift redemption at WO/POS), E (supplier ledger + price-list
  import), F (special-order PO→receive), H (AI call insights) — still pending; each touches money
  or larger surfaces and is tracked in ROADMAP.md.

## build 144p2 — Booking overhaul + timer-clock on/off

**Bookings** — the hub was a dead-end (rows didn't open, no status control, no convert, and the
status pills used wrong keys). Now:
- Clicking a booking (agenda row or scheduler card) opens a **detail drawer** (new design): client,
  specialist, time, duration, note.
- **Status control** — Booked / Done / Canceled / No-show; changing it calls PATCH `/bookings/:id`,
  which fires the org's booking notification rules server-side (client gets notified).
- **Convert** — to Work order *or* Estimate (backend `to-order?record=` extended to support
  estimates; previously WO-only). Once converted the booking shows a "Converted" badge and the
  convert buttons are replaced by a linked-document note. Status pills fixed to backend keys.
- **Reschedule** — date + time → PATCH (clash-checked server-side, notifies the client).
- Backend: `to-order` now takes `record=work_order|estimate` and sets `is_estimate`; the list
  serializer exposes `order_id` so the UI can tell converted from open.
- Tests: `test_144p2_booking_convert.py` (convert→estimate, convert→WO + double-convert 409,
  status change) — 3 passing; all 26 existing booking tests still green.

**Timer clock** — the job/labour clock showed a running timer but had no controls. Added **Start**
(general labour, category "other") and **Stop** (POST `/timeclock/stop`) to the Job-timer section,
so both clocks now have on/off in the top-bar widget (attendance already had clock-in/out).

- Verified: `tsc` clean; vite build + deploy (index-uSUelWeG.js). Backend booking changes need a
  server restart (`make run`) for convert-to-estimate + the "Converted" badge to go live; the
  status/reschedule/convert-to-WO actions work against the running server now.

## build 144p1 — Phase 1: wire backend-complete features into React (A/B/C)

Overnight batch closing the highest-value "built but not surfaced" gaps. All frontend-only
against endpoints that already existed, so they work live without a backend restart.

- **A — Asset catalog & identifier-rule admin** (Settings ▸ Industry & assets). New `AssetCatalog`
  panel: full CRUD for per-template Groups / Brands / Models, each group showing its live
  identifier rule (Serial / IMEI / VIN / HIN / Engine serial), plus CSV bulk-import
  (`brand,model,group`). This is the cockpit for the multi-industry engine — previously the
  brands/models/groups/identifier endpoints had no React UI. Verified live on the small-engine org
  (5 groups, each showing "Serial / Engine serial").
- **B — Manual stock adjust + Batches tab** (Inventory product drawer). Stock tab gains an
  "Adjust stock" panel (location, receive/write-off/correction, qty, note → POST
  `/inventory/parts/:id/adjust`); new **Batches** tab lists cost/stock layers (document, supplier,
  unit cost, remaining/received, amount, expiry) from `/inventory/parts/:id/batches`.
- **C — Fleet & owner analytics** (Reports ▸ Fleet & analytics). Surfaces the dormant
  `/analytics/*` layer: owner KPIs (revenue, orders closed, close rate, avg ticket, avg turnaround),
  B2B fleet account roll-up, technician productivity, and location comparison, with a 7/30/90-day
  range. Verified live.
- Verified: `tsc --noEmit` clean; vite build + deploy (index-DQdNK96I.js); Fleet tab and Asset
  catalog confirmed rendering against real data in the running app.
- Roadmap for the rest (D–H this phase, plus Phases 2–4) written to `ROADMAP.md`.

## build 144kb — identifier fields are template-driven (Serial/IMEI/VIN/HIN) + "+ details" mirrors the asset form

Follow-up to 144ka. The create flow was showing a fixed Serial/IMEI + IMEI/VIN/HIN set
regardless of industry — wrong for a phone shop (no VIN/HIN) and a body shop alike. The
backend already owns the rule per template (`services/industry.py` →
`describe().identifier = {primary, allowed}`; phones allow imei+serial, autos vin, vessels
hin+vin+engine_serial, with per-group overrides). Now the UI reads it:
- `useTerminology` carries `identifier {primary, allowed}`; new `lib/assetIdentifiers.ts`
  maps allowed kinds → the Device columns we can persist (serial/imei/vin/hin).
- **AssetDrawer** (the asset form) renders only the template's allowed identifiers instead
  of a hardcoded Serial + IMEI; vin/hin added to its type + save body.
- **CreateOrderDrawer** shows the template's *primary* identifier up top (labelled per
  template — "Serial number" for small-engine, "VIN" for auto, etc.), and "+ More details"
  now mirrors the real asset form: Condition, Color, plus any *remaining* allowed
  identifiers — never a fixed VIN/HIN with fake "Vehicles only" placeholders.
- Verified: `tsc` clean; build + deploy (index-DXmf70n1.js); pytest
  `test_144kb_identifier_rule_contract.py` (phones→imei/serial, autos→vin, marine→hin);
  live-checked on the small-engine org — primary field reads "Serial number" and
  + More details shows only Condition + Color (no stray IMEI/VIN/HIN).

## build 144ka — "+ More details" on the WO/Estimate create flow's asset block

The WODrawer already had "+ Details" to open the full asset form on an existing order, but
the *create* flow (`CreateOrderDrawer`) only captured Group / Brand / Model / Serial + device
custom fields. Added a collapsible **+ More details** section under the asset block that
reveals Condition, Color, IMEI, VIN and HIN — matching what the asset form and WO card can
hold — so you can enter the full identity while first creating a work order or estimate.
- Wired end to end: new fields post as `asset_imei`/`asset_vin`/`asset_hin`/`asset_color`/
  `asset_condition`. Backend `OrderIn` extended with `asset_color` + `asset_condition`
  (the rest were already accepted), and the auto-create-device path now sets `color` +
  `condition` and also fires when only colour/condition are supplied.
- React + Tailwind idiom (glass-input, expander toggle), state reset on "create & new".
- Verified: `tsc --noEmit` clean; vite build + deploy (index-BiYmjLpX.js); pytest
  `test_144ka_order_asset_details.py` (2 passed) proves the identifiers round-trip on the
  auto-created asset.

## build 144jz — WO asset card: explicit "+ Details" to open the full asset form

The Work Order's device card already opened the asset card on click, but there was no
visible affordance to add more detail. Added a clear **+ Details** button (top-right of the
device card) that opens the full `AssetDrawer` in place ("Back to WO-…") — editable
identifiers, owner, custom fields (Settings ▸ Forms), and the Work-orders + Repair-history
tabs. `stopPropagation` so it doesn't double-trigger the card's own click.
- Verified live: WO-00010 → device "1234 234" → + Details opens the asset form
  (SN DEV-CK4QR339, client 12321) with all fields editable. `tsc` clean. FE-only, React +
  Tailwind idiom.

## build 144jy — Cross-entity connectivity audit (Product · Client · Work Order)

Deep trace of every create/edit entry point and cross-link across the four core forms
(against how Orderry connects contacts ↔ assets ↔ orders ↔ stock). Result: creation +
custom fields are already wired everywhere, and cross-links are complete — with one dead
wire, now fixed.

- **Fixed: client → device opened nothing.** `ClientDrawer`'s "Devices & Assets" list fired
  a no-arg `onOpenAsset`, and the Clients hub passed `() => {}` — so clicking a client's
  device did nothing (and from a WO opened the *wrong* asset). ClientDrawer is now
  self-contained: clicking a device opens the real `AssetDrawer` for *that* device
  (verified live: client 12321 → "1234 234" opens SN DEV-CK4QR339). Works standalone and
  inside a Work Order.
- **Verified already-connected (no change needed):**
  - Product: `InventoryDrawer` creates products with product custom fields; Movements/
    Purchasing tabs link POs + reservations (144ju); used in WO/POS via `CatalogPicker`.
  - Client: `NewClientDrawer` creates clients with client custom fields; `ClientDrawer`
    edits + has Devices/WorkOrders/Purchases/Loyalty/Comms tabs.
  - Work Order: `CreateOrderDrawer` posts orders with WO-type custom fields + client picker
    (+ inline new-client) + asset with device custom fields + catalog line items; `WODrawer`
    opens the shared Client and Asset cards and the device-aware catalog picker.
  - One shared `AssetDrawer` serves Work Orders, the Assets hub, and Inventory Equipment.
- QA/PM: the connectivity graph WO↔Client↔Asset↔Product is complete; every create path
  persists custom_data; `tsc` clean; live-verified the client→device→asset chain. React +
  Tailwind idiom.

## build 144jx — Create asset from the UI + custom fields (fully wired)

PM/QA pass on assets found a dead end: the Assets-hub "New" button had **no handler**
(did nothing) and Inventory "+ Add Equipment" opened a read-only stub that couldn't POST —
so an asset could only be created inline from a Work Order, and never with custom fields.

- **Extended the shared `AssetDrawer` with a create mode** (`assetId` now optional). No
  assetId → "New {asset}" form: owner picker (We/Client), all identifiers (type, brand,
  model, serial, IMEI, condition, color), **CustomFields (entity=device) so staff can add
  the extra fields defined in Settings ▸ Forms**, and a "Create {asset}" button that POSTs
  `/assets` (incl. `custom_data`). History/Work-orders tabs and Archive are hidden until the
  asset exists. Client-owned requires an owner (guarded FE + BE).
- **Wired both entry points to it:** Assets hub "New {asset}" and Inventory ▸ Equipment ▸
  "+ Add {asset}" now open the real create drawer; both reload their list via `onSaved`.
- **Connected everywhere:** the *same* `AssetDrawer` is used from Work Orders (opens with
  `woNum` + shows the linked WO), the Assets hub, and Inventory Equipment — one component,
  one `/assets` contract. Edit mode also passes `onSaved` so lists refresh after edits.
- Reuses existing endpoints/component; no schema change. React + Tailwind idiom.
- Verified: new pytests — create company asset persists identifiers + `custom_data` and it
  round-trips on the detail; client-owned without an owner is rejected (400). `tsc` clean;
  live in the app the "+ Add Equipment" create form renders (owner picker + identifiers +
  Create button); 144jw asset-detail tests still green.

## build 144jw — Asset (Equipment) card: real detail, wired end-to-end

Checked Orderry's asset card (tabs incl. service-history timeline + linked jobs) and our
Equipment detail. Two problems: Inventory ▸ Equipment ▸ Inspect opened a lightweight,
read-only drawer mislabeled "New asset" / "Register asset", and the *rich* AssetDrawer's
"Work orders" and "Repair History & Logs" tabs were unimplemented stubs ("Loads from…").

- **Equipment Inspect now opens the real `AssetDrawer`** (the same one Work Orders and the
  Assets hub use) instead of the mislabeled read-only view — correct title, owner, editable
  identifiers, Archive/Save. Fixes the "New asset/Register asset" bug.
- **Filled the two stub tabs with real data** (nothing left unwired):
  - *Work orders* — linked-order history from `/assets/{id}` (`history`: WO number, status,
    total, date).
  - *Repair History & Logs* — lifecycle log from `/assets/{id}/movements` (event, zone
    change, note, who, when), fetched on demand.
- Reuses existing endpoints and the existing component — no new backend, no schema change,
  same React + Tailwind idiom app-wide.
- Verified: new pytest asserts `/assets/{id}` carries `history` and `/movements` returns
  items; `tsc` clean; live in the app — Equipment Inspect opens the rich card and both
  Work-orders and Repair-history tabs render real rows.

## build 144jv — Product drawer redesign (Orderry-parity stock card)

Checked Orderry's live stock-item card (tabs General · Batches · History · Purchase
order · Stock; Stock = per-warehouse Reserved/In-stock/Amount table with totals;
History = In/Out filter + document links). Reworked our product drawer to match/beat it.

- **Stock summary hero** — opening an existing product now leads with 4 KPI cards
  (Available · Reserved · Avg cost · Stock value) instead of a big empty image dropzone.
  Header shows the product image/icon, SKU·barcode, and a Low-stock badge.
- **New Stock tab** — per-location table (Location · Reserved · In stock · Value) with a
  totals row, matching Orderry's Stock tab. BE `/inventory/parts/{id}/stock` gained a
  `value` per row plus `total_value`/`total_reserved`/`unit_cost` (valued at avg cost,
  falling back to cost price). Additive; no schema change.
- **Movements tab** — added All / Inbound / Outbound filter chips (client-side by qty sign).
- Image dropzone is demoted: shown only when creating, or when an existing product has no
  image; products with an image show it in the header instead.
- Stays in the app's React + Tailwind idiom (no vanilla-JS/DOM style); all changes live in
  the real `InventoryDrawer` (the one `InventoryHub` renders).
- Verified: new pytest asserts `/stock` value + totals end-to-end; 144ju/144jt suites still
  green; `tsc` clean; live-verified in the running app — header, KPI hero, Stock/Movements/
  Purchasing tabs all render (empty states correct for the zero-stock demo product).

## build 144ju — Product drawer: Movements + Purchasing tabs (+ 144jt drawer fix)

Surfacing win from `docs/INVENTORY_DETAIL_FLOWS.md`: the product card now shows the
data the engine already tracks — no new backend beyond one additive serializer field.

- **Movements tab** — full stock-movement audit trail per product from the existing
  `/inventory/parts/{id}/movements` (type · reference · signed qty · location · when).
- **Purchasing tab** — linked purchase orders (`/parts/{id}/purchase-orders`, received/
  ordered + expected date) and open reservations (`/inventory/reservations` filtered to
  this product).
- **BE (additive):** `/inventory/reservations` items now include `part_id` so the drawer
  can filter per product. No schema change — the column already existed; only the
  serializer changed. Single migration head unchanged (`g2b4d6f8h0j2`).
- **Correction:** 144jt's "Compatible devices" editor and this work were first written
  into `ProductDrawer.tsx`, which is **dead code** — the live product drawer is
  `InventoryDrawer.tsx` (rendered by `InventoryHub`). Both the compatible-devices editor
  and the new Movements/Purchasing tabs are now in `InventoryDrawer` (tabs shown only
  when editing an existing product); `compatibility_json` added to its save payload so
  the 144jt setter actually reaches the API. `ProductDrawer.tsx` neutralized (sandbox
  can't delete it).
- Verified: new pytest asserts the reservations `part_id` field end-to-end (create
  product → seed stock → reserve → GET); 144jt compatibility suite still green; `tsc`
  clean; deployed bundle confirmed to contain both features. Live browser verify pending
  (no running instance this session).

## build 144jt — Parts↔device compatibility (repair-native inventory)

First inventory differentiator from the council review: surface which parts fit the
device on the bench — something Orderry has no answer for. Fully additive, no schema
change (reuses `Part.compatibility_json`).

- **Matcher** `services/compatibility.py` (pure): `compatibility_json.fits = [{brand, model?}]`
  vs a device's brand/model; brand-only entry fits any model; case/space tolerant;
  unrelated keys (e.g. conversions' `from_asset`) ignored.
- **Search** — `/catalog/search?device_id=` adds a `fits` flag to product results
  (services/bundles untouched); `/inventory/parts?device_id=&compatible_only=` flags
  and optionally filters. Both are no-ops without `device_id` — existing callers
  unchanged.
- **Data entry** — product create/update + serializer accept `compatibility_json`
  (partial-save guard so it's never wiped); the product drawer gained a "Compatible
  devices" editor (brand/model rows).
- **WO picker** — `CatalogPicker` takes an optional `deviceId`; the work-order
  Services & Products add-line picker passes the WO's device so matching parts show a
  green **Fits** badge. Other pickers (POS, invoice, create) pass nothing and are
  unaffected.
- Verified: 8 new/related tests (pure matcher incl. edge cases; search flagging;
  `compatible_only` filter; product create with compatibility; org isolation) plus the
  full inventory/product suite — **23 passed, no regression**; `tsc` clean; caught &
  fixed an out-of-scope `dev` reference before shipping; corrected bundle deployed;
  Inventory loads clean live (no console errors).

## build 144js — EU e-invoice adapter (EN 16931 / Peppol BIS Billing 3.0)

First RegionPack e-invoice adapter, and the reason Europe was the right first market:
it extends the existing UBL pipeline instead of needing a government clearing account.

- **Region-aware UBL** (`services/einvoice.py`) — for EU orgs the generated invoice XML
  now targets **EN 16931 / Peppol BIS Billing 3.0**: the correct CustomizationID +
  ProfileID, supplier/customer country codes and `PartyLegalEntity`, a real
  `TaxTotal → TaxSubtotal` with tax category (S/Z) + percent, and a
  `ClassifiedTaxCategory` on every line. Non-EU orgs get the EN 16931 base
  customization without the Peppol profile. Standard, tax rate, currency and country
  all come from the org's RegionPack, so it adapts per market automatically.
- **Refactor for testability** — XML assembly is now a pure `render_ubl(data)` function;
  `build_ubl(db, org, inv)` gathers the data and calls it. The endpoint signature is
  unchanged, so the existing "issue e-invoice" + download-XML buttons work as-is.
- Still no auto-submission to any authority — live Peppol/SdI transmission needs a
  certified Access Point; this produces the compliant document to hand to one.
- Verified: 3 new pure tests (EU → Peppol profile + tax subtotal + per-line category +
  country codes + well-formed XML; non-EU → base, no profile; zero-tax → category Z) and
  the existing e-invoice endpoint tests (4) all pass; no regression.

## build 144jr — RegionPack spine + Owner P&L (US · LATAM · Europe)

First slice of the "locally correct everywhere" finance strategy: the pluggable
localization spine, plus the first region-agnostic feature riding it.

- **RegionPack** (`services/regions.py` + `/api/regions/pack` · `/catalog`) — one
  resolver that, per country/region, returns the correct currency, tax model
  (VAT/IVA/sales-tax, inclusive vs imposed), e-invoice standard (EU EN-16931/Peppol,
  MX CFDI, BR NF-e, CL DTE, CO/PE/AR factura electrónica — with live/planned status),
  accountant exports (QuickBooks/Xero/DATEV/Contabilizei/…), dunning channel, and
  number formatting. Covers US, Canada, 12 EU markets, and 8 LATAM markets. Builds on
  the existing signup region/tax seeding rather than duplicating it. The e-invoice
  *adapters* are staged (several need provider sandbox creds); the pack advertises the
  standard + status without implying it's live.
- **Owner P&L** (`services/finance_pnl.py` + `/api/finance/pnl`) — a plain-language
  "did I make money?" statement neither Orderry nor most repair CRMs give the owner:
  net revenue (ex-tax, issued invoices) − parts COGS (product invoice lines →
  order-line cost) − operating expenses (period cash expenses) = gross & net profit
  with margins, org-scoped, any period. Method documented in-code and in the UI.
- **Finance → P&L tab** — net-profit hero (green/red), the five component cards, and a
  P&L statement, all in the shop's own currency from the RegionPack.
- Verified: 3 pytest cases (P&L math incl. draft exclusion + org isolation; RegionPack
  resolves US/EU/LATAM correctly; P&L + pack APIs) — all pass; app imports clean
  (74 routers); FE tsc + build clean and deployed. Live browser click-through pending
  (Chrome extension dropped mid-session).

## build 144jq — Custom report builder (Reports Tier 3)

A user-defined report builder: pick a data source, choose columns, add filters,
optionally group + aggregate, run, chart, export, and save — no SQL, no code.

- **Backend** — `services/custom_reports.py` is a *whitelist-driven* query builder:
  five sources (work orders, invoices, payments, clients, products) each expose a
  fixed set of typed fields. A report spec (source, columns, filters, group_by,
  aggregates) is validated against that whitelist and turned into a parameterised
  SQLAlchemy query. Multi-tenant safety is structural: `org_id == caller's org` is
  always applied and cannot be widened, only whitelisted fields/operators are ever
  used, and values are bound parameters — a spec naming anything else raises before
  it touches the DB. Flat mode (list rows, capped at 2000) and group-by mode
  (count / sum / avg on numeric fields) both supported; date range + location honor
  each source's own columns.
- **Model + API** — `custom_reports` table (org-scoped JSON specs; migration
  `g2b4d6f8h0j2`) and `/api/custom-reports` (sources metadata, list/create/update/
  delete, ad-hoc run-by-config, run-by-saved-id). Every path is org-scoped.
- **Frontend** — a "Custom report" tab in Reports: saved-report sidebar + a builder
  (source, column chips, filter rows, group-by + aggregate rows, date range,
  location) that runs into a results table, a bar chart, and Excel/CSV export
  (reusing the shared lazy-SheetJS export helper). Save / Update / Delete.
- Verified: 5 pytest cases (whitelist rejects unknown source/field/op, org
  isolation, group-by aggregate correctness, flat mode, API CRUD) all pass; app
  imports clean (73 routers), single migration head; live end-to-end in the browser
  (build → group-by → run → chart + table → save → persists), no console errors.

## build 144jp — Reports Tier 2 (charts, period-over-period, richer Feedback)

All FE-only (`pages/reports/ReportsHub.tsx`), no backend change, no new dependency —
charts are hand-rolled CSS bars.

- **Report chart** — a `BarChart` (CSS-width bars, no canvas/lib) renders the current
  report: X = first text column, Y = a selectable numeric column (dropdown when the
  report has more than one), top 20 by absolute value, zero rows dropped, with a
  Hide/Show toggle. Respects the active search filter.
- **Period-over-period deltas** — on run, a non-blocking second fetch pulls the
  immediately-preceding equal-length window (same key + location, dates shifted back)
  and totals its numeric columns; the totals row then shows ▲/▼ percent per column
  vs. the prior period. Best-effort — never blocks or breaks the table if it fails,
  and "no prior baseline" shows "—" rather than dividing by zero.
- **Richer Feedback tab** — summary cards (Avg rating, NPS, Response rate, #
  responses) + a rating-distribution bar chart (5→1★), above a cleaned-up comments
  table (submitted only, with order number). All from the existing `/feedback/list`
  payload (previously the extra metrics were fetched and discarded).
- Verified by a standalone check (prev-period window math incl. single-day and
  month-boundary, delta % incl. zero/negative baselines, distribution bucketing,
  chart top-N sort) and live in the browser (panels, selector, toggle, delta row,
  feedback cards + empty states; no console errors).

## build 144jo — Reports Tier 1 (export, presets, totals, location, sort, search)

Made the Reports runner actually useful without touching the backend — every
capability below rides endpoints that already existed.

- **Export** — Excel (.xlsx) and CSV buttons that export exactly what's on screen:
  the search-filtered, sorted rows plus the totals row. Generated client-side from
  the rendered view via SheetJS, so there's no second copy of the filter/sort logic
  on the server to drift. SheetJS is a lazy dynamic `import()` — a separate 143 KB
  gzip chunk fetched only on first export, so the main bundle is unchanged. (We only
  *write* our own data with it, never parse untrusted files, so its parser advisories
  don't apply.) Verified by a standalone check of the serialize path (search
  exclusion, sort order, totals, CSV comma/quote escaping, valid xlsx ZIP).
- **Date presets** — Today / This week / This month / Last month / This quarter /
  YTD chips that set the range and re-run in one click.
- **Location filter** — a location `<select>` (shown when the org has more than
  one) that scopes both the run and the export via `location_id`.
- **Totals row** — client-side column sums for every numeric column, rendered as
  a sticky-styled footer; only appears when there's something to total.
- **Sort + search** — click any column header to sort (asc/desc/off); a per-run
  search box filters rows client-side with an "N of M rows" count.
- FE-only (`pages/reports/ReportsHub.tsx`); tsc + vite build clean; verified live
  in the browser (export fires, presets update the range, totals render).
- A11y + correctness polish: column headers are now keyboard-operable (focusable,
  Enter/Space to sort, `aria-sort` state) and the search box has an `aria-label`;
  the totals "Total" label now lands in the first non-numeric column so a report
  with a numeric leading column keeps its own sum (covered by the export check).
- Senior-architect review fix: `iso()` now formats from local date components
  instead of `toISOString()` (UTC), which was shifting local-midnight preset
  boundaries (e.g. "This month") to the previous day in IST and other ahead-of-UTC
  zones — a real off-by-one for the India target market.

## build 144jn — Per-shop integration credentials (multi-tenant isolation)

Made the keyed connectors truly per-tenant so no two shops ever share a payment
or messaging account. Audit first confirmed all integration config + business
data already carry `org_id` and every authed query is org-scoped (no data
leakage); the gap was that provider *credentials* came from one global env.

- **New encrypted store** `org_integration_credentials` (one row per org+provider,
  Fernet-encrypted at rest via SECRET_KEY) + `services/org_creds.py` with an
  `effective()` resolver: a shop's own keys win; global env is only an operator
  fallback. Providers: stripe, square, sumup, twilio, smtp, meta.
- **Payments** (`services/payments.py`) now build the gateway from the calling
  shop's keys — `resolve_gateway`/`available_gateways_org`; refresh + Stripe
  Terminal paths load per-org keys too. `/payments/config` reflects the shop.
- **Notifications** — SMTP + Twilio adapters accept per-shop creds; the rules
  engine, dispatch, manual sends (orders/comms/chats/test) and the Meta inbox
  reply all pass the shop's own keys. Platform auth emails stay on env.
- **API + UI** — `/api/integrations/credentials` (masked GET, settings-gated
  PUT/DELETE); the Integrations hub cards show "Your keys / Shared default /
  Not set" and open a right-side setup drawer to enter keys per shop.
- Migration `f1a3c5e7g9i1`; tests in `test_144jn_org_creds.py` (encryption,
  per-org isolation, masking, API, config). Verified live end-to-end.

## build 144jm — Integrations hub (Phase 1: surface every wired connector)

The React Integrations page only showed Email + QuickBooks, hiding connectors
the backend already supports. Rebuilt it as an Orderry-style grouped card grid
that surfaces everything, with honest, live status badges:

- **Payments** — Stripe (Live/Simulated + which gateways are configured)
- **Accounting** — QuickBooks, Xero (Connected / Not connected / Not configured)
- **Communication** — Email (Gmail/Outlook/SMTP), SMS & WhatsApp (Twilio),
  Calls/VoIP (Twilio/Aircall/Zadarma), Meta chat inbox (Messenger/IG/WhatsApp)
- **E-commerce** — Shopify/WooCommerce with a "Manage stores" jump
- **Automation & developer** — Zapier/Make/n8n, Public REST API, Workflow AI
- **On your website** — repair-status widget with one-click embed-code copy

Reads /integrations/{email,qbo,xero}/status, /payments/config,
/telephony/config, /ecommerce/integrations. No backend changes. API-keys and
Webhooks managers remain below. `tsc` + `vite build` clean (index-mrgHG9UD.js);
verified live.

## build 144jl — Status picker matches the app's status UI + scroll fix

- **Couldn't scroll the list.** The close-on-scroll handler was registered with
  `capture:true`, so it fired for scrolls *inside* the popover too and slammed it
  shut the moment you scrolled the status list. It now ignores scrolls that
  originate inside the popover — the full list scrolls and every status is
  reachable.
- **Looked plain / off-brand.** The picker now mirrors `StatusControl`: each row
  is a colored status dot + semibold ink label grouped by lifecycle (New / In
  progress / Pending / Ready / Closed), the selected row gets the status's own
  soft-tint background + check, chosen statuses show as soft-tint colored pills
  in the field, and the popover uses the clay drawer shadow with an icon search.
  Status `color` is now threaded through to the picker from `/statuses`.

Verified live in the browser. `tsc` + `vite build` clean (index-N03fPCKu.js).

## build 144jk — Status dropdown: real root-cause pass (verified live)

Diagnosed the "still broken" report by opening the control in the live app
instead of guessing. Three real defects found and fixed:

1. **Inner list couldn't scroll.** The scroll area was a flex child of a
   height-capped `flex-col` parent but lacked `flex-1 min-h-0`, so long status
   lists overflowed the cap and the bottom items were clipped and unreachable.
   Added `flex-1 min-h-0` — the list now scrolls within the popover.
2. **It flipped upward and covered the card above.** The popover is now
   **always opened downward**, anchored under the trigger (Orderry-style); when
   the field sits low it scrolls the trigger up (`scrollIntoView`) first so
   there's room, rather than flipping over unrelated content.
3. **No room below the last card.** Added `pb-80` to the settings scroll area so
   fields near the bottom (the Public-pages status pickers) have space to open
   down and be fully visible.

Verified live in the browser: portaled, grouped, searchable, scrollable, opens
downward, fully on-screen. `tsc` + `vite build` clean (index-DrbiOrej.js).

## build 144jh — Status dropdown flips up + height-capped

QA follow-up: the portaled StatusMultiSelect now measures the space below the
trigger and **flips upward** when it's cramped (fields low in the viewport, like
Work orders), and caps its height to the available space with internal scroll —
so the list never runs off the bottom of the screen. `tsc` + `vite build` clean.

## build 144jg — Fix clipped status dropdown

The StatusMultiSelect popover was rendered inside the settings card, whose
`overflow-hidden` clipped it — the open list got cut off and overlapped the next
card. It now renders through a **portal with fixed positioning** anchored to the
trigger, so it floats above everything; closes on outside-click, scroll, or
resize. `tsc` + `vite build` clean.

## build 144jf — Public-page approval settings cleaned up

The Estimates and Work-orders "accept / decline" blocks on the Public pages
settings were cluttered — every status showed as an always-visible chip wall, and
the whole config (signature, statuses, move-to) stayed visible even when the
feature was toggled off. Now:

- The detail config (signature, "show in these statuses", accept/decline move-to)
  is **hidden until the toggle is on**, then reveals in a tidy indented block.
- The status chip wall is replaced with a compact **StatusMultiSelect** popover:
  the control shows selected statuses as small removable chips (or "All statuses
  (any)"), and an "Add status" dropdown lists the rest grouped by status group.

FE-only, no schema change. Gates: `tsc` clean · `vite build` clean.

## build 144je — Email template editor upgraded to match the document editor

The notification Email template editor now mirrors our document template editor:
inline variable **pills** ({Tokens} render as non-editable chips and serialize back
on save), an **HTML source** toggle, **table** insert, headings, bullet lists,
divider, text highlight, and the existing font family / size / colour / B-I-U-S /
alignment / link / image / one-click **company logo** controls. Variables clicked
from the picker drop in as pills at the cursor. SMS templates stay a plain textarea
with the segment counter. Reuses the shared `.tok-pill` styling.

No schema change. Gates: `tsc` clean · `vite build` clean.

## build 144jd — Notifications: status picker + rich email template editor

Two Orderry-parity fixes found by scanning the live app:

- **"Status changed to" status picker.** The rule editor's status field was a
  free-text box; it's now a real dropdown of the org's statuses, grouped by
  status group (matches Orderry, which shows the actual coloured statuses).
  Populated per module record type — Jobs vs Estimates vs Inquiries — via
  `/statuses?record_type=`. "Any status change" = fire on every change; pick a
  specific status = "status changed to X". Stored in `trigger_status_key`.
- **Rich email template editor.** Email templates now use a WYSIWYG editor
  (font family, size, text colour, bold/italic/underline/strikethrough,
  alignment, link, image, and one-click **company logo** insert) storing HTML —
  like Orderry's TinyMCE email editor. SMS stays plain text + segment counter.
  The SMTP adapter now sends `text/html` when the body contains markup, else
  plain text.

No schema change. Gates: `tsc` clean · `vite build` clean · pytest 12/12
(notif suites) · migration head unchanged.

## build 144jc — Notifications: fix dead triggers + test send + delivery log + quiet hours

Follow-up gap-fix after the Orderry re-check. Four items:

- **Dead triggers fixed (correctness).** The rules engine now actually fires on
  **Order created** (create path ran only the legacy template dispatch before, so
  every rules-UI "created" rule — client and internal-staff — was silently dead),
  and **comment_added** + **payment_received** are now real rules events wired at
  the comment and payment paths (rules-first, legacy template fallback, plus staff
  notify). Sensible default messages per event when no template is attached.
- **Send test.** New `POST /notifications/test`; the template editor has a
  **Send test** button that delivers a sample to the signed-in user.
- **Delivery status on the order.** The work-order Activity tab's old hardcoded
  timeline is replaced with the real **Notifications & delivery** log
  (`/notifications/log?order_id=`) — per-message channel, sent/failed/scheduled
  status, recipient, time, and error.
- **Per-location quiet hours.** The "only during working hours" hold now reads
  each Location's `working_hours` for the day instead of a hardcoded 09:00–18:00,
  falling back to the org default when unset.

No schema change. Gates: `tsc` clean · `vite build` clean · pytest 15/15
(`test_144jc` + `144jb` + `144ja` + `144iz`) · migration head unchanged.

## build 144jb — Notifications hub: Automations + Internal Workflows + Orderry structure

Reworked the Notifications settings screen to match Orderry's Workflows layout and
fold Automations in, plus real internal (staff) workflow notifications.

- **Automations under Notifications.** The standalone "Automation" settings item
  is gone; the full automation engine (create-task / assign / add-tag / webhook /
  notify, plus overdue-order and low-stock triggers) now lives as a third tab in
  the Notifications hub: **External · Internal · Automations**.
- **Internal Workflows for managers + techs.** New `scope` (client|staff) on
  NotificationRule. Internal now has **Workflows · Inquiries · Tasks** tabs, and a
  new engine path `notify_staff_order` fires staff-scoped order rules to the
  assigned technician and the order's manager (never the person who made the
  change) — channel `push` → in-app bell + web push, `email`/`sms` → adapter.
  Wired into the order status-change path.
- **Jobs vs Estimates split.** New `record_type` on NotificationRule; the
  Workflows module shows Jobs / Estimates sub-tabs and the engine only fires a
  record-type-scoped rule for that type (Orderry parity).
- **Reminders as their own sub-section**, **Location column + All/Selected
  picker** on rules, matching Orderry's rule table.
- **Email vs SMS templates.** New dedicated template editor with a **Type**
  toggle: Email shows Subject + long body; SMS shows plain body + the segment
  counter and no subject. Templates library gained a **Type** column, and the
  template body now has the module-scoped variable picker.

Migration `e9g1i3k5m7o9` adds `notification_rules.scope` + `.record_type`.
Gates: `tsc` clean · `vite build` clean · pytest 10/10
(`test_144jb_notif_workflows.py` + `test_144ja` + `test_144iz`) · single head.

## build 144ja — Notifications: Orderry parity polish (all four N-P items)

Closed the four editor/settings-UX parity gaps from the Orderry notifications
comparison (ROADMAP.md Part B). All are additive; the rules engine was already
at/ahead on capability.

- **N-P1 — SMS character + segment counter.** The message-template editor now
  shows a live `N characters` count, and for SMS templates the GSM-7 segment
  count (160/153 rule) with an amber note once it spills past one segment —
  matching Orderry's `0/1000` + length note.
- **N-P2 — External vs Internal split with per-module tabs.** The Notifications
  settings screen now has an *External (clients)* / *Internal (staff)* toggle.
  External carries per-module tabs Workflows · Inquiries · Bookings · Contacts;
  Internal carries Tasks. Rules are filtered by the active module. A dedicated
  `NotifRuleDrawer` replaces the generic editor for rules — it presents
  module-correct events, a *Send via* channel (email/SMS/WhatsApp/chat for
  clients; in-app-push/email/SMS for staff — the equivalent of Orderry's
  "gateway"), template picker, delay/reminder-offset, working-hours hold, and
  chat-first. This also fixes a latent bug: the old generic editor emitted
  `event="status_change"` and modules (`client`/`invoice`) the backend rejects.
- **N-P3 — module-scoped variable picker.** `GET /notifications/variables` now
  takes `?module=`; the picker shows only groups that module can resolve (Lead
  vars for Inquiries, staff vars for Tasks, …) like Orderry's per-tab catalog.
  The template body also gained the variable picker it was missing.
- **N-P4 — Contacts module (birthday lifecycle).** New `contact` module +
  `birthday` event in the rules engine, a `contact-lifecycle-sweep` job that
  greets clients on their birthday (idempotent per client/rule/year, consent-
  gated), a `clients.birthday` column (migration `d8f0h2j4l6n8`), and a birthday
  field on the client drawer + create form.

Gates: `tsc --noEmit` clean · `vite build` clean · pytest
`test_144ja_notif_parity.py` (3) + `test_144iz_notifications.py` (3) green ·
single migration head `d8f0h2j4l6n8`.

## build 144iz — Notifications hardening (all four audit gaps)

Closed the gaps found in the notifications audit (see ROADMAP.md Part B):

- **N-GAP-1 — scheduler now runs.** New `notification-sweep` periodic job (every
  120s, on the existing `job_runner`) queues due reminders and dispatches every
  scheduled/delayed NotificationLog per active org. Previously `run_due` /
  `queue_reminders` were manual endpoints only, so appointment reminders and
  delayed/working-hours sends were queued but never actually delivered.
- **N-GAP-2 — consent enforced.** New `channel_consented()` gate: a client with
  `consent_phone`/`consent_email` = False is skipped (logged
  `status="skipped", error="No client consent for …"`) across the order rules
  engine, reminder queueing, and the legacy template path. Explicit-opt-out only,
  so nulls don't mute the existing base. (Bookings = implicit consent, unchanged.)
- **N-GAP-3 — one canonical send path.** On status change the rules engine runs
  first; the legacy NotificationTemplate dispatch only fires when **no** rule
  matched — no more double-sends, template-only orgs still work.
- **N-GAP-4 — delivery visibility.** New `GET /api/notifications/providers` reports
  each channel live vs simulated (SMTP / Twilio SMS+WhatsApp / connected email /
  VAPID push); Settings → Notifications shows a delivery-status banner so a shop
  knows whether messages actually go out.

Gates: 3 new tests (`test_144iz_notifications.py`: provider status, consent block,
run_due dispatch) + full notification/booking suite green (69 passed), tsc clean,
providers endpoint live (auth-gated) on the running server.

## build 144iy — Booking 2.0: React wizard, catch-all, confirmation, phone recognition

The last legacy public page is gone: `/book/{slug}` is now a React wizard
(`frontend-react/src/public/PublicBooking.tsx`) on the app tokens, white-labelled
with the shop's logo + page colour. Flow: [location with open/closed-today status]
→ service (category chips, price/duration) → [specialist with "No preference"]
→ time (day strip with free counts, first open day auto-picked, slots grouped
Morning/Afternoon/Evening, running service summary) → details → branded
confirmation with **Add to calendar (.ics)** and the location's address/hours/
Directions/Call card.

What beats Orderry/Shopmonkey:
- **"Something else / not sure" catch-all** — describe the issue free-text and
  still book a slot (issue lands in the booking note).
- **Never a dead end** — no slots (or nothing fits) → "Leave a request" posts to
  new `POST /api/public/booking/{slug}/request`, creating a rate-limited web
  **Lead** in the Inquiries board with a 60-min SLA clock.
- **Returning-customer recognition** — new `POST /api/public/booking/{slug}/hello`
  (rate-limited, first name only) greets known phones and prefills the name.
- booking_config now exposes per-location address/phone for the confirmation card.

Backend additions live in `public.py` only — the availability engine is untouched.
Legacy `frontend/book.html` retired to a stub. Gates: 3 new tests
(`test_144iy_booking2.py`) + existing booking suites green (7 passed), tsc clean,
full wizard walked live end-to-end (location → service → specialist → time →
details → "You're booked!" with .ics). Follow-ups parked: photo upload on the
catch-all, deposits at booking (waits for the payments integration).

## build 144ix — Public track page: branding, live status, reviews, pickup, lookup

Five upgrades to the customer-facing track page, all wired end-to-end:

1. **White-label** — the page now uses the shop's uploaded logo and `page_color`
   accent (logo chip, rail border, stepper, CTAs) instead of generic RepairOS blue,
   so it reads as the shop's own page. Payload exposes logo_data / brand_color / website.
2. **Live status** — the page polls every 20s (paused while the tab is hidden) so the
   stepper, updates and photos advance on their own, with a "Live · updates
   automatically" indicator. Dumb polling on the already-cached public endpoint.
3. **Review capture** — when active (Settings → Customer feedback), a rating card
   appears once the job is ready/done; submitting posts to a new track-scoped
   endpoint (`POST /api/public/track/{token}/feedback`, find-or-create) and high
   raters get a one-tap button to the shop's public-review link. New
   `feedback_settings.google_review_url` (+ migration, settings API, settings UI).
   Everyone is asked to rate; the public-review link is only offered, never gated.
4. **Pickup logistics** — once ready, a rail card shows the location name, address,
   today's opening hours, a Directions link (Google Maps) and a Call button, from
   the location/org record.
5. **Re-findability** — bare `/track` is a React lookup page (order number + phone →
   the existing rate-limited `/api/public/lookup`), for customers who lost their link.

Backend: `main.py` serves React for `/track`; `public.py` track payload gains
branding + pickup + feedback blocks and the feedback submit endpoint. Gates: tsc
clean, 4 new tests + public suite green (`test_144ix_track_extras.py`), migration
head `c7e9g1i3k5m7`. Branding + live indicator + lookup verified live; review +
pickup verified by tests (org had no address/feedback config to show them on-screen).

## build 144iw — Signup + reset + verify + invite ported to React

The remaining auth deep-links now use React pages (new `frontend-react/src/public/
AuthPages.tsx`), sharing the same flat shell as `<Login/>` — no legacy vanilla-JS
auth shell anywhere in the sign-in / onboarding flow.

- `/signup` → `<Signup/>` (loads /api/auth/signup-options; POST /api/auth/register;
  timezone derived from the browser). `/reset-password` → `<ResetPassword/>`
  (POST /api/auth/password/reset). `/verify-email` → `<VerifyEmail/>` (auto POST
  /api/auth/email/verify with ok/fail states). `/accept-invite` → `<AcceptInvite/>`
  (preview + POST /api/auth/invite/accept).
- `main.tsx` routes all five auth paths to standalone React pages; `main.py` serves
  the React bundle for `/login`, `/signup`, `/reset-password`, `/verify-email`,
  `/accept-invite`. Legacy `auth-flows.js` / legacy shell no longer referenced by
  any auth route. Verified live (signup options load, reset form renders).

## build 144iv — Public track + login are now React (legacy vanilla JS removed)

The public tracking/estimate/e-sign page and the sign-in page were the last two
vanilla-JS pages loading the old `design.css` (the "clay" look that kept coming
back). Both are now real React pages on the app's Tailwind tokens, flat styling —
no `design.css`, no leftover clay shadows.

- New `frontend-react/src/public/PublicTrack.tsx` — the document + sticky action
  rail layout, built on app tokens (`bg-white`, `border-slate-200`, `text-ink-*`,
  primary `#0059ba`), follows the visitor's device theme (light/dark). Signature
  capture, recommendations approve/decline, updates, invoice + e-invoice, signed
  certificate, timeline and message box all ported to React.
- New `frontend-react/src/public/Login.tsx` — React sign-in (POST
  /api/auth/token, stores `ros_tok`, hands off to `/`). Same flat brand styling.
- `main.tsx` routes `/track/*` and `/login` to these standalone pages, bypassing
  the app shell + MeProvider; the no-session guard now sends visitors to the
  React `/login` (was `/legacy.html`).
- Backend `main.py`: `/track/{token}` and `/login` serve the React bundle;
  `frontend/track.html` retired to a redirect stub (folder mount blocks delete —
  the file is unreferenced and safe to remove from the repo).
- Signup / reset-password / verify-email / accept-invite deep links still use the
  legacy shell for now (separate follow-up).

## build 144iu — Track page: document + sticky action rail (not a stacked list)

The public page was a single vertical column of separate cards — a "list view." Rebuilt it on
Orderry's public-page pattern: one white **document sheet** on the left holding the whole
order/estimate (letterhead → title + status → details → status stepper → itemized table →
recommendations → updates → invoice → progress history → message), and a **sticky right rail**
carrying the total + primary action (Review & sign / Decline, or a status pill when nothing is
due) plus the signed-approval download. The itemized estimate is now a real Description/Qty/Amount
table with a totals box instead of stacked rows. Collapses to a single column on ≤820px with the
action rail first (action-first on mobile). Same React app tokens as 144it; light + dark verified
live on WO-00010.

## build 144it — Track page rebuilt on the React app's real tokens

The public page had been styled with the legacy `design.css` (frosted glass/clay) — a different
system from the new React app, which is why it read as "the old build." Rebuilt `track.html`'s
visual layer on the app's actual "Liquid Clay Studio" tokens extracted from
`frontend-react/tailwind.config` + `src/index.css`: Inter, surface `#f7f8fd` / dark `#0b1020`,
white / `#151b2e` cards with hairline borders (`#e6e9f2` / `#2a3550`) and `clay-sm` shadow, ink
ramp `#14213f/#435577/#6c7a99`, primary `#0059ba`, blue→purple gradient CTA, primary-blue
stepper, itemized-estimate lines. Light + dark both verified live on real data (WO-00010).

## build 144iq — Track page: match app theme (auto light/dark) + fixes

- Public track/e-sign page now follows the visitor's device theme (prefers-color-scheme), so it
  matches the app's own light and dark themes instead of the old frosted-pastel look. Removed the
  light mesh orbs; theme-adaptive canvas, stepper dots, connectors, and status tint for both modes.
- Fixed the "None None" asset label — the public serializer now joins only the present
  brand/model parts and falls back to "—".

## build 144ip — Public track page redesigned to the system (DESIGN.md extracted)

Used the Stitch extract-design-md flow to capture the app's real tokens into a root `DESIGN.md`
(source: `frontend/static/css/design.css`, which the app and public pages already share), then
rebuilt `track.html` against it so the public page reads as the same product:

- App-style branded header (shop logo + name + order number) with a soft-tint status pill
  (colour dot + ~12% fill + darker text, keyed by stage), replacing the loud blue→purple block.
- Action-first for estimate / e-sign: when a doc awaits approval the page leads with an action
  card (device + estimated total + Review & sign / Decline) and a sticky mobile CTA; otherwise
  it leads with the live status/stepper.
- All existing behaviour preserved (signature pad, approve/decline, recommendations, messaging,
  invoice + e-invoice + signed-certificate downloads). Served from `frontend/` — no build step.

## build 144io — Wire the work-order "Tracking link" (public page)

The "Tracking link" button in the work-order/estimate drawer footer was a dead control (no
handler) — that's why the public tracking page never opened. Wired it: it now calls
`POST /orders/{id}/public-link` (idempotent — returns the existing token or mints one), copies
`{origin}/track/{token}` to the clipboard, and opens it in a new tab. Verified the whole chain
end-to-end (public_token column → staff link endpoint → `/api/public/track/{token}` payload →
`/track/{token}` HTML page → order detail exposes the token).

## build 144in — Fix status-change 500 from duplicate estimate statuses

Root cause of the "Internal server error" on status change: some orgs ended up with duplicate
estimate statuses — every `est_*` key existed twice, once correctly `record_type='estimate'`
and once orphaned as `record_type='work_order'` (both `is_estimate=1`). This happened when the
startup auto-migrate added the `record_type` column defaulting to `work_order` on existing
estimate rows, and the seeder then created a fresh estimate-typed set. Bare-key status lookups
(`scalar_one_or_none`) then matched two rows → `MultipleResultsFound` → 500.

- `ensure_seeded` now self-heals: it deletes `record_type='work_order'` rows whose key is an
  estimate key (`est_*`) before seeding — runs automatically per org on the next `/statuses`
  call, no-op on clean orgs. Also cleans the polluted work-order status list in settings.
- `change_status` resolves the target and previous status scoped to the record's own pipeline
  (`record_type`) and with `.first()` instead of `scalar_one_or_none`, so duplicate/legacy keys
  can never crash the status change again.

## build 144im — Drop status stepper; harden status-change side effects

- Removed the lifecycle stepper/rail from the work-order & estimate drawer — the kanban board
  already shows the pipeline, and the strip added clutter. The header keeps the clean pill.
- Fixed a 500 on status change: the two side-effects in `change_status` that weren't wrapped
  (client-notification dispatch and the asset/warehouse auto-move) are now best-effort with
  logged exceptions, matching every other side effect in that handler. A misconfigured
  notification template or asset move can no longer fail the status change itself.

## build 144il — Status rail → connected stepper

Replaces the flat 4-bar rail above the status pill (it read like a loading bar) with a
connected stepper: one node per progress group, labelled, with completed groups shown as a
filled check, the current group as a coloured ring, and future groups hollow. Node/connector
colours come from each group's status colour. Purely visual; same data, no backend change.

## build 144ik — StatusControl: differentiated status UI for work orders + estimates

Replaces the plain status dropdown in the order/estimate drawer with a purpose-built
`StatusControl` that goes well beyond Orderry's flat solid-fill pill + bare list. One
component drives both pipelines; the record type it's handed decides the statuses, colours
and hints, so a work order and an estimate look distinct at a glance.

- **Lifecycle rail** above the pill — one segment per progress group, filled up to the
  current stage in each group's colour. Shows the whole journey, not just the current pill.
- **Soft-tint pill** (glass/clay) with a status dot, tinted from the status's own colour —
  no more saturated solid blocks.
- **Live SLA / time-in-status** on the pill (`Xh Ym · SLA Zh`), amber when nearing and rose
  when overdue, driven by the status's `time_limit_min` and the order's dwell time.
- **Smart picker**: recommended next status pinned and highlighted at the top; the rest
  grouped by status group; each row carries a coloured dot and a one-line trigger hint
  ("invoices", "needs payment", "notifies client", "converts to work order", "client
  sign-off", "moves asset", "locks edits"); type-to-filter box for long status sets.
- The drawer now loads statuses with `?record_type=estimate|work_order`, so estimates finally
  show estimate statuses in the picker (previously always work-order statuses).
- All data comes from the existing 144ij status serializer — no backend change. Table pills
  keep their existing tinted dot styling.

## build 144ij — One status engine, five configurable pipelines (Orderry Statuses parity, deeper)

Orderry ships five separate status screens (Inquiries / Estimates / Work orders / Invoices /
Transfers), each a shallow list. RepairOS already had a richer status engine — groups,
automation triggers, SLA time-limits, asset-move, requirement gates, per-status view-ACL and
a transition/set-ACL — but it was keyed to a single `is_estimate` boolean, so it only drove
2 pipelines. This generalises the discriminator to `record_type` and reuses the whole engine
across all five, matching Orderry's breadth while keeping our depth.

- **`OrderStatus.record_type`** (work_order | estimate | inquiry | invoice | transfer) replaces
  `is_estimate` as the pipeline discriminator; `is_estimate` is kept as a back-compat mirror
  (True iff record_type == estimate). Migration `b6d8f0h2j4l6` adds the column and backfills.
- **Three new default pipelines** seeded lazily to mirror today's hard-coded entity statuses:
  Inquiries (New/In progress/Won/Lost), Invoices (Draft/Issued/Paid/Canceled), Transfers
  (Draft/Ready/In transit/Received/Completed/Canceled). Keys are namespaced (`inq_`/`inv_`/`tr_`)
  so they can't collide with a work-order status key.
- **Settings → Statuses** gains a record-type switcher (Work orders / Estimates / Inquiries /
  Invoices / Transfers); each pipeline is editable in the same rich per-status editor (groups,
  triggers, SLA, gates). SLA time-limits now render as a badge in the list. Transition matrix
  stays scoped to Work orders + Estimates.
- **`GET /api/statuses`** accepts `?record_type=…` (legacy `?is_estimate=…` still maps through);
  create/update scope default-uniqueness per record_type. Every order-scoped status lookup that
  keyed off the `is_estimate` boolean (create/complete/default/timing/reports/bookings/leads/
  chats/cycle-time) was moved to `record_type` to stay unambiguous.
- Already present, so **not** rebuilt (Orderry "gaps" that weren't): per-status **SLA time
  limits** (`time_limit_min`) and the **"can set status" role ACL** (the `StatusTransition`
  graph). Deep entity enforcement (routing `Lead`/`Invoice`/`StockTransfer` reads through the
  configurable statuses) is deferred — post-launch, not launch-blocking.

## build 144ii — Two-tab Public pages + status-dropdown accept/decline transitions

Reworks the public-pages settings to match Orderry exactly and go beyond it: **two tabs**
(Online booking / Order · Estimate page) and real **status dropdowns** instead of free text.

- **Two tabs** in Settings → Public pages: "Online booking" (link, rules, appearance) and
  "Order / Estimate page" (header/products/action toggles + the two approval blocks).
- **Estimates** and **Work orders** each get an Orderry-exact block: allow accept/decline,
  **require client signature**, **"Show accept/decline in these statuses"** (multi-select chips
  from the org's real estimate/work-order statuses), **"If accepted, move to"** and
  **"If declined, move to"** single-status dropdowns (grouped "Group › Status" labels).
- On sign, the order now **moves to the configured status** (accept or decline), logged to the
  status history; if the accept target `converts_to_order`, an estimate still converts. No
  config = prior default behaviour. Require-signature is enforced: accepting without a drawn
  signature returns 422.
- Status options load from `GET /api/statuses?is_estimate=…`; the fields round-trip through
  `/api/staff/public-pages`.
- Schema: 6 columns on `organisations` (require-signature ×2, accept/decline target ×4) —
  migration `144ii`, head `a5c7e9g1i3k5`; needs `make migrate`.

## build 144ih — Order/Estimate settings tab restored + accept/decline gating

Restores the public Order/Estimate settings the React port dropped (a regression from the
legacy build) and adds Orderry's per-status accept/decline gating — including for work orders.

- **Settings → Public pages** now has an "Order / Estimate page" section (Settings hub):
  header field toggles (logo / name / address / email / description), products-&-services
  display toggles (separate tables, show zero-price products/services), customer-action toggles
  (messages, recommend, staff reply, scheduled date), a **background-pattern** picker, and two
  accept/decline blocks — Estimates and Work orders — each with an allow toggle and a
  comma-separated status allow-list (blank = any). The `oe_*` fields already existed in the DB
  but had no UI after the React rewrite; they're wired now.
- **Gating enforced** in `/track/{token}/respond` and `/portal/.../respond` via `_can_sign`:
  estimates use `oe_allow_approval`; **work orders are opt-in** (`oe_wo_allow_approval`), each
  scoped by its status list. The track page's `awaiting_approval` now reflects this for both
  doc types, so the sign UI appears exactly where the shop allows it.
- Schema: 3 columns on `organisations` (`oe_wo_allow_approval`, `oe_approval_statuses`,
  `oe_wo_approval_statuses`) — migration `144ih`, head `z4b6d8f0h2j4`; needs `make migrate`.

## build 144ig — Real e-signatures + proof, beyond Orderry's scribble

Three customer-facing upgrades that go past Orderry's public order/estimate pages rather
than just matching them. English-only for now (locale/translation deferred).

**Real e-signature on estimates AND work orders (not just estimates).** Client accept/decline
now records a tamper-evident `DocumentSignature`: typed name, drawn image, timestamp, signer
IP + user agent, and a SHA-256 hash of the exact document snapshot (number, total, status) the
customer approved. Orderry captures only a bare drawn scribble with no audit trail.
- Both `/track/{token}/respond` and `/portal/.../respond` capture it, for estimates and work orders.
- **Signed-approval certificate PDF** (in-house reportlab): downloadable by the customer at
  `GET /api/public/track/{token}/certificate.pdf` and by staff at
  `GET /api/orders/{id}/certificate.pdf` (+ `GET /api/orders/{id}/signatures` list). Holds up for
  insurance / fleet / B2B disputes where a scribble doesn't.
- Track page shows a "Download signed approval (PDF)" card once signed.

**Proof bound to the money.** Recommendations already carry a photo (`media_data`) rendered right
next to the approve control on the track page — proof and price in the same card, which Orderry
can't do (its photos live in a separate Files tab). Confirmed working; no change needed.

**Downloadable staff files on the public page.** Staff-posted files (not just photos) now render a
download link on the track/portal page; the public payload carries file media + name + mime.
Per-item public/private was already honored via the feed `visibility` flag.

Schema: `document_signatures` table (migration `144ig`, head `y3a5c7e9g1i3`) — needs `make migrate`.
Customer-facing changes are backend + `track.html` (served directly, no React rebuild).

## build 144if — E-invoicing (in-house UBL 2.1) + customer-facing e-invoice

Closes the last real Orderry gap: standards-based electronic invoicing, built entirely in-house
(no third-party service, no external API), and wired into the public track page so the customer
who accepts an estimate can also pull their compliant invoice.

- **UBL 2.1 generator** (`services/einvoice.py`): deterministic OASIS/EN-16931 Invoice XML from the
  invoice + legal entity + client + resolved display lines — the format underlying most national
  e-invoicing mandates. No dependency added.
- **Issue / status / download** (Finance → Invoices): `POST /invoices/{id}/einvoice` assigns a
  sequential fiscal number (EINV-00001…), stores the XML, marks it *issued*; `PATCH …/einvoice`
  records the clearance outcome (*cleared / rejected / cancelled*) manually, since live authority
  submission needs each country's credentials; `GET …/einvoice.xml` downloads it. New E-invoice
  column with Issue / Download / mark-cleared actions.
- **Customer-facing**: the public `/track/{token}` page now shows the invoice (total, balance) and,
  once issued, a token-gated **Download e-invoice (XML)** button — served by
  `GET /public/track/{token}/einvoice.xml`, no login required.
- Invoice serializer carries `einvoice_status / number / format / issued_at`.
- Schema: 5 columns on `invoices` (migration `144if`, head `x2z4b6d8f0h2`) — needs `make migrate`.

## build 144ie — Print anything: clients + invoices (bulk, exact size)

Extends the bulk exact-size print pipeline to the last two record types, so **any** record
can be printed from its own list — the full realisation of "make anything with variables and
print on any printer."

- **Contacts → select clients → "Print"**: contact cards, loyalty tags, mailing labels.
  `POST /api/documents/render-client-labels` renders a template per client×copies via a new
  `_resolve_client` variable map ({Client Name/Phone/Email/Address/Tax ID/…}, barcode/QR).
- **Finance → Invoices → select rows → "Print N selected"**: batch-print full invoices, one
  invoice per physical page in a single job. `POST /api/documents/render-invoice-batch` resolves
  each invoice through the existing invoice variable map (line rows and all).
- New shared `BulkPrintDialog` component (template picker + optional copies + hidden-iframe print)
  reused by clients and invoices.
- Frontend + endpoints only; no schema, no migration.

## build 144id — Smart-doc: conditional blocks + reusable components

Templates that adapt themselves — genuinely beyond Orderry's static documents. Fully in-house
and deterministic; no template engine or third-party dependency.

- **Conditional blocks**: `{#if Warranty Expiry}…{#else}…{/if}` and `{#unless …}…{/unless}` in
  any template body. A condition is true when its variable resolved to real content (not blank,
  the "—" placeholder, a zero amount, or a falsey word), so one template hides/shows sections
  instead of needing five. Innermost-first resolution supports nesting; runs before row-repeat
  so a false branch drops its tables cleanly.
- **Reusable components**: mark a template `doc_type: "component"` and embed it anywhere with
  `{{component: Name}}`. Branding (header/footer) is authored once and reused; nested components
  resolve up to 5 levels; components are excluded from the order print dropdown.
- Both resolve in every render path (order render, editor preview, exact-size print-view).
- Backend only; no schema, no migration.

## build 144ic — Print anything: asset labels (bulk, exact size)

Extends the print pipeline beyond products to **assets** — Orderry's "document assigned to
Assets/Vessels" flow, done our way (with true-size output they can't match).

- **Assets tab → select rows → "Print labels"**: pick an asset-label template + copies and print.
  Uses the shared exact-size, one-label-per-page pipeline.
- New endpoint `POST /api/documents/render-asset-labels` renders a template against selected
  assets via a new `_resolve_asset` variable map ({Asset Brand/Model/Serial/IMEI/VIN/Owner/…},
  barcode/QR from the asset's serial/IMEI/VIN/HIN).
- The label dialog + hidden-iframe print are now shared by products and assets.
- Frontend + endpoint only; no schema, no migration.

## build 144ib — Bulk label printing at exact size (50 tags in one click)

Builds on the exact-size pipeline: print a whole batch of price tags/labels straight from the
Products list — the volume workflow Orderry has no answer for.

- **Products → select rows → "Print labels"**: pick a label template + copies-per-product and
  print. A dialog shows the total ("Print 50 labels").
- **Exact size, one label per page**: the bulk endpoint now emits an `@page { size: WmmxHmm }`
  document with one label per physical page, so a label/thermal printer feeds each tag correctly
  (no A4 tiling or scaling). Honours the template's custom mm size + margin.
- Reuses the existing `/api/documents/render-labels`; prints via a hidden iframe (no popup).
- Frontend + endpoint only; no schema, no migration.

## build 144ia — Exact-size print pipeline (beats Orderry's browser print)

The differentiator: make a document with variables and print it true-to-size on ANY printer.
Orderry hands its HTML to the browser print dialog, which defaults to A4 and scales — so labels
and thermal/receipt output come out wrong. RepairOS now controls the page geometry itself.

- **Paper/label presets**: A4, Letter, Receipt 80mm, Receipt 58mm, and **custom label W×H mm**,
  plus an optional margin. Stored on the template (`page_w_mm`/`page_h_mm`/`margin_mm`).
- **Paper-true canvas**: the editor page renders at the real physical size (1mm = 3.78px), so a
  40×30mm label looks 40×30mm while you design it — WYSIWYG at true scale.
- **Real Print button**: `POST /api/documents/print-view` returns a standalone print document
  wrapped in `@page { size: <W>mm <H>mm; margin }`, resolved against a real sample record. The
  browser then prints at the exact physical size on whatever printer is selected (laser, inkjet,
  thermal receipt, label). Prints via a hidden iframe — no popup.
- Migration `w1y3a5c7e9g1` adds the three size columns (single head). No third-party PDF engine
  added — we use the platform's own `@page` sizing.
- Also: reversed the copied green "+ New" button to a neutral outline (Save is the only accent).

## build 144hz — Orderry-style template flow: switcher + New + Actions menu

Makes managing templates as easy as Orderry — everything happens in the editor, no settings
list to bounce through, and the separate rename/delete icons are gone.

- **Template switcher** — a dropdown in the top bar loads any saved template in place (name,
  layout, body) without leaving the editor.
- **+ New** — starts a blank template instantly.
- **Actions menu (⋮)** replaces the pencil/trash: Duplicate, Set as default, Delete — clear
  labels, one target. Rename is just editing the title field + Save.
- The editor is now self-contained (its own create/update/duplicate/default/delete via the
  documents API) and refreshes the Settings list on change. Inline "Saved / Duplicated" flash.
- Frontend-only; no schema, no migration.

## build 144hy — Strikethrough + HTML code view in the document editor (Orderry parity)

- **Strikethrough** button added to the formatting toolbar.
- **Code view (`</>`)** — toggles the page into a raw-HTML source editor (dark, monospace,
  full height) so you can paste or hand-edit HTML directly, exactly like Orderry's code view.
  Toggling back applies the source and re-renders `{tokens}` as pills; Save also applies any
  open source edits first so nothing is lost.
- Frontend-only; no schema, no migration.

## build 144hx — Fix the "circles": register the editor's toolbar + group icons

Root cause: the icon component maps names to a Lucide set and falls back to a plain circle for
any unknown name. The document editor used Material-Symbols names (format_bold, storefront,
devices, …) that weren't registered, so the toolbar and group headers rendered as circles.

- Registered every icon the editor uses (bold, italic, underline, headings, lists, align,
  divider, table, text/highlight colour, plus the variable-group category icons and block icons).
- Toolbar now reads like a normal editor toolbar — clean icons, no circles — matching Orderry.
- Frontend-only; no schema, no migration.

## build 144hw — Category icons on variable groups (easy to identify)

Each variable group now carries a small identifying icon so the panel is scannable at a glance,
beyond Orderry's plain text headers.

- Company (storefront), Legal entity (gavel), Client (person), Payer (wallet), Ticket (receipt),
  Device (devices), Tables (rows), Employees (badge), Finance (payments), Invoice (document),
  Date (calendar), Links (link), Custom fields (tune), Other (more).
- Pairs with 144hv's clean named list (one variable per row, no chip "circles").
- Frontend-only; no schema, no migration.

## build 144hv — Variables panel as a clean named list (Orderry-style)

Replaces the wrapped chip "bubbles" in the Variables panel with a plain, scannable list —
matching Orderry's readability.

- One variable per row, clean human name (no braces), blue = clickable, hover-highlight with a
  subtle "insert" (+) affordance. No bordered chips, no wrapping cloud.
- Search box and collapsible groups unchanged; click still inserts the `{Token}` pill at the cursor.
- Frontend-only; no schema, no migration.

## build 144hu — Clean default editor (Orderry-style), power features tucked away

Simplifies the document editor's default view to match Orderry's clean layout, without losing
any of the extras we built.

- **Docked Variables panel by default** — the grouped, click-to-insert variable list sits open
  on the right (like Orderry), instead of a hidden slide-over.
- **Blocks panel is now a toggle** (top-bar "Blocks" button) — off by default, so the default
  screen is just toolbar + page + variables.
- **Smart builder is a toggle** (toolbar "Smart builder" button) — the preset/describe strip is
  hidden until you ask for it, keeping the toolbar clean.
- Everything from 144hq–144ht (full-screen page, Edit⇄Preview, rich text, blocks, in-house
  Smart builder) stays — just one click away rather than always on screen.
- Frontend-only; no schema, no migration.

## build 144ht — Smart builder: RepairOS's own document intelligence (no third party)

Reworks the document generator to be **fully in-house** — no external AI service, no API key,
nothing leaves the server. Deterministic and offline by design.

- **Preset chips.** One-click ready layouts in the editor: Standard estimate, Detailed invoice,
  Simple receipt, Warranty slip, Full work order. Click one → the blocks assemble on the canvas.
- **Describe-your-own.** The same in-house engine reads a plain-English description and maps it to
  the block library by keyword (parts→items table, deposit→deposit line, signature→signature, etc.).
- **No external calls.** The generator no longer touches any AI provider; presets are defined in the
  app and can be extended. `provider` is always `builtin`.
- Endpoints: `GET /api/documents/smart-presets` (list) and `POST /api/documents/ai-generate`
  `{prompt?, doc_type, preset?}` → `{body_html, provider:"builtin"}`.
- No schema change, no migration. Backend restart to load the endpoints; editor ships in the bundle.

## build 144hs — AI: describe → document template (superseded by 144ht — now fully in-house)

The headline differentiator Orderry has no answer for: type what you want and the editor
builds it.

- **Prompt bar in the editor.** In edit mode, describe the document — e.g. "branded estimate
  with logo, parts table, deposit line and a signature box" — hit **Generate**, and the blocks
  assemble onto the canvas (each a real, reorderable section with the right `{variables}` wired).
- **Works with no API key.** Reuses the existing `services/ai.py` seam: when `ANTHROPIC_API_KEY`
  is set the model composes the template; otherwise a deterministic stub keyword-maps the prompt
  to the block library (parts→items table, deposit→deposit line, signature→signature block, etc.)
  so it's genuinely useful out of the box and gets smarter with a key.
- New endpoint `POST /api/documents/ai-generate {prompt, doc_type}` → `{body_html, provider}`;
  live output is stripped of `<script>` before it reaches the canvas.
- No schema change, no migration. Backend restart to load the endpoint; editor ships in the bundle.

## build 144hr — Rich text controls + left Blocks panel in the document editor

Two upgrades that push the editor past Orderry's (Orderry has no block/section concept at all).

- **Rich text like a word processor.** The toolbar now has a **font family** dropdown, a
  **font size** picker (10–48px), a **text colour** swatch, and a **highlight colour** swatch —
  plus the existing bold/italic/underline, headings, lists, alignment and divider. Selection is
  preserved when you reach for a control, so formatting lands on exactly what you highlighted.
- **Left Blocks panel.** Build a document from labelled sections instead of a blank page:
  Header, Client, Device/asset, Line items, Totals, Work summary, Signature, Terms, or a blank
  block. **Add** from a menu, **drag to reorder**, click to **jump** to a section, and remove it.
  In edit mode each block shows a faint dashed outline so the structure is visible; blocks
  persist in the saved template (they're plain `<section>` wrappers the renderer ignores).
- No schema change, no migration. Ships in the rebuilt frontend bundle.

## build 144hq — Full-screen document editor (Edit⇄Preview + variables slide-over)

Reworks the visual editor from a cramped drawer into a full-screen workspace, so the
document gets real room. Beats Orderry's modal editor on space and clarity.

- **Full-screen, real page.** The editor fills the screen; the template sits on a grey
  "desk" as an actual A4/Letter-width page (auto-sizes to the chosen page size).
- **Edit⇄Preview toggle** on one surface — flip the same page between editable pills and a
  live, data-filled render (via `/api/documents/preview`) instead of splitting into two
  tiny panes.
- **Variables slide-over.** The grouped, searchable variable list (custom fields included)
  opens from the right only when you want it, then gets out of the way — no permanent column
  eating canvas width.
- **Document settings tucked into a popover** (type, page size, font, default) so the top bar
  stays clean; formatting toolbar + Logo / Items table / Starter layout remain one click away.
- No schema change, no migration. Backend restart for the preview endpoint (already shipped in
  144hp); editor ships in the rebuilt bundle.

## build 144hp — Visual (WYSIWYG) document-template editor

Replaces the raw-HTML textarea with a type-like-a-document editor, so building an estimate,
invoice or receipt needs no HTML knowledge — easier than Orderry's editor, not just at parity.

- **Type like a document.** A formatting toolbar (bold/italic/underline, H1/H2, bullets,
  align, divider) over a live canvas that shows the real page — your font, headings, tables.
- **Variables as inline pills.** Click a variable in the right rail (grouped + searchable,
  including your custom fields) and it drops in at the cursor as a coloured pill you can see
  and delete — no `{Curly}` typing. Pills serialize back to `{Tokens}` on save, so the
  existing render engine is unchanged.
- **Live preview beside the canvas.** A second pane renders your template against a *real*
  sample order (or newest invoice) as you type, via the new `POST /api/documents/preview`
  endpoint — you see actual client names and totals before you ever print.
- **One-click Items table + Logo + Starter layout.** Insert a ready-made line-items table
  (with the row-repeat tokens wired), drop the company logo, or load a clean starter
  estimate/invoice to begin from instead of a blank page.
- No schema change, no migration. Backend restart for the preview endpoint; the editor ships
  in the rebuilt frontend bundle.

## build 144ho — Document variables aligned to Orderry's exact catalog

Makes a template authored against Orderry's variable names print correctly in RepairOS
(no leftover `{…}` tokens), without breaking any existing template.

- **Orderry-exact names as aliases.** Every RepairOS variable keeps its current name; the
  Orderry spelling now resolves to the *same* value: `{Order sum}`=`{Order Total}`,
  `{Balance Due}`=`{Order Balance Due}`, `{Order Malfunction}`=`{Order Complaint}`,
  `{Order Comment}`=`{Order Manager Notes}`, `{Order Address}`=`{Client Address}`,
  `{Urgent}`=`{Order Urgent}`, `{Client Tax Identification Number}`=`{Client Tax ID}`,
  `{Client Business Registration Number}`=`{Client Registration No}`,
  `{Legal Entity Registration Number/TIN/Phone Number/IBAN/Additional Info}`,
  `{Public Page URL}`/`{Order URL}`=tracking URL, `{Estimated Price (Spelled Out)}`,
  `{Paid Spelled Out}`.
- **New data-backed variables.** `{Company Details}`, `{Company Note}`, `{Order Duration}`,
  `{Order Type}`, `{Order Scheduled For/To}`, `{Order Closed Date}`, `{Today Datetime}`,
  `{Order Due Date Time}`, full **Payer** group (First/Last name, Tax ID, Business Reg,
  Billing Address), `{Total Paid Amount}`, `{Total Amount Without Taxes}`,
  `{Total Discount Pct}`, and QR-code variants of the link vars
  (`{Public Page QR Code}`, `{Client Feedback QR Code}`, `{Order Payment QR}`).
  Legal-entity structured-address parity names (`Country/State/City/Address Line 1-2/
  Postal Code/Bank Address/SWIFT/Additional Payment Info`) are exposed for name parity and
  render blank until those fields are captured.
- **Custom fields become variables.** An org's own order/client/asset custom fields now
  appear in the editor's variable picker as `{Order/Client/Asset <label>}` (a new
  **Custom fields** group) and resolve from each record's saved values. Built-ins always
  win a name clash (`setdefault`).
- **Variable picker in the template editor.** The document-template editor now shows a
  grouped, searchable, click-to-insert variable panel (Settings → Document templates →
  edit a template). It reads the live catalog from `/api/documents/templates`, so every
  name above — including the org's own custom fields — is browsable and inserts at the
  cursor. Chips show the label; clicking drops the full `{Token}` into the body at the caret.
- No schema change, no migration. Backend restart picks up the new variables; the editor
  panel ships in the frontend bundle (rebuilt).

## build 144hn — Invoices go through the customizable template engine

Closes the biggest remaining Orderry document gap: invoices can now be **designed** in the
same template editor as work orders, instead of only printing through a fixed PDF layout.

- New `Invoice` variable group: `{Invoice Number}`, `{Invoice Date}`, `{Invoice Due Date}`,
  `{Invoice Status}`, `{Invoice Comment}`, `{Invoice Subtotal/Tax/Total}`, `{Invoice Paid}`,
  `{Invoice Balance Due}`, `{Invoice Table}`, `{Invoice Payment URL}` — plus the shared
  company / logo / legal-entity / client variables so a full branded invoice works.
- New renderer `GET /api/documents/render-invoice/{invoice_id}/{template_id}` resolves an
  invoice (respecting its detailed / simplified / one-line display mode via the shared
  invoicing service) and runs it through the same row-repeat engine — so `<tbody>` with
  `{Product Name}` / `{Product Amount}` expands per invoice line, exactly like work orders.
- No migration; no schema change. 13 document/invoice tests pass. The existing fixed-PDF
  invoice printer stays as the default; a template is used when one is rendered via the new
  endpoint. **FE follow-on:** offer custom invoice templates in the invoice print menu.

Still deferred, deliberately: **Sale** and **Order-list** (`{Orders Table}`) template types,
and the `{Approve}`/`{Pay Now}` measurement instrumentation — the latter is premature until
documents actually render as live pages (nothing to measure yet), so it's held rather than
built speculatively.

## build 144hm — UI to fill the new billing fields

Follow-on so the 144hk/144hl variables can actually be populated by users (they were
API-settable but had no input boxes).

- **Legal entities (Settings ▸ Finance):** the add-entity form now has VAT no.,
  registration no., bank name, bank account, phone, email, and director inputs alongside
  name/tax-id/address.
- **Client drawer:** a "Billing details (for invoices)" block with Tax ID, Registration
  no., and a separate billing address — saved on the existing client PATCH.
- `tsc` clean; frontend built + deployed. No migration (columns shipped in 144hk/144hl).
  Hard-refresh.

## build 144hl — Document variable parity sweep vs Orderry (131 → 146)

Audited RepairOS's template variable catalog against Orderry's full advertised set and
filled every gap that maps to real data:

- **Client:** `{Client First Name}`, `{Client Last Name}` (split from name), plus B2B billing —
  `{Client Tax ID}`, `{Client Registration No}`, `{Client Billing Address}` (new client columns
  tax_id / reg_no / billing_address, settable via the client API).
- **Ticket:** `{Order Scheduled Date}`, `{Order Urgent}`, `{Ad Campaign}` (from the client's
  lead source), `{Location}`, `{Location Address}`.
- **Finance:** `{Total Amount Spelled Out}`, `{Order Paid Spelled Out}`, `{Balance Due Spelled Out}`,
  `{Order Payment URL}`.
- **Product row:** `{Product Barcode}`.
- Migration v0x2z4b6d8f0 (3 client columns). 16 document/client tests pass; single head; no FE
  build (the editor's variable picker is API-driven). **Run `make migrate` and restart.**

Remaining honest gaps (structural, not just a missing variable): Orderry's **Invoice / Sale /
Order-list** documents have their own table-block variables (`{Invoice Table}`, `{Orders Table}`,
`{Sale Number}` …). RepairOS renders those doc types through dedicated printers in `printing.py`
rather than the work-order template engine, so those blocks are a separate rendering track — a
follow-on, not a one-line variable add.

## build 144hk — Documents become live: branding, action-CTAs, work summary, richer legal entity

The document-template engine already had the big variable catalog, row-repeat tables, and
real PDF. This turns the printed doc into a channel and fills the branding gaps — all new
variables the owner drops into their existing customizable template (the editor's variable
picker reads the catalog from the API, so they appear automatically).

- **Branding.** `{Company Logo}` (renders the org's logo image) and `{Brand Color}` (the
  org accent) — the single most-wanted branding element, previously missing.
- **Interactive action-variables (print-safe).** `{Approve Button}`, `{Pay Now Button}`,
  `{Sign Here}`, `{Verify Badge}`. On the live/public page these are the real approve /
  pay / sign actions (which already work there); on paper they degrade to a bordered CTA
  with the link **and a scannable QR**, so a printed estimate still routes the customer to
  approve or pay online. `{Verify Badge}` is a QR to the canonical server copy — anti-forgery.
- **`{Work Summary}`.** A plain-English "Reported / Work performed / Parts used / Outcome"
  composed deterministically from the order's own complaint, service lines, parts, and
  conclusion — the prose techs hate writing, with nothing invented (no LLM, so no hallucination).
- **Richer legal entity.** LegalEntity gains registration no, VAT, bank name/account, phone,
  email, director, exposed as `{Legal Entity …}` variables (was 4, now 11). Migration
  u9w1y3a5c7e9; wired through the legal-entity create/update API.
- No FE build (the template editor's variable picker is API-driven). `tsc` n/a; 14 document
  tests pass; single migration head. **Run `make migrate` and restart.**

## build 144hj — Cascading directory fields, inline-add, safe delete

Builds on 144hi's directory binding.

- **Cascading directory fields (Group → Brand → Model → Modification, any depth).** A
  directory-bound field can name a parent field (`CustomField.parent_field_key`); its
  options then filter to the children (by `parent_id`) of the parent's selected value. A
  plain directory field shows root items. Because a directory hierarchy has no depth
  limit, chaining fields gives a 4-level (or deeper) cascade on any form — this is how
  RepairOS delivers Orderry's Group/Brand/Model/**Modification** asset cascade without a
  bespoke 4th table. The forms editor has an "under <field>" cascade picker; the renderer
  chains them live.
- **Inline add-to-directory from the form.** A directory-bound select shows "+ Add …";
  any staff member can add a missing value on the spot (`POST /directories/by-key/{key}/items`),
  child-linked to the parent selection when cascading — matching Orderry's design where
  adding a value bypasses the directory-edit permission. Dupes return the existing item.
- **Usage count + safe delete.** Item lists now carry `child_count`; deleting an element
  with children returns 409 and the UI confirms before force-deleting (which unparents
  the children rather than orphaning them silently).
- Migration t8v0x2z4b6d8 (one nullable column). `tsc` clean; 6 directory tests pass;
  single head; built + deployed. **Run `make migrate` and restart**, then hard-refresh.

## build 144hi — Directories earn their place: field binding, per-location, CSV, merge

Closed all four gaps vs Orderry's Settings ▸ Directories, plus a real item editor.

- **Directory → form-field binding.** A custom select/multiselect field can now pull its
  options from a directory (`CustomField.directory_key`). The forms editor offers a "from
  directory" picker; the shared field renderer fetches the directory's items at runtime.
  This is what makes directories drive dropdowns instead of being lists nothing reads.
- **Per-location item visibility.** `DirectoryItem.location_id` — blank = every location,
  set = only that branch. `?location_id=` filters both the item list and the runtime
  by-key resolver (global items always included).
- **CSV import / export.** `GET /directories/{id}/export` returns an Excel-openable CSV
  (value, parent); `POST /directories/{id}/import` bulk-adds from pasted CSV, resolving a
  second "parent" column by value. Import skips existing values.
- **Merge elements.** `POST /directories/items/{id}/merge` re-parents the source's
  children onto the kept element and retires the duplicate.
- **Item editor UI.** Clicking a directory in Settings ▸ Directories opens an editor to
  add elements (with parent + location), delete, merge duplicates, and import/export CSV.
- Migration s7u9w1y3a5c7 (two nullable columns). `tsc` clean; 4 tests pass; single head;
  frontend built + deployed. **Run `make migrate` and restart**, then hard-refresh.

## build 144hh — Weekly reorder digest to the notification feed

`POST /api/inventory/reorder-digest` computes the velocity forecast and pushes a single
roll-up notification ("N products to reorder (~$X) · soonest: A 5d, B 6d…") to every
inventory manager's in-app bell feed (kind `inventory.reorder`, deep-links to Inventory).
No-op when nothing is forecast to run out; anyone can mute the kind in notification prefs.
Meant to be called on a weekly schedule, exactly like `/api/automations/sweep` is called
hourly — the app exposes the action, the host's scheduler picks the cadence. Recipients are
owners plus any role not explicitly denied inventory management. No schema change / migration
(reuses the staff-notification feed + push). 3 reorder tests pass (forecast→PO, no-supplier
skip, digest→feed).

## build 144hg — One-click POs from the reorder forecast

Turns the velocity forecast from a list into an action. The Inventory ▸ Stock ▸ "Reorder
list" drawer now has two tabs: the old min-stock list, and a new **Velocity forecast** that
shows each moving part's free stock, days-left, suggested order qty and default supplier.
Check the ones to buy and hit **Create draft POs** — the backend groups them by supplier
and raises one draft PO per supplier (`POST /inventory/reorder-forecast/create-pos`), then
the drawer jumps to the Purchase orders tab. Parts with no default supplier are listed but
skipped (and reported back). POs are drafts, so nothing is sent to a supplier automatically.
No migration. `tsc` clean; 2 new tests (forecast→draft-PO, no-supplier skip) pass; deployed.

## build 144hf — Inventory intelligence: valuation, dead stock, velocity reorder, cross-location stock

Four inventory upgrades that push past Orderry — three new reports (no schema change,
so no migration) plus a point-of-need stock hint.

- **Stock valuation (by warehouse).** New Inventory report rolling up on-hand value at
  moving-average cost, subtotalled per warehouse with a grand-total row. The number an
  accountant asks for — Orderry makes you total the line-item list yourself.
- **Dead & slow stock.** On-hand parts with no sale/use in 90+ days (or never sold),
  ranked by tied-up cash. The inverse of the reorder list — money you could free up.
- **Reorder forecast (velocity).** From 28-day use, projects each moving part's days-of-
  cover and stockout date, then suggests an order quantity covering the supplier's lead
  time plus a 14-day buffer, netting off what's already on PO. Reacts to how fast things
  actually move, unlike the static min-stock purchase list.
- **Cross-location stock on the work order.** Picking a product on the WO "Services &
  products" tab now shows where it's in stock across warehouses ("In stock: 5 · Downtown 3
  · Warehouse B 2"), or flags "out of stock everywhere — order or transfer it." Answers
  "we're out here, is it at another branch?" without leaving the job.
- No migration (reports read existing data). `tsc` clean; 4 new report tests pass;
  frontend built + deployed. Hard-refresh; no server migrate needed.

## build 144hd/he — Discount-approval gate · quantity break tiers

Two pricing controls Orderry doesn't have.

- **144hd — discount-approval gate.** A new org setting (Settings ▸ Pricing →
  "Discount needing manager approval (over %)") blocks any line whose discount exceeds
  the threshold — for roles whose "approve discount" permission is turned off. Owners
  and roles without the restriction always pass; default-allow, so 0 = off and existing
  roles are unaffected. Enforced on all three line paths (add / edit / create-order).
  Migration q5s7u9w1y3a5. Tested: round-trip + a restricted role blocked while a manager
  exceeds freely.
- **144he — quantity break tiers.** Price types gain optional volume tiers (Settings ▸
  Pricing → "Quantity break tiers": at qty ≥ N, use markup %). When a line's quantity
  reaches a tier, the shared price resolver uses that tier's markup instead of the base —
  bulk lines auto-price cheaper. Highest matching tier wins. New `price_type_tiers` table
  (migration r6t8v0x2z4b6) + `GET/POST/DELETE /pricing/types/{id}/tiers`; `resolve_price_for`
  now takes qty and covers default prices and client discount-by-price-type. Tested: CRUD
  round-trip + tier markup applies at the qty threshold.
- `tsc` clean; pricing/margin/approval/tier tests pass; single migration head. **Run
  `make migrate` and restart the server**, then hard-refresh.

## build 144ha/hb/hc — Guard loophole closed · scheduled pricing · customer-facing noun

Three things in one pass.

- **144ha — margin-guard loophole closed.** The guardrail only ran on ADD; editing a
  line's price/discount or creating an order with lines both bypassed it (cheap-add,
  then discount in the edit). The check now runs on the edit-line PATCH and the
  create-order path too — all three block a below-floor line (409), and edit surfaces a
  warn note. Tested: add / edit / create all blocked.
- **144hb — scheduled / promotional pricing.** Price types gain optional
  `starts_at` / `ends_at` (Settings ▸ Pricing → "Promo starts / ends"). Outside the
  window a price type doesn't apply — promos auto-expire and fall back to standard
  pricing. Orderry has no time dimension at all. Migration p4r6t8v0x2z4; window enforced
  in the shared resolver, so it covers default prices and client discount-by-price-type.
- **144hc — customer-facing asset noun.** The public repair-tracking page said "Device"
  regardless of industry; it now shows the asset's own noun (Vessel / Vehicle / Unit /
  …) in the detail card and the approval prompt, from the track API's new `asset_label`.
- `tsc` clean; 21 pricing/industry tests pass; single migration head. **Run `make
  migrate` and restart the server**, then hard-refresh.

## build 144gz — Add lines from the React work order (+ margin warn/block inline)

The React work-order "Services & products" tab was read-only — adding lines happened
only on the legacy surface. Added an inline "Add service or product" control (catalog
search → pick → qty + discount → Add) that posts to the existing `/orders/{id}/lines`
and refreshes the tab. This is also where the margin guardrail now surfaces: a **block**
returns 409 and shows the reason in the toast; a **warn** adds the line and toasts the
low-margin note. Verified live: searched, picked "Screen repair", added, "Line added".
FE only; `tsc` clean; deployed. Hard-refresh — no migration needed for this build.

## build 144gy — Better than Orderry: margin-floor guardrail + effective-price preview

Two pricing features Orderry doesn't have, both fully wired.

- **Margin-floor guardrail.** Settings ▸ Pricing gains a mode (off / warn / block) and a
  minimum-margin %. Every work-order line is evaluated at add time from the item's real
  cost: in **block** mode a line whose margin falls below the floor is refused (409 with
  a clear message); in **warn** mode it's allowed but the response carries a
  `margin_warning`. Orderry lets staff discount into a loss silently — this doesn't.
  New org fields `min_margin_pct` + `margin_guard` (migration o3q5s7u9w1y3), a reusable
  `evaluate_line_margin` service, and a `POST /api/pricing/margin-check` endpoint.
- **Effective-price preview.** The client discount matrix now shows the real outcome
  live — "a $100-cost item bills this client at $X (Wholesale, 15% markup)" for
  by-price-type, or "a $100 item bills at $90 (−10%)" for fixed %. Orderry makes you
  find out at the line; this shows it while you set it.
- Also surfaced RepairOS's existing edge over Orderry: editing a markup re-prices all
  stock instantly (Orderry needs a delete-and-recreate).
- `tsc` clean; margin-guard + discount-patch tests pass. **Run `make migrate` and
  restart the server**, then hard-refresh.

## build 144gx — Pricing & discounts wired to the UI (Orderry parity)

The pricing engine was already complete server-side (price types with markups,
per-item overrides, per-document default price, and a client discount matrix for
services vs materials as fixed % or by price type, applied at line creation) but the
React app only exposed the price-types table. Wired the rest:

- **Settings ▸ Pricing** gained a "Default price per document" panel — the price type
  auto-applied when a product is added to a Work Order / Sale / Invoice, plus price
  decimals. Backed by GET/PUT /pricing/defaults.
- **Client drawer ▸ General info** gained a "Pricing & discounts" matrix: services and
  materials each set to none / fixed % / by price type (from the price-type list). The
  backend already applies these automatically to that client's orders, invoices and
  sales.
- **Fix (pre-existing):** the client drawer saved via PATCH but the backend only had a
  full-replace PUT, so client edits — including custom-field values — were silently
  405-failing. Added a real partial PATCH /api/clients/{id} that updates only the
  fields sent. New test covers the discount-matrix round-trip.
- `tsc` clean; client tests pass. **Restart the server** for the new PATCH endpoint,
  then hard-refresh.

## build 144gw — Fix: template switch now relabels existing (unstamped) assets too

The 144gv wording change looked broken in Inventory: switching the template left every
existing asset still showing "Device" / "Asset". Cause — all existing assets are
stamped `general` (the never-chosen default), and `general` mapped to the literal
"Asset", so nothing moved. Now `general`/unstamped records follow the org's *current*
template noun (only assets created under a real, specific template keep their own).
Verified live on a Tools org: Inventory tab reads "Tools", button "+ Add Tool", and
every asset row's type shows "Tool". Also relabeled the Inventory Devices tab and its
type column, and the asset drawer badge/title, through the same rule. FE only; `tsc`
clean; deployed. Hard-refresh.

## build 144gv — Industry-driven asset wording (Orderry-style), + Fleet & Truck template

Orderry names the asset by the shop's industry — Vessel, Vehicle, Device, Tool,
Unit — and that noun follows the object everywhere. RepairOS already stored this per
template (`asset_label`), but the React app never read it, so every screen said
"Device" / "Asset" no matter the template. Now it reads it.

- New `useTerminology()` hook fetches the org's current template once and drives the
  app chrome: the Assets hub title, search and "New …" button; the Inventory
  Devices & Assets "+ Add …" button; the client table's asset column; and the
  create-order asset section header and Group dropdown (its options now come from
  the template's groups, so a marine shop sees Boat / Jet Ski / Outboard, not
  Device / Vehicle / Equipment).
- **Existing assets keep the noun they were created under.** Each asset now carries
  its own `asset_label` (derived from its stamped `template_key`) in the API, and the
  asset profile, the work-order asset card, and the printed work order use *that*
  label — so switching the org template never rewrites history.
- **Settings ▸ Industry & assets** gains a template picker: change the industry and
  the whole app re-labels for new records; a note makes the "existing keep their
  wording" behaviour explicit.
- **New "Fleet & Truck Repair" template** — asset noun **Unit**, groups Truck /
  Tractor / Trailer / Reefer / Van / Bus, VIN primary identifier plus a Unit number
  field, DOT number and GVWR, starter brands (Freightliner, Volvo, Peterbilt, …).
  Ten templates total now.
- Seeding only (no migration). `tsc` clean; 40 industry/asset tests pass; built +
  deployed. **Restart the server** to pick up the new template and per-asset label,
  then hard-refresh.

## build 144gu — Fix: applied checklists showed "0 of 0" (items never loaded)

The WO ▸ Checklists tab listed applied checklists but always rendered "0 of 0" with
no fill rows, because the list endpoint (`GET /orders/{id}/checklists`) returns a
summary only (`items_total`/`items_done`), while the card renders from `c.items` —
which was never fetched. `load()` now pulls each checklist's detail
(`GET …/checklists/{cid}`) in parallel and merges its `items` in, so the item rows,
Pass/Fail/Caution controls, and Attach control appear.

- Verified live end to end: built a template with an item, applied it to WO-00009,
  and filled it — progress moved 0 of 1 → 1 of 1 / 100%, item turned green, Attach
  control present. FE only; `tsc` clean; built + deployed. Hard-refresh to pick up.

## build 144gt — Checklist attachments: photos, files, videos (like Orderry)

- Every checklist item on a work order can now carry attachments — an "Attach"
  control (accepts images, videos, PDFs, multi-select) uploads through the existing
  `/api/media` store and appends the blob ids to the item's `file_ids`, saved via the
  item PATCH. Thumbnails render inline (image preview, or a FILE chip for non-images),
  each opening the full media at `/api/media/{id}`, with hover-to-remove.
- Matches Orderry's per-check photo/file capture. FE only (media + checklist item
  endpoints already existed); `tsc` clean; built + deployed. Hard-refresh to pick up.

## build 144gs — Checklists made usable: template builder + fill on the order

Fixes both broken checklist surfaces (they were read-only, so the feature was inert
end to end despite the backend being complete).

- Settings ▸ Checklists: an **Add / edit template builder drawer** — name, applies-to
  (all types or selected), Steps → typed Items with per-item field kind
  (check_result, yes/no, text, number, date, dropdown, multiselect, file), and the
  required / client-visible / fail-fails-checklist flags + dropdown options. Wired to
  the existing `/checklist-templates` POST/PUT (nested payload); rows now open to edit,
  and delete works.
- WO ▸ Checklists tab: an **"Apply a checklist"** picker (templates scoped to the WO
  type) that attaches via `POST /orders/{id}/checklists`, plus **inline fill controls**
  — Pass/Fail/Caution for check_result, Yes/No, and typed inputs — saving through
  `PATCH …/items/{iid}`, with per-checklist progress and remove. Previously the tab
  only listed items with no way to add or complete them.
- FE only (endpoints already existed); `tsc` clean; built + deployed. Hard-refresh to
  pick up — no server restart or migration needed.

## build 144gr — Smart Fields: field-gates-status + estimate form linkage

Completes the Smart Fields spec's last action, and fixes the forms editor to match
Orderry's shared work-order/estimate form.

- Field-gates-status: an order-type custom field can block entering a status until
  its value clears (e.g. "Damage severity = severe" blocks Ready). Config
  `gate_status_key` + `gate_comparator` (is|is_not|lt|gt) + `gate_value`/`gate_threshold`
  on `CustomField`; enforced in `change_status` (409) via `status_gate_block`, using
  the shared trigger vocabulary. Migration `m1o3q5s7u9w1`. Forms editor: a "Block
  status … when value …" row + a "gate" chip.
- Forms editor fix: Work Orders **and** Estimates share one form, as in Orderry —
  the System-fields entry is now labelled "Work orders & estimates" and drives both
  (estimates are the same order document type). No separate estimate form.
- Test: severe value blocks the status (409), minor value passes (1 new); field
  upsell + 144gj status-gate regressions green. `tsc` clean; built + deployed.

This closes the Smart Fields spec: conditional fields, unified rule surface
(upsell + gate), parity types, show-on-print, and auto-fill are all shipped.

## build 144gq — Smart Fields: auto-fill (forms that fill themselves)

Custom fields can pre-fill on a fresh form — the shop stops retyping device specs
every visit. Orderry makes you type it each time.

- `CustomField.autofill_source`: `asset.<attr>` (from the linked device — brand,
  model, serial, imei, vin, hin, asset_type, color) or `last_order.<field_key>`
  (from the client's most recent order). Migration `l0n2p4r6t8v0`.
- `GET /api/forms/autofill?type_id&entity&device_id&client_id` resolves the values
  best-effort (unresolvable sources skipped). Serializer + schemas + create/update wired.
- Shared `CustomFields` renderer: on a fresh form (`creating`) it fetches auto-fill
  and pre-fills only *empty* keys, so it never clobbers what the tech typed. Takes an
  optional `autofillCtx` (device/client) for asset/last-order sources.
- Forms editor: an "Auto-fill → device <attr>" selector on the custom-field builder.
- Test: asset.model pre-fills from the linked device; empty when no device (1 new).
  `tsc` clean; built + deployed.

## build 144gp Phase C — Smart Fields: parity field types + show-on-print

Closes the raw parity gaps vs Orderry now that the differentiators are in.

- Two new custom-field types: **multiselect** (pill toggles, value stored as a list)
  and **link** (URL input). Added to `VALID_FIELD_TYPES` and the shared renderer.
- **show_on_print** flag on custom fields — surface a field's value on printouts /
  the customer portal (mirrors the checklist item flag). Migration `k9m1o3q5s7u9`.
- Wired through the serializer, both field-In schemas, and all create/update paths;
  Forms editors gain the two types, a "Show on printout" checkbox, and an "on print"
  chip. `tsc` clean; conditional/upsell/product/client tests green (9); built + deployed.
- Remaining Smart Fields work: field-gate-status (Phase B deferred) and auto-fill
  (Phase C's other half — pre-fill from asset/model/last order).

## build 144gp Phase B — Smart Fields: fields as one unified rule surface

A custom field value can now auto-draft an upsell — through the *same* engine as
checklist items, not a second bolted-on system. This is the moat: any data point
on a work order can carry a rule, one place, one lifecycle.

- New `backend/services/field_rules.py` holds the single upsell lifecycle
  (`sync_upsell_draft`): idempotent (one draft per source+ref), retracts when the
  trigger clears, never touches a recommendation already sent/approved. The 144go
  checklist upsell was refactored to call it — one code path now, proven by the
  existing checklist tests staying green.
- Order-type `CustomField` gains an upsell rule (`upsell_ref_id/kind/price` +
  `upsell_comparator` is|is_not|lt|gt with `upsell_value`/`upsell_threshold`).
  Migration `j8l0n2p4r6t8`.
- On order create, `sync_order_field_upsells` evaluates each order field's rule
  against the intake `custom_data` and drafts/retracts via the shared surface
  (`source="field"`). A hidden field (its show-rule unmet) never fires. Wrapped so
  it can never block order creation.
- Forms editor (order-type builder): a "Show only when…" row and a "Draft upsell
  when value [op] [n] · price" row, plus conditional/upsell chips on fields.
- Tests: field-below-threshold drafts a priced field-sourced upsell, above-threshold
  drafts nothing (1 new); checklist + conditional + product/client regressions green
  (11). `tsc` clean; built + deployed.
- Deferred (spec P0-2 gate_status, P2): a field value *blocking a status* — needs the
  status-change path to read field values; held for a focused follow-up.

## build 144gp — Smart Fields Phase A: conditional custom fields (beyond Orderry)

A custom field can now show only when another field's value calls for it — IMEI
appears for a phone, hides for a laptop, live as the tech fills the form. Orderry
shows every field always; this is the demoable win from the Smart Fields spec.

- `CustomField` gains `show_when_field_key` / `show_when_op` (is | is_not | gt | lt |
  filled) / `show_when_value`. Migration `i7k9m1o3q5s7`.
- Server-side truth: `_field_visible` + a two-pass `resolve_custom_data` — a hidden
  field is neither required nor stored, so a conditional field can never block a
  submit or leak a stale value. One condition per field (deliberate ceiling).
- Wired through **all forms**: order-type fields and client/device/product entity
  fields carry the config (create + update), and — closing a real gap — **clients now
  run through `resolve_custom_data` too** (previously only products enforced custom-
  field rules; client custom fields were stored raw with no required/lock/visibility).
- Shared `CustomFields` renderer evaluates the rule live, so every form that uses it
  (WO create, client, device, product drawers) hides non-matching fields automatically.
- Forms editor: a "Show only when [field] [op] [value]" row on the custom-field
  builder, plus a "conditional" chip on fields that have a rule.
- Tests: hidden-required doesn't block + hidden value dropped + visible-required still
  enforced (1 new); product/client/form-layout regressions green (15). `tsc` clean;
  built + deployed.

## build 144go — Smart checklist: findings that auto-draft upsells

The headline differentiator Orderry can't match — a checklist that sells for you,
wired into the existing Recommendation → approval → commission pipeline.

- Checklist template items gain optional upsell config: a linked catalog item
  (`upsell_ref_id` / `upsell_kind` / `upsell_price`) plus a trigger
  (`upsell_comparator` = fail | lt | gt with `upsell_threshold`). Snapshotted onto
  the order checklist item so template edits never rewrite a job's history.
- On checklist fill, a Fail (or a numeric value crossing its threshold)
  **auto-drafts a pending recommendation** for the linked item — priced from the
  config, attributed to the finding. Idempotent (one draft per item) and it
  **retracts** the draft if the value is corrected before the customer sees it.
  Never touches a recommendation already sent to / approved by the customer — the
  tech stays in control, nothing is charged or sent automatically.
- Reuses the whole existing flow: an accepted checklist upsell approves,
  invoices, and pays commission exactly like a manual one (no new path).
- WO drawer: drafted recs from a checklist carry a "From checklist" chip so their
  origin is clear. Recommendation API now returns `source` / `source_ref_id`.
- Migration `h6j8l0n2p4r6`: upsell columns on checklist items + source columns on
  recommendations. Tests: fail auto-drafts + idempotent, and retracts on correct
  (2 pass). `tsc` clean; built + deployed.
- Follow-up: the React Settings ▸ Checklists is still a read-only list, so the
  linked-product/threshold config is set via the template API for now — the full
  React checklist template builder (Steps → Sections → typed Items + upsell
  fields) is the remaining piece to expose it in the UI.

## build 144gn — Cycle-time intelligence: predicted ready-time + stall detection

A work-order differentiator Orderry has no equivalent for, built entirely from
the status-transition history already in the DB (no schema change).

- New `backend/services/cycle_time.py`: computes dwell-time stats (avg / p50 /
  p80) per `(status, work-order type)` from `order_status_logs`, with a
  type-agnostic fallback bucket and an explicit confidence level. A status with
  no history yields **no prediction** — predicted ready-time only turns on per
  bucket once real orders accumulate `MIN_N` samples, so nothing fictional ships.
- `GET /api/orders/{id}/timing` — elapsed vs typical for the current status,
  stall flag (elapsed past p80), and predicted ready-time with confidence.
- `GET /api/orders/timing/flags` — batch stall/ready flags for open orders in one
  shared stats pass (no N+1) for the WO list.
- WO drawer: a cycle-time band under the header shows "X in this status vs ~Y
  typical", turns amber/rose + "Running long" when stalled, and shows an
  estimated-ready time (marked "rough" on thin data).
- WO list: a "Stalled" badge next to the status pill on jobs past their typical
  time. Tests: dwell math, stall detection, endpoint shape, batch flags (4 pass).
  `tsc` clean; built + deployed.

## build 144gm — Status editor fixes: colour picker anchor + warehouse dropdown

- Fixed the custom colour picker popping at the top-left of the screen: the
  hidden `<input type="color">` was `display:none`, so the native picker had no
  anchor — switched to `sr-only` so it opens next to the swatch. Selected swatch
  now shows a ring + check with no layout shift.
- "Move asset to" is now a **warehouse dropdown** (asset warehouses from
  `/settings/warehouses`) instead of a free-text box, so you pick a real
  destination; keeps any legacy free-text value as an option. `tsc` clean; deployed.

## build 144gl — Status editor redesign (dedicated drawer, better than Orderry)

- Replaced the generic 13-field status form with a purpose-built
  `StatusEditorDrawer`: live status-pill preview in the header, colour swatch
  presets + custom picker, grouped sections (Identity; When an order enters this
  status — create invoice / notify / prompt payment / lock edits / move asset to;
  Require before entering — comment / full payment / checklist as toggle pills;
  Timing overdue field), and a live plain-English behaviour summary that composes
  from the toggles. Add + edit both route to the new drawer; delete unchanged.
  All fields hit the existing `/statuses` API — no backend change. `tsc` clean;
  deployed.

## build 144gk — Status transitions: chips editor (replaces the matrix)

- Replaced the confusing N×N transition matrix with a per-status "can move to"
  chips row: each status lists its allowed next statuses as removable chips with
  a "+ add" picker; a status with none shows "open — any status". Same
  `/statuses/transitions` data, one direction per row, far easier to read. `tsc`
  clean; deployed. (Backend unchanged.)

## build 144gj — Status requirement gates + transition-graph editor

- **Requirement gates (Orderry):** new `order_statuses.require_comment /
  require_payment / require_checklist` columns (migration `g5i7k9m1o3q5`).
  Entering a gated status is blocked (409) until the order has a comment / is
  fully paid / has every checklist item done. Enforced in
  `PATCH /orders/{id}/status`; exposed as three checkboxes in the status editor;
  a "gated" chip shows on gated statuses in the list. Test: `test_144gj`.
- **Transition-graph editor:** a From→To matrix in Settings ▸ Statuses (backed
  by `/statuses/transitions`) — tick which status each row may move to; a row
  with no ticks stays "open" (any status allowed), matching the existing
  enforcement in `is_transition_allowed`.
- Gates: `tsc` clean, single migration head, status/transition + gate tests
  green; deployed.

## build 144gi — Statuses: surface time-limit, payment prompt, asset auto-move

- The status editor already covered name/group/colour/default/triggers-invoice/
  notify/locks-edits, but the backend also supports three Orderry status fields
  that weren't exposed: **Prompts payment** (triggers_payment), **Time limit
  (minutes)** — Orderry's per-status time limit / overdue badge — and **Auto-move
  asset to** (moves_asset_to). All three added to the status form; already
  accepted and persisted by `/statuses`. `tsc` clean; deployed.

## build 144gh — Locations: legal entity + price type (Orderry parity)

- Orderry binds each location to a legal entity (1:1, which drives the invoice's
  legal entity) and can carry a per-location price level. Both columns already
  existed on `Location` but weren't editable, serialized, or accepted by the API.
- Backend: `LocationIn` + `create_location` + `_ser_location` now handle
  `legal_entity_id` and `price_type_id` (update already persisted via model_dump).
- Location editor drawer gains **Legal entity (invoices)** and **Price type**
  dropdowns, sourced from `/settings/legal-entities` and `/pricing/types`; empty
  = company default. Alongside the existing name/phone/address/timezone/receipt
  header, per-day working hours, and per-location resources. `tsc` clean;
  location tests green (23); deployed.

## build 144gg — Payroll commission events: editable rules (Orderry-style)

- The commission engine (event-triggered payroll rules) was fully built on the
  backend but read-only in the UI. Payroll rules under Team & roles are now
  clickable with an **Add rule** button and a full editor drawer wired to
  `/api/payroll/rules`:
  - **Trigger** — rule name, the event that pays (Order created / closed /
    closed-success / closed-fail, Labour performed, Product sold, Assignee's
    labour or product, Task closed), and who it applies to (an employee or all).
  - **Amount** — percentage or flat; percentage of total or of gross-profit
    margin; plus a coefficient multiplier.
  - **Conditions** — accrue on close / immediately / at a chosen status; only
    for on-time / overdue / urgent orders; rule level (all items, a product, a
    category, a service, or the employee's profile default); and reverse-on-delete
    clawback toggle.
  - List shows event, employee, and the amount summary. `tsc` clean; deployed.
    Backend unchanged (endpoints + engine already existed and tested).

## build 144gf — Payroll rules moved under Team & roles

- Payroll rules was a stray top-level Settings nav item; it now lives as a card
  inside **Settings ▸ Team & roles** (below Employees / Roles / Invites), where
  it belongs since the rules apply to employees and roles. Removed the standalone
  nav entry and its section; data loads when the Team section opens. `tsc` clean;
  deployed.

## build 144ge — Role editor redesign (tabbed + searchable)

- The Edit-role drawer is now tabbed — **Permissions · Data access · Statuses** —
  with the role name pinned above the tabs and a live on/total count on the
  Permissions tab. Adds a **permission search** (matching blocks auto-expand), a
  **Full access / No access** pair to flip everything at once, and a single
  master switch per permission block. Scopes and hidden-statuses moved to their
  own tabs so the main view isn't one long scroll. `tsc` clean; deployed.

## build 144gd — Employee editor: Orderry-style tabs

- The employee drawer is now split into tabs under the header — **General ·
  Access · Payroll · Security** (Security hidden while creating) — instead of one
  long scroll, matching Orderry's employee card. Same fields, same endpoints,
  shared Save footer. `tsc` clean; deployed.

## build 144gc — Team: Orderry-style employees + richer roles

- **Employees** are now managed, not just listed. The Team ▸ Employees table gains
  Position / Phone / Status columns, an **Add employee** button, and rows open an
  **EmployeeEditorDrawer**: profile (name, position, email, phone, public name,
  tax ID, hired date, note), access (role, home location, active/blocked toggle),
  payroll (wage type/value, labour commission, job-timer + attendance-clock
  toggles), and security actions (set password, email reset link, reset 2FA) —
  all on the existing `/staff/employees` + password/reset/mfa endpoints.
- **Role editor** rebuilt to Orderry shape: collapsible permission blocks with
  per-block Allow-all / Deny-all and an on/count badge, plus record-level **data
  access** scopes (contacts, orders), **payroll visibility** window, and a
  **hidden order statuses** matrix (A5 per-status view ACL).
- **Bug fix:** the old role editor saved only name + permissions, silently
  wiping each role's scopes and hidden-status keys on every edit; it now
  round-trips the full role object.
- Design matches the app system (glass/clay cards, `M` icons, slide-in drawers).
  `tsc` clean; deployed to `dist`. Backend unchanged (all endpoints already existed).

## build 144gb — Multi-currency flows end-to-end (client → document → print → pay)

Currency now flows through the whole earning loop, not just Settings:
- **Client billing currency** on the New-client drawer (shown when the org has
  extra currencies) → `Contact.currency_code`; surfaced read-only on the client
  detail. `resolve_for_client` already consumes it.
- **Document currency** on the New WO/estimate drawer: optional per-document
  override with a live converted-total preview (`1 code = rate base`). Open WO
  Invoices tab shows the document currency, a converted outstanding balance, and
  a currency switcher wired to `PATCH /orders/{id}/currency` (re-snapshots rate).
- **Printed / PDF documents** render in the document's display currency — a
  local `_money` shadow converts every amount, `{Currency}` shows the display
  symbol, and new `{Currency Code}`, `{FX Rate}`, `{FX Note}` template variables
  carry the rate line. Public **track** page shows the converted total; the
  **pay** page already renders in the intent currency.
- **Payment dialog** shows the base amount's document-currency equivalent.
- **Rate-as-of** captions on each currency row in Settings; FX note dates the
  snapshot on printed docs.
- Shared `useCurrencies` hook powers all pickers (base + active registry
  currencies). `tsc` clean; currency + multicurrency + document-var + public
  tests green (29 passed); deployed to `dist`.

## build 144ga — Wire real multi-currency (Orderry-style) into the React UI

- The proper multi-currency backend (`/api/finance/currencies` — real `Currency`
  table, append-only `currency_rate_history`, best-effort auto-rate lookup,
  in-use protection) existed but nothing in React used it. Settings ▸ General had
  a fake "Additional currencies" table writing to a dead `currencies_json` blob,
  and the account currency picker used a hardcoded 5-currency list.
- New `CurrencyManager` component (Settings ▸ General) fully wires the endpoint:
  add currency (code/symbol/position/rate) with an **Auto** rate fetch, inline
  edit of symbol/position/rate (rate changes append to history), per-currency
  **rate-history** modal, and delete blocked while an account still uses it.
  Rate convention shown as `1 <code> = rate <base>`, matching Orderry.
- Cash ▸ New/Configure account currency picker now lists the base currency +
  the org's added currencies (from the same endpoint) instead of a static list;
  currency stays locked after creation. Cross-currency transfers already snapshot
  the FX rate on both legs (`transactions.fx_rate`). `tsc` clean, deployed.

## build 144fz — Fix country/currency wiring (ISO-code keyed)

- Root cause: the `organisations.country` column stores an ISO 3166-1 alpha-2 code
  (`String(2)`), but the dropdown was keyed on full country names, so the saved
  value never matched a reload and the country→currency/timezone auto-fill never
  re-fired (currency looked like it "didn't set").
- `COUNTRY_META` is now keyed by ISO code `[iso2, name, timezone, currency_code,
  symbol]`; the country dropdown's option values are ISO codes (labels show the
  name), matching how the value round-trips through `/staff/company`. Picking a
  country now reliably sets timezone + currency (code + symbol), both still
  editable. `tsc` clean, bundle builds, deployed to `dist`.

## build 144fy — Country drives timezone + currency in Settings ▸ General

- Country, timezone, and currency are now all dropdowns. Each country carries a
  default timezone and currency (`COUNTRY_META`, 60 countries); picking a country
  auto-fills both — timezone and currency stay editable afterwards. Currency is a
  de-duped picker (code — symbol) that also sets the symbol, with the symbol field
  left editable for custom cases. `tsc` clean, bundle builds, deployed to `dist`.

## build 144fx — Settings ▸ General to full parity

- Rebuilt the React Settings ▸ General section to the complete field set: company
  logo upload, currency symbol + code, country / timezone / document-language
  dropdowns, an additional-currencies rate table (add/edit/remove), industry
  template picker, company registration/tax details + printed company note, the
  labour-billing time-clock toggle, and a "Rebuild balances from history" action
  (POST `/staff/ledger/rebuild`). All fields round-trip through the existing
  `/staff/company` GET/PUT.
- Styling uses the app's current design system (Stitch-export `glass-input` +
  `shadow-clay-card` cards, `M` symbol icons) — same card/input classes as every
  other Settings section; no legacy styling. `tsc` clean, bundle builds.

## build 144fw — Surface new features in the React UI (FE widgets)

- Reusable `TagChips` component (find/create/attach/remove) wired into the Client
  drawer and Work-order drawer headers — B6 tags now visible in the app.
- Work-order Invoices tab gains a **Payment link** action: create, status chip,
  copy, rotate (revokes old), revoke — surfaces the A2 lifecycle. `tsc` clean,
  bundle builds.

# Changelog

## build 144fv — Live telephony (Sprint 3 · B7)

- New `telephony_config` (provider/enabled/caller-id) + `employees.extension`.
- `/api/telephony`: get/set config, **click-to-call** (provider-agnostic; the
  `simulated` provider logs a Call with no external creds so the flow works out
  of the box, real Twilio/Aircall/Zadarma plug in behind the same interface),
  and an **incoming-call feed** (`/incoming`) powering the call-pop card, with a
  mine_only filter by the employee's extension. Inbound calls keep arriving via
  the existing token-gated `/api/comms/calls/webhook` (client matched by phone,
  missed-call follow-up task). Telephony is off until explicitly enabled.

## build 144fu — C hardening: public-link rotate/revoke + duplicate-phone surface

- Order public links can be **rotated** (invalidates old shared links) and
  **revoked** (track page 404s) — beats Orderry, which can't revoke links.
- `/api/clients/duplicates` surfaces clients sharing a phone (with a latest-created
  keep-hint) so duplicates can be merged before they break notifications/call
  history. Per-account cashbox ACL enforced (144ft); webhook payloads already
  HMAC-signed. Custom-field values already persist per-record and prints render
  server-side via ReportLab (no external fetch), so those Orderry risks don't apply.

# Changelog

## build 144ft — Per-account cashbox ACL enforced (Sprint 3 · C)

- The existing per-role `account_ids` restriction is now actually applied to the
  cash accounts list: a role limited to specific cashboxes only sees those (role
  finance rights still gate the area). Confirmed webhook payloads are already
  HMAC-signed (`X-RepairOS-Signature: sha256=…`) per endpoint secret — a C item
  that was already in place and beats Orderry's unsigned webhooks.

# Changelog

## build 144fs — Unified Tags (Sprint 3 · B6)

- New `tags` + `taggables` tables and `/api/tags` router: create/rename/delete
  tags (case-insensitive unique per org) and attach/detach them to contacts,
  orders and chats. `/api/tags/for` lists an entity's tags; `/api/tags/entities`
  lists what carries a tag (for filtering). Deleting a tag removes its attachments.

# Changelog

## build 144fr — Per-status VIEW ACL (Sprint 2 · A5)

- Roles gain `hidden_status_keys`: a role can be denied visibility of orders/
  estimates in specific statuses (deny-wins, default-allow). Enforced at the
  query in `scope_orders`, so lists, counts and pagination all respect it — a
  role that can't see "Done" can't open those orders anywhere. Editable via the
  role API alongside the existing transition (set) ACL. Additive migration.

## build 144fq — Inventory posting freeze (Sprint 2 · A4)

- A stock posting can no longer be deleted once its goods have moved downstream
  (sold, used, transferred or written off): the on-hand for a line is below what
  it added, so reversing it would drive stock negative and desync FEFO. The API
  returns a clear 409 pointing to a write-off or supplier return instead.

# Changelog

## build 144fp — Deletion dependency chains (Sprint 1 · A3)

- Deleting a work order that still has unrefunded payments is blocked unless you
  delete-with-refund (or void the payments first) — money is never orphaned.
- A cash transaction that settles a refund / client-return / purchase-return can
  no longer be voided on its own; you must delete the parent return/refund, so the
  document and its ledger leg stay in sync.


## build 144fo — Payment-link lifecycle (Sprint 1 · A2)

- Pay links now have a visible lifecycle: Created → Viewed → Waiting for payment →
  Paid / Failed / Revoked (payment_intents.viewed_at + a `lifecycle` label).
- Public `POST /api/payments/intents/{id}/view` records when a client opens a link.
- Staff `POST /api/payments/intents/{id}/revoke` kills an unpaid link immediately
  (a paid link can't be revoked) — improves on Orderry, which can't revoke links.
- Order pay-link is now generate-once: reuses the live link unless `?regenerate=true`,
  which revokes the stale one first. Additive migration; single Alembic head.

## build 144fn — Append-only cash ledger: corrections + soft-delete (Sprint 1 · A1)

- Transactions are never silently edited. A correction posts a signed delta row
  (`reverses_txn_id`, mandatory reason) and the original is flagged `corrected`,
  not mutated. `POST /api/cash/transactions/{id}/correct`.
- Deletes are soft (`status=deleted`, struck-through in the ledger, restorable):
  `DELETE .../transactions/{id}` reverses the amount out of the live balance but
  never recomputes other rows' frozen `balance_after`; transfer legs void together.
  `POST .../transactions/{id}/restore` re-applies.
- Every row now carries a frozen `balance_after` snapshot. Gated by
  `can_correct_transactions` / `can_delete_transactions`. Cash ledger UI shows
  strikethrough, a Balance column, and Correct/Delete/Restore actions.

# Changelog

## build 144ez — Guarded root cutover (React redesign at /)

- The Liquid Clay React redesign is now served at "/" (the whole app), replacing
  the legacy UI as the default surface. Same FastAPI backend, same endpoints,
  same session token — UI/UX only.
- Login preserved (guard): the redesign has no login screen, so the unchanged
  legacy login/signup/reset/verify/invite screens are served at /login,
  /signup, /reset-password, /verify-email, /accept-invite. The React app
  redirects to /login when there is no session, and on 401. Legacy auth
  success now hands off to "/" (React).
- Fixed React api client to read the real bearer key `ros_tok` (was ros_token),
  so the redesign authenticates against the live session.
- vite base "/" + router basename "/"; assets served from dist at /assets/*.
- main.py: "/" and catch-all serve the React SPA shell; /api, /static, /assets,
  and all public routes (/track /admin /book /tech /pay /portal /offline) are
  untouched. Requires one server restart to load the new routes.


All notable changes to RepairOS. Newest first.

## build 144ey — Full React redesign (Liquid Clay) app-wide + Inquiries pipeline, multi-assignee tasks

- **Every module migrated to the React app** (`frontend-react/`, served at `/app2`): Work orders (hub +
  detail drawer + 2-step create/estimate wizard), Contacts, Assets, Inventory (redesign-3: Stock ·
  Devices & Assets · Services · Bundles · Purchase orders + audit tabs, colored SKU badges, stock pills,
  right-drawer create/edit for product/service/bundle/asset), Sales Quick POS, Finance, Cash (Orderry
  cashbox: account cards, 3-dot Income/Expense/Transfer/Configure, New-account drawer with % + flat fees),
  Reports (catalog-driven runner), Settings, Tasks, Inquiries, Chats, Bookings. All `ROUTE_OWNERS` = react.
- **Inquiries = lead pipeline** with a **Pipeline / List** switcher, details drawer, SLA badges, and a
  status-driven **Win → Work order / Estimate** split button.
- **Chats**: channel filter, **Conversation / List** switcher, quick replies, and **Create → Work order /
  Estimate / Inquiry** from a conversation.
- **Tasks**: list + detail drawer with **Start timer**, comments/feed, WO link, status buttons, and
  **multiple technicians per task** (new `tasks.assignee_ids`).
- **Reusable New-client drawer** wired into POS and the WO create form.
- Backend deltas are additive/backward-compatible only: `is_estimate` on `POST /api/leads/{id}/convert`
  and `POST /api/chats/threads/{id}/to-order`; new `tasks.assignee_ids` JSON column (migration
  **e1g3i5k7m9o1 / 144ex**). Schema/data otherwise untouched.
- Quality gate: impeccable detector **0 issues**, `tsc --noEmit` clean, `vite build` clean; retired dead
  early-sprint components (Drawer/Modal/StatusPill/WorkOrdersPreview).
- NOTE: run `alembic upgrade head` (adds `tasks.assignee_ids`) and restart the API to load the new
  endpoints/params.

## build 144db — Forms Editor rebuilt Orderry-style (entity tabs + System fields)

- **Entity tab switcher** across the top: **Work orders · Invoices · Contacts · Products · Vehicles** —
  each shows that form's fields. Work orders keeps its Type chips (Repair/Diagnostics/…).
- **System fields** per entity (Client, Vehicle, Manager, Specialist, Type, Scheduled for, Due date,
  Priority, Malfunction, Manager notes, Payer, Estimated price, Comment for orders; full catalogs for
  invoices/contacts/products/vehicles) each with **Show** + **Required** toggles, saved per entity/type.
- **Custom fields** editor unchanged, now shown beneath the system fields with **+ Add field**.
- New `form_layouts` table + `GET/PUT /api/forms/system-layout` (migration c9e1g3i5k7m9). 3 backend tests green.
- NOTE: activating saved toggles needs a **backend restart** to load the new endpoints + table.

## build 144da — Full asset ("+ Asset") form: image upload + VIN/plate + vehicle system fields

- Rebuilt the New-asset form: **image drop-zone**, **VIN + Generate**, **Number plate**, Type/Brand/Model/
  Condition, and vehicle details (Year, Tyre size, Engine displacement, Engine power) + custom fields.
  Reusable with an onCreate callback so it can be opened from "+ Asset" and hand the asset back.
- Backend `POST /api/assets` now accepts `image_data` + `custom_data` (no migration; columns existed).

## build 144cz — Create-WO: choose form Type, plus Manager + Specialist (default & changeable)

- **Manager on the create form.** New WO / estimate create now has a **Manager** field alongside the
  **Specialist (assignee)**. Both default to whoever is creating the order and are freely changeable;
  `manager_id` is sent on create (backend already accepts it).
- **Work-order Type chooser** (Repair / Diagnostics / Warranty / etc.) was already present but only renders
  when the org has types configured. Seeded sensible defaults so the chooser appears; verified a created
  order persists its type_id, manager and assignee.
- Overview strip fixed: the Schedule field no longer wraps/misaligns; Manager & Specialist render as avatar
  chips (dashed "Assign" when empty); empty Client/Asset cards show a tidy placeholder line. Cache `?v=144cz`.

## build 144cx — Overview rebuilt to the Repair Canvas prototype (exact match)

- **Overview tab now matches the prototype.** A single **Type · Manager · Specialist · Schedule** strip,
  then two rich clickable cards — **Client** (→ profile) and **Asset** (→ info + history) — each with a
  header (avatar/photo + name link) and a rows body (Mobile/Balance; Serial/IMEI/VIN/Prior-visits/Warranty),
  then the Complaint card and the system-fields note. Replaces the old detail-blob grid.
- Multi-technician, Manager, Specialist and the clickable Client/Asset all kept; every handler preserved
  (pickPerson, openClient, openAsset, changeAssetPhoto, reschedule, custom fields).
- Verified live in Chrome (strip + 2 cards + complaint render), zero console errors. Cache `?v=144cx`.

## build 144cw — Repair Canvas: top status dropdown, job cards, unified Activity, checklist rings

- **Status at the very top (Tekmetric-style).** The drawer header status is now a click-to-open pill
  listing every status; picking one patches the order and updates the pill in place. The old bottom
  "Move to status" chip row is removed (relocated, not lost).
- **Services & products = job cards.** The spreadsheet table becomes per-technician job cards with a
  per-job subtotal; every inline control (edit qty/price, reassign tech, receive pending stock, delete,
  cost/profit) is preserved — just re-laid-out as cards. Empty state is a composed card, not a lone button.
- **History merged into Activity.** The redundant History tab is gone; the status timeline now lives at the
  bottom of the Activity tab under the event feed. One place, no triple-redundancy.
- **Checklists get a progress ring.** Each checklist card shows a % completion ring; the empty state is a
  real card with a primary CTA.
- Verified live in Chrome, zero console errors. JS clean, order tests + new assign tests green. Cache `?v=144cw`.

## build 144cv — WO Overview people bar + Invoices balance hero (Orderry parity)

- **Manager + Specialist on every work order.** The Overview tab now leads with a **Type · Manager ·
  Specialist (assignee)** bar, matching Orderry's General info. Both people are clickable to reassign;
  changing the assignee **notifies** the newly-assigned technician.
- **New `orders.manager_id`.** The WO now carries an owner/manager distinct from the assigned specialist
  (additive, nullable column + migration `b8d0f2h4j6l8`). On create it defaults to the client's account
  manager; `PATCH /api/orders/{id}/assign` sets manager and/or assignee, touching only the fields sent.
- **Multi-technician work orders (already supported, now clearer).** Services & products lines keep their
  per-line technician (grouped by tech), feeding payroll commission and logged hours — one WO, many techs.
- **Invoices & payments balance hero.** The tab opens with a large Balance-due figure and a single
  **Collect payment** CTA (falls back to a "Settled" badge). Reuses order totals; no extra fetch.
- No features removed. Order + assign tests green; single migration head; cache tag `?v=144cv`.

## build 144el — Work-order drawer redesign (Blend layout, Orderry-clean)

- **Ranked header.** The six equal header buttons became **Print + Message as icon-only** plus one **Actions**
  menu that now also holds Billing currency and Order code (with Copy link / Duplicate / Create PO / Delete).
  Nothing removed — just ranked, so the eye lands on the status and the primary flow, not a wall of buttons.
- **Persistent summary rail (Blend).** Tabs stay full-width; below them the body is a two-column row — the tab
  panels on the left and a sticky right rail showing the **customer** (avatar, phone, balance), the **device**,
  the **money summary** (subtotal / discount / tax / total / paid / balance due) and a **Recent activity** slice.
  The rail reads the already-loaded order — no extra request. Shopmonkey's always-visible totals + Orderry's
  activity, without the tab-hop.
- Everything else in the drawer (all tabs, inline line editor, status chips, recs, invoices) is untouched.
  Cache-buster → `?v=144el`. Prototype the direction came from: `repairos-wo-redesign-variants.html`.

## build 144ek — Service form: prices-by-type, who-can-perform, resource types

Three remaining Orderry service-form pieces, built lazily (reuse over rebuild):

- **Prices by price type** — the service drawer now lists every price type with an editable value for this
  service. Zero new backend: reuses the existing `/api/pricing/types` + `/types/{id}/items` (`PriceTypeItem`).
  Values save after the service is created (same pattern as commissions).
- **Employees who can perform** — All / per-employee checkboxes on the service. Stored on
  `Service.allowed_employee_ids`; the public booking page returns each service's allowed staff, and the booking
  endpoint **rejects a disallowed employee server-side** (409) so the gate holds regardless of the client.
- **Required resource types** — comma-separated types stored on `Service.required_resource_types` and returned
  on the booking config. `ponytail:` enforcement in the slot engine is deferred — there's no
  resource-availability model yet; upgrade when shops actually book by resource.

Migration `a7c9e1g3i5k7` adds the two JSON columns. Tested: rules round-trip, price-type value persists for a
service — 9 passing with the service/price-list suites. JS parse-clean; single migration head. Cache-buster →
`?v=144ek`.

## build 144ej — Fix: order numbers are unique per-org, not globally (multi-tenant)

- **The multi-tenant bug found in 144ei is fixed.** `orders.order_number` was declared globally unique but
  numbered per-org, so a second tenant's first order collided on "WO-00001" and 500'd. Researched Orderry
  first — it scopes order numbering to the account (T-series / B-series per workflow, unique within the
  account, freely repeatable across accounts) — and matched that: the constraint is now a **composite unique
  on (org_id, order_number)**. Each tenant numbers from its own sequence; the scannable **code** (barcode)
  remains globally unique (144ei), which is the right split — display numbers per-account, scannable codes
  global.
- Model swaps the column-level global unique for `UniqueConstraint(org_id, order_number)`; migration
  `z6b8d0f2h4j6` drops the old constraint and adds the composite (SQLite batch + naming convention, portable
  to named-constraint databases).
- **Tested:** two tenants can each create their first order without collision (the exact failure), order
  numbers stay unique within an org, and scannable codes stay unique across tenants — plus the work-order /
  invoice / POS-scan / RBAC suites (25) still green. Single migration head. Cache-buster → `?v=144ej`.

## build 144ei — Globally-unique work-order code (never repeated, across all tenants)

- Every work order now carries a scannable code that is unique across the **entire system**, not just one
  org — Orderry semantics. Enforced three ways: a **DB-wide unique index** on `orders.barcode` (the hard
  guarantee), a generator that produces a 12-digit EAN-style code checked against every existing order, and
  **auto-assignment at creation** so a WO has a unique code from birth. The Order code modal's **Generate**
  now pulls a server-issued unique value, and saving a code already used by another work order is rejected
  with a clear message. Clearing a code **regenerates** a fresh unique one rather than leaving the order
  codeless. Migration `y5a7c9e1g3i5` backfills any missing/duplicate codes before adding the unique index.
- **Tested:** codes never repeat over many orders, the generate endpoint returns an unused value, a duplicate
  is rejected (409), and clearing re-issues a unique code — plus the existing work-order / invoice / POS-scan
  suites (26 tests) still green. JS parse-clean; single migration head. Cache-buster → `?v=144ei`.
- **Discovered (not fixed here):** `orders.order_number` is declared globally unique but numbered per-org, so
  two tenants both reach "WO-00001" and collide — a pre-existing multi-tenant bug, separate from the code work.
  Flagged for a follow-up (order_number should be unique per-org).

## build 144eh — Order code modal placement fix

- Restacked the Order code modal so it reads cleanly in the narrow width: Barcode/QR toggle, then
  **Barcode type** on its own row (hidden entirely in QR mode), then the **Code value + Generate** row,
  then a framed, centered **preview** panel — instead of cramming the value field, its Generate button and
  the type selector into one tight row. Cache-buster → `?v=144eh`.

## build 144eg — Work-order barcode / QR code (screen + printable), Orderry "Order code"

- New **Code** button in the Work Order drawer opens an Orderry-style **Order code** modal: a **Barcode / QR
  code** toggle, a code-value field with a **Generate** button, a barcode-symbology selector, and a **live
  preview** via the shared generator. Save persists the value on `order.barcode`.
- **Printable + scannable, all matching.** That same value already drives the `{Barcode}` and `{QR Code}`
  document-template variables and the `/documents/by-barcode/{code}` scanner lookup — so what you see in the
  drawer is what prints on the work-order document and what resolves when scanned. New
  `PATCH /api/orders/{id}/code` setter; empty value clears back to the order number as the default code.
- **Tested:** setting the code persists and the scanner resolves both the explicit code and the order number;
  empty clears to none; org-scoped (a foreign order 404s) — 3 new cases. JS parse-clean; cache-buster →
  `?v=144eg`. No migration (reuses the existing `order.barcode` column).

## build 144ef — One barcode/QR generator app-wide + service commissions

- **Unified barcode/QR generator.** A single authed endpoint `GET /api/catalog/code.svg?data=…&type=code128|qr`
  renders the real SVG (reusing `services/codes.py` — the same generator the label/print engine uses), and a new
  shared `Barcode` helper (`barcode.js`) renders it anywhere. Wired into the **Products** form (the fake
  `|| ||| |` placeholder is now a real live preview; new products can generate a value client-side without
  saving first) and the **Service** drawer (barcode field + type + Generate + live preview). Same code, same
  look, everywhere.
- **Service exceptional commissions (Orderry parity).** `CommissionRule` gained `service_id` and a new
  `service` rule level; the payroll `CommissionEngine` now prefers a service-scoped rule for that service's
  labor lines over category/generic (mirroring the product tier). New endpoints under
  `/api/catalog/services/{id}/commissions` (list/add/delete), and an **"Employee exceptional commissions"**
  section in the Service drawer — per-employee or all-staff, percent or flat. Migration `x4z6b8d0f2h4`.
- **Tested:** code.svg renders Code128 + QR and rejects empty data; service-commission CRUD; the engine's
  exceptional-priority resolver prefers a service rule over generic — 12 new cases, plus the full 31-test
  payroll/commission suite still green (no regression). JS parse-clean (70 files); migration compiles; single
  Alembic head. Cache-buster → `?v=144ef`.

## build 144ee — Richer Service drawer (Orderry New Service parity)

- The New/Edit Service drawer now carries the full field set: **Type** (Labor / Service), **Category**,
  **Name**, **Description**, **Unit of measurement** (piece / hour / job / metre / m² / kg / litre / set /
  visit), **Warranty** with a days/months toggle, **Duration** (h + min), **Barcode**, **Cost** and
  **Price** with the "From cost" helper + live margin, and an **Available for online booking** toggle.
  Buttons and labels tidied — inline "+ Category", "From cost" beside the price, segmented Type/warranty
  controls.
- **Backend.** `Service` gained `description`, `is_labor`, `unit`, and `bookable` (existing `duration_min`
  and `barcode` are now surfaced through the API). `ServiceIn`, the serializer, and create/update round-trip
  all of them. The public booking page now lists only services flagged **bookable** (`Service.bookable ==
  True`), so unchecking the box hides a service from clients while staff can still book it manually.
  Migration `w3y5a7c9e1g3` adds the columns (single head; `bookable` defaults true to preserve current
  behaviour).
- **Deferred** (Orderry shows these lower in its form; each is its own subsystem, not built this pass):
  multiple price-types table, required resource types, per-employee "who can perform", and exceptional
  commissions.
- **Tested:** 3 new pytest cases (field round-trip, update, sane defaults) + price-list suite = 7 passing.
  JS parse-clean; migration compiles; single Alembic head. Cache-buster → `?v=144ee`.

## build 144ed — New Invoice & New Service as right-side drawers (Orderry parity)

- **New Invoice** is now a right-side drawer (`modal-side-wide`, same pattern as the Work Order
  drawer) instead of a bare subtotal/discount/tax box. It exposes the features that were already in
  the backend but had no create UI: searchable **client**, optional different **payer** ("Payer is
  the same as the client" toggle), **issue/due dates** with a payment-term preset (On receipt / Net
  7/15/30 / Custom), the **Order display** mode segmented control (Detailed / Simplified / One line),
  a **CatalogPicker** line table for free-form labors/services/products, optional **work-order
  attachment** (multi-order), and a **comment**.
- **New / Edit Service** is now a matching right-side drawer: category (+ inline new), name, a
  warranty value with a **days/months** unit toggle, cost and price with a **"From cost"** helper
  (applies a 40% margin) and a live margin readout.
- **Backend — free-form invoice lines.** `InvoiceLine` gained `kind` / `source_id` / `sort_order`
  and `Invoice` a `comment`; `create_invoice` now accepts `order_ids`, `lines`, `issued_at`,
  `due_at` and `comment`, validates orders are in-org (foreign orders are dropped), and auto-sums
  the subtotal from the free lines + billed orders. `display_lines` folds the free lines into all
  three display modes; detail serialization returns `comment` and `due_at`. Migration
  `v2x4z6b8d0f2` adds the columns (single head).
- **Tested:** 4 new pytest cases (free-lines-only, fold-into-all-modes, multi-order-at-create,
  foreign-orders-dropped) + the existing invoice suite = 18 passing. Full JS parse sweep clean;
  migration compiles; single Alembic head. Cache-buster → `?v=144ed`.

## build 144ec — Fix broken cache-busting (shipped work now actually reaches the browser)

- **Root cause:** 65 of 67 `<script>`/`<link>` tags in `index.html` had no `?v=` query string, so the browser
  served stale cached copies indefinitely. The prior `sed`-based bumper only rewrote tags that *already* had a
  version, so newly-touched files (settings.js, orders.js, other.js, core.js, …) never invalidated. Effect:
  builds 144dy (WO/estimate status colors), 144ea (grouped Settings nav) and 144eb (styled dialogs) were live on
  disk but invisible in the browser until a manual hard-refresh.
- **Fix:** versioned **all 67 assets** with `?v=144ec` and bumped the build tag. Verified live in Chrome — grouped
  Settings rail (Company/Workflow/Catalog/Customer/Advanced), estimate status = transparent green outline vs
  work-order status = solid blue fill, tabular figures + ORDER column in Finance, and the neutral background all
  now render without a manual refresh.

## build 144eb — Styled confirm/prompt (no more native browser dialogs)

- Added on-brand `Modal.confirm(msg, {danger, title, confirmLabel})` and `Modal.prompt(msg, {default, placeholder})`,
  each resolving via a closure (fixes the old `confirm2` shared-global bug), stacking over open modals, with
  backdrop/Cancel resolving the "no" value so a promise never hangs, and Enter/Escape in prompt.
- Rewired **all 20 native dialogs** — 8 `confirm()` (deletes → red danger button) and 12 `prompt()` — across
  gdpr, category-manager, category-tree, orders, products, insights, checklists, currencies, feedback, sales,
  inventory. Made `insertImage()`/`setDiscount()` async where needed.
- **Tested (jsdom):** confirm resolves true/false, prompt resolves value/null, overlays open & close, danger
  styling renders. Full-app JS parse sweep clean; zero native `confirm(`/`prompt(` remain. Cache-buster → `?v=144eb`.

## build 144ea — Grouped Settings navigation

- The 18 flat settings items are now grouped into scannable clusters — **Company / Workflow / Catalog / Customer /
  Advanced** — with quiet uppercase group headings, so owners find "numbering" or "webhooks" fast.
- `SettingsPage._railHtml()` is a pure, unit-tested renderer; `go()` switched from fragile index-based to
  key-based active state (`data-key`) so grouping can reorder items without breaking selection.
- Verified with a Node/vm unit test: 5 groups render, all 18 items present, exactly one active. FE-only
  (`settings.js` + `.settings-rail-group` CSS). Cache-buster → `?v=144ea`.
- Deferred (need Chrome up to verify visually): width-discipline on Finance/Settings, and rewiring the ~29 native
  `confirm()/prompt()` calls to the existing styled `confirm2()`/a new `Modal.prompt`.

## build 144dz — Polish pass: tabular figures + real WO number in Finance

- **Tabular numbers everywhere.** `.tbl-num` and all number surfaces (`.money`, `.mono`, `.fig`, KPI/stat values)
  now use `font-variant-numeric:tabular-nums`, so money and quantity columns line up vertically like a real
  finance tool instead of jittering.
- **Finance shows the WO number, not a UUID.** The invoice list's Order column showed a raw `39875358…`;
  `list_invoices` now batch-resolves each invoice's primary order number (no N+1) and the row renders `WO-00001`.
- Empty states were already handled app-wide via the shared `emptyRow()` helper — no change needed.
- FE + a small read-only backend addition; invoice suites green (14/14). Cache-buster → `?v=144dz`.

## build 144dy — Estimate vs work-order status, told apart at a glance

- Estimates and work orders no longer share a status look. **Work orders** now render a SOLID coloured chip;
  **estimates** stay a TRANSPARENT outline chip — the Orderry convention, so a quote never reads as committed work.
- Applied per-row (keyed on `is_estimate`) so it's correct even in the unified list that mixes both, and in the
  order/estimate detail header. `badge()` gained an `{est, color}` option and now handles custom org-defined
  status keys (falls back to the status's own colour instead of a generic grey).
- Pure FE (`design.css` `.row-status-sel.wo`, `core.js` `badge()`, `orders.js` list + detail). No backend/logic
  change. Cache-buster → `?v=144dy`.

## build 144dx — Keystone UX fix: kill the rainbow background

- Removed the pink→lilac→mint pastel mesh + the five animated rainbow "orbs" that sat behind every light-mode
  screen. Replaced with one calm, barely-there cool off-white surface (`linear-gradient(180deg,#f7f8fb,#eef1f6)`)
  so the data and cards lead the eye, not the wallpaper. Grain texture kept but de-tinted and dialled down.
- Dark mode was already a calm graphite (`#0A0C14` with two ~6% radials) — left untouched.
- Pure CSS in `design.css`; no markup/logic change. Cache-buster bumped to `?v=144dx`. This is the single
  highest-leverage item from the UX audit — it lifts every screen at once. Rest of the audit/redesign explorations
  are archived (in the outputs folder), not implemented.

## build 144dw — Send invoice by email + payer picker (bill-to)

- **Send invoice by email** (Orderry's envelope button): `POST /api/finance/invoices/{id}/send` renders the invoice
  PDF (shared builder with the print endpoint) and emails it as an attachment through the org's connected Gmail/
  Outlook account. No account connected → the send is "simulated" and logged (gated-seam pattern), never an error.
  Recipient defaults to the payer/customer email, overridable per send. Every send is logged to the client's inbox
  thread. `send_via_connection` now supports attachments (Gmail MIME multipart, Graph fileAttachment).
- **Payer picker** (bill a different party — employer / insurer / fleet): `PATCH /api/finance/invoices/{id}` now
  accepts `payer_client_id` (validated in-org; null clears back to the customer). Invoice detail returns resolved
  `payer` + `customer` objects. FE: a "Bill to (payer)" row with Change-payer (ClientPicker) and Reset actions,
  plus a "Send by email" button — both in the invoice detail modal.
- Sending is explicit-permission by design: it's a user-initiated action behind a confirm dialog, never automatic.
- Gates: `test_144dw_send_and_payer.py` (5/5 — payer set/reset, payer in-org enforcement, send simulated w/o
  connection, recipient-required + override, send org-scoped) + a MIME-attachment assembly check; 144dt/du/dv
  suites green (17 together). No schema change; migration head unchanged at `u1w3y5a7c9e1`.

## build 144dv — Order display flows into the printed invoice AND the client pay page

- The invoice's display_mode (detailed / simplified / one_line) now drives every surface, not just the staff
  detail modal. What you pick is what the customer sees and what prints.
- Printed/PDF invoice: new `GET /api/print/invoices/{id}` renders the resolved display lines across all linked
  work orders, with a header showing bill-to, the work orders billed, the display mode, dates and status. A
  "Print / PDF" button is wired into the invoice detail modal (auth-safe via `openPrintPdf`).
- Client pay page: `GET /api/public/pay/{intent}` now returns the invoice's `lines`, `display_mode`, and
  `invoice_number`; `pay.html` shows an itemized breakdown above the Pay button, so the customer sees the same
  grouping (per-line, per-ticket, or single line) before paying.
- Gates: `test_144dv_invoice_render.py` (3/3 — printed invoice is a real PDF, org-scoped 404, pay page reflects
  the mode and updates live when it changes); print-endpoints + 144dt/du suites green (14 together). No schema
  change; migration head unchanged at `u1w3y5a7c9e1`.

## build 144du — Multi-order invoices (one invoice, several work orders)

- An invoice can now bill several work orders, matching Orderry ("You can add multiple work orders to the same
  invoice"). New `invoice_orders` link table; `Invoice.order_id` stays the primary/first order for back-compat and
  is auto-backfilled into the link table by the migration.
- `display_lines` fans out across every linked order: detailed = all lines from all orders (grouped by order),
  simplified = one line per ticket, one_line = a single line ("WO-00001 +2 more"). This makes "Simplified — one
  line per ticket" finally meaningful.
- API: `POST /finance/invoices/{id}/orders` attach, `DELETE /finance/invoices/{id}/orders/{order_id}` detach; both
  org-scoped and return the re-resolved invoice detail (`{lines, orders}`). `GET`/`PATCH`/`POST` invoice responses
  now include the `orders` array.
- FE: invoice detail modal lists the work orders it bills as removable chips, with an "add work order by number"
  box; lines re-render live on attach/detach and on mode switch.
- Gates: `test_144du_multi_order_invoice.py` (4/4 — primary link seeded, attach fans out all 3 modes, detach drops
  lines, attach org-scoped); 144dr/144ds/144dt suites green (16 together). Migration `u1w3y5a7c9e1`, single head.

## build 144dt — Order display made functional (Detailed / Simplified / One line)

- An invoice's `display_mode` was stored but never applied. Now it drives what the invoice actually shows:
  `detailed` = every order line, `simplified` = one line per source ticket, `one_line` = a single line labelled
  with the order. Lines are resolved on the fly by `services/invoicing.py::display_lines`, so switching modes
  never rewrites or destroys stored line data.
- API: `GET /finance/invoices/{id}` returns the header + resolved `lines`; `PATCH /finance/invoices/{id}`
  persists a mode switch and returns the re-resolved lines. Both org-scoped (404 cross-tenant).
- FE: invoice number in Finance → Invoices is now clickable → detail modal with an "Order display" selector that
  re-renders lines live, plus a Record-payment shortcut.
- Gates: `test_144dt_display_modes.py` (5/5 — three modes, mode-switch round-trip preserves lines, org scoping);
  144dr + 144ds suites still green. No migration (column already shipped in 144dr).

## build 144ds — Sprint 3: commission by close outcome, invoice status on orders

- **Commission by close outcome.** Two new payroll events — **`order_closed_success`** (terminal status in
  the `closed` group) and **`order_closed_fail`** (`dropped` group) — fire alongside the existing general
  `order_closed`. A shop can now pay only for successful outcomes, or different amounts for each. Surfaced in
  Settings → Payroll rules ("Order closed — successful / unsuccessful").
- **Invoice status on the orders list.** The work-order list now shows an **Invoice** column (Draft / Issued /
  Paid, or — for not-yet-invoiced), batched server-side (no N+1). Toggleable via the column menu.
- **Payer ≠ client** is exposed on the invoice API + serializer (schema shipped in 144dr); the invoice-form
  payer picker is the remaining FE bit.
- **Approved recs → invoice** needs no new code: customer approval on the portal already creates an OrderLine
  (144be), so those charges are in the order total and flow onto the invoice / auto-draft automatically.
- Gates: `test_144ds_sprint3.py` (2/2 — close-outcome events available + rule accepted, invoice-status on the
  orders list) + regression across payroll, rec-commission, gates, work orders (24/24); FE sweep **97/97**.

## build 144dr — Orderry Aug/Sep parity: bookings-without-assignee, scheduled-on-track, invoice loop

Sprint 1 + the Sprint 2 flagship from the roadmap.

- **Bookings without an assignee (E1).** `Booking.employee_id` is now nullable — take the request first,
  assign the tech later (the repair flow Orderry itself calls out). `GET /bookings?unassigned=true`
  lists the backlog; the overlap guard only applies once assigned. Online booking honors a new
  **"ask the customer to pick an employee"** toggle — off → slots come from the location's hours and the
  booking arrives unassigned.
- **Scheduled date on the track page (E2).** New org opt-in `oe_show_scheduled`; when on, the customer's
  `/track` page shows "Scheduled for" (and from–to for multi-day). Hidden by default.
- **Invoice loop — the flagship (E3).** `Invoice.display_mode` (detailed | simplified | one_line) +
  `payer_client_id` (bill a different party). The differentiator over Orderry: an org can opt into
  **`auto_draft_invoice_on_close`** — closing a work order **auto-drafts an invoice** (pre-linked,
  totals carried, default display mode, `auto_drafted` flagged), so with the existing order payment-link
  + gateway webhook a shop goes from "close job" to "client paid" with no document-building step.
- Settings: the two public-page toggles ship in Settings → Public pages. Schema in migration
  `t0v2x4z6b8d0`; the extra Invoice→Client FK (payer) is disambiguated on the `client` relationship.
- Gates: `test_144dr_invoice_booking.py` (4/4 — unassigned booking + filter, scheduled hidden-until-opt-in,
  invoice display mode persisted, auto-draft-on-close) + regression across bookings, public toggles,
  group invoice, work orders (28/28); FE sweep **97/97**. Single migration head.
- **Deferred to next (Sprint 3):** approved-recs → invoice lines, invoice-status column on the orders
  list, commission split by successful/unsuccessful close, resource types + auto-assign (pending
  segment validation).

## build 144dp — Checklists v2 (Orderry-style, with revenue tie-ins)

Turned the flat boolean checklist into a full inspection tool: **Template → Steps → Sections → typed
Fields**, filled step-by-step on the work order.

- **Settings builder** (Settings → Checklists): templates with `all`/`selected` order-type scope, drag
  steps/sections/items, and typed fields — check-result (Pass/Fail/Caution/Unknown), Yes/No, text,
  textarea, dropdown, multi-select, date, file. Unique-name guard, snapshot-on-attach.
- **Work-order Checklists tab**: add a checklist (permission-gated), stepped fill with per-kind controls,
  progress + author on each card, auto-**Completed** + derived **pass/fail** result.
- **Differentiators over Orderry:** a **Fail → one-click Recommendation** (drafts an upsell that flows to
  the client portal); **client-visible** fields + answers surface on the `/track` page; **required items
  block completion** via the existing status gate. Four new role permissions
  (`manage_checklist_templates` / `create` / `edit` / `delete_checklist`).
- Schema: new `checklist_steps`, `checklist_sections`, `order_checklists`; `checklist_templates` gains
  `scope`/`type_ids`; items gain `field_kind`/`options`/`section_id`/`client_visible`/`affects_result`;
  order items gain `checklist_id`/`value`/`file_ids`/step+section snapshots. Migration `s9u1w3y5a7c9`.
  The flat 144-era per-order endpoints stay intact for the mobile PWA.
- Gates: backend `test_checklists_v2.py` (6/6 — nested CRUD, attach/fill/derive result, tenant isolation,
  fail→recommendation + client-visible track, required-blocks-completion, permission gate); FE
  `checklists_v2_144dp` + full sweep **97/97**; broad backend regression **36/36**.

## build 144do — security fixes (asset tenant isolation + instant suspension)

Fixes the two findings from the 144dn security audit.

- **HIGH — cross-tenant asset IDOR (fixed).** `devices` now has its own `org_id` (was inferred only
  through `client_id → Client.org_id`, leaving company-owned assets unscoped). Every asset query is
  scoped to `org_id`: the list (previously leaked all orgs' company assets via `owner_type=="company"`),
  `GET/PUT /assets/{id}`, transfer, write-off, convert-to-product, and `GET /tech/device/{id}/history`.
  The fake `hasattr(Device,"org_id")` guards are gone. `org_id` is stamped at all five device-creation
  sites (asset create, client asset, order auto-asset, product→asset conversion). Alembic migration
  `r8t0v2x4z6b8` adds the column and backfills from the owning client, else the latest order; unresolved
  rows stay NULL (fail-closed / unreachable cross-tenant).
- **MEDIUM — suspension now instant.** `get_current_employee` enforces org lifecycle on **every**
  request (suspended `sub_status`, past `sub_end`, and per-employee access window), not just at login.
  Previously a suspended org's existing 24h tokens kept working.
- Gates: new `test_asset_tenant_isolation.py` (cross-tenant read/edit/transfer/write-off/history all
  404; suspended org's live token → 403) + updated raw-`Device` fixtures to stamp `org_id`. Regression
  sweep across auth, platform-admin, clients, compliance, work orders, invoices, payments, conversions,
  assets, timeclock — all green. Injection/secrets/headers already clean per the audit.

## build 144dn — clock privacy + manager "who's on the clock" roster

Made the time clocks strictly private per employee, and gave managers a live roster.

- **Privacy (already enforced, now covered by tests).** Every widget endpoint (`/active`,
  `/shift/active`, `start`, `stop`, `switch`, `clock-in`, `clock-out`, `shift/history`) is scoped to
  the signed-in employee's own id. `/entries` and `/timesheet` force non-managers to their own data
  (or return 403). An employee only ever sees and controls their own clock.
- **Manager roster.** New `GET /api/timeclock/roster` (manager-gated via `can_manage_payroll`; 403
  otherwise) lists everyone currently clocked in with their location, plus running job timers — each
  joined to employee + location names, org-scoped.
- **Live roster card.** The Timesheets ("Employee hours") page shows a manager-only "On the clock now"
  card at the top: who's at work + where, and running timers, ticking live. Hidden for non-managers.
- **Schema/DB wiring confirmed.** Attendance rides `shifts` (employee_id, location_id) and job time
  rides `time_entries` (org_id, employee_id, order/task, rates) — both correctly keyed.
- Gates: backend `test_clock_roster_privacy.py` (3/3: roster manager-only, worker sees only self,
  manager reads any timesheet) + existing clock suite (10/10); FE `clock_roster_144dn` + full sweep
  **96/96**. Verified live: roster returned "Admin @ Main" right after clock-in.

## build 144dm — time-clock moved to a topbar chip + expand popover

The global clock was a fixed bottom-right pod that covered page action buttons. It's now a compact chip
docked in the topbar; clicking it expands a popover with both clocks and their stop/switch/clock-in-out
buttons (closes on outside-click). Bottom-right corner is fully clear. Gates: `tc_topbar_chip_144dm`
+ full FE sweep; verified live in Chrome.

## build 144dl — Orderry-style Services & Products add dropdown (category tabs + grouping)

Reworked the inline add-line search on the work-order Services & Products tab to match Orderry's field.

- **Category tabs.** The dropdown now opens with an Orderry-style tab bar — **All items · Services ·
  Products · Bundles** — pinned to the top. The active tab drives the catalog search `type`, so each tab
  narrows results to that kind. Placeholder updated to "Name, barcode, code, SKU…".
- **Grouped results.** Matches are grouped under their catalog **category** headers (falls back to
  "Uncategorized"), with an empty-state line and the "Add … as a custom line" free-text fallback preserved.
- **Opens on focus** to browse without typing; closes on outside click.
- **Positioning fix.** The animated tab panel keeps a persistent `transform` (from `page-in … both`),
  which makes it the containing block for `position:fixed`. The dropdown now offsets against that
  containing block (falling back to the viewport), so it lands directly under the input instead of
  off-screen.
- Gates: `wo_add_tabs_144dl.test.js` + full FE sweep **94/94**; verified live in Chrome (tabs render,
  box aligned under the field). Roll-over-proofed two pinned build-tag/regex asserts in 144dj/144dk.

## [1.1.0] — build 144cn: "Edit layout" — drag anything, anywhere (in the real app)

Ported the approved drag-anywhere demo into RepairOS itself.

- **Free-form layout editor.** A floating **Edit layout** button enters edit mode; hold and drag any
  piece of chrome — sidebar nav items, the user pod, topbar buttons, and every page action button —
  to any x/y. **Reset** restores defaults; **Done** exits and locks it.
- **Per-user + persistent.** Offsets save to localStorage and `/api/prefs/layout`, and re-apply after
  every page render (hooked into `setContent` / `_renderPageToolbar`) so a custom layout survives
  navigation and reloads. One teammate's arrangement never affects anyone else.
- **New** `layout-editor.js`; elements keyed by a stable id (nav→data-page, user-pod, or button text)
  stored on `data-lx`. Prefs router now allows the `layout` key alongside `lang`.
- Gates: functional jsdom `layout_editor_144cn.test.js` (12/12 — real drag moves an element to a free
  x/y, persists, re-applies on render, resets); prefs BE 5/5; ruff clean; FE sweep 76/77 (only
  pre-existing `ux_overhaul`); no schema change.
- Note: this is the mechanical half; the premium visual reskin is the next build, pending your sign-off
  on the demo's look.

## [1.1.0] — build 144cm: deterministic location order (fixes flaky transfer) + release hygiene

Fixes a real bug an external reviewer flagged, and cleans the release package.

- **Bug: transfers could 409 "Only 0 at the source warehouse."** Root cause: `/api/staff/locations`
  ordered rows by random UUID (`Location.id`), so the "first" location shifted once a second one
  existed — stock adjusted at one location, the transfer shipped from another. Added
  `Location.created_at` (migration `o5q7s9u1w3y5`) and ordered listings by it, so the primary
  "Main location" is always first. `test_transfer_credits_only_what_arrived` now passes in the
  mixed-run context that previously reproduced the failure (34/34 in that pair; 51/51 across the
  location-dependent suites).
- (The other test the reviewer cited, `test_rule_location_scoping`, already passed on this line of
  builds — location scoping was fixed earlier; no change needed.)
- **Release hygiene:** the packaged tarball now excludes `.env` (was leaking a dev `SECRET_KEY`),
  `*.db` / `*.db-journal`, `.venv`, `.pytest_cache`, `.ruff_cache`, and `node_modules`.
- Gates: single migration head `o5q7s9u1w3y5`; ruff clean; targeted suites green; FE 75/76 (only
  pre-existing `ux_overhaul`).

## [1.1.0] — build 144cl: seed a region-appropriate default tax at signup

Decided against a full "per-jurisdiction tax" sprint — the app already does configurable
per-shop tax (named rates, inclusive/exclusive, VAT code, per-location). This is the one cheap,
high-value slice: a new shop no longer starts with an empty tax table.

- On signup, `register()` seeds one **default Tax** based on region/country: EU/Gulf VAT as
  price-**inclusive** (DE 19, FR 20, ES/IVA 21, GB 20, AE 5, KSA 15…), US/Canada sales tax as
  **exclusive** (US 0% placeholder, CA GST 5%). Country overrides region when provided; "Other"
  seeds nothing. Owner can edit or disable it in Settings.
- Gates: BE `test_build144cl_tax_seed.py` (5/5); ruff clean; app imports; single migration head
  unchanged (`n4p6r8t0v2x4`, no schema change); FE sweep 75/76 (only pre-existing `ux_overhaul`).

## [1.1.0] — build 144ck: Spanish dictionary expanded to full coverage

Walked every page file and translated the visible UI, taking the Spanish catalogue to **400
phrases**.

- Extracted real strings across all pages — field labels, table headers, buttons/actions,
  section & tab names, toast messages, weekdays — and translated them accurately.
- Live DOM translation verified: rendered button text, span text, and input placeholders swap
  to Spanish when the language is set; English stays a pass-through.
- Gates: functional jsdom `i18n_coverage_144ck.test.js` (11/11, incl. real DOM swaps);
  dictionary parses (400 entries); FE sweep 75/76 (only pre-existing `ux_overhaul`); no backend change.

## [1.1.0] — build 144cj: language switch moves into the per-user profile menu

The language toggle now lives where each person expects it — their own profile — and only
changes the language for *them*, never the whole team.

- **Profile menu** opens from the sidebar user pod (avatar/name). It shows the person's name &
  role, **My payroll** (their own payroll statement PDF), a **Language** switch, and **Sign out**.
- The language switch is per-user (localStorage + `/api/prefs/lang`) — one teammate choosing
  Español does not affect anyone else. Removed the shared topbar switcher.
- Gates: functional jsdom `profile_menu_144cj.test.js` (13/13, renders the menu + language
  `<select>`); FE sweep 74/75 (only pre-existing `ux_overhaul`); no backend change.

## [1.1.0] — build 144ci: internationalisation — Spanish, RTL-ready (SaaS Sprint 3)

The app speaks Spanish. First slice of Sprint 3 (market-fit).

- **i18n engine** (`i18n.js`): a dictionary-driven DOM translator. After every render — and for
  any dynamically-added node (modals, toasts, drawers) via a MutationObserver — it swaps whole
  text nodes and `placeholder`/`title`/`aria-label`/`alt` attributes from an English→target
  phrase dictionary. No per-file string extraction needed; a missed phrase is a one-line add.
- **Language switcher** in the topbar (English · Español); choice persists in localStorage and
  server-side via `/api/prefs/lang`, so it follows the user across devices. Switching reloads for
  a guaranteed-clean state in either direction.
- **Spanish catalogue** covering navigation, actions, statuses, entity/field labels, and the auth
  &amp; onboarding flows.
- **Locale-aware formatting**: `fmt` now groups numbers/dates by the active locale (es-ES vs en-US).
- **RTL-ready**: selecting a right-to-left language sets `<html dir="rtl">`, mirrored by a new
  `[dir=rtl]` CSS block. Arabic is scaffolded (`LANGS.ar`, dir rtl) — enable later by adding its
  catalogue only, no code changes.
- Gates: functional jsdom translation test + wiring `i18n_144ci.test.js` (16/16); BE
  `test_build144ci_lang_pref.py` (3/3) + prefs regression; ruff clean; app imports; FE sweep
  73/74 (only pre-existing `ux_overhaul`).

## [1.1.0] — build 144ch: instant suspension + currency symbols + prod-cred hardening

Post-review cleanup of the SaaS block.

- **Instant suspension.** Suspending an account (or setting a term end in the past) from the
  `/admin` portal now bumps every employee's token version, so **live sessions are booted
  immediately** instead of lasting until token expiry. Non-blocking edits (e.g. status → active)
  don't disturb sessions.
- **Currency symbols.** Signup now maps ISO codes to display symbols (GBP → £, EUR → €, JPY → ¥,
  AED → AED, …), falling back to the code for anything unknown — no more bare "GBP" where "£" belongs.
- **Production credential safety** (folded in during review): the platform-admin seed refuses to
  create a default `change-me-please` account in production; it only seeds when
  `PLATFORM_ADMIN_EMAIL` / `PLATFORM_ADMIN_PASSWORD` are set. A dev default remains for local use.
- Gates: BE `test_build144ch_hardening.py` (5/5) + regression (25 passed); ruff clean; no schema
  change (logic only); FE sweep 72/73 (only pre-existing `ux_overhaul`).

## [1.1.0] — build 144cg: Orderry-style signup onboarding (SaaS Sprint 2 tail)

New clients now self-onboard with region, trade, and company details — no more Kolkata/₹ defaults.

- **Signup screen** (tenant app): "Create an account" on the sign-in card opens a form with
  company name, your name, email, password, **business type** (industry template), **region**,
  currency, **team size**, and phone.
- **Region presets.** Picking US / Canada / Europe / Middle East / Other auto-fills currency,
  timezone and language (editable); an explicit currency overrides the preset.
- **Backend.** `RegisterRequest` extended (all optional, backward-compatible); new public
  `GET /api/auth/signup-options` (templates + regions + brackets); register applies the choices
  and normalises the industry template. New `organisations.region` / `employee_count` columns
  (migration `n4p6r8t0v2x4`, single head).
- Gates: BE `test_build144cg_signup.py` (4/4) + Sprint-1/2 regression (36 passed); ruff clean;
  migration fresh-applies to single head `n4p6r8t0v2x4`; FE sweep 72/73 (only pre-existing
  `ux_overhaul`), new `signup_144cg.test.js` 9/9.

## [1.1.0] — build 144cf: operator portal UI at /admin (SaaS Sprint 2)

The separate portal you asked for — its own page, its own login, outside every tenant.

- **`/admin` portal** (`frontend/admin.html`, served by a dedicated route). Own login using
  the platform-admin credentials; token kept under a separate `ros_admin_token` key so it
  never mixes with a tenant session.
- **Lifecycle dashboard.** Six clickable segment cards — **New · Trial · Paid · Lost trials ·
  Lost paid · Suspended** — each filtering the client table below. Search by name.
- **Client drawer.** Per client: edit plan, status, term start/end, discount %, apply a coupon
  code, and private operator notes; Suspend button. An "expires soon" flag shows on any term
  ending within 14 days.
- **Team access windows.** Inside each client, every employee has editable access-from /
  access-until dates and an active toggle — saved per user.
- **Coupons panel.** Generate discount codes (auto-code if blank, optional plan scope + expiry)
  and see existing ones.
- All views call the 144ce platform API; enforcement (suspended / expired / out-of-window →
  blocked tenant login) was shipped in 144ce.
- Gates: new `admin_portal_144cf.test.js` (13/13); `/admin` route verified to serve; ruff clean;
  BE smoke + platform tests (16 passed); FE sweep 71/72 (only pre-existing `ux_overhaul`).
- Deferred to a follow-up: region-aware signup onboarding (currency/timezone/language picker).

## [1.1.0] — build 144ce: platform-admin backend + subscription lifecycle (SaaS Sprint 2)

The operator side of the SaaS — a separate super-admin tier, completely outside any
tenant. (Portal UI ships next in 144cf.)

- **Separate admin auth.** New `PlatformAdmin` table + `/api/admin/login` issuing a
  `scope="platform"` token; `get_current_platform_admin` rejects any tenant token. A
  bootstrap admin is seeded on startup from `PLATFORM_ADMIN_EMAIL` / `PLATFORM_ADMIN_PASSWORD`.
- **Lifecycle segments.** `organisations` gains `sub_status` (trial/active/trial_lost/churned/
  suspended), `sub_start`, `sub_end`, `discount_pct`, `admin_notes`. The portal buckets these
  into **new · trial · paid · lost trials · lost paid · suspended** ("new" = trial started ≤14d).
  Signup now stamps `sub_status=trial`, `sub_start=today`.
- **Term + per-employee windows.** `PATCH /api/admin/tenants/{id}` sets plan/status/term/discount/
  notes; `PATCH …/employees/{id}` sets per-user `access_start`/`access_end` (new columns).
- **Enforcement.** Tenant login is blocked when the org is **suspended**, past its **sub_end**,
  or the employee is outside their access window (403 with a clear message).
- **Discounts + coupons.** `GET/POST /api/admin/coupons` generate codes (auto-code if omitted,
  optional plan scope); `POST …/apply-coupon` sets a tenant's `discount_pct` (respects plan scope).
- Endpoints: `/api/admin/stats`, `/tenants`, `/tenants/{id}`, employee window, coupons.
  New tables `platform_admins`, `platform_coupons` (migration `m3o5q7s9u1w3`, single head).
- Gates: BE `test_build144ce_platform_admin.py` (9/9) + Sprint-1/2 regression (32 passed); ruff
  clean; migration fresh-applies to single head `m3o5q7s9u1w3`; FE sweep 70/71 (only pre-existing
  `ux_overhaul`).

## [1.1.0] — build 144cd: mandatory 2FA for everyone (SaaS Sprint 1)

Org-enforced two-factor authentication (TOTP) — free, no SMS. Completes Sprint 1.

- **Org policy** `require_mfa` (new `organisations.require_mfa`, migration `l2n4p6r8t0v2`,
  defaults ON). Toggle in Settings → General → Security.
- **Forced enrollment.** Login returns `mfa_enrollment_required` when the org mandates 2FA
  and the user hasn't set it up; `showApp()` opens a non-dismissable enrollment flow
  (`MfaGate`): scan/enter the authenticator key → verify a 6-digit code → save one-time
  recovery codes. `/me` now returns `mfa_required` + `mfa_enabled`.
- **Can't opt out.** `/api/mfa/disable` is blocked while the org requires 2FA.
- **Admin unlock.** `POST /api/staff/employees/{id}/reset-mfa` clears a teammate's 2FA and
  forces re-login (lost-device recovery); surfaced as "Reset 2FA" on the employee profile.
- Reuses the existing self-contained RFC-6238 TOTP implementation — zero third-party cost.
- Gates: BE `test_build144cd_mandatory_mfa.py` (5/5) + Sprint-1 regression (23 passed); ruff
  clean; migration fresh-applies to single head `l2n4p6r8t0v2`; FE sweep 70/71 (only
  pre-existing `ux_overhaul`), new `mandatory_mfa_144cd.test.js` 10/10.

## [1.1.0] — build 144cc: teammate invite by email (SaaS Sprint 1)

Admins invite teammates by email; the invitee sets their own password — no more
typing passwords for people.

- **Invite flow.** Settings → Employees → "Invite teammate" (`POST /api/staff/invites`,
  email + name + role) emails a 7-day single-use link. Pending invites list with
  resend + revoke (`GET` / `DELETE /api/staff/invites`). Blocks emails already on the
  team or already invited (re-inviting just refreshes the link).
- **Accept flow (logged out).** `/accept-invite?token=…` deep link → `POST
  /api/auth/invite/preview` pre-fills who invited them, `POST /api/auth/invite/accept`
  creates the Employee with the chosen password (min 8), marks them verified, and returns
  a session so they land straight in the app.
- **New model + table** `Invite` / `invites` (migration `k1m3o5q7s9u1`, single head). Only
  the token hash is stored; single-use + time-boxed, mirroring 144cb's token design.
- **Frontend** — invite modal + pending-invites card in Settings → Employees; accept card
  added to the sign-in screen via `auth-flows.js`.
- Gates: BE `test_build144cc_invites.py` (5/5) + smoke + cb (18 passed); ruff clean;
  migration fresh-applies to single head `k1m3o5q7s9u1`; FE sweep 69/70 (only pre-existing
  `ux_overhaul`), new `invites_144cc.test.js` 11/11.

## [1.1.0] — build 144cb: password reset + email verification (SaaS Sprint 1)

First slice of the multi-tenant launch work: self-service account recovery so B2B
customers never need you in the database to get back in.

- **Password reset (logged out).** `POST /api/auth/password/forgot` (silent — never
  reveals whether an email is registered, rate-limited 5/15min) emails a one-hour,
  single-use link; `POST /api/auth/password/reset` sets the new password, verifies the
  email, and bumps `token_version` so every existing session is logged out.
- **Email verification.** Signup now emails a 48-hour verification link; `POST
  /api/auth/email/verify` confirms it and `POST /api/auth/email/resend` re-sends. `/me`
  now returns `email_verified` + `mfa_enabled` for the UI.
- **Per-profile reset.** Settings → Employees → Password now offers "Email a reset link"
  (`POST /api/staff/employees/{id}/send-reset`) so admins can let teammates set their own.
- **New model + table** `AuthToken` / `auth_tokens` (migration `j0l2n4p6r8t0`, single head).
  Only the SHA-256 hash of each token is stored; tokens are single-use and time-boxed.
  New `email_verified` column on employees (defaults false, never locks out existing users).
- **New service** `services/tokens.py` (issue / redeem / invalidate) and
  `notifications.send_direct_email` reusing the SMTP-or-simulated adapter seam.
- **Frontend** `auth-flows.js` — forgot / reset / verify cards on the sign-in screen;
  `/reset-password` and `/verify-email` deep links intercepted before login.
- Gates: BE `test_build144cb_auth_flows.py` (6/6) + smoke + prefs (18 passed); ruff clean;
  migration fresh-applies to single head `j0l2n4p6r8t0`; FE sweep 68/69 (only pre-existing
  `ux_overhaul`), new `auth_flows_144cb.test.js` 12/12.

## [1.1.0] — build 144ca: server-side saved views (per-user UI prefs)

- **List column layouts, sort, and saved views now follow the user across devices.** They were
  browser-only (localStorage); now they persist server-side and sync on every change.
- **New model + table** — `UserPref` / `user_prefs` (migration `i9k1m3o5q7s9`, single head):
  `(employee_id, pref_key)` unique, JSON `value`. Scoped to the current employee.
- **New router** `/api/prefs/{pref_key}` (GET + PUT), auth-gated via `get_current_employee`. Keys are
  allowlisted to the `listview:` prefix so the store can't be used as an arbitrary KV bucket.
- **Frontend** — `list-extras.js` pulls `GET /api/prefs/listview:<entity>` once per session on
  `attach` (merged into the localStorage cache, which stays as the instant-render / offline layer),
  and fire-and-forgets `PUT` on every column/sort/view change. Offline edits keep working locally.
- Gates: BE `test_build144ca_user_prefs.py` (5/5) + smoke (12 passed total); ruff clean; migration
  fresh-applies to single head `i9k1m3o5q7s9` (table + unique index verified); FE sweep 67/68 (only
  pre-existing `ux_overhaul`), new `list_extras_sync_144ca.test.js` 9/9.

## [1.1.0] — build 144bz: hardening (booking race, rate limiting) + emoji cleanup

- **R1 — booking concurrent-create race closed.** New partial unique index
  `uq_booking_emp_slot` on `(org_id, employee_id, scheduled_for)` where `status='booked'`
  (migration `h8j0l2n4p6r8`, single head). Both create paths catch the `IntegrityError` and return a
  clean **409** ("that time slot was just taken") instead of a double-book / 500. (Partial-overlap of
  different durations stays app-guarded.)
- **R2 — rate limiting.** New in-process sliding-window limiter (`services/ratelimit.py`): `/auth/token`
  is capped at 10 tries / 5 min per IP+username, public booking create at 8 / 5 min per IP+slug —
  both return **429** with `Retry-After`. Swap the store for Redis to scale horizontally.
- **Design cleanup** — the last residual emoji replaced with `icon()` SVGs / typographic glyphs
  (Documents rename/delete, payroll remove, the AI-drafted note). App is now emoji-free per the rule.
- Gates: BE `test_build144bz_hardening.py` (3/3) + booking regression (17 passed); ruff clean; app
  imports; migration fresh-applies to single head; FE sweep (only pre-existing `ux_overhaul`).

## [1.1.0] — build 144by: Services + Inventory toolbar order

- **Services**: **+ Service** now sits to the left of the search box; the **Taxes** button is removed
  (taxes live in Settings → Finance); **Manage categories** is now a single **flash-gear** icon on the
  far right (the category rail's own Manage link stays too).
- **Inventory · Stock**: **+ Product** moved to the front of the filter row — before Warehouse /
  Availability / Category / Search — with the Actions menu on the right.
- FE-only, no schema. Gates: FE `toolbar_polish_144by.test.js` (7/7) + full sweep (only pre-existing
  `ux_overhaul`); app imports.

## [1.1.0] — build 144bx: toolbar polish across the app

- **Contacts**: one clean row — **+ New contact** then the All / Clients / Suppliers tabs on the left;
  on the right a resized search box, a **flash/gear** icon (columns & views), and a single **Import /
  Export** menu. The old "Columns" text button is gone.
- **Assets**: same one-row layout — **+ Add asset** + owner tabs (All / Company-owned / Client-owned),
  search + flash-gear on the right.
- **Products**: the columns control is now a flash-gear icon next to the search (removed the duplicate
  "Columns & views" menu item).
- **Work orders**: removed the old, broken second column switcher; the single **col-gear** in the
  search row is the one working column picker (it now applies via the `hide-*` classes correctly).
- **Reports**: charts were stretching hugely — capped to a sensible height so every report reads well.
- **Settings**: sub-page actions (+ Employee, + API key, + Directory, …) no longer sit stranded on the
  right — they render in a left-aligned toolbar at the top of the pane, matching every other tab.
- New shared `.list-toolbar` + `.ic-btn` (flash-gear) styles. FE-only, no schema. Gates: FE
  `list_polish_144bx.test.js` (9/9) + full sweep (only pre-existing `ux_overhaul`); app imports.

## [1.1.0] — build 144bw: Work-orders action layout, app-wide (clean top bar)

- **Every tab now reads like the Work Orders page.** The stranded top-right primary buttons
  (+ New contact, + New invoice, + Add asset, + New task, + New booking, + New posting/PO, …) are
  gone from the corner. The top-right keeps only **view switches** (Table / Board / Scheduler) and
  the existing search + location; everything else — the primary **＋ create**, secondary buttons and
  **Filter** — is hoisted into a left-aligned **toolbar row above the table**.
- One shell change does it for all pages: `setTopbarActions` splits its buttons — a `.view-toggle`
  stays in the corner, the rest render into a `.app-toolbar` at the top of the page content;
  `setContent` re-renders it, and navigation resets it (no stale toolbars). Work Orders is untouched
  (it already had this layout); pages with only a view switch keep a clean corner.
- **Cash / Finance money buttons** are grouped into one colour-coded segment (green Income / red
  Expense / Transfer); "+ Account" is the primary.
- FE-only, no schema. Gates: FE `toolbar_layout_144bw.test.js` (11/11, jsdom — corner vs. toolbar
  hoist, view-switch stays, call-order independence, nav reset) + full sweep (only pre-existing
  `ux_overhaul`); app imports.

## [1.1.0] — build 144bv: custom-field columns + sort + saved views (Contacts / Products / Assets)

- **Add custom-field columns to any list.** A **Columns** control on Contacts, Products and Assets
  opens a "Columns & views" panel: tick which custom fields to show as extra columns. They appear in
  the table immediately, populated from each row's `custom_data`.
- **Sort by any column.** Click a header (built-in or custom) to sort; click again to flip direction;
  a caret shows the active sort. Numeric columns sort numerically, text alphabetically. The choice is
  remembered per list.
- **Saved views.** Name the current combination of visible columns + sort and recall it in one click;
  delete views you no longer need. Stored per list in the browser.
- Built as one shared module (`list-extras.js`, `ListExtras`) that **augments** the table each page
  already renders — pages only tag rows with `data-rid`/`data-id` and call `ListExtras.attach(...)`,
  so existing columns, selection, and actions are untouched. Assets now return `custom_data` (device
  custom fields); Contacts/Products already did.
- FE + one serializer field, no schema/migration. Gates: FE `list_columns_144bv.test.js` (jsdom:
  attach injects columns, header sort reorders rows, saved views persist) + full sweep (only
  pre-existing `ux_overhaul`); app imports; ruff clean on gate scope.

_(The toolbar-arranger idea explored earlier was scrapped before shipping.)_

## [1.1.0] — build 144bu: service categories (matches products & bundles)

- **Services now use the shared category tree**, exactly like My company → Products and Bundles.
  New `Service.category_id` FK (migration `g7i9k1m3o5q7`, single head); the legacy free-text
  `category` string is kept for back-compat. The `service` category tree already existed — now it's
  actually wired: `_category_counts` counts services, `/catalog/services` filters by `category_id`
  and returns `category_id` + `category_name`.
- **Services page rebuilt to the Products layout**: a collapsible category rail on the left
  (`CategoryTree` entity="service"), search + "＋ Service" on the toolbar, a **Manage categories**
  button, a category **select** in the form (replacing the free-text box) with an inline "＋" to add a
  category on the fly, and a category badge in each row — buttons in the same places as Products.
- **Booking wizard groups services by category.** The public page's config exposes each service's
  category name; step 1 shows All / <category> chips that filter the service list — so customers see
  categories instead of one long list.
- Shared `CategoryTree` / `CategoryManager` now say "All services" / "Service" for the service entity.
- Gates: BE `test_build144bu_service_category.py` (4/4) + booking regression; FE
  `service_categories_144bu.test.js` (11/11) + full sweep (only pre-existing `ux_overhaul`); ruff clean
  on gate scope; migration fresh-applies to single head; app imports.

## [1.1.0] — build 144bs + 144bt: booking confirmations wired + detail drawer

- **Confirmations & change notifications now actually fire.** The booking notification engine
  (`NotificationRule` module="booking", `evaluate_booking`, delivery adapters, `NotificationLog`,
  reminders) already existed but nothing invoked it on writes. Now every write path calls it:
  public online booking + internal create → `created`; drag/drawer reschedule → `changed`; cancel /
  no-show / done → `status_changed`. Messages are event-aware (confirm / moved / cancelled / missed).
  Nothing sends unless the shop has a booking rule (Settings → Notifications) — no rule = no-op, and
  the call is wrapped so it can never break the booking request. Reminders keep their existing
  queue-reminders / run-due endpoints.
- **Booking detail drawer (bt).** Clicking a timeline block now opens a right drawer (not a tiny
  modal): status pill, inline **reschedule** (date / time / technician / duration → one PATCH),
  **→ Work order** (hidden once an order exists), No-show / Cancel, and a note that the client is
  auto-notified when a rule is set. Drag-to-reschedule on the grid is unchanged.
- FE + service wiring only, no schema. Gates: BE `test_build144bs_booking_notify.py` (4/4) + booking
  regression; FE `booking_notify_drawer_144bs.test.js` (10/10) + full sweep (only pre-existing
  `ux_overhaul`); ruff clean; app imports.

## [1.1.0] — build 144br: booking availability is timezone-correct (tech-debt fix)

- **Fixes a real timezone bug** flagged in the sprint plan. Working hours and stored bookings are the
  location's local wall-clock, but the availability engine compared them against `datetime.utcnow()`.
  For any non-UTC shop that meant "now", the lead time, and past-slot filtering were off by the
  location's UTC offset — e.g. an India (UTC+5:30) shop could be offered slots that had already passed,
  or have "1 hour ahead" measured against the wrong clock.
- New pure helper `_local_now(tz)` returns naive local now in the location's timezone (falls back to
  UTC on an unknown tz). `/slots`, `/days` and the create endpoint now resolve the location first and
  evaluate lead time, the booking window, and past-day checks in that timezone.
- No storage change — bookings stay naive local wall-clock, so no migration and no data rewrite; this
  only corrects the comparison clock. UTC shops are unaffected (all prior tests pass unchanged).
- Gates: BE `test_build144br_timezone.py` (4/4, incl. a fixed +5:30 offset assertion) + booking/
  availability regression; ruff clean on gate scope; app imports; FE sweep unchanged.

## [1.1.0] — build 144bq: internal scheduler timeline

- **Bookings gains a Timeline view** (List | Timeline toggle). Technicians are rows, the day runs
  across the top; each appointment is a positioned block coloured by status.
- **Drag to manage.** Drag a block sideways to re-time it (snaps to 15 min), or drop it on another
  technician's row to reassign — both go through `PATCH /api/bookings/{id}`, which now accepts
  `scheduled_for` / `employee_id` and re-runs the same no-double-booking guard as create (409 on a
  clash, with a toast and revert). A plain click opens a mini action sheet: No-show / Cancel /
  → Work order.
- Status-only PATCH is unchanged; an empty PATCH is now a clean 400.
- New timeline CSS is namespaced `bkt-*` so it can't collide with the existing work-order scheduler's
  `sch-*` styles.
- FE-only + one endpoint extension, no schema. Gates: BE `test_build144bq_reschedule.py` (4/4) +
  booking regression (28 passed); FE `scheduler_timeline_144bq.test.js` (10/10) + full sweep (only
  pre-existing `ux_overhaul`); ruff clean; app imports.

## [1.1.0] — build 144bp: themed client booking wizard

- **The booking page is now a 4-step wizard** (Service → Time → Details → Confirm) with a progress
  bar, Back/Continue, and a confirmation summary — no more one long scroll.
- **Four selectable skins.** Settings → Public pages → Booking theme picks 1 Premium glass · 2 Bold
  minimal · 3 Warm friendly · 4 Editorial (`booking_theme`), each a full type + colour treatment;
  theme 1 also tints to the shop's brand colour. Logo shows in the header.
- **Calendar, not a lone date field.** A horizontal day strip is built from `/booking/{slug}/days`
  with per-day free-slot counts and greyed-out closed days, plus "Earliest" / "Next week" jump chips —
  so clients reach tomorrow, next week or next month in one tap instead of doomscrolling.
- Slots and availability now pass the chosen `service_id` (so duration is respected). Create posts the
  same contract as before — the 144bo server-side re-validation is unchanged.
- FE-only, no schema. Gates: FE `booking_wizard_144bp.test.js` (11/11) + full sweep (only pre-existing
  `ux_overhaul`); public/booking BE regression (24 passed); no emoji on the public page.

## [1.1.0] — build 144bo: booking availability engine

- **Public slots are now computed, not hardcoded.** `/booking/{slug}/slots` builds each day's
  openings from the location's `working_hours` for that weekday (closed → no slots), stepped by the
  org slot size, minus windows already taken by bookings (and scheduled work orders when
  "consider work orders" is on). It honours the **lead time** (earliest bookable) and the **booking
  window** (how far ahead), and respects **capacity**: with double-booking off, one appointment per
  overlapping window per location; an employee is never double-booked either way.
- **New `/booking/{slug}/days`** returns per-day open/closed + free-slot counts across a range, so the
  client calendar can jump to tomorrow / next week / next month without endless scrolling.
- **Race-safe create.** `POST /booking/{slug}` re-validates lead time, window, the location's opening
  hours, and capacity on the server before writing — the client's chosen slot is never trusted.
- **Org policy fields** `booking_lead_min`, `booking_window_days`, `booking_theme` (migration
  `f6h8j0l2n4p6`, single head). Settings → Public pages → Booking gains **lead-time** and
  **book-up-to** pickers; the "allow multiple" toggle is relabelled as double-booking.
- Gates: BE `test_build144bo_availability.py` (6/6) + public/schedule regression (18 passed); FE
  `booking_engine_144bo.test.js` (6/6) + full sweep (only pre-existing `ux_overhaul`); ruff clean on
  gate scope; app imports; migration fresh-applies to single head.

## [1.1.0] — build 144bn: per-location working-hours editor

- **Set working hours per location** — Settings → Locations → edit a location now has a full
  7-weekday grid (Mon–Sun). Each day gets a start time, an end time, and a **Closed** toggle that
  disables the time inputs. Saved to `Location.working_hours` via the existing
  `PUT /api/staff/locations/{id}`. The locations list shows an "N day(s) open" summary column.
- This is the data source for the booking availability engine (next builds): a slot is only offered
  when it falls inside its location's open window for that weekday.
- FE-only, no schema/migration (backend already accepted `working_hours`). Gates: FE
  `location_hours_144bn.test.js` (7/7) + full sweep (only pre-existing `ux_overhaul`); ruff clean;
  app imports; no emoji.

## [1.1.0] — build 144bm: drag-to-resize columns + column gear (icon, top-right)

- **Resize any list column with the mouse** — drag the border between headers; widths persist per
  list in the browser. New shared `col-tools.js` (`ColTools.resizable`), wired into all four lists:
  Workflows, Contacts, Products, Assets.
- **Column picker is now an icon-only gear** at the top-right of the Workflows toolbar (Orderry
  placement), replacing the "Columns" text button. Toggle behaviour unchanged.
- FE-only, no schema. Gates: FE `col_resize_144bm.test.js` (7/7) + full sweep (54/55, only
  pre-existing `ux_overhaul`); app imports; no emoji.
- Follow-up (144bn): the custom-field column picker on Contacts / Products / Assets (each list has a
  different row render), plus sort-by-header — this ship delivers resize app-wide and the gear on
  Workflows.

## [1.1.0] — build 144bl: commission on approved recommendations

Closes the upsell → payroll loop. When a customer approves a recommendation, the created order
line is now attributed to **whoever recommended it** (`OrderLine.technician_id = rec.author_id`),
so the existing per-line commission engine pays that person when the order closes — no new payroll
code. The recommendation card also shows the recommender's name.

- Backend: one-line attribution in `approve_recommendation`; commission flows through the current
  engine (line 217 already keys commission on `line.technician_id`). No schema change.
- Gates: BE `test_build144bl_rec_commission.py` (1/1) + recs/reply/linked regression (5 passed);
  FE `rec_commission_144bl.test.js` (3/3) + full sweep (53/54, only pre-existing `ux_overhaul`);
  ruff clean; app imports; migration head unchanged. No emoji.

## [1.1.0] — build 144bk: store price vs repair price

The repair-shop-specific pricing gap: a product can carry two prices — the over-the-counter
**store price** and the **repair price** billed when it's used on a work order.

- New `Part.repair_price` column (migration `e5b7d9f1a3c5`); `0` falls back to the store price, so
  nothing changes until a shop sets one.
- Product form gains a **Repair price** field ("on work orders") next to Store price; the products
  list shows the repair price under the store price when they differ.
- Catalog search returns `repair_price` (already falling back to store). The shared CatalogPicker gets
  a `repairPricing` flag — turned on only in work-order contexts (WO detail add-line + WO create), so a
  product added to a job bills at the repair price while the POS/counter sale keeps the store price.
- Gates: BE `test_build144bk_repair_price.py` (2/2 — persist, search exposes, fallback) + notes/PO/POS
  regression (15 passed, POS untouched); FE `store_repair_price_144bk.test.js` (6/6) + full sweep
  (52/53, only pre-existing `ux_overhaul`); ruff clean; app imports; migration single-head. No emoji.

## [1.1.0] — build 144bj: roadmap "Now" lane — asset history, WO notes, list columns

Three small Orderry-gap closers shipped together.

- **Prior-visits badge on the WO device card** — shows `N prior visits` and jumps to the asset's
  service history (which already listed a device's past work orders). `get_order` now returns
  `device.visit_count`. Repeat-repair legible at a glance.
- **Technician notes + Conclusion / client recommendations** free-text fields on the Services &
  Products tab, saved via `PATCH /api/orders/{id}/notes`. New `Order.tech_notes` + `conclusion`
  columns (migration `d4f6a8c0e2b4`). Orderry parity alongside our structured recommendations.
- **Configurable work-order list columns** — a Columns menu toggles Client / Device / Status /
  Priority / Total / Assigned / Created; the choice persists per browser (localStorage) and hides
  columns via CSS. Pure FE, no row-render changes.
- Gates: BE `test_build144bj_notes_visits.py` (2/2) + coupon/board/PO regression (12 passed); FE
  `wo_notes_visits_columns_144bj.test.js` (8/8) + full sweep (51/52, only pre-existing `ux_overhaul`);
  ruff clean; app imports; migration single-head. No emoji.

## [1.1.0] — build 144bi: returning-customer coupon loop

Closes the loop the earlier IA cleanup opened: from a work order you can now reach the
client, mint a coupon for them, and redeem it on their next visit.

- **Clickable client + device on the WO.** The General-info Client and Device cards are now
  links — client opens the contact profile, device opens the asset (were static text). The
  client card also shows **balance** inline (`owes us ₹X` / `in credit`).
- **Client-issued coupons.** `Coupon` gains a nullable `client_id` (migration `c3e5b7d9f1a3`).
  The client Loyalty tab gets **Generate coupon** (percent or fixed, optional minimum, single-use
  by default) — minted for that client with an auto code, offered now.
- **Client-scoped redeem.** On the return work order, Invoices &amp; payments → Redeem → Coupon lists
  *that customer's* coupons to apply in one click (manual code still works). Redeeming validates the
  coupon belongs to the order's client (403 otherwise) and respects its use limit. Applies order-level,
  unchanged — no invoice-level semantics (dropped: not the shop's model).
- **Amount-changed activity event.** Adding or removing a line now logs `Amount changed: X → Y`, so
  the Activity timeline shows totals move like Orderry's rail.
- Gates: BE `test_build144bi_client_coupon.py` (3/3 — cross-client redeem blocked, single-use,
  amount event, balance exposed) + messages/linked-rec regression (6 passed); FE
  `client_coupon_loop_144bi.test.js` (10/10) + full sweep (50/51, only pre-existing `ux_overhaul`);
  ruff clean; app imports; migration single-head. No emoji, glass/clay.

## [1.1.0] — build 144bh: work-order IA cleanup + Activity timeline

Money actions moved to where money lives, plus three Orderry-inspired learns. No schema change.

- **Money actions relocated (WS1).** The seven-button strip under Services & Products is gone.
  `Take payment`, `Take prepayment`, and a **Redeem** group (`Coupon / Gift card / Points`) now live on
  the **Invoices & payments** tab; `Print / document` collapses into the single header **Print** button;
  and **Currency** moves to the drawer header. Services & Products is now just lines + `+ Add`.
  Behavior unchanged — redemptions still adjust the order (true redeem-at-invoice is a later build).
  Coupons are still minted only from the client profile; the WO redeems.
- **Create-form throughput (WS4).** The new-work-order footer gains `Create & open` (creates then opens
  the record) and `Save & create another` (creates then reopens a fresh form).
- **Lines grouped by technician (WS2).** Services & Products lines now group under a per-technician
  subheader (single-tech orders stay flat). Ties into payroll/commission.
- **Activity tab (WS3).** "Files & feed" becomes **Activity**: an `All events / Comments` filter over the
  existing typed event stream, and before→after diff styling for change events (e.g. status
  `Ready → Done` renders muted-before → bold-after). Stays a tab — no separate rail.
- Gates: FE `wo_reorg_activity_144bh.test.js` (15/15) + full sweep (49/50, only pre-existing
  `ux_overhaul`); app imports; feed/board/linked-rec regression (6 passed); ruff clean; migration
  single-head (no schema). No emoji, glass/clay.

## [1.1.0] — build 144bg: work-order drawer + recommendations folded into Services & Products

Presentation + IA cleanup, no schema change.

- **Work orders open as a right-side drawer.** Both the detail view and the create form
  (`+ Order` / `+ Estimate`) now slide in from the right (`modal-side-wide`) instead of a centered
  modal — the same slide-over pattern as the product panel. Same content, tabs, feed, and timers.
- **Recommendations moved under Services & Products.** The standalone Recommendations tab is gone;
  the recommendations block now lives as a section inside the Services & Products tab (a recommendation
  is just a proposed line, so it belongs there). The customer approve/decline unread dot moved onto the
  Services & Products tab and clears when you open it — so staff still catch decisions.
- **Add a recommendation from the catalog or manually.** The add form gains a catalog search with
  `All / Inventory product / Service · labour` filters. Picking an item links it (`ref_id`) and prefills
  title, type, and price; on approval the created order line carries that `ref_id`, so it behaves like a
  normally-picked catalog line (real COGS). Manual entry still works as the fallback; cards show
  "linked to catalog" vs "manual".
- Gates: FE `wo_drawer_recs_subtab_144bg.test.js` (13/13) + updated 144bd/144be tests + full sweep
  (48/49, only pre-existing `ux_overhaul`); BE `test_build144bg_linked_rec.py` (ref_id survives approve)
  + recs/reply/profit regression (13 passed); ruff clean; app imports; migration single-head. No emoji.

## [1.1.0] — build 144bf: Job Profitability report

A new read-only report under Reports that finally answers "did this work order make money?"
— folding together three inputs you already capture but never combined.

- **Margin per closed work order** = revenue (`order.total`, post tax/discount) − parts cost
  (the FIFO/moving-average `cost_price` already stamped on each line at stock deduction) −
  labour cost (Σ `TimeEntry duration × snapshot cost_rate`, counting **both** timer and manual
  hours). Rolled up per location, with KPI cards (revenue, margin, margin %, jobs) and a
  per-WO table sorted by margin. Row click opens the work order.
- **Honest labour signal.** A work order with no logged hours (neither timer nor a manual entry)
  gets a "no clocked labour" flag, so a parts-only margin never silently reads as a full one.
  Manual hours are added from the existing Timesheets flow — no WO-page changes.
- **Reports only.** New page at route `profitability`, reached from a "Job profitability" button in
  the Reports toolbar — no new sidebar item, nothing added to the work-order modal. Date-range and
  location filters; gated by `can_view_cashflow` (it exposes cost data).
- No schema change — pure aggregation over `Order`, `OrderLine`, and `TimeEntry`. New endpoint
  `GET /api/reports/job-profitability`; new page `profitability.js`.
- Gates: BE `test_build144bf_profitability.py` (3/3 — margin incl. manual labour, no-labour flag,
  open-WO exclusion, finance-permission gate) + recs/reply regression (12 passed); FE
  `job_profitability_144bf.test.js` (11/11) + full sweep (47/48, only pre-existing `ux_overhaul`);
  ruff clean; app imports; migration single-head. No emoji, glass/clay.

## [1.1.0] — build 144be: staff reply, recommendation gates, correct approved-line math

Three follow-ups to 144bd, each with its own on/off control:

- **Approved-line tax/discount now matches the estimate (correctness fix).** When a customer
  approves a recommendation, the created `OrderLine` runs the same chain as a hand-added line —
  `_auto_apply_tax` (org default tax, tax-inclusive handling, product/category exceptions) then
  `_calc_line` — instead of a bare `qty × price`. Proven by a test asserting an approved line and
  an identical manual line get identical tax rate, mode, and total.
- **Publish is a separate permission.** Drafting a recommendation stays `can_recommend`; **publishing**
  to the customer now needs `can_publish_recommend`. Junior techs can suggest, only advisors send —
  the guard I flagged earlier. Both default-allow; the FE Publish buttons carry `data-perm` so they
  vanish for denied roles, and the whole Recommendations tab vanishes without `can_recommend`.
- **Staff reply to the customer over the public page.** A "Reply to the customer" composer in the WO
  Files &amp; feed tab posts a client-visible `staff_reply` message that shows on the track page and
  portal. Gated two ways: **Settings → Public pages → Customer actions → "Let staff reply to customers"**
  (`oe_allow_staff_reply`, default on) and the `can_reply_customer` role permission — turn either off and
  the reply box disappears and the endpoint returns 403.
- New migration `b2d4f6a8c0e2` (single head) adds `oe_allow_staff_reply`; order detail now exposes
  `allow_staff_reply`. Gates: BE `test_build144be_reply_and_gates.py` (3/3) + recs/toggles/categories
  regression (18 passed); FE `staff_reply_and_gates_144be.test.js` (10/10) + full sweep (46/47, only
  pre-existing `ux_overhaul`); ruff clean; app imports. No emoji, glass/clay, all connected.

## [1.1.0] — build 144bd: recommendations (upsell checklist the customer approves)

Suggest extra services or products on a work order; the customer approves single or
multiple items from their tracking page, and each approval flows straight into billing.

- **New WO tab "Recommendations"** (no new sidebar entry). Staff draft items with title,
  type, qty, price, a "why" note, and an optional photo. Items are **draft · internal**
  until **published** to the customer; a draft-vs-publish gate keeps half-baked upsells
  from leaking. Gated by the `can_recommend` role permission (default-allow — turn it off
  to make a role read-only here).
- **Customer approve surface** on the track page and portal: a checklist with per-item
  checkboxes, a live "N selected · total", **Approve selected**, and **Decline all**.
  Approving creates the matching `OrderLine` (service→labour, product→part, else fee) and
  recomputes the order total; declining just records the choice. Decided items show an
  Approved/Declined state back to the customer.
- **Notification dots**: every approve/decline bumps `rec_unread`, shown as a green dot on
  the board card, the list row, and the Recommendations tab — cleared when staff open the
  tab. Mirrors the 144ba customer-message dot.
- **Settings → Public pages → Customer actions** gains "Let customers approve recommended
  services" (`oe_allow_recommend`, default on). Off hides the checklist and blocks the
  decide endpoint (403); drafts stay staff-only regardless.
- New `Recommendation` model + migration `a1c3e5b7d9f1` (single head); `oe_allow_recommend`
  on organisations, `rec_unread` on orders. New router `recommendations.py`; public
  `/track|/portal …/recommendations/decide` endpoints reuse the shared approve seam.
- Gates: BE `test_build144bd_recommendations.py` + PO/category regression; FE
  `recommendations_144bd.test.js`; app imports; ruff clean; migration single-head. No emoji,
  glass/clay styling, all connected.

## [1.1.0] — build 144bc: security hardening

Four defensive fixes, no schema change:

- **IDOR fixed — PO receipt is org-scoped.** `POST /api/inventory/po/{id}/receive` no longer trusts the
  raw id; it resolves the PO through its supplier's org (same guard as GET/send), so one tenant can't
  receive another tenant's purchase order. Cross-org attempts now return 404.
- **Category cycle guard.** `update_category` walks the descendant set before a reparent and rejects
  moving a category under one of its own sub-categories (400), preventing an orphaned subtree.
- **Security response headers.** New `SecurityHeadersMiddleware` sets `X-Content-Type-Options`,
  `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`,
  and a `Content-Security-Policy` (self + inline for the SPA, cdnjs for scripts, `data:`/`blob:` images,
  `object-src 'none'`). Uses `setdefault` so per-route headers win.
- **Stored-XSS fixed in the category rail.** `category-tree.js` no longer interpolates user-controlled
  category names into HTML or inline handlers — names are escaped via `textContent` for display, and
  `rename()` takes only an id and looks the name up from the tree.
- Gates: BE `test_build144bc_security.py` (3/3) + PO/stocktake/category regression (35 passed);
  FE `security_hardening_144bc.test.js` (6/6) + full FE sweep (44/45, only pre-existing `ux_overhaul`);
  app imports; ruff clean; migration single-head (no new migration). No emoji.

## [1.1.0] — build 144bb: public-page feature toggles

You can now turn the customer-facing actions on or off. **Settings → Public pages → Order / Estimate
page** has a new **Customer actions** section:

- **Let customers approve / decline estimates online** — off hides the approve/sign buttons on the
  track page + portal and blocks the endpoint (403).
- **Let customers message the shop** — off hides the "Message the shop" box everywhere and blocks the
  endpoint (403).
- Both default **on**, so existing behaviour is unchanged. New org columns `oe_allow_approval` /
  `oe_allow_messages` (migration `f0b2d4e6a8c1`); the public track + portal payloads carry the flags so
  the UI hides the controls, and the endpoints enforce them server-side (defense in depth).
- BE `test_public_toggles.py` (3/3) + public/orders/staff sweep (93 passed); FE `public_toggles.test.js`
  (5/5); app imports; ruff gate clean; migration single-head. No emoji.

## [1.1.0] — build 144ba: customer messages → work-order feed + unread dots

Customers can now message the shop from the public pages, and it lands where staff work.

- **"Message the shop"** box on the **track page** and inside each order on the **customer portal**.
- Each message posts a **`client_message`** into that work order's **Feed &amp; events**, highlighted with a
  "Customer" tag and a blue card (reusing your feed system; text is HTML-escaped, XSS-safe).
- **Unread notification dot** with a count wherever you look: on the **board card**, the **list row**,
  and the **Files &amp; feed tab** — so you can spot a work order with a new message from any view.
- Opening the order's feed **marks it read** and clears the dot everywhere. New column
  `orders.client_msg_unread` (migration `e9a1c3d5f7b0`), surfaced by the board + list serializers.
- BE `test_client_messages.py` (2/2) + orders/feed/public/insights sweep (96 passed); FE
  `client_messages.test.js` (9/9); app imports; ruff gate clean; migration single-head; dark-mode
  styled. No emoji. (Staff reply-to-customer over portal/SMS is a follow-up.)

## [1.1.0] — build 144az: dark-mode surface fix

- **Fixed the grey blocks in dark mode.** Several newer product-form surfaces used hardcoded light
  backgrounds, so in dark mode they showed as pale grey slabs: the **image drop-zone**, the **Prices
  table**, the **option checkbox cards**, and the **placement / insights segmented pills**. Each now
  has a proper dark-theme surface (subtle white-on-dark tints + darker borders), matching the rest of
  the dark UI. Light mode is unchanged. FE `dark_mode_surfaces.test.js` (6/6); full suite green
  (pre-existing `ux_overhaul` excepted). CSS-only.

## [1.1.0] — build 144ay: Company Insights (build-your-own analytics)

Orderry-style Company Insights, the first genuinely new feature after the catalog work. Lives under
**Reports → Company Insights** (no new sidebar item).

- **Diagrams** — build a chart in two clicks: pick a **Subject** (closed orders / sales / payments), a
  **Metric** (quantity / amount / gross profit / average), and a **Group by** (order type, seller,
  payment method, location, status group, month, quarter). View as a bar chart or a sortable table.
- **Trends** — the same subjects/metrics plotted over time: last 30 days, 24 weeks, or 24 months, as a
  line or columns.
- **Saved cards** — add as many as you want; they persist (`insight_cards`, migration `d8f0a2c4e6b9`),
  reorder and delete. A period selector at the top applies to every card.
- Data is aggregated **live** from your existing orders, sales and payments — no new tracking. Gross
  profit uses sale total minus cost (excludes commissions, matching Orderry).
- New `insights` router + `InsightCard` model; renders through the existing Charts helper. BE
  `test_insights.py` (4/4) + the reports/catalog/product sweep (76 passed); FE `company_insights.test.js`
  (9/9); app imports; ruff gate clean; migration single-head. No emoji.
- Follow-ups (Orderry parity): per-card filters + segmentation, and custom-field grouping (checkbox /
  value-list fields from the Forms editor).

## [1.1.0] — build 144ax: collapsible category rail + list thumbnails

Based on Orderry's Product/Bundle Categories docs. Architected as one shared component so Products and Bundles stay identical.

- **New collapsible category rail (the approved "Idea 2")** on both **Products** and **Bundles**: a
  pinned gradient **All products / All bundles** row with a live total, parents that **collapse/expand**
  with a chevron (state remembered), per-node counts, **+ Category** / hover **+ sub-category**,
  **double-click to rename**, and **drag-to-reparent** — all Orderry mechanics. One `CategoryTree`
  component drives both pages.
- **Bundles now have categories** end to end: new `bundles.category_id` (migration `c7e9a1b3d5f8`),
  category filter, inline **+ Category** on the bundle form, and the tree counts **bundles** (not
  products) in each bundle category.
- **+ Product moved to the left with the search beside it** (left-aligned toolbar), per request.
- **Image fix:** the product form drop-zone content is now properly centred (it wasn't a centred flex)
  with a cleaner framed-photo icon.
- **Bug fix — pictures now show in lists:** small thumbnails in **Inventory stock**, **Assets** and
  **Products** lists, each with **hover-to-enlarge** (one shared `thumbImg` helper; inventory API now
  returns the product image). Muted placeholder when a row has no photo.
- New `image` icon. `CategoryManager` is now entity-aware (product/bundle). FE
  `category_tree_and_thumbs.test.js` (12/12) + updated product suites; BE `test_bundle_categories.py`
  plus the product/catalog/inventory suite (62 passed); app imports; ruff gate clean; migration
  single-head (`c7e9a1b3d5f8`). No emoji; glass/clay throughout.

## [1.1.0] — build 144aw: product form polish + toolbar placement

Design pass (premium-saas-design principles — 8px spacing grid, clear section hierarchy, roomier controls):

- **Roomier, less cramped form.** Fields sit on a consistent spacing scale with taller inputs, and the
  General tab is now grouped under labelled sections — **Product details · Barcodes · Options ·
  Pricing** — with hairline dividers, so the drawer reads as a structured form instead of a dense list.
- **Bigger, nicer media tile.** The image drop-zone is taller with a soft gradient fill, a new
  framed-photo icon (replacing the camera glyph that looked off), and a small "square photo looks
  best" hint — filling the previously empty left column.
- **Toggles in two columns** as tidy bordered cards, which fills the width and shortens the form.
- **Prices** rendered as a clean bordered table.
- **+ Product moved beside the search bar**, with the search + button **right-aligned** on the page
  (out of the top bar); Actions stays in the top bar.
- New `image` icon added. Frontend/CSS only — no schema or endpoint change. FE `products_rebuild`
  (17/17), `product_form_layout` (12/12), full suite green (pre-existing `ux_overhaul` excepted).
  No emoji.

## [1.1.0] — build 144av: product form rebuilt to match the mockup

The product form's General tab now matches the approved mockup, field-for-field:

- **Image drop-zone** on the left — "Drop image or browse / or take a photo" — that opens the shared
  take-photo/choose-file picker on click and also accepts **drag-and-drop** of an image.
- **Type + Category** on one row, with the **+ Category** button beside the category select (opens the
  add-category drawer). **Name**, then a **Description** field (new, saved).
- **Unit / Code / SKU** on one row (unit shown grouped, e.g. "Quantity › pcs").
- **Barcodes** as a button row — **+ Barcode**, **Generate barcode**, **Generate QR** — revealing the
  value + type inputs; generate calls the real code endpoints on a saved product.
- The Orderry **checkbox list**: Set dimensions & weight, Warranty to client, Expiration tracking,
  Enable serial-number tracking (one-way), Set default supplier — each revealing its fields.
- A **Prices** table (Cost / Sell).
- Brand and reorder-point (min stock) are preserved as hidden fields, so nothing is lost on save; the
  serialized flag is kept intact when editing a serialized product.
- New product fields: `parts.description` and `parts.default_supplier_id` (migration `b6d8f0a2c4e9`),
  wired through the product API. FE `product_form_layout.test.js` (12/12); products/custom/catalog
  suite 44/44; app imports; ruff gate clean; migration single-head; full FE green (pre-existing
  `ux_overhaul` excepted). No emoji; glass/clay throughout.

## [1.1.0] — build 144au: fix photo-blocks-create + Orderry-style product form

- **Fixed: taking a photo (or choosing a file) killed product creation.** The shared photo picker
  opened *without stacking*, so `Modal.open`'s `closeAll()` destroyed the product form underneath —
  after you snapped, there was no form left and **Create** did nothing. The picker (both the chooser
  and the live camera view) now **stacks over the caller's form**, so the form survives, the image
  lands in it, and Create works. This also fixes the same silent break for **Choose file** and for
  every other place the picker is used (asset photo, logo, work-order feed).
- **Product form now opens as a slide-in side panel** (the approved mockup / same feel as the
  Add-category drawer) instead of a centered box — a wider drawer that fits the two-column form and
  its tabs.
- **Orderry-placed "+ Product" button.** A prominent primary **+ Product** button now sits top-right
  (like Orderry's add button); the rest (manage categories, bins, import, export) stay under
  **Actions** beside it. Selecting rows still swaps in the bulk Actions menu.
- **Shorter search bar** on the Products page (capped width, tighter placeholder) so it no longer
  spans the whole table.
- Frontend/CSS only — no schema or endpoint change. FE `photo_input.test.js` (13/13) now asserts the
  stacking fix; `products_rebuild.test.js` (17/17) covers the button, side panel and search. Full FE
  suite green (pre-existing `ux_overhaul` harness excepted). No emoji.

## [1.1.0] — build 144at: per-field Required + "lock after creating"

- **Every custom field now has two switches** in the Forms editor: **Required field** and
  **Disable editing after creating** (Orderry parity). The field list shows `required` and `locked`
  tags at a glance.
- **Required is enforced on save.** Creating a product without a required custom value is rejected
  (422 with the field's name); the product form also blocks submit and points at the missing field.
  Partial edits that don't touch custom fields are still allowed, so existing products never break.
- **Locked fields freeze once set.** After a value is captured on create, the input renders disabled
  on edit and the server ignores any attempt to change it — the stored value is kept.
- Schema: `custom_fields.lock_after_create` (migration `a5c7e9b1d3f6`). The enforcement helper is
  shared, so it's ready to extend to client/asset forms later. BE `test_product_custom_fields.py`
  (5/5), FE `product_forms_editor.test.js` (13/13); gated ruff clean; migration single-head; full FE
  suite green (pre-existing `ux_overhaul` excepted). No emoji; glass/clay throughout.

## [1.1.0] — build 144as: Add-category side panel + product Forms editor

Shipped in phases, each verified before the next.

- **Phase 1 — "Add category" side panel (Orderry-style, but premium).** Clicking **+ Category**
  (or the **+** on a tree node) now opens a right-side glass drawer instead of a prompt: a category
  **title**, a placement toggle — **New top-level** vs **Under a category** — and, for the latter, a
  **parent picker** (the clicked node is preselected). Used by the Products tree and the inline **+**
  on the product form (stacked over the form). `parent_id` was already supported server-side, so this
  is frontend-only. FE `category_add_panel.test.js` (9/9).
- **Phase 2 — Product custom fields via the Forms editor.** The existing product form stays the
  **default**; under **Settings → Forms** there's now a **Product fields** entity alongside Client and
  Asset. Fields you add there render below the built-in product fields on the add/edit form, and their
  values persist on the product (`parts.custom_data`, new migration `f4b6d8c0e2a7`). Backend adds
  `product` to the custom-field entities and accepts/returns `custom_data` (partial saves never wipe
  it). BE `test_product_custom_fields.py` (2/2); FE `product_forms_editor.test.js` (8/8).
- **Phase 3 — richer field types for every form.** The shared field renderer (used by Client, Asset
  **and** Product forms) gained **Date &amp; time**, **Address**, and treats **Value list** as an
  optional select. So the Orderry field palette — text / text area / value list / checkbox / date /
  date &amp; time / number / address — is available on all custom forms, not just products.
- No new sidebar menus or icons; no emoji; glass/clay look throughout. Gated ruff clean; app imports;
  migration chain single-head (`f4b6d8c0e2a7`) applies cleanly; full FE suite green (pre-existing
  `ux_overhaul` harness excepted).

## [1.1.0] — build 144ar: fix Actions dropdown + Orderry-style category tree adds

- **Fixed the Products Actions menu.** The top-bar had no stacking context, so the dropdown
  painted *under* the page content — it looked faded and clicks fell through. The top bar now owns
  its own stacking layer, so the Actions menu sits above the page and every item is clickable.
- **Add categories from the tree (Orderry-style).** A green **+ Category** button sits above the
  category tree for a top-level category, and every node shows a **+** on hover to add a
  **sub-category** under it — nested, with counts, matching Orderry's tree but in the RepairOS look.
  "Manage" still opens the full rename/delete manager.
- Frontend-only (no schema/endpoint change — `parent_id` was already supported). FE
  `products_rebuild.test.js` updated (14/14); full suite green.

## [1.1.0] — build 144aq: take a photo or choose a file — everywhere

- **One shared photo picker.** Every "add photo / choose image" spot now opens the same premium
  chooser: **Take photo** (live camera via `getUserMedia`, with a Capture button) or **Choose file**
  (files or, on mobile, the gallery/camera natively). Images are downscaled in-browser to a small
  JPEG so stored data URLs stay light.
- **Wired everywhere it's needed:** work-order **asset photo**, **product image**, company **logo**
  (Settings → General), and the work-order **Files &amp; feed** photo — all go through the one
  component, so behaviour and styling are identical.
- **Graceful + safe:** if the camera is blocked or absent, it falls back to the file chooser; the
  camera stream is always stopped when you capture or cancel. Works on `localhost`/HTTPS (secure
  context required for the camera).
- Frontend-only (`photo-input.js`), no schema or endpoint change — each caller still saves through
  its existing API. Tests: FE `photo_input.test.js` (12/12); asset-image test updated. No emoji.

## [1.1.0] — build 144ap: Products rebuilt — nested tree, multi-select, labels

- **Left nested category tree** replaces the old dropdown: a persistent, indented tree with live
  counts; click any node to filter the product list, "All products" clears it, "Manage" opens the
  category manager.
- **Multi-select with one Actions menu** (the Work-orders pattern, no crowded top bar). Row +
  select-all checkboxes in the modern glass/clay style. When nothing is selected the top-right
  shows page actions (New product, Manage categories, Bins &amp; serials, Import, Export); the
  moment you tick a box it swaps to a selection menu (never overlapping page text).
- **Bulk actions:** **Print labels** (pick a label template + copies → renders real barcodes/QR
  via the existing `/render-labels`, opens print-ready), **Generate barcode**, **Generate QR code**
  (reuse the existing generators), **Move to category**, **Bulk price update** (set or ± percent),
  **Export**, and **Delete / write-off** (soft — history kept, drops out of the catalog).
- Everything stays connected — categories drive Inventory + POS filtering, products remain findable
  by SKU/barcode/serial, and labels use the shared print pipeline. No schema change (reuses
  `barcode/generate`, `/render-labels`, label templates); one new endpoint `POST
  /api/catalog/products/bulk`. Tests: `test_products_bulk.py` (move/price/delete + guards) and FE
  `products_rebuild.test.js` (11/11). Gated ruff clean; no new sidebar menu; no emoji.

## [1.1.0] — build 144ao: managed product categories (create, assign, filter)

- **A real place to create and manage categories.** A new Category manager (create / rename /
  delete, with an in-use guard) reachable from **My company → Products → Categories** and from the
  **POS category rail** ("Manage categories"). Deleting a category that still has products or
  sub-categories is blocked until you move them.
- **Assign products to a category anywhere.** The product form's category picker now has a **＋**
  to create a category inline and select it on the spot. Categories are the managed tree, not
  free text.
- **Connected end-to-end.** Products filter by `category_id` on the Products page and Inventory,
  the POS category rail is now driven by the managed tree with **accurate live counts** and filters
  the grid by category, and a name-mirror keeps the old string-based views correct. Renaming a
  category updates its products automatically.
- **Per-template.** Categories carry a `template_key`, so each industry template gets its own tree
  (plus shared, null-template categories); the manager and POS scope to the current template.
  Categories are org-level and counts respect the active location's stock — no per-location trees.
- Schema: `categories.template_key` + migration `e3a5c7b9d1f4` (additive). API: `PATCH
  /api/catalog/categories/{id}`, counts + `template_key` on `GET /categories`, `category_id` filter
  on `GET /api/inventory/parts`. Tests: `test_categories.py` (create/assign/count/rename/delete-guard/
  template) + FE `category_manager.test.js` (14/14). Gated ruff clean; no new sidebar menu.

## [1.1.0] — build 144an: Sales redesigned as a Quick POS

- **The Sales page is now a full quick-POS**, in the app's glass/clay style (no emoji — all
  icons). Three zones: a category rail, a searchable/scannable product + service grid, and a live
  cart, modelled on Square/Loyverse/Toast counter flows.
- **Findable by name, SKU, barcode, and serial.** The product search now matches name/SKU/barcode/
  internal code, and a new `GET /api/inventory/scan?code=` resolves an exact SKU, barcode, code, or
  a serialized unit's **serial number** to its product (org-scoped, 404 on unknown). Pressing Enter
  in the search box (or a hardware scanner) adds the item instantly.
- **Live cart.** Tap tiles to add, ± steppers, order-level discount, a numeric **keypad** for the
  next item's quantity, **Hold** (park a sale) and resume, **Clear**, quick-cash denominations with
  change due, a tender-account selector, and a prominent Take-payment button. Est. profit shows live
  from FIFO cost.
- **Client attach reuses the full customizable form** (Contacts + custom fields): search-and-attach
  an existing client or create a new one inline. Recent sales stay one click away in the topbar.
- Completing a sale posts to `/api/sales` and refreshes stock counts so tiles stay accurate.
  Tests: backend `test_pos_scan.py` (SKU/barcode/serial resolve + org scoping) and FE `pos_sales.test.js`
  (14/14); updated the two older sales tests. Gated ruff clean; no schema change, no new menu.

## [1.1.0] — build 144am: fix Add-client, dead links, and all document printing

- **"Add client" in New sale works again.** The button was guarded by `window.ClientsPage`,
  but page objects are top-level `const` and never attach to `window`, so the check was always
  false and it spammed "Client form unavailable." Fixed to a proper `typeof` guard — clicking it
  now opens the full customizable client form (with your Forms-editor custom fields), saves to
  Contacts, and drops the client into the sale.
- **Same root cause fixed everywhere.** The 144ah live clock-toggle refresh, the Suppliers →
  Incoming-stock → PO link, and the command palette's "New work order / customer / PO" actions
  were all silently dead for the same reason. All now use bare refs / `typeof` guards.
- **Printing is fixed — POs and every other document.** Print buttons did a raw
  `window.open('/api/print/…')`, which sends no auth token, so the endpoint returned 401 and the
  tab opened blank. Added an authenticated `openPrintPdf()` helper (fetch with bearer → open the
  PDF blob, with a download fallback if pop-ups are blocked) and routed **all** print/export
  buttons through it: purchase orders, payroll statements, transfers, stock-take acts, and the
  client/stock-take CSV exports. Also corrected the PO print path (`/api/print`, not `/api/printing`).
- Frontend-only fixes plus tests: backend `test_print_endpoints.py` (PO PDF returns `%PDF` 200 with
  auth; 401 without) and FE `window_globals_and_print.test.js` (8/8). Editable-PO suites still green.

## [1.1.0] — build 144al: editable Purchase Orders (2 tabs, charges, fully linked)

- **Purchase orders are now clickable and editable.** Opening a PO (from Inventory →
  Purchase orders, Suppliers → Incoming stock, or a product's PO list) shows a premium
  glass/clay detail with **two tabs**:
  - **General** — supplier card (name, phone, email, what we owe them), our PO number +
    status, expected date, **supplier invoice / bill number + date**, on-credit toggle, and notes.
  - **Products to order** — add / edit qty & unit cost / remove line items with a live totals
    footer. A PO can be edited until it's fully received; a received PO is read-only.
- **Optional, customizable charges.** Add **Shipping**, **In-transit**, or any charge type you
  define — opt in only when they apply. A "Types" button lets you customize the charge list
  org-wide. Charges fold into the PO total.
- **Landed cost toggle.** Turn on "Add charges to item cost" and freight is spread across the
  received units into moving-average + FIFO cost layers, so product margins stay honest. Off by
  default (charges stay a flat expense).
- **Everything is linked.** On receive, on-credit POs post the goods **and** the charges to the
  **supplier balance / contact ledger**, so what you owe — and the AP figure in finance reports —
  is complete. Print/PDF now carries the supplier invoice no/date and the charges breakdown.
- Schema: `purchase_orders` gains supplier_invoice_no/date, notes, charges (JSON), charges_to_cost;
  `organisations` gains po_charge_types. Migration `d2f4a6b8c0e1` (additive, no backfill). New
  API: `GET/PATCH /api/inventory/po/{id}`, `GET/PUT /api/inventory/po-charge-types`. Tests:
  backend `test_po_editable.py` (detail/edit/charges/landed-cost/supplier-balance/lock) + FE
  `po_editable_detail.test.js` (15/15). PO/supplier/report suites green; migration verified; no
  new sidebar menu.

## [1.1.0] — build 144ak: Sales "Add client" opens the full customizable form

- **Fixed the blank client field in New Sale** and reworked how you add a client there. The
  field is a normal client selector again (Walk-in + your contacts), with a glass **Add client**
  button beside it — the same premium style as the rest of the app.
- **Add client now opens the real, customizable client form** — the exact form Contacts uses,
  including any **custom fields you defined in the Forms editor**. It layers over the sale (the
  sale keeps its lines), saves to Contacts, and drops the new client straight into the sale,
  selected. No more cut-down mini-form: full name/org, phone, email, lead source, manager,
  loyalty tier, currency, per-category discounts, notes, and your custom fields.
- Everything stays connected: the created contact appears under **Contacts**, and their
  **Purchases** tab (what they bought / what work orders we did) — added in 144aj — populates as
  you sell to them.
- Frontend only: `ClientsPage._clientForm(c, {stack, onCreated})` now stacks and can hand the new
  client back to its caller; Sales calls it. FE tests updated (13/13 + 9/9). No schema change,
  no new menus.

## [1.1.0] — build 144aj: searchable client-in-sales + purchase history

- **Adding a client during a sale is now a real, searchable contact.** The Sales screen's
  client field was a plain 50-item dropdown; it's now the shared **ClientPicker** — type to
  live-search any contact by name or phone, or create a brand-new one inline (name, phone,
  email, loyalty tier, source). Whatever you create is a full **Contacts** record, so it shows
  up everywhere the client form is used — no more orphan entries.
- **"Purchases" tab on every contact — what they bought / what we sold.** The contact profile
  gains a Purchases tab that unifies **point-of-sale sales** and **work-order billing** into one
  newest-first history: date, item, source (Sale # or WO #, click a WO to open it), qty, price,
  total. A header summarises **total spent, items bought, transactions, and last purchase**, and
  an in-tab search box filters by item — so you can answer "has this customer bought a screen
  protector before?" in seconds.
- Backend: `GET /api/clients/{id}/purchases` (org- and client-scoped, 404 on unknown). Tests:
  `test_client_purchases.py` (sales + WO lines combine; totals/counts; scoping) and FE
  `sales_picker_and_purchases.test.js` (11/11). Client/sales suites green (45); gated ruff clean.
  No schema change, no new menus.

## [1.1.0] — build 144ai: board drag-drop, asset photos, general-info schedule

- **Board drag-drop actually works now.** The kanban used native HTML5 drag-and-drop,
  which was unreliable on trackpads and fought the click-to-open handler. Rewrote it with
  pointer events: a small movement threshold means a **tap opens** the card and a **drag
  moves** it, with a floating ghost following the cursor and the target column highlighting
  under the pointer. Drop detection uses `elementFromPoint`, so it lands in the right column
  every time. Touch works too (`touch-action:none` on cards).
- **Asset photos across every view.** Added an optional photo to an asset (device). It shows
  as a **cover image at the top of the board card**, as a **small thumbnail in the work-order
  list** that **enlarges on hover**, and as a larger tile on the **general-info page** with an
  **Add / Change photo** button. Uploads are downscaled in the browser (max 900px, JPEG) so the
  stored image stays small, and saved through the existing asset-update endpoint.
- **General info is roomier and schedulable.** The general-info panel now uses an auto-fitting
  grid and carries a **Schedule** block showing the booked time with a **Reschedule / Schedule**
  button — available even after the work order exists. The same reschedule action is now a
  one-click calendar button on **every list-view row**. The schedule endpoint was upgraded to
  distinguish "field omitted" from "explicitly null", so blanking both dates clears the schedule.
- Schema: `devices.image_data` (nullable) + migration `c1e3f5a7b9d2`. Tests: backend
  `test_asset_image.py` (image round-trips to detail/list/board; reschedule set+clear) and FE
  `board_dnd_and_asset_image.test.js` (15/15). Full order/asset/board suites green (95); no new
  menus or icons.

## [1.1.0] — build 144ah: add-client-in-sales, live clock toggle, modern checkboxes

- **Add a client while creating a sale.** The New-sale modal's client field now has a **+ New**
  button that opens a small inline form (name, phone, email) right there. Saving posts to
  `/api/clients`, adds the new client to the dropdown and selects it — no need to leave the sale,
  go to Contacts, and start over.
- **Clock master switches now take effect immediately.** Turning the work-order or attendance
  timer off (or on) in Settings → General used to do nothing visible until a page reload, because
  the cached profile wasn't refreshed. `GeneralPage.save` now re-fetches the profile
  (`App.refreshPerms()`) and re-renders the time-clock widget, so the timer **vanishes from the
  screen** the moment you switch it off — one clock, both, or none, matching the toggles exactly.
- **Modern checkboxes app-wide.** Replaced the default browser checkboxes with a glass/clay style
  matching the rest of the UI: soft rounded tile, clay shadow, blue→purple gradient fill with a
  crisp clip-path checkmark when checked, spring animation, focus ring, and a dark-mode variant.
  Radio buttons get the same treatment. No markup changes — a single global CSS rule restyles
  every existing checkbox.
- No new menus, icons, or nav — all three are in-place fixes. FE suites green (new
  `inline_client_and_clock.test.js`, 10/10); backend unchanged.

## [1.1.0] — build 144ag: QuickBooks item mapping, richer invoices, two-way pull

- **Product ↔ QBO item mapping.** Products can now sync to QuickBooks as items (`/sync/item/{id}`
  or bulk `POST /sync-items`), recording a mapping so they aren't duplicated.
- **Invoices push properly linked.** The invoice payload now carries a **CustomerRef** (from the
  synced client), **per-line `ItemRef`** when the product is mapped (falling back to a free-text
  line otherwise), the real line quantities/prices/descriptions, and a **tax total** — instead of
  a single anonymous summary line. Payments carry the CustomerRef too.
- **Two-way pull.** `POST /api/integrations/qbo/pull/{contact|item}` imports the shop's existing
  QuickBooks customers/items and links them to matching RepairOS records (by email/name/SKU), so
  a shop already using QBO doesn't get duplicates — future pushes update in place. Idempotent.
- All gated + best-effort (no QBO connection → skipped; a bad call is logged, never raises).
  Tests cover item sync+mapping, the enriched invoice payload (CustomerRef/ItemRef/tax), and pull
  linking + de-dupe (mocked Intuit transport). QBO OAuth + autosync suites green. No schema change.

## [1.1.0] — build 144af: WhatsApp / Messenger / Instagram in the inbox

- **Outbound replies actually send now.** The inbox reply endpoint previously just recorded the
  message (`# _dispatch` was a stub). It now routes through a new `messaging` service to the right
  provider — WhatsApp (Cloud API, falling back to Twilio), SMS (Twilio), Messenger & Instagram
  (Meta Graph) — and reports the delivery status. Gated: no provider creds → 'simulated' (logged),
  never fails the reply.
- **Inbound from Meta.** New webhook `GET/POST /api/inbox/webhook/meta/{org_id}`: the GET does the
  subscription handshake (echoes `hub.challenge` when the verify token matches); the POST parses
  Messenger, Instagram and WhatsApp-Cloud payloads, normalises them, and threads them into the
  inbox (matched by sender, de-duped by provider message id) — so DMs from all three land in one
  stream alongside SMS/email.
- Config seams added (`META_VERIFY_TOKEN`, `META_APP_SECRET`, `META_PAGE_TOKEN`, `META_IG_TOKEN`,
  `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`) — all default empty, so nothing changes until a shop
  configures a provider.
- Tests: payload parsing (all 3 shapes), simulated-send without creds, webhook verify (match/mismatch),
  and inbound→thread + reply→dispatch end to end. Inbox suites green. No schema change, no new nav.

## [1.1.0] — build 144ae: Gmail — send wiring + inbox polling ingest

- **Notifications now send from the shop's own Gmail/Outlook.** The connected-account send code
  (Gmail API + auto token refresh with rotation) already existed but was never used — dispatch
  always fell back to SMTP. Now email notifications route through the connected account first
  (threaded, from the shop's address) and only fall back to SMTP/simulated when none is connected.
- **Test-send endpoint** (`POST /api/integrations/email/{id}/test`) so a shop can verify outbound
  works before relying on it.
- **Inbox polling ingest** — a new `POST /api/integrations/email/poll` pulls recent Gmail inbox
  messages into RepairOS inbox threads (matched by sender, de-duped by Gmail message id), ready
  for a scheduled task. Adds the `gmail.readonly` scope for new connects (a restricted scope —
  production polling needs Google verification; send-only shops that don't grant it are handled).
- **Fixed a regression from build 144s:** changing `_resolve()` to return a tuple silently broke
  order notification/rule rendering (a 500 when a template existed). Both callers now unpack the
  variable map. Caught by the new Gmail test.
- Tests: notifications prefer the connected Gmail account; poll ingests + de-dupes into one thread;
  `list_recent` no-ops safely without read access. Notifications + work-order suites green. No schema change.

## [1.1.0] — build 144ad: charts, motion & accessibility

- **Charts on every report.** The tables-only Reports page now has a **Table ↔ Chart** toggle:
  any report with a label column + a value column renders as a premium inline-SVG **bar chart**
  (or a **trend line** when the labels are dates), coloured from the design tokens so it inherits
  light/dark. A new zero-dependency `charts.js` (`bars` / `line` / `sparkline`) is available app-wide.
- **Number count-up.** Dashboard metric tiles animate their values up on load — currency prefixes
  and % suffixes preserved — for that "expensive product" feel.
- **Delightful empty states.** The shared empty-state blob is now an on-brand layered-glass
  illustration with a dashed ring, instead of a bare icon — every "No data" reads intentional.
- **Staggered entrance.** Dashboard tiles rise in with a gentle spring stagger on page enter.
- **Accessibility — `prefers-reduced-motion`.** Users who ask for calm now get it: ambient orb
  drift, the button sheen sweep, count-up and entrance animations all stand down. (Previously the
  background animated for everyone — a real a11y/battery gap.)
- Frontend-only, no schema/nav change. New jsdom test (charts util + count-up + reports toggle);
  full frontend suite green (25/26; the one failure is the pre-existing unrelated harness issue).
  Deferred for a follow-up: command-palette spotlight, table density toggle, responsive table→card.

## [1.1.0] — build 144ac: premium design enrichment + DESIGN.md

- **Richer glass × clay, app-wide, from one layer.** A design-system enrichment (no per-page
  edits) makes the whole app read more premium: the pastel mesh gains a warmer multi-stop
  gradient plus a barely-there film grain and soft corner vignette so panels visibly float;
  every glass surface now carries a crisp specular top edge and cards a radial shoulder
  highlight ("light caught in glass"); a new `clay-xl` elevation for hero surfaces; and the
  primary button gets a three-stop gradient with a sheen that sweeps across on hover. Cards and
  interactive rows lift on a spring. Tabular numerics everywhere numbers line up. Dark mode
  keeps it subtle (no grain).
- **DESIGN.md added** — a Stitch-format design-system document (tokens + components + do's/don'ts)
  so future UI stays pixel-consistent with the glass/clay language.
- CSS-only + a doc; brace-balanced; no JS/schema change. A standalone preview is included.

## [1.1.0] — build 144ab: fix reports date range (Activity Log showed nothing)

- **The "To" date is now inclusive of the whole day.** The Reports UI sends the end date as
  a plain date (YYYY-MM-DD), which the backend parsed as 00:00:00 — silently excluding
  everything that happened *on* the end date. The Activity Log is inherently "what just
  happened today", so it always looked empty/broken even though the events were recorded.
  `_parse_range` now extends a date-only "To" to end-of-day, so the range means what users
  expect. This fixes **every** dated report (activity log, work orders, cash flow, labor, …)
  and their CSV/XLSX exports in one place — the data was always there; the window was cutting
  today off.
- Tests: today's events now appear with `date_to=today`; a second report confirms the fix is
  in the shared range parser, not one report. Reports suites green.

## [1.1.0] — build 144aa: inventory — bulk create-PO, min order qty, conversions fix

- **Select products → create a purchase order.** The stock list now has a checkbox per row
  (plus a select-all header). Actions → **Create PO from selected** opens a PO builder listing
  the ticked products with editable order quantity and unit cost, a supplier + destination
  warehouse, expected date and on-credit option — one click to a real purchase order.
- **Minimum order quantity per product.** New `min_order_qty` field on products (create/edit),
  used as the default order quantity when raising a PO (falls back to the below-min-stock gap,
  then 1). Persists through the API and shows on the product record.
- **Conversions history fixed.** Converting a company asset to a product previously created the
  product but never wrote a conversion document, so the Conversions page always looked empty.
  The convert-to-product endpoint now records an `AssetProductConversion`, so the history shows
  it (direction, serial, who, when). (Product → Asset already recorded correctly.)
- **QA hardening:** `create_po` now rejects line items whose product isn't the caller's, and an
  unknown supplier, closing a cross-tenant IDOR the bulk flow would have exposed.
- Migration `b8d0f2a4c6e9` adds `parts.min_order_qty` (default 0). No new navigation. 5 new tests
  (moq persist, bulk PO, PO ownership guard, conversion-in-history) + inventory/conversions/asset
  suites green. Known follow-up: the two asset↔product conversion code paths should be consolidated.

## [1.1.0] — build 144z: QuickBooks auto-sync on invoice + payment events

- **Money now flows to QuickBooks automatically.** RepairOS already had the full QBO plumbing
  (OAuth, token auto-refresh with rotation, idempotent push sync + mapping/log audit trail);
  this build wires it to fire on its own. When an invoice is auto-created on order close, the
  customer + invoice push to QBO; when a payment is confirmed, the customer, invoice and
  payment push in dependency order. A new `qbo.autosync()` helper is a silent no-op unless the
  org has QuickBooks connected, so it's safe everywhere and needs no per-shop config beyond
  connecting the account.
- Everything is best-effort and self-logging — a QBO outage never blocks taking a payment or
  closing an order; failures land in the existing sync-log. No new navigation, no schema change.
- Tests: `qbo.autosync` no-ops without a connection, pushes each item (in order, skipping nulls)
  when connected, and the payment-confirmation path calls it with the invoice + payment. Existing
  QBO OAuth, payments, and work-order suites remain green.

## [1.1.0] — build 144y: optional time-based labour billing from the job clock

- **Bill labour from the job timer (optional).** A new checkbox sits right beside the clock
  on/off switches in Settings → General, off by default. When on, clocking time against a work
  order automatically adds/updates a **labour line priced by tracked time × the employee's
  billable rate** — the same model Orderry uses to turn time into a service charge. The line is
  keyed to the time entry, so editing the time reprices the same line (never double-bills) and
  voiding the entry removes it; the order total reprices through the existing recalc path.
- Fires on every way time lands on an order: stopping a timer, switching timers, a manual time
  entry, or editing one. Payroll (what you pay the employee) is unchanged and still flows from
  timesheet approval — this toggle only affects billing (what you charge the client).
- Employee hours & jobs reporting already covers this (Technician utilization, Labor by work
  order, Labor cost, Payroll summary) and now includes task time from 144x.
- Migration `a7c9e1b3d5f8` adds the single `clock_labour_billing` org column (default off). No new
  navigation. 3 new tests (default-off, no line when off, manual entry bills 2h × rate + void removes).

## [1.1.0] — build 144x: clock master switches + work-order timer on tasks

- **Company-level clock switches.** Settings → General now has two master toggles — the
  work-order / job & task timer, and the shop attendance clock. A clock counts toward payroll
  only when it's ON here **and** enabled for that employee (the existing per-employee toggles
  are unchanged), so you get org-wide control without losing per-person overrides. Both default
  ON, so nothing changes for existing shops. The switches are surfaced on `/auth/me` so the
  time widget hides a clock the moment it's turned off, and the clock-in / start-timer endpoints
  refuse (403) when their clock is disabled.
- **The work-order timer now runs on tasks too.** `TimeEntry` gained a `task_id`; starting a
  timer against a task (▶ Timer on the Tasks list) records `category='task'` and flows through
  the *same* stop → timesheet-approval → payroll pipeline as job time — no separate accounting.
  A task that belongs to an order inherits its order/location for context.
- New `automation_rules`-era migration `f6b8d0c2e4a7` adds the two org columns + `time_entries.task_id`
  (defaults keep current behaviour). No new navigation. Tests: 4 new (toggle persistence, clock-in/
  timer gating, task-timer entry + payroll flow) plus the full payroll/timeclock suites green.

## [1.1.0] — build 144w: workflow automation engine (triggers, part 1 of reporting+triggers)

- **New automation rules engine.** WHEN an event fires (work order created, status changed to X,
  estimate approved, payment received, order overdue, product low on stock) and optional
  conditions match (status / priority / total / location / type / is-estimate, with
  eq/ne/gt/gte/lt/lte/contains/in), THEN run an action: create a task, assign a technician,
  add a tag to the order, send a notification, or call a webhook. This complements the existing
  notification-rules engine (which owns delayed / working-hours / reminder messaging).
- **Wired into the order lifecycle**: fires on order create, status change, estimate approval,
  and payment confirmation; a `/api/automations/sweep` endpoint fires the time-based ones
  (overdue orders by promised time; low-stock products) for a scheduled task to call. Every
  fire is best-effort and logged to the order's event feed — a bad rule never breaks the request.
- **No new navigation.** Per request, the rules manager is hosted inside the existing
  **Settings → Notifications** page (with a live dry-run "would match" tester and a Run-sweep
  button), not a new menu — and the older "task from a missed call" toggle is preserved there.
- New `automation_rules` table + migration; engine, CRUD API, and UI all tested (5 backend +
  updated frontend suites). Reporting enhancements (KPIs/comparison, charts, new reports,
  scheduling) follow in subsequent builds.

## [1.1.0] — build 144v: real barcodes/QR + labels unified into document templates

- **Real, scannable Code128 barcodes.** {Barcode} now renders a proper Code128 vector
  (subset B with auto-C for digit runs, correct mod-103 checksum, human-readable caption) —
  no more decorative bars. Pure-python, no system dependencies.
- **Real QR codes.** {QR Code} renders an actual QR (via the pure-python `segno`), drawn as
  crisp SVG modules. Guarded import: if the library is ever absent it falls back to a Code128
  of the same value so documents never break.
- **Labels & price tags unified into Document templates.** Price tags and product labels are
  now first-class DocumentTemplates (doc_type price_tag / label / product_label / vehicle_label),
  authored in the same rich editor as estimates and invoices — exactly like Orderry. The
  separate "Labels & price tags" tab and its old mm-based editor are retired; the Documents
  settings page is a single unified list (label templates are tagged "· label").
- **Inventory label printing repointed.** Printing labels for selected products now renders the
  chosen label template through the unified engine (`/api/documents/render-labels`) into a
  tiled, print-ready sheet — with the real barcodes/QR — instead of the old PDF path.
- Tests: new barcode/QR unit suite, render-labels endpoint tests, and the editor + stocktake
  label suites rewritten for the unified model. Full frontend suite green (24/25; the one
  failure is a pre-existing unrelated harness issue). No schema change.

## [1.1.0] — build 144u: document-render QA fixes (audit follow-up)

- **Price tags no longer leak into order printing.** The order Print dialog now excludes
  `label`-type documents (price tags are inventory artifacts, not order documents), while
  the Estimate/invoice/work-order docs are still offered and gated by their print statuses.
- **Tax-inclusive pricing handled correctly.** Line math now honours `tax_mode`: when a
  unit price already includes tax, the pre-tax figures are derived by dividing out the rate,
  so {Product Price Without Taxes}, {Product Amount Without Taxes}, {Subtotal} and {Tax Value}
  are right for both inclusive- and exclusive-tax orgs.
- **Row-repeat guards against nested tables.** A product-row `<tbody>` that contains a nested
  table is left untouched instead of being corrupted by the flat row-expander.
- Tests added for all three; full document suite (17 backend + editor UI test) green.

## [1.1.0] — build 144t: backfill Orderry defaults to every org

- **Existing orgs now get the defaults too.** The Estimate + Price tag templates are
  backfilled for every org on next load — not just brand-new ones — matched by name so
  they're added exactly once and never duplicated. A user's own templates are left
  untouched and sit alongside the seeded pair. If someone deletes a seeded default it
  stays gone (we check all rows including soft-deleted, so it isn't re-created).

## [1.1.0] — build 144s: Orderry-parity document variables + smart tables

- **Full Orderry variable catalog.** The document-template editor's Variables panel now
  advertises the complete Orderry set, grouped exactly like Orderry: Company, Legal entity,
  Client, Payer, Ticket, Device, Tables, Employees, Finance, Date, Other. New variables
  include the whole Device/asset block ({Asset Owner}, {Asset Warehouse}, {Asset Modification},
  {Asset Color}, {Asset Year}, {Asset Cost}, {Asset State}, …), the Employees block
  ({Current User}, {Order Assignee Name/Phone/Position}, {Order Manager Name/Phone/Notes},
  {Order Technician Notes}), price-tag variables ({Product Title}, {Product Article}, {Price}),
  and {Today Date}. All resolve on render with graceful fallbacks.
- **Repeating product rows.** A products table's `<tbody>` row is now repeated once per line
  item, substituting per-row variables: {Row Number}, {Product Name}, {Product Qty},
  {Product Price / Price Without Taxes / Without Discount …}, {Product Amount …},
  {Product Tax Amount}, {Product Discount Sum/Pct}, {Product Code/SKU/UoM/Description},
  and per-line {Technician Name/Phone/Job Title}.
- **Table-block variables.** {Products Table}, {Services Table}, {All Items Table} (and the
  By-Technicians variants) expand to a full itemised table. Document totals — {Subtotal},
  {Tax Name}, {Tax Rate}, {Tax Value}, {Tax Value Spelled Out}, {Total Amount},
  {Total Amount Without Discount …}, {Total Discount}, {Total Qty} — are computed from the lines.
- **Orderry-style defaults.** New orgs are seeded an Estimate document (the Orderry estimate
  layout with a repeating line table, subtotal/tax/total footer, and terms) and a Price tag
  label, so the editor opens with a real, ready-to-print template.

## [1.1.0] — build 144r: rounded scheduler / calendar corners

- **The scheduler now matches the app's soft clay/glass style.** The day-timeline, week,
  and month calendar grids read as rounded cards instead of a hard square table. The week
  grid is clipped to `--r-md` with `overflow:hidden` so its seven day-columns tuck cleanly
  into the rounded container, and the month tiles were rounded up from `--r-sm` to `--r-md`
  with slightly larger gaps for a softer grid. Applies everywhere scheduling appears
  (work-order Scheduler and Bookings, which share the same styles). CSS-only; no schema,
  API, or JS change.

## [1.1.0] — build 144q: system-wide premium polish + colour correction

- **Colour-corrected text ramp.** The secondary and tertiary greys were sitting below WCAG AA
  on the bright glass (washed out). The whole ramp is now deeper and crisper — primary
  `#14213f`, secondary `#435577` (7.5:1), tertiary `#6c7a99` (4.3:1), faint `#9aa7c4` — so
  every label, caption, and table header across the app reads clearly with a cleaner
  hierarchy. Both dropdown-arrow icons were re-tinted to match.
- **Crisper premium text rendering** app-wide: grayscale font-smoothing, `optimizeLegibility`,
  a subtle −0.006em tracking, and Inter's stylistic sets — the type looks tighter and more
  refined at every size.
- **Sharper icons** via `shape-rendering: geometricPrecision` on all SVGs.
- **Finishing touches:** premium blue text-selection, a consistent focus-visible ring, and
  thin translucent glass scrollbars (light + dark) replacing the default chunky bars.
- Pure CSS at the token/base level, so the lift applies to every screen with zero layout or
  behaviour change. A standalone `repairos-144q-preview.html` shows the corrected ramp,
  table, buttons, and badges on the real stylesheet. All frontend suites still pass.

## [1.1.0] — build 144p: visual polish (table clipping fix + document editor)

- **Fixed: totals row clipped by the card corner.** Tables have header and body cell padding
  but the **footer (totals) row had none**, so on the Payroll calculation / accruals tables
  the "Total —" text and the totals were sliced by the card's rounded corners. Footer cells
  now get proper padding plus a tinted glass finish and tabular-aligned numbers — the totals
  sit cleanly inside the card (fixes every `.tbl` with a footer).
- **Document editor refined** to match the liquid-glass / clay aesthetic. The toolbar is now a
  frosted-glass bar with a soft inner highlight; the mismatched emoji glyphs (align/image/
  table/divider) are replaced with clean, consistent line icons; buttons have uniform hover
  and press states; the selects and colour swatch are styled to match; the page canvas has a
  subtle inset depth; and the Variables side-panel is a proper glass card. No behaviour change.
- CSS-only (plus the editor's icon markup); no backend or schema change. Existing frontend
  suites still pass, including the document-editor DOM test.

## [1.1.0] — build 144o: per-status print rules + Legal-entity / Payer variables

- **Per-status "allow printing".** A document's settings now has an **Allow printing in
  statuses** section — tick the work-order statuses the document may be printed in (all ticked
  = any status). It's enforced where it matters: the order's **Print / document** menu now only
  offers documents allowed in that order's current status (`/api/documents/templates?order_id=`
  filters server-side).
- **Two new variable groups, wired to real data:** **Legal entity**
  (`{Legal Entity Name / Tax ID / Address / Details}`, resolved from the order location's legal
  entity, else the org default, else company info) and **Payer**
  (`{Payer Name / Phone / Email / Address}`, resolved from the client's designated payer, else
  the client). They show up in the editor's grouped Variables panel automatically.
- Schema: one nullable column `document_templates.print_statuses` (JSON list, migration
  `d4f6a8c0e2b5`); empty/absent = printable in any status. Drift clean, chain applies.
- Tests: `backend/tests/test_document_print_rules.py` (the new variable groups, print-status
  round-trip, the order print list gated by status, and the Payer variable resolving to the
  designated payer) plus the editor DOM test extended for the status checkboxes.

## [1.1.0] — build 144n: full-page document-template editor + labels merged

- **Full-page document editor** (Settings → Document templates), replacing the cramped modal
  and matching Orderry. A template picker with **+ New document**, and a rich editor:
  formatting toolbar (undo/redo, **font family**, **size**, **text colour**, bold / italic /
  underline / strikethrough, align left/centre/right/justify), **insert image**, **insert
  logo** (drops the company logo), **insert table**, **divider**, and an HTML view toggle —
  all on a WYSIWYG page canvas sized to the chosen **page size** (A4 / Letter / Label).
- **Grouped Variables side-panel** — Company, Client, Order, Vehicle / asset, Employees,
  Finance, Tables, Date, Other — click any variable to drop it at the cursor.
- **Document settings** (title, **assignment** — Work orders/estimates, Invoices, Sales,
  Product/Vehicle labels, etc., page size, default font, auto-print, default) and **Print**
  preview.
- **New variables, wired to real data:** `{Company Address/Phone/Email/Website}`,
  `{Order Due Date}`, `{Currency}`, `{Current Date}`, `{Asset IMEI}`, `{Asset VIN}` — resolved
  in the renderer alongside the existing ones.
- **Labels & price tags moved under Document templates** as a second tab (no separate rail
  item), the way Orderry groups them. Nothing removed — the full label editor is intact.
- Schema: two columns `document_templates.page_size / font_family` (migration `c3e5a7b9d1f4`,
  defaults A4 / Sans-Serif); drift clean, chain applies.
- Tests: `backend/tests/test_document_templates.py` (grouped + new variables, page-size/font
  round-trip, new variables render from an order) and
  `frontend/tests/document_templates_editor.test.js` (hub tabs, the editor's toolbar +
  page-size + grouped variables + canvas, insert-variable, save posts body/page size, and the
  Labels tab embeds the label editor).

## [1.1.0] — build 144m: Payroll accruals tab

- **Finance → Payroll accruals** (third tab beside Invoices and Payroll calculation) — the
  read side of payroll, matching Orderry. Every accrual you create from the calculation page,
  newest first: Employee, Period, Base, Commission, Hourly, Bonuses, Penalties, Gross and
  Status, with a totals footer.
- **Mark paid.** An accrued run has a **Mark paid** button; it posts the payout to the
  employee's balance and flips the row to *paid* (with the paid date). Paid rows show their
  paid state instead of a button. Nothing double-posts — the payout is idempotent per run.
- Backend (additive, no schema change): `GET /api/payroll/runs` now returns the employee
  name, the bonus/penalty totals and accrued/paid timestamps plus a totals block; the
  existing `POST /api/payroll/runs/{id}/pay` drives Mark paid.
- Tests: `backend/tests/test_payroll_accruals.py` (accrue → enriched listing with the right
  totals → mark paid flips status and posts the payout; 404 on a missing run) and
  `frontend/tests/payroll_accruals_page.test.js` (the tab renders the columns + totals, an
  accrued run offers Mark paid while a paid run shows its paid state, and Mark paid hits the
  pay endpoint). Migration head unchanged; drift clean.

## [1.1.0] — build 144l: unchecking a permission now takes effect immediately

Two bugs were stopping role permissions from applying after you saved:

- **Attribute collision.** The role editor's permission checkboxes used `data-perm` — the
  very attribute the enforcement layer scans to *hide* denied controls. So the editor and the
  hider fought over the same markup, and a save could sweep in unrelated elements. The editor
  now uses `data-permcode`, fully separate from enforcement.
- **Stale permissions.** The signed-in user's permissions were loaded once at login and never
  refreshed, so unchecking a box and saving had no visible effect until a full reload. Saving
  (or deleting) a role now calls a new `App.refreshPerms()` that re-pulls the current user's
  permissions and re-applies them across the page — the denied feature disappears right away,
  and every later page render stays consistent.
- Net effect: uncheck a function in a role, Save, and the matching control (e.g. **+ Order**,
  the **Invoices** tab, or a gated sidebar link) vanishes for anyone on that role — no reload
  needed. Re-check it and it comes back. (Deny-wins, so full-access roles are unaffected.)
- Frontend-only; no backend or schema change. Test:
  `frontend/tests/role_enforcement_refresh.test.js` reproduces the old failure and proves the
  fix — checkboxes use `data-permcode`, a denied permission's own checkbox stays visible in
  the editor while the real control is hidden, saving records unchecked = false, and the
  denied control disappears live after save.

## [1.1.0] — build 144k: richer, editable employee profile

- **More employee fields, Orderry-style.** The employee form now has a **General info**
  section: **Position** (job title), **Hire date**, **Employee ITN / tax number**, **Name for
  public documents**, and a free-text **Note** — alongside the existing name, email, phone and
  role. Everything is backed by real columns and wired through create, edit and the list API.
- **Editing is obvious.** The employee profile now has a prominent **Edit profile** button in
  the header (as well as the existing one in the footer), and the profile shows the new fields
  (position under the name, hire date, ITN, public-document name, and the note).
- Nothing removed: salary & commission, the two hourly clocks, clock visibility and working
  hours are all still there and pre-fill correctly when editing.
- Schema: four nullable columns `employees.position / tax_id / public_name / note`
  (migration `b2d4f6a8c0e1`); the existing `hired_at` column is now surfaced. Drift clean.
- Tests: `backend/tests/test_employee_profile_fields.py` (create/read, edit, blank-date
  clearing, optional fields) and `frontend/tests/employee_profile.test.js` (the profile's
  Edit button + fields, the edit modal's General-info section pre-filled, existing sections
  intact, and submit collecting the new fields).

## [1.1.0] — build 144j: choose which time clocks an employee sees

- **Per-employee clock visibility.** In Settings → Employees & roles, the employee form has a
  **Time tracking** section with two checkboxes: **Track work-order hours (job / labour
  timer)** and **Track shop attendance (clock-in / clock-out)**. Turn on either, both, or
  neither — so an employee can have only the attendance clock, only the job timer, both, or
  none. Both default on, so nothing changes for existing staff. The employee detail shows
  which clocks are enabled.
- **The floating time-clock widget honours it.** It now shows only the clocks the signed-in
  employee is set to see (read from `/api/auth/me`); with both off, the widget hides entirely.
- Schema: two NOT-NULL boolean columns `employees.track_job_hours` / `track_attendance`
  (default true, migration `a1c3e5f7b9d2`); no data change. Drift verified clean.
- Tests: `backend/tests/test_clock_visibility.py` (defaults on, choose one clock, patch flips,
  `/me` exposes the flags) and `frontend/tests/clock_visibility.test.js` (widget shows both /
  attendance-only / job-only / hides when both off / defaults to both when unknown).

## [1.1.0] — build 144i: payroll report + two hourly clocks

- **Payroll summary report** (Reports → Finance → "Payroll summary"). Per-employee
  compensation for any date range, straight from the payroll ledger: Commission, Hourly,
  Bonuses, Penalties, Base and Total. Exports to XLSX/CSV like every other report.
- **Two hourly clocks, named properly.** Hourly pay can now come from either clock:
  **Labour hours** — time tracked on work orders (the existing timesheet clock), or
  **Shop attendance** — clock-in / clock-out shifts. Each employee has an **"Hourly pay
  based on"** setting in their profile (Settings → Employees & roles), and it's shown on the
  employee detail. Default is Labour hours, so nothing changes for existing staff.
- **Attendance wages wired in.** New `POST /api/payroll/attendance-wages` totals an
  employee's completed shifts for a period, multiplies by their hourly rate, and posts an
  hourly-wage ledger entry dated to that period — so it flows straight into the month's
  Payroll Calculation and the report. Idempotent per employee + window (no double pay).
- Schema: one nullable column `employees.hourly_basis` (migration `f3a5b7c9d1e2`); no data
  change (null = labour). Drift verified clean.
- Tests: `backend/tests/test_payroll_clocks_report.py` (hourly-basis round-trip + coercion,
  attendance shifts → wages → month calculation, idempotency, and the payroll report in the
  catalog with correct totals). Existing payroll suites still green.

## [1.1.0] — build 144h: Finance → Payroll Calculation page

- **New Payroll Calculation page under Finance** (Finance now has Invoices | Payroll
  calculation tabs, Orderry-style). A month-by-month table of every employee's
  **Base / Commission / Penalties / Bonuses / Total**, with a totals footer and month
  navigation. Penalties show in red, bonuses in green.
- **Per-employee drawer.** Click a row to open a breakdown: itemized commissions (date,
  document/item, amount), the month's penalties and bonuses, a summary of Base / Commission /
  Penalties / Bonuses / Total, and Print.
- **+ Penalty / + Bonus.** Add an ad-hoc penalty or bonus (amount + comment) right in the
  drawer; it posts to the employee's ledger for that month and flows into the calculation
  immediately. Manual entries can be removed.
- **Create accrual.** Snapshots the month's calculated payroll into a PayrollRun and posts
  the base-salary entry (commissions, hourly and manual penalties/bonuses are already on the
  ledger, so nothing is double-counted). Guards against accruing the current/future month or
  a second time for the same period.
- Backend (additive, no schema change): `/api/payroll/calculation` now returns position,
  penalties, bonuses and a totals block; new `POST/DELETE /api/payroll/adjustment`,
  `GET /api/payroll/calculation/detail`, and `POST /api/payroll/calculation/accrue`.
- Tests: `backend/tests/test_payroll_calculation.py` (penalty/bonus flow into the month's
  totals and detail, validation, removal, accrual snapshot + current-month/duplicate guards)
  and `frontend/tests/payroll_calculation_page.test.js` (the real page renders the columns +
  totals, the drawer itemizes commissions and exposes +Penalty/+Bonus/Create accrual, and
  the actions hit the right endpoints). Drift verified clean; migration head unchanged.

## [1.1.0] — build 144g: Payroll rules moved under Employees & roles

- **Payroll (commission) rules now live under Settings → Employees & roles**, as a third tab
  next to Employees and Roles & permissions — matching Orderry, where compensation rules sit
  with the employees they pay. The standalone "Payroll rules" rail item is removed (no
  duplicate). Every rule feature is unchanged: the same 7 events (order created/closed, labor
  performed, product sold, assignee labor/product, task closed), percent/flat, margin basis,
  coefficient, per-employee/category/status conditions, create/edit/delete — just in one
  place. The tab's top-bar action becomes "+ New rule".
- First slice of a larger payroll pass. Still to come (as you asked): a Finance → Payroll
  Calculation page (Base / Commission / Penalties / Bonuses / Total with a per-employee
  drawer and accruals), ad-hoc penalty/bonus events wired into the calculation, and a payroll
  report.
- Tests: `frontend/tests/payroll_rules_under_employees.test.js` drives the real pages — the
  standalone rail item is gone, the Employees page shows the Payroll rules tab, switching to
  it renders the commission-rules UI from `/api/payroll/rules`, the action flips to "+ New
  rule", and leaving the tab cleans up. No backend or schema change.

## [1.1.0] — build 144f: Warehouses merged into Inventory + template-label fix

- **Warehouses now live under Settings → Inventory.** The separate "Warehouses" rail item is
  gone; the Inventory settings page (renamed **Inventory & warehouses**) now embeds the full
  warehouse workspace where the old "Manage warehouses" button was — create a warehouse and
  see every **product and asset** warehouse (Product / Asset / Deleted tabs), inline. Every
  warehouse feature is intact — create, edit, delete/restore, per-employee access, price of
  goods, balances — just in one place instead of two.
- **Asset-warehouse label now follows the industry template.** The label fell back to
  "Assets" whenever the org's stored template was the default `generic` or a legacy alias
  (`device_repair`, `auto_repair`, …) because it was looked up without normalization. It now
  normalizes first, so the tab reads **Devices / Vehicles / Tools / Vessels…** to match the
  chosen template. (Canonical keys already worked; legacy/default values were the bug.)
- Tests: `backend/tests/test_warehouse_label.py` (default → Assets, setting a template flips
  the label, and a legacy alias now resolves — the case that used to return "Assets");
  `frontend/tests/warehouses_under_inventory.test.js` (no standalone rail item, the Inventory
  page embeds the workspace with create + Product/Asset/Deleted tabs and the template-driven
  label, and sub-tab switching stays embedded). No schema change.

## [1.1.0] — build 144e: retire the duplicate Location resources page

- **Removed the standalone "Location resources" item** from the Settings rail. Resources are
  now managed only where they belong — inside each location (Settings → Locations → open a
  location → **Location resources**), matching how Orderry keeps resources in the location's
  own panel. No duplicate page, one place to add/remove a branch's bays, lifts, rooms, and
  equipment (added in 144d, unchanged here).
- **No dead links.** The old `resources` route now redirects into Settings → Locations, so
  any bookmark, deep link, or command that pointed at the retired page still lands somewhere
  sensible. The scheduler's "By resource" mode is untouched — it reads `/api/resources`
  directly, which still returns every location's resources.
- Tests: `frontend/tests/location_resources.test.js` now also asserts the standalone rail
  item is gone and the route redirects; routing-integrity confirms nothing is orphaned.
  No backend or schema change.

## [1.1.0] — build 144d: manage location resources inside each location

- **Resources live under each location now.** Opening or editing a location in
  **Settings → Locations** shows a **Location resources** section right under Working hours,
  where you can add and remove that branch's schedulable resources (bays, lifts, rooms,
  equipment) inline — no need to leave the panel. The list is filtered to that location,
  and adding one saves it against that location. A brand-new location asks you to save it
  first (a resource needs a location to belong to), then shows the controls on reopen.
- Same data as the standalone **Location resources** settings page — this just surfaces the
  create/delete for one branch where you'd expect it, so there's no second copy.
- Tests: `frontend/tests/location_resources.test.js` drives the real settings page in a DOM —
  it checks the section renders like Working hours, lists only this location's resources,
  POSTs new ones with the right `location_id`, and deletes via `DELETE /api/resources/{id}`.
  No backend or schema change.

## [1.1.0] — build 144c: Suppliers consolidated under Contacts

- **One home for suppliers.** The separate top-level **Suppliers** menu item is gone;
  suppliers now live under **Contacts → Suppliers** (matching Orderry, where suppliers are a
  contact type). The tab renders the full supplier workspace — the supplier list plus the
  **Incoming stock** sub-tab, price-list import, balance ledger, lead time — right inside the
  Contacts page. Nothing was removed: every supplier feature is intact, just in one place.
- **No duplication.** The old Contacts → Suppliers tab showed a second, thinner list
  (contacts flagged `is_supplier`); it now shows the same rich list the standalone page did,
  fed by `/api/suppliers`. A contact marked as a supplier still mirrors to that list as
  before, so there's a single source of truth rather than two parallel views.
- Any jump to the old `suppliers` route (command palette, a saved link) now lands on
  Contacts with the Suppliers tab open, so existing entry points keep working.
- Tests: `frontend/tests/suppliers_under_contacts.test.js` drives the real pages in a DOM —
  it confirms there's no top-level Suppliers link, that the Contacts Suppliers tab renders
  the rich list + Incoming stock from `/api/suppliers` (and does **not** fire a duplicate
  contact-filtered call), and that leaving the tab cleans up. Routing-integrity updated to
  expect suppliers under Contacts. No backend or schema change.

## [1.1.0] — build 144b: board view fixed (null-device crash)

- **The Board (Kanban) view works again.** Any work order whose device had a missing
  brand or model — e.g. a unit checked in by serial number only, shown as "null null" in
  the table — crashed the whole board: the server built the card with
  `None + " " + None`, which raised a `TypeError` and returned **500**, so the board never
  loaded. The card label now joins only the parts that exist and falls back cleanly, both
  on the server (board endpoint) and in the table/detail views (no more "null null").
- **Board is never blank on first visit.** The board endpoint now seeds the default status
  set on demand, the same way the statuses list already did, so a brand-new org sees its
  six columns immediately instead of an empty board.
- Tests: `backend/tests/test_board.py` — reproduces the 500 on a serial-only device and
  proves the board returns its columns/cards, covers a partial (brand-only) device name,
  and checks the self-seeding path. No schema change (migration head unchanged).

## [1.1.0] — build 144a: search results selectable + role permissions applied

Two fixes on top of build 144. (Build 145 was withdrawn; none of its settings
restructuring is included here.)

- **Search results are selectable again.** Hovering a command-palette result re-rendered
  the whole list, which replaced the row's DOM node between mousedown and mouseup — so the
  browser never fired a click and nothing could be picked. The highlight now moves by
  toggling a class instead of rebuilding, and the rows use delegated `mousedown` handlers
  on the list container, so a result stays clickable even if live search refreshes it
  mid-click. Keyboard (↑/↓/Enter) is unchanged.
- **Role permissions now take effect in the UI.** `/api/auth/me` returns the employee's
  effective permissions and the frontend hides what the role denies — same deny-wins rule
  as the API, so a role that denies nothing (including the built-in admin) is completely
  unaffected. Sidebar links gate on their area permission (Contacts → view client info,
  Finance/Cash → view cash flow, Settings → manage settings), and any element tagged
  `data-perm` disappears when unchecked: **+ Order** (create tickets), **+ Estimate**
  (create estimates), and the work-order **Invoices & payments** tab (view invoices tab).
  Re-checking brings the item straight back. Hiding is convenience only — the API still
  enforces every permission server-side (covered by a test).
- Tests: `frontend/tests/search_and_roles.test.js` drives a real DOM — it reproduces the
  unclickable-result bug against the old code and proves the fix, and checks each
  permission hides exactly its own control and nothing else.
  `backend/tests/test_role_permissions.py` covers the `/me` payload and server-side denial.
  No schema change: migration head unchanged, drift verified clean.

## [1.1.0] — build 144: workplace attendance clock + employee working hours

- **Workplace clock-in / clock-out.** The floating time-clock widget now has a second
  row for *attendance* — clocking in to the workplace (a shift), separate from the
  per-work-order job timers. New endpoints on the existing (previously unwired) `Shift`
  model: `POST /api/timeclock/shift/clock-in` (falls back to the org's default location
  when the employee has none, rejects a double clock-in), `POST /api/timeclock/shift/clock-out`
  (closes the open shift and logs `hours_worked`), `GET /api/timeclock/shift/active`, and
  `GET /api/timeclock/shift/history` (last 60 shifts + total hours). A live-ticking
  "On shift" timer shows elapsed time; Clock out toasts the hours logged.
- **Employee working hours.** New `employees.weekly_hours` column (migration
  `e2f4a6c8b1d0`) — expected hours/week, settable in **Settings → Team → employee profile**
  (Add/Edit modal + detail view) and returned by `GET /api/staff/employees`. Gives a target
  to track logged shift hours against.
- Note: per-employee commission % (labour/parts) already lives on the employee profile and
  feeds the payroll commission engine; no schema change was needed there this build.
- Tests: `test_build144.py` (clock in/out lifecycle, double clock-in + clock-out-with-none
  rejections, location fallback, history totals, `weekly_hours` create/patch round-trip).
  Migration + drift verified clean in a staged copy.

## [1.1.0] — build 143: message templates + AI reply drafting (Phases 4 & 8)

- **Message templates (Phase 4).** New `MessageTemplate` model + CRUD
  (`/api/clients/message-templates/all`, migration `d1a2b3c4e5f6`). The client's
  Communication composer gains an **Insert template…** picker that drops a saved snippet
  into the message.
- **AI reply drafting (Phase 8).** New `POST /api/clients/{id}/communication/suggest`
  drafts a reply from the last inbound message via the existing AI service — which runs a
  **deterministic stub** when no model key is set, so it works in every environment. The
  composer gets an **AI suggest reply** button that fills the draft and shows an
  **"✨ AI drafted (provider) — review before sending"** marker. AI never auto-sends;
  staff must press Send (covered by a test).
- Tests: `test_build143.py` (template CRUD + validation; AI suggest returns a draft, marks
  it AI-drafted, and does **not** add a message to history). New table → migration/drift
  verified clean in a staged copy.

_Honest scope note: the rest of Phases 4/8 (live SMS/WhatsApp/Gmail delivery, real-model
AI) are gated on external provider credentials, not code — those remain "needs real keys"
milestones, not sandbox-completable build tasks._

## [1.0.4] — build 142: all loyalty under the client profile (points + gift cards + coupons)

- The client's **Loyalty** tab is now the single home for loyalty — no separate/extended
  menu. Alongside points/tier it now lists the **gift cards issued to that client** (code,
  balance, status) with an **+ Issue gift card** action, and the **available coupons**
  (code, value, min-total, uses). Everything is under the profile tab.
- Reuses the existing engage endpoints (`/api/giftcards`, `/api/coupons`); the tab filters
  gift cards to the active client. Redemption still happens on the work order at checkout.
- Tests: `test_build142.py` (gift card issued to a client ties to them and shows in their
  list; coupons list is available; points remain on the profile tab). No schema change.

## [1.0.3] — build 141: location scope reaches Reports & Purchase Orders

- **Reports scope to a branch.** `GET /api/reports/run/{key}` (and both exports) accept
  `location_id`, threaded to the report builders; the order-based reports (work orders
  created/closed, per-order profit) filter to the selected branch. The Reports page and
  its CSV/XLSX exports pass the global scope.
- **Purchase Orders scope to a branch.** `GET /api/inventory/po?location_id=` filters POs
  by their location; the PO list passes the scope.
- Tests: `test_build141.py` (wo_created report scopes to a branch, empty branch = 0 rows;
  PO list scopes by location). No schema change; drift untouched.

_With this, the location switcher now scopes the dashboard, work orders + board, sales,
cash, finance, inventory stock, purchase orders, and order-based reports — the full app._

## [1.0.2] — build 140: inventory stock scoped by branch (+ transfers confirmed)

- **Stock scoped to the selected branch.** The Inventory stock list now shows each
  product's quantity **for the chosen location** (not the org-wide total) when a branch is
  picked in the topbar. `GET /api/inventory/parts?location_id=` filters the stock levels;
  the stock tab passes the global scope.
- **Inter-location transfers** (moving stock between branches) were already fully built —
  `StockTransfer` with from/to locations, transit statuses (draft → ready → in transit →
  received → completed), printing, and cancel — so that feature is confirmed and covered by
  a test here, not rebuilt.
- Tests: `test_build140.py` (per-branch stock quantities vs org-wide total; a two-location
  transfer is accepted while same-location is rejected). No schema change; drift untouched.

## [1.0.1] — build 139: location scope reaches Cash & Finance

- **Cash accounts** now scope to the selected branch: a location sees its own accounts
  **plus** company-wide accounts (those with no location). `GET /api/cash/accounts` gained
  a `location_id` param; the Cash page passes the global scope.
- **Finance invoices** scope by the location of the order they bill: `GET /api/finance/
  invoices?location_id=` filters (and its total count) via the invoice's order; the
  invoices list passes the scope.
- Tests: `test_build139.py` (accounts keep company-wide + hide other-branch tills;
  invoices scope by order location; empty branch returns none). No schema change; drift
  untouched.

## [1.0.0] — build 138: location scope reaches work orders & sales

- The topbar location switcher now scopes the **work-order list and Kanban board** and the
  **sales list**, not just the dashboard — pick a branch and the whole app follows it.
- **Work orders:** the list `_buildQuery` falls back to the global `App.locationId` (an
  explicit column filter still wins); the board fetch passes it too. `GET /api/orders/board`
  gained a `location_id` param to match the already-supported list endpoint.
- **Sales:** `GET /api/sales?location_id=` added; the sales page passes the current scope.
- Tests: `test_build138.py` (orders list/board scope to a branch; sales list scopes; empty
  branch returns nothing; org-wide default unchanged). No schema change; drift untouched.

## [0.99.0] — build 137: global location switcher + location-scoped dashboard

- **Global location switcher** in the topbar (auto-hidden when there's only one branch):
  pick "All locations" or a specific branch; the choice is persisted per device
  (`localStorage`) and re-renders the current page. Exposed app-wide as `App.locationId` /
  `App.locParam()`.
- **Location-scoped dashboard.** `GET /api/reports/company-dashboard?location_id=` filters
  every order-derived metric (today/monthly counts, status mix, by-assignee, receivables)
  to the chosen branch; omitting it keeps the org-wide view unchanged. The "My company"
  dashboard passes the current scope automatically.
- Tests: `test_build137.py` (dashboard scopes to a branch; empty branch shows zero; no
  param = org-wide). Frontend-only + one query param — no schema change; drift untouched.

## [0.98.0] — build 136: Locations (Settings) — CRUD + working hours

- **New Settings › Locations** page: full CRUD over branches (name, address, phone,
  timezone, receipt header) with per-day **working hours** (open/close + Closed toggle),
  active/archived status, and a guard that keeps at least one active location.
- **Backend:** expanded `GET /api/staff/locations` to full fields + new
  `POST/PUT/DELETE /api/staff/locations` (`can_manage_settings`); DELETE archives rather
  than hard-deletes (locations are referenced by orders/cash/warehouses). New nullable
  `Location.working_hours` JSON column, migration `c9e5a1b3d7f2`.
- **Already wired, surfaced here:** per-location **document/invoice numbering** works
  through the numbering service's location-scoped sequences (Finance & numbering), and
  **employee access** to a location is enforced by the role's `location_scope`
  (Employees & roles) — the Locations page points to both.
- Tests: `test_build136.py` (create/edit/archive + hours round-trip; can't archive the
  last active location). Column is nullable → migration safe on populated tables; drift
  verified clean in a staged copy.

## [0.97.0] — build 135: command palette (⌘K) + global search

- **⌘K / Ctrl+K command palette.** A keyboard-first overlay to jump anywhere and act fast:
  - **Go to** any of the 15 top-level pages.
  - **Quick actions:** New work order / customer / supplier / product / purchase order.
  - **Live search** of customers, work orders, and products (debounced, race-safe) — Enter
    opens the record.
  - Full keyboard nav (↑/↓/Enter/Esc), mouse hover sync, and a topbar **Search** trigger.
- Frontend-only (`command-palette.js` + CSS + a topbar button); no backend, schema, or
  markup-structure change. All 13 jsdom suites still pass.

_Note: the multi-location system you asked for (per-location working hours, WO/document/
invoice numbering, employee access, and location-scoped reporting) is a larger, multi-part
build — scoped as build 136 so it can be done carefully and fully tested rather than rushed
alongside the palette._

## [0.96.0] — build 134: app-wide premium polish (Apple · liquid glass · clay)

A cohesive, additive design pass — every rule targets an existing class, nothing is
renamed or restructured, so all 13 jsdom suites keep passing.

- **Inputs & selects:** calm resting state with a soft focus *halo* (4px blue glow) instead
  of a hard outline; light + dark variants.
- **Segmented tabs** (work order / reports / catalog picker): a gliding gradient underline
  pill that animates in on the active tab.
- **Work order:** the payment-action row is now one frosted-glass toolbar; the invoice
  totals block gets a weighted grand total with a hairline lead-in; the invoices list sits
  in a soft card.
- **Navigation:** sidebar and settings-rail items get a quiet idle state and an indigo wash
  + left accent bar when active.
- **Depth & finish:** badges read as clay chips, chips spring on press, dropdowns/toasts
  ride the same layered glass, and slim native-feel scrollbars app-wide (light + dark).
- Respects `prefers-reduced-motion`. CSS-only — no backend, schema, or markup change.

## [0.95.0] — build 133: Settings › Inventory page

- **New Settings › Inventory page** (Orderry parity). Controls: enable **bin locations**,
  enable **product reservations** + max reservation days, **unique product identifier**
  (Name/SKU/Barcode/Code), **default product type**, **product & asset serial-number
  templates**, **default vehicle type**, and a shortcut to manage **warehouses**.
- **Backend:** 8 new nullable `Organisation` columns (migration `b7c4e2f1a9d3`, chained
  from the real head `a3f1c9d2b4e7`) + `GET/PUT /api/staff/inventory-settings` that
  coalesces to sensible defaults and validates the identifier choice. `can_manage_inventory`
  gated.
- Tests: `test_build133.py` (defaults, full round-trip, bad-identifier 422). Columns are
  nullable so the migration is safe on populated tables; drift verified clean.

## [0.94.0] — build 132: purchase orders — group, print, send

- **Group POs** (Orderry parity). Select two or more *open* POs from the same supplier and
  merge them into one — lines for the same product are summed, sources marked `grouped`.
  New `POST /api/inventory/po/group`; the PO list gained row checkboxes + "Group selected".
- **Print / PDF.** New `GET /api/print/purchase-order/{id}` renders a proper PO document
  (supplier, dates, line table, total); a print button on each PO row opens it.
- **Send by Email / SMS / WhatsApp.** New `POST /api/inventory/po/{id}/send` logs the PO to
  the supplier's conversation on the chosen channel (delivery stubbed at the provider
  boundary, like every other channel); a Send button + channel picker on each row.
- Tests: `test_build132.py` (group merges & rejects mixed suppliers; send logs + rejects a
  bad channel; print returns a real PDF). No schema change — migrations/drift untouched.

## [0.93.0] — build 131: full granular roles & permissions matrix

- **Every function is now a per-role toggle (Orderry-style).** Settings → Employees &
  roles → *Roles & permissions* previously exposed only 8 hard-coded permissions; the
  editor now renders the **complete server catalog — ~68 functions in 8 groups** — from
  `GET /api/settings/rbac/catalog`, each as a checkbox you can grant or deny per role.
- **Role editor upgrades.** Grouped sections with **All / None** per group, a live
  **filter** box to find a permission fast, and the roles list shows *granted / total*.
  Deny-wins semantics: a function is allowed unless explicitly unchecked.
- **Wired to the real RBAC v2 API.** Create/update/delete now go through
  `/api/settings/rbac/roles` (the structured endpoint with scopes) instead of the legacy
  flat one; access conditions (contacts/orders scope, payroll visibility) are preserved.
  The employee count per role is computed from the live employee list.
- Tests: `test_build131.py` (catalog is rich; create/edit a role with a denied permission
  round-trips; unknown codes rejected 422). Uses existing models/endpoints — no schema
  change, migrations/drift untouched.

## [0.92.0] — build 130: product Batches tab + inbound/outbound history

- **Batches tab (Orderry-style).** Products gain a **Batches** tab showing each cost/stock
  layer: source document + supplier, price, received & remaining qty, amount, and expiry,
  with an **All / In stock / Out of stock** filter. New
  `GET /api/inventory/parts/{id}/batches` reads the `StockItemBatch` layers and joins the
  originating PO/supplier.
- **Directional stock history.** The product History tab now leads with a **Flow** column
  (▼ In / ▲ Out) so inbound receipts vs outbound sales/write-offs/transfers read at a glance.
- Tests: `test_build130.py` (empty batches for a new product; a seeded layer surfaces with
  correct remaining/amount/in-stock). No schema change — migrations/drift untouched.

_Queued next (this was scoped down from a larger request): the deeper WO layout redesign,
the Inventory Settings page (bin locations, serial templates, reservations), granular
per-function employee roles, PO grouping / printing / send-by-email-SMS-WhatsApp, and the
app-wide premium polish pass._

## [0.91.0] — build 129: invoice drawer with line selection + on-order redemption

- **Invoice-creation drawer (Orderry-style).** The work order's **+ Invoice** now opens a
  drawer listing every line with checkboxes (select-all + per-line), a live invoice total
  that updates as you check/uncheck, and payment-method chips. It bills exactly the chosen
  lines via `POST /api/cash/orders/{id}/invoices` (`line_ids`), so an order can be split
  across several invoices; leftover lines can be billed later.
- **Coupon / Gift card / Points back on the order.** Redemption of coupons, gift cards, and
  loyalty points returned to the work order's payment actions (their *management* stays on
  the client's Loyalty tab from build 127) — so discounts can actually be applied at
  checkout again.
- Tests: `test_build129.py` (invoice a subset of lines → partial total; a second invoice
  bills the rest → two invoices). No backend or schema change — the drawer uses the
  existing sub-invoice endpoint; migrations/drift untouched.

## [0.90.0] — build 128: custom-line name editable + discount toggle fix

- **Bug — custom line name wasn't editable.** Adding a custom labour/service/product left
  the line stuck as "New line" with no way to name it. The custom line now renders an
  editable name field (left-aligned, prominent), so it works like a proper add-item form:
  type the name, set qty/price/discount/tax/technician, and choose **Save to catalog**
  (as service or product) vs one-off. Front-end only; the payload already carried the name.
- **Bug — discount %/symbol dropdown overlapped.** The tiny discount-type toggle was too
  narrow for the native select arrow, so the arrow overlapped the "%". Dropped the custom
  arrow for that control and centred the glyph; it's clean now.
- No backend or schema change — migrations/drift untouched; all jsdom suites still pass.

## [0.89.0] — build 127: WO invoices tab, loyalty on client, premium polish

- **Work order → Invoices & payments tab.** The invoices/payments list moved out of the
  bottom of the Services panel into its own tab (Orderry-style), with a **+ Invoice**
  action that bills the whole order (`POST /api/cash/orders/{id}/invoices`) and a
  **Take prepayment** shortcut. Prepayment + Take payment stay on the work order per your
  note; the invoices list refreshes in place.
- **Loyalty moved to the client profile.** Removed **Coupon / Gift card / Points** from
  the work-order action bar and added a **Loyalty** tab on the contact showing points
  balance (+ cash value), tier, lifetime points, and program status
  (`GET /api/clients/{id}/loyalty`). Coupons/gift cards are issued from Sales → Marketing.
- **Premium liquid-glass + claymorphism polish.** An additive CSS pass deepened and
  layered card/modal shadows with a crisp top highlight, added a sheen + spring lift to
  primary/glass buttons, softened table and invoice-row hovers, and gave detail blobs a
  clay-chip finish — with matching dark-theme depth and a reduced-motion fallback. No
  class was renamed, so structure and the jsdom class-existence checks are untouched.
- Tests: `test_build127.py` (create-invoice-for-order, client loyalty shape). No backend
  logic or schema changed this build — migrations/drift untouched.

## [0.88.0] — build 126: editable assets, client comms, catalog custom lines

- **Editable asset detail.** The asset drawer became a tabbed, editable form
  (General / Work orders / History) instead of a read-only card. Backend
  `PUT /api/assets/{id}` (gated by `can_manage_inventory`) updates identity fields and
  supports re-owning We ↔ Client; the GET now returns `client_balance` so the owner's
  balance shows inline (as in Orderry). History tab reads the AssetMovement lifecycle log.
- **Client balance + adjustments, visible.** The Balance tab gained clear
  **+ Advance / − Payout / + Adjustment** buttons opening a quick modal, so balances and
  corrections are one click away instead of buried in a separate sub-tab.
- **Real Communication tab.** Replaced the activity-feed stub with a working
  cross-channel message log + composer: `GET/POST /api/clients/{id}/communication`
  records outbound messages (email/sms/whatsapp/social/call) and lists the full history.
  Delivery stays stubbed at the provider boundary like the other channels.
- **Bug — currency symbol on line items.** The catalog line-item discount toggle showed a
  hard-coded `₹` even after switching the org currency; it now uses the org symbol.
- **Custom lines → catalog (Orderry parity).** Adding a custom labour/service/product now
  offers "Save to catalog" (as a service or product) vs keep it one-off for this order.
  When saved, `POST /orders/{id}/lines` creates the `Service`/`Part` and re-points the
  line at it. New `LineIn` fields `save_to_catalog` / `catalog_kind`.
- Tests: `test_build126.py` (asset edit + re-own, communication send/list + bad channel,
  save-to-catalog service/product, one-off stays out of catalog). No schema change —
  migrations/drift untouched.

## [0.87.0] — build 125: currency display fix, ledger backfill, catalog modal

- **Currency now follows Settings.** `fmt.currency()` was hard-coded to `₹`, so changing
  the org currency in Settings → General never took effect anywhere (inventory, services,
  orders — new or existing items all stayed ₹). The base symbol is now sourced from the
  org: `/api/auth/me` returns `currency`/`currency_code`, `fmt.setBase()` sets it at boot
  (persisted in localStorage), and saving General settings refreshes it live with no
  reload. Stored amounts are unchanged — only the displayed symbol updates, which is the
  correct behaviour for a base-currency relabel.
- **Historical ledger backfill.** New idempotent `rebuild_org_ledger()` +
  `POST /api/staff/ledger/rebuild` (owner/finance) reconstructs the unified ledger from
  existing closed orders (charge), payments (income/prepayment/refund, now keyed by
  payment id), and balance-settled supplier postings/returns — then sets each touched
  contact's balance to the sum of its ledger, superseding any pre-ledger drift. Safe to
  run repeatedly; surfaced as a "Rebuild balances from history" button in Settings →
  General. This makes the Balance tab correct for pre-build-124 data, not just going
  forward.
- **Catalog modal height.** The "Add services & products" picker had no reserved height,
  so its dropdown spilled over the footer and looked cramped; the modal body now has a
  comfortable min-height and the list renders fully inside it.
- Payment ledger rows are now keyed `ref_type="payment"`/`ref_id=<payment id>` (was the
  order id) so each payment maps to exactly one ledger row, which is what makes the
  backfill idempotent.
- Tests: `test_unified_ledger.py` gains rebuild idempotency, history reconstruction, and a
  `/auth/me` currency check. No schema change — migrations/drift untouched.

Caveat: the rebuild derives balances purely from source documents, so a manual
`adjustment` that was previously entered to compensate for a missing charge could
double-count after a rebuild; review manual adjustments if you relied on them pre-124.

## [0.86.0] — build 124: unified contact settlement ledger

The Balance tab is now a *derived statement* (Orderry-style) rather than a manual
store-credit log. Since a supplier is a contact (build 114), there is **one** ledger and
**one** honest balance per contact, whether they're a customer, a vendor, or both.

- **New ledger service** `backend/services/ledger.py` — `post_contact_ledger()` appends a
  signed movement and moves the contact's running balance in lockstep (never silently).
  Convention: `+` = the contact owes us (A/R); `-` = we owe them (credit / A/P).
- **Auto-posting wired into the events that already fire:**
  - Order **closed** → `charge` (+total); **reopened** → the charge reverses. Reconciled by
    delta against the order total, so re-closing or editing the total never double-bills.
  - Any payment (cash/card/**close-on-account**) → `income` (−amount); prepayment →
    `prepayment` (−); overpayment refund → `refund` (+). The old silent
    `Client.balance -= amount` on close-on-account is gone — it now posts a real, audited
    row and nets correctly against the charge (fixes the ambiguous sign).
  - Supplier **postings / PO-receive-on-credit / purchase-returns** post to the *same*
    contact ledger (postings negative = we owe them; returns positive), so a supplier's
    Balance tab is a real statement, not a bare float.
- **New endpoints:** `GET /api/clients/{id}/payments` (Payments tab — full payment history
  from the Payment table), `GET /api/suppliers/{id}/ledger` (supplier Balance statement).
  The balance response now carries `ref_type`/`ref_id` so rows link to their source doc.
- **Frontend:** contact detail gains a **Payments** tab; the Balance tab renders the new
  entry types (charge/income/prepayment/refund/posting/return); suppliers list gains a
  **Balance** button opening the ledger modal.
- **Reporting:** the company dashboard adds true `ar` (sum of positive contact balances)
  and `ap` (sum of supplier balances) KPIs. Existing revenue reporting reads the Payment
  table and is unaffected — the ledger is additive, never double-counted.
- Tests: `backend/tests/test_unified_ledger.py` (charge/income/reopen idempotency, close-on
  -account sign, payments tab, supplier posting → unified ledger, dashboard A/R). No schema
  change (the ledger reuses `client_ledger_entries`), so migrations/drift are untouched.

Honest caveats: the derived statement is complete from build 124 forward (historical
orders aren't back-charged, to avoid double-counting against existing payments); the
Payments tab is fully historical since it reads the Payment table directly. Rounding is
baked into the closing charge rather than shown as its own row.

## [0.85.0] — build 123: Public pages polish + company country

- **Public pages redesign.** Both the Online booking and Order / Estimate cards
  moved off the generic `.card` (which had no light-mode padding — the cause of the
  clipped "YOUR PAGE URL" label) onto a dedicated `.pp-card` with proper padding,
  a cleaner cool-toned border, and a softer shadow. Content now flows into a
  responsive two-column `.pp-grid` of `.pp-sub` panels (Appearance / Booking
  settings; Header / Products & services), so the card fills the space efficiently
  instead of a tall single column with an empty right gutter. Wider max-width (900px).
  New CSS: `.pp-card`, `.pp-eyebrow`, `.pp-urlbar`, `.pp-grid`, `.pp-sub`,
  `.pp-sub-title`, `.pp-foot` (+ dark-theme variants). All input IDs unchanged, so
  save/saveOrder logic is untouched.
- **Company country.** New `Organisation.country` column (ISO 3166-1 alpha-2,
  nullable) with migration `a3f1c9d2b4e7`. Surfaced in Settings > General as a
  Country dropdown (60 countries) alongside Timezone and Document language, wired
  through the `/api/staff/company` GET/PUT endpoints.

## [0.84.0] — build 122: multi-currency applied + document language in print

Builds on 121's currency/language storage to actually apply them across documents.

- **Per-document currency, with client default.** A contact can be given a preferred billing
  currency (Client → Billing currency); their new work orders/invoices/estimates inherit it,
  and any document can override it from a new **Currency** picker in the order detail. Stored
  money always stays in the base currency; the document carries the chosen currency plus an
  **fx-rate snapshot taken at creation**, so later rate changes never alter old documents.
- **Amounts shown in the chosen currency.** When a document uses a non-base currency, its
  line prices, line totals, subtotal, tax and grand total all display converted with the
  right symbol, and a "Billed in <code>" note is shown. Client balances (and their ledger
  entries) display in the client's currency too. New `fmt.money(base, {symbol, rate})` helper
  does the conversion at display time.
- **Document language in printed output.** Printed invoices, estimates, work orders,
  receipts, postings and write-offs now translate their fixed labels (title, Item,
  Description, Qty, Price, Subtotal, Tax, Total, …) into the org's document language — English,
  Hindi, Spanish, French, German, Portuguese, Arabic, Russian, Chinese, Japanese. User content
  (names, descriptions) is left exactly as entered.

New endpoints/fields: `Client.currency_code`, `Order.currency_code` + `Order.fx_rate`,
`PATCH /orders/{id}/currency`, and a `backend.services.currency` resolver (override → client
pref → base) with base-relative rates from the org's currency list.

### Tests (build 122)
- Backend **359 passing** (was 353): `test_currency.py` (5 — base default, client-inherited,
  per-doc override, rate snapshot survives a rate change, client currency round-trip) plus a
  document-translation unit test. One migration (`Client.currency_code`, `Order.currency_code`,
  `Order.fx_rate`); zero drift; ruff clean. 575 routes.
- All **13** jsdom suites green.

## [0.83.0] — build 121: document language, multi-currency, conversion-stock fix

- **Fixed: asset→product conversion didn't add stock.** Converting an asset created the
  product but left it at 0 available (you had to add quantity manually) whenever the asset
  had no location. Conversion now falls back to the org's default location and always posts
  **+1 available**, recording a stock movement (`ref_type=conversion`) so the product's
  History shows where the unit came from.
- **Settings → General: document language.** New per-org **Document language** (English,
  Hindi, Spanish, French, German, Portuguese, Arabic, Russian, Chinese, Japanese) — the
  language for printed invoices and documents.
- **Settings → General: multiple currencies.** A new "Additional currencies" section lets
  you add currencies beyond your base one (code, symbol, and rate per 1 base unit), with an
  inline add/remove list. Stored on the org and returned from the company endpoint. Timezone
  list expanded too.
- **Bigger service/product tags.** The "Add services & products" picker had cramped 9.5px
  SERVICE / PRODUCT / BUNDLE tags that looked overlapped. They're now larger (10.5px, more
  padding), the meta row wraps with proper spacing, and bundles get their own violet tag —
  and the "Add a custom line" option remains at the bottom of the list for ad-hoc entries.

### Tests (build 121)
- Backend **353 passing** (was 349): `test_company_settings.py` (3 — doc language saved,
  multi-currency round-trips, defaults) and a conversion-adds-stock test. One migration
  (`document_language`, `currencies_json`); zero drift; ruff clean. 574 routes.
- All **13** jsdom suites green.

## [0.82.0] — build 120: asset lifecycle actions + supplier on inflows

- **Asset Transfer & Write-off.** The asset modal now has **Transfer** (move to a different
  owner/company, with a note) and **Write off** (mark damaged/lost/scrapped) actions, each
  recorded in the asset's history. New `POST /assets/{id}/transfer` and `/write-off`
  endpoints; double write-off is blocked.
- **Real asset movement log.** `AssetMovement` gained `event_type`, `note` and
  `created_by_id`, turning it into a full lifecycle log. Asset creation, transfers,
  write-offs and zone moves are all recorded, and the asset **History** tab now shows the
  complete audit trail — event, detail, who, and date — via `GET /assets/{id}/movements`,
  merged with the work-order history.
- **Supplier on Purchase Orders — with add-new.** The New PO modal's supplier dropdown now
  has an inline **➕ Add new supplier** option: name/phone/email, saved on the spot (which
  also creates the unified contact from build 114) and selected without leaving the modal.
- **Supplier on Postings.** The "New posting — bulk stock inflow" modal now has a supplier
  selector (also with inline add-new). Choosing a supplier tags each received movement with
  the supplier reference and adds the posting's total cost to that supplier's outstanding
  balance, so buying stock in updates what you owe them.

### Tests (build 120)
- Backend **349 passing** (was 344): `test_asset_lifecycle.py` (5 — creation logged,
  transfer changes owner + logs, write-off sets status + logs, double write-off blocked,
  posting-with-supplier updates balance). One migration (`asset_movements` +3 columns);
  zero drift; ruff clean. 574 routes.
- Frontend: `inventory_detail_tabs.test.js` extended for the Transfer/Write-off actions and
  the real movement-log history. All **13** jsdom suites green.

## [0.81.0] — build 119: inventory detail tabs + Actions bar placement

Closing the gaps from screenshot review of build 118.

- **Product detail (from Inventory) now has lifecycle tabs.** The inventory product
  quick-view was still a flat card ("Recent movements / No movements yet"). It now carries
  the same Orderry-style tabs as the catalog editor — **General**, **Stock** (per-warehouse
  available/reserved), **History** (every movement with type, reference, location, quantity
  and date), and **Purchase orders** — so a product opened from any screen shows its full
  lifecycle, not just its price card.
- **Asset detail now has tabs.** The Inventory → Devices asset modal was a flat six-field
  card showing only a work-order *count*. It now has **General**, **Work orders** (the full
  linked-ticket list, clickable through to each order), and **History** (lifecycle timeline:
  creation, each service ticket, and conversion events). Company assets keep the Convert to
  product action.
- **Work-order Actions bar moved below the table.** The bulk "N selected / Actions" bar was
  fixed to the bottom-left and floated over the last table rows. It's now a sticky toolbar
  that sits *below* the table (matching Orderry's bottom action bar) — it stays reachable
  while scrolling long lists but never covers order rows. The Actions menu still opens
  upward with its two-level submenus.

### Tests (build 119)
- Frontend: new `inventory_detail_tabs.test.js` (5 — product tabs + history load, asset tabs
  + work-order list + lifecycle history). `nav_restructure.test.js` updated for the sticky
  Actions bar. All **13** jsdom suites green.
- Frontend-only change (reuses existing endpoints) — backend unchanged: 344 tests, zero
  drift, 571 routes.

## [0.80.0] — build 118: bulk-actions fix + product lifecycle detail

Two follow-ups from screenshot review of build 117.

- **Fixed: work-order bulk Actions menu did nothing.** The menu opened but every item
  was inert — clicking "Change status" (or any item) closed the menu instead of acting.
  Root cause: re-rendering the submenu detached the clicked node, so the outside-click
  handler treated it as a click outside and closed the menu. The handler now tests the
  stable menu wrapper, and submenu-opening clicks stop propagation. All items work:
  Change status → status list, Assign to → tech list, Priority, Create invoices, Group
  invoice — verified end-to-end (open → submenu → apply → posts to backend).
- **Product lifecycle detail tabs.** Opening a product now shows Orderry-style tabs —
  **General** (the full editor), **Stock** (per-warehouse available/reserved), **History**
  (every posting, sale, transfer, write-off and adjustment with quantity, location, cost
  and date), and **Purchase orders** (open POs with ordered/received/expected). Backed by
  enriched `/parts/{id}/movements` (now returns location + cost), a new `/parts/{id}/stock`
  endpoint, and the existing `/parts/{id}/purchase-orders`. Products now carry a full,
  audited movement history rather than just their catalog fields.

### Tests (build 118)
- Backend **344 passing** (was 340): `test_product_detail.py` (4 — stock levels, enriched
  movement history, PO list, 404). No new migration; zero drift; ruff clean. 571 routes.
- Frontend: new `bulk_actions_menu.test.js` drives the menu (open → status submenu → apply
  → group invoice) against a mocked backend. All **12** jsdom suites green.

## [0.79.0] — builds 113–117: Orderry workflow alignment

A six-part pass reworking core workflows to match Orderry, from the customer's screenshots.

### Build 113 — Chats/Inbox merged into one
The separate Inbox page is gone. **Chats** is now the single multi-channel messaging
surface, backed entirely by `/api/inbox`: WhatsApp, SMS, email, Facebook, Instagram and
calls, each threaded and linked to the client. Channel + status filtering happen inline, so
there's no more page-reload lag when switching. New `POST /api/inbox/threads` starts an
outbound conversation on any channel (reusing an open thread instead of duplicating).

### Build 114 — Contacts: Client / Supplier unification
Contacts now has an **All / Clients / Suppliers** toggle (`?role=` on the list). A contact
can be marked a supplier at creation and can be **both** a client and a supplier. Creating a
contact-as-supplier auto-provisions a linked PO-facing Supplier; creating a Supplier directly
(from inventory) auto-provisions the unified contact. `Supplier.client_id` keeps the two in
sync; clearing the flag removes an unused mirror but preserves any with PO history.

### Build 115 — Floating action bar no longer overlaps
Bumped page bottom-clearance so the fixed timer widget and bulk-action bar never sit on top
of table rows. The bulk **Actions ▾** menu is now two-level: pick Change status / Assign /
Priority to open a focused submenu (with a back button) instead of one long flat list, plus
**Create invoices** and **Group invoice**.

### Build 116 — Client/Supplier profile tabs
The contact slide-over is reorganised into Orderry-style tabs: **General** (info, notes,
assets), **Balance** (running balance + full ledger), **Documents** (work orders),
**Advance / Payout** (post advances, payouts and manual adjustments), **Communication**
(unified message feed). New `ClientLedgerEntry` model + `GET/POST /api/clients/{id}/balance`
give an append-only, audited balance history (advance reduces what's owed, payout returns it,
adjustment is signed) with the contact's balance kept as the running sum.

### Build 117 — Assets → Products preserve full history
Converting a company- or client-owned asset to a product now snapshots its complete service
history — work orders, serials, owner, notes, purchase price — into the conversion's audit
record. The device row is preserved (not deleted) and linked via `converted_to_part_id`, so
all attachments, notes and work orders stay reachable, with a self-documenting trail of what
existed at conversion time.

### Tests (builds 113–117)
- Backend **340 passing** (was 326): `test_contact_supplier.py` (5), `test_client_balance.py`
  (5), plus new inbox-thread and asset-history tests. Two new migrations (`Supplier.client_id`;
  `client_ledger_entries`), zero drift, ruff clean. 570 routes.
- Frontend: `nav_restructure.test.js` reworked for the unified multi-channel Chats; Inbox test
  removed; all **11** jsdom suites green.

## [0.74.0] — build 112: dashboard, bulk actions, reports & finance polish

More UI/UX fixes from screenshots:

- **Dashboard stat tiles no longer overlap.** The percentage pill (e.g. "New 1 · 25%")
  was absolutely-positioned and collided with the number on the shorter small tiles.
  Small tiles now reserve bottom space so the number and its % pill sit cleanly apart.
- **Bulk-select action bar redesigned.** The big dark centered floating bar that covered
  the work-orders table is gone. In its place: a small, left-anchored clay/glass pill
  ("N selected") with a single **Actions ▾** dropdown grouping Change status / Assign /
  Priority / Create invoices. Compact, out of the way, and on-brand.
- **Report totals row restyled.** The cramped "Total" footer now has proper padding, a
  clay/glass tint, tabular-aligned numbers and rounded corners — reads as a proper summary
  row instead of a squashed line.
- **Native date pickers themed.** Date inputs across Reports, Work orders, Bookings and
  Estimates now carry a blue-tinted, hover-highlighted calendar control and tabular digits
  matching the clay/glass system, instead of the browser default.
- **Finance & numbering rebuilt for clarity.** Each area (Numbering, Legal entities, Taxes,
  Tax stack, Payment categories) is now its own titled clay **section card** with an icon
  and a one-line description, so the page reads as clear sections instead of a wall of
  floating headings. Tax rows use badges (Inclusive/Exclusive), right-aligned rates, and
  the tax-stack picker uses clay pills.

### Tests (build 112)
- Frontend: `nav_restructure.test.js` extended with 9 UX-fix guards (tile no-overlap,
  left-anchored bulk bar + dropdown, report-total padding, date-picker styling, finance
  section cards) and a Finance-page render test. All **12** jsdom suites green.
- Pure frontend/CSS change — backend untouched: 326 tests, zero drift, 567 routes.

## [0.73.0] — build 111: navigation restructure & tab styling

Acting on UI/UX feedback from live screenshots:

- **Inventory family folded back under Inventory.** Removed the top-level nav links for
  Stocktake, Transfers, Returns, Assets and Conversions — they were duplicates; those
  pages already exist as tabs *inside* the Inventory page. The nav is no longer cluttered
  with them.
- **Inbox moved under Chats.** Removed the standalone Inbox nav link. Chats now has a
  segmented **Chats / Inbox** tab bar — Chats for SMS/WhatsApp, Inbox for the multi-channel
  stream — so messaging lives in one place.
- **Clients renamed to Contacts; Suppliers promoted to a sibling.** The nav now shows
  **Contacts** and **Suppliers** as adjacent items so you can switch between the two
  address books directly. ("+ New client" → "+ New contact".)
- **Automations settings screen removed permanently.** The missed-call→task automation
  backend stays (it's used and tested); only the settings UI panel is gone.
- **Website widget moved into Integrations.** No longer a standalone settings page — it's
  now an "Website status widget" card under Settings → Integrations. Clicking **Get embed
  code** opens a modal with the copy-paste snippet, step-by-step instructions and a live
  preview.
- **Fixed flat, unstyled tabs (the main visual bug).** The `.tab` class had *no CSS rule
  at all*, so the Warehouses (Product / Assets / Deleted) and Suppliers tabs rendered as
  plain text. Added a proper **claymorphism + liquid-glass segmented control**: a
  translucent blurred pill group where the active tab lifts on a glassy chip with an inset
  top-light highlight and soft drop shadow, matching the rest of the design system.
- **Swept for the same defect elsewhere.** Audited every button/tab class against the
  stylesheet: found and fixed `.modal-x` (5 uses, undefined → now the styled
  `.modal-close`). All other interactive classes verified defined.

### Tests (build 111)
- Frontend: new `nav_restructure.test.js` (9 — `.tab`/`.tabs` CSS present with clay inset
  shadow, warehouse tabs use the segmented control, Chats hosts an Inbox tab). Updated
  `routing_integrity.test.js` to enforce the new structure (inventory pages are sub-tabs,
  inbox is under Chats, Contacts + Suppliers both in nav) and `fefo_widget.test.js` for the
  widget-in-Integrations modal. All **12** jsdom suites green.
- Backend unchanged: 326 tests, migrations apply, zero drift, 567 routes.

## [0.72.0] — build 110: production hardening — webhook auth, rate limiting, Postgres

The first go-to-market build: closes the security gaps that would matter the day real
traffic arrives, and proves the schema + queries against real PostgreSQL.

- **Webhook authentication.** Every org now has a `webhook_secret` (generated at
  registration, viewable/rotatable at `GET/POST /api/settings/webhook-secret[...]/rotate`
  with settings authority). The inbox webhook and the VoIP calls webhook **reject posts
  without a matching `X-Webhook-Token`** — previously anyone who knew an org id could
  inject fake messages/calls. Fail-closed once a secret exists; unknown org ids return
  404 (not 401) so the endpoint can't be used to probe which orgs exist. Rotation
  invalidates the old token immediately.
- **Twilio signature verification.** The chats webhook verifies `X-Twilio-Signature`
  (HMAC-SHA1 over URL + sorted form params) when `TWILIO_AUTH_TOKEN` is configured —
  the same fail-closed pattern as the existing Square/Stripe payment webhooks.
- **Rate limiting on the public lookup.** `/api/public/lookup` is now throttled by a
  sliding-window limiter (10/min) keyed **both** per caller IP and per order number, so
  neither one IP nor a distributed guess against a single order can brute-force the
  order-number + phone pairing. In-process by design (single-node deploy); swap for
  Redis when scaling out.
- **PostgreSQL compatibility — proven, not assumed.** Installed PostgreSQL 16 and ran
  the **entire 33-migration chain** against it: clean. Ran the payroll, inbox, FEFO,
  stock-document, work-order, reports/automation, schema-integrity and security suites
  against Postgres: **all green** after one test-harness fix (NullPool for asyncpg's
  per-loop connection affinity — `TEST_DATABASE_URL` env now selects the test dialect).
  Static audit confirmed no SQLite-only SQL in app code; partial indexes already declare
  both dialects; `asyncpg` added to dev requirements.

### Tests (build 110)
- Backend **326 passing** (was 320): `test_webhook_security.py` (6 — missing/wrong token
  rejected, org-probe returns 404, comms webhook gated, rotation invalidates, lookup
  brute-force throttled at the 11th attempt, sliding window expiry). Pre-existing webhook
  tests updated to present the token. 88 tests also verified green on PostgreSQL 16.
- One migration (`webhook_secret`, nullable — no backfill needed); zero drift; ruff
  clean; all 11 jsdom suites green. 567 routes.

## [0.71.1] — build 109 QA pass: linkage, schema & UI audit

A full software + UI/UX QA sweep over build 109. Findings and fixes:

- **Fixed: 5 orphaned pages.** `assets`, `conversions`, `returns`, `stocktake` and `transfers`
  were fully built (builds 84–95) but had **no navigation entry point** — unreachable by users.
  Added them under the Operations nav group next to Inventory, with existing icons and titles.
- **Fixed: N+1 query in the inbox thread list.** The list previewed each thread's last message
  with a separate query (up to 200 extra round-trips). Replaced with a single grouped
  latest-message query.
- **Verified: routing integrity.** No nav link points to an unregistered page; no `Router.go`
  target is unregistered; `mycompany` is a correct highlight-group alias (routes to dashboard),
  not a broken link. Every registered page is reachable via nav, the settings rail, or company
  tabs. Locked in by a new `routing_integrity.test.js`.
- **Verified: DB schema.** All 32 migrations apply from scratch with **zero drift**; 125 tables;
  every model maps to a table and every model column exists in the schema; **zero orphan foreign
  keys**; Phase C/D tables all indexed on `org_id`. Locked in by `test_schema_integrity.py` (4).
- **Verified: design-system consistency.** Topbar uses `glass`; dashboard uses the full clay
  palette; every button/card/chip/badge/modal class referenced by the new inbox, widget and
  expiring-stock UIs exists in `design.css`. Liquid-glass (`glass-liquid`, backdrop-blur +
  refraction) and claymorphism (`clay-*`, inset light/shadow) tokens confirmed intact.

### Tests (QA pass)
- Backend **320 passing** (was 316): +`test_schema_integrity.py` (4).
- Frontend: +`routing_integrity.test.js`; all **11** jsdom suites green. No new migration; zero
  drift; ruff clean. 565 routes.

## [0.71.0] — build 109: polish — FEFO clearance & website widget

> **Scope note.** Of the four "polish" items on the roadmap, two were already shipped and
> verified in the codebase: **warranty fields** (line-level `warranty_days`, `warranty_until`
> computed on close) and **SSO** (full config/discover/authorize router). This build delivers
> only the two genuine gaps.

- **FEFO batch depletion.** Write-offs and supplier returns now consume stock
  **first-expired-first-out**: `_take_stock` drains the earliest-expiry batches first and keeps
  `StockItemBatch.qty_remaining` in sync (NULL-expiry stock sorts last, so perishables go before
  undated goods). Parts with no tracked batches are unaffected — FEFO is a no-op for them.
- **Expiring-stock clearance list.** `GET /stock-docs/write-offs/suggestions` returns expired (or
  soon-to-expire) batches, earliest first, with value-at-risk totals — the actionable other half
  of the expiration report. Surfaced as "Expiring stock to clear" in the inventory Actions menu.
- **Public status lookup + embeddable widget.** `POST /public/lookup` finds an order from its
  number **plus** the customer's phone (both must match; a wrong phone returns the same 404 as an
  unknown order, so nothing is confirmed to a guesser). `GET /public/widget.js` serves a
  self-contained, dependency-free status checker a shop drops on their own site with one script
  tag; it looks up then renders through the existing `/track` view — no new status-rendering path.
  New **Settings → Website widget** page with a copyable snippet and live preview.

### Tests (build 109)
- Backend **316 passing** (was 310): `test_fefo_widget.py` (6 — earliest-expiry consumed first,
  non-perishable write-off unaffected, expired-stock suggestions + value-at-risk + window widening,
  lookup requires matching phone / no info leak, lookup token renders via track, widget.js served).
- Frontend: new `fefo_widget.test.js` (7 checks); all **10** jsdom suites green.
- **No new migration** (endpoints and logic only); zero schema drift; ruff clean. 565 routes.

## Phase C/D — builds 103–108 (roadmap execution)

> **Scope note.** A roadmap audit found that most planned Phase A/B work — estimates with
> online approval and e-signature, the week/timeline/resource scheduler, appointment
> reminders, the coefficient matrix, payroll drafts, feedback modes, employee groups,
> cancel-if-deleted clawbacks — was **already shipped** in earlier builds. Rather than
> rebuild it, these builds deliver only what was genuinely missing: the back-office and
> new-channel features of Phase C/D. External providers (WhatsApp/Meta, Shopify, SMTP,
> speech-to-text) are gated seams — the RepairOS-side workflow is real and tested; the
> third-party transport is stubbed at the boundary and marked as such.

## [0.70.0] — build 108: approval requests
- **Refund/discount ceilings as graduated approval**, not a blunt permission. At or below the
  requester's role ceiling auto-approves; above it, the request parks for someone authorised.
  `Role.refund_ceiling` added. A requester **cannot decide their own request**, and deciding
  needs settings authority — real separation of duties.

## [0.69.0] — build 107: e-commerce sync
- **Connected online stores** (`shopify`/`woocommerce`/`etsy`/`amazon`) via `EcommerceIntegration`.
- **Idempotent order sync.** `EcommerceOrderMap` keys on the external order id, so re-sending
  the same order **updates rather than duplicates** — the property most integrations get wrong.

## [0.68.0] — build 106: multi-account email
- **Up to 10 sending mailboxes** per org (`EmailAccount`), each with a per-account allow-list of
  who may send. An employee only sees and sends from accounts they're permitted to use.

## [0.67.0] — build 105: VoIP + AI depth
- **Calls carry AI results** — transcript, summary, sentiment enum — attached after the edge
  model runs. **Insights view** filters processed calls by sentiment.

## [0.66.0] — build 104: unified inbox
- **Every channel in one stream.** `InboxThread`/`InboxMessage` unify WhatsApp, SMS, email and
  FB/IG DMs, threaded per customer (the multi-channel superset of the existing Chats view).
- **Provider-agnostic inbound webhook**; threading matches on external-id or handle; a new
  inbound **reopens a closed thread**. Reply, assign, snooze/close, and **turn a conversation
  into a work order** in one step. New `Inbox` page (jsdom-tested).

## [0.65.0] — build 103: bulk payroll accrual
- **Accrue a whole team in one action** (`POST /api/payroll/run-all`). Anyone already accrued is
  **skipped, never double-paid**; one failure doesn't sink the batch; single- and bulk-accrual
  share one core so they can't drift. Current/future months still blocked.

### Tests (builds 103–108)
- Backend **310 passing** (was 297): `test_phase_cd.py` (13). Frontend: new `inbox.test.js`
  (10 checks); all **9** jsdom suites green. One migration; **zero drift**; ruff clean. 562 routes.

## [0.64.0] — build 97: automation, payroll statement, and the scheduler
- **Task from a missed call** (Settings → Automations). When an inbound call is missed,
  RepairOS raises a "call back" task so nobody has to be watching the call log to catch
  it. If the caller is a known client with an account manager, it routes to them;
  otherwise to a chosen fallback. Off by default; an answered call never spawns a task.
- **Printable payroll statement.** Every accrual and deduction over a period with a
  running total, in the same document layout as the stock papers. Access is restricted —
  an employee can print their own, and only someone who manages payroll can print a
  colleague's. Payroll figures aren't something to leak sideways.
- **Scheduler now shows appointments, not just work orders.** The day view was built
  earlier but only ever drew orders; build 92 put bookings in the same feed, and now they
  render (in purple, tagged as appointments), drag to reschedule against the *bookings*
  endpoint rather than the order one, and click through to the bookings page. One honest
  picture of the day.

## [0.63.0] — build 96: warehouse reports and bulk product actions
- Three **inventory reports**, all catalog-driven so they get CSV export for free:
  - **Assortment by warehouse** — what's stocked and where, valued at moving-average cost
    (what the goods actually cost, not what we hoped to pay). "Stock levels" flattened the
    warehouse dimension away; this keeps it.
  - **Purchase list** — what to buy. A product is short when the shelf, *minus what's
    already promised to a customer*, falls below its minimum. Anything already on an open
    PO is netted off, so the same part isn't ordered twice while the first delivery is
    still in transit.
  - **Expiring stock** — batches by days-to-expiry. Already-expired stock is reported as a
    negative day count rather than hidden — that's the thing the report exists to surface.
- **Bulk product actions.** Select a filtered set and change category, minimum stock,
  active state, or price in one call. Price changes are a **percentage**, not an absolute —
  the only thing that makes sense across a mixed selection: +5% means the same intent for
  a £2 cable and a £200 board.

### Fixes (found while building)
- Purchase-order queries in the new report and the build-94 incoming-stock endpoint now
  scope by supplier org (`PurchaseOrder` carries no `org_id`); previously an open PO in one
  org could leak into another org's figures.

### Tests (builds 96–97)
- Backend **297 passing** (was 282): `test_reports_bulk_automation.py` (15 — the three
  reports including reserved-vs-free and on-order netting, expired/soon flagging and value-
  at-risk, CSV export; percentage price bulk-edit proving equal intent across different
  absolutes, min-stock/active bulk edits, bad-input guards; missed-call automation off by
  default, task creation when on, account-manager routing, answered-call no-op, unknown-key
  rejection; payroll-statement PDF and its cross-employee access guard).
- Frontend: new `frontend/tests/reports_bulk_automation.test.js` (jsdom, 14 checks incl.
  the scheduler drag-reschedule routing bookings vs orders correctly). All **eight** jsdom
  suites green.
- One migration (`automation_settings`, one new table); **zero schema drift**; ruff clean.
  545 routes.

## [0.62.0] — build 95: labels, price tags, and document printing
- **Settings → Labels & price tags.** A template sets the size, what appears on it, and
  which barcode symbology to use; printing is then just "these products, that template".
  A price tag can pull from a specific **price list**, so trade and retail tags print
  different numbers from the same product.
- Barcodes are drawn as **vectors** (reportlab's own Code128/QR) rather than rasterised
  images — a fuzzy barcode is a barcode that doesn't scan. No extra dependency, and no
  image round-trip.
- Printable documents: **posting, write-off act, supplier return, transfer note** (which
  shows shipped vs received side by side) and the **stocktake settlement act**.
- A label smaller than 20×10mm is refused — a barcode wouldn't scan at that size.
- Write-off comments marked **internal never reach the printout**; only public ones do.

## [0.61.0] — build 94: suppliers and purchase-order reservations
- **Supplier CRUD.** Suppliers were read-only, which made the balance that postings and
  returns maintain oddly unreachable. A supplier with an outstanding balance or open POs
  can't be deleted — the debt is real and deleting the counterparty wouldn't settle it.
- **Price-list import** using the build-82 column-mapping engine, pointed at products.
  Matching is on **SKU or barcode, never name** — names aren't stable enough to key on —
  and a **dry run** shows what would change first, because a bad mapping here would
  rewrite the cost basis of the whole catalogue.
- **Reserve incoming goods for a customer.** A PO line can be tied to the work order
  waiting on the part; when the goods land, the reservation becomes real. Otherwise the
  part gets sold over the counter to the next person through the door while the customer
  who's been waiting a week is told "still on order".
- Incoming stock shows what's on the way, what it's promised to, and flags a PO whose
  expected date has quietly gone by as **delayed**.

## [0.60.0] — build 93: postings as documents + transfer verification
- **Postings are documents now.** A mistyped purchase price used to be permanent and
  quietly poisoned the moving-average cost. A posting can now be deleted (stock comes
  back out, the supplier debt unwinds), and markup pricing writes the sell price — or a
  specific price list — from what the goods *actually* cost, at the moment they land.
  Reversal deliberately leaves `avg_cost` alone: un-averaging isn't a well-defined
  operation, and faking it would corrupt the cost basis of everything else.
- **Transfers are verified on arrival.** A box that leaves with ten and arrives with
  nine is the normal case this exists for. Completion now credits the destination with
  what **actually arrived**, not what was shipped, and the shortfall is booked as a real
  loss at the source rather than silently reappearing as stock at the far end.
- Transfer lines can be built by **scanning** — repeat scans accumulate.

## [0.59.0] — build 92: bookings share the calendar
- The scheduler returned only work orders, so it would happily offer a slot an
  appointment already owned. Bookings now appear in `scheduler/day` tagged by `kind`,
  and **count as busy in free-slots** — one honest picture of the day instead of two
  views that each omit half of it.
- (Builds 86 already shipped task notifications and the task payroll hook, so nothing
  further was needed there.)

## [0.58.0] — build 91: stocktake depth
- **Counting by scanner.** Each scan *increments* the count rather than setting it —
  that's what pointing a scanner at a shelf actually means. An unrecognised code is
  refused, so a typo can't silently invent stock; with `register_unknown` it's added as
  a new product and flagged for review before posting. **Posting is what brings a
  found-on-the-shelf product into stock.**
- **Full vs partial counts.** In a partial count an item you never scan is left alone,
  rather than being treated as zero on the shelf.
- **Count sheets in and out**: export a blank sheet, hand it to whoever's counting, then
  import what they wrote down. An imported quantity is the **total** (it's the sheet's
  number), unlike a scan.
- **Serial mismatch warnings** when a scanned unit is filed to a different warehouse.
- Lines filter by the kind of difference — **surplus / shortage / match / uncounted** —
  which is the question you're actually asking when reviewing a count.
- **Settlement act**: every discrepancy valued at cost, with surplus/shortage/net totals.
- Stocktakes are now addressed by **warehouse**, and a posted one is locked.

### Tests (builds 91–95)
- Backend **282 passing** (was 261): `test_stocktake_postings_print.py` (21 — full-vs-
  partial seeding, scan increments + unknown refusal/registration, unknown product
  entering stock on post, difference filters, CSV import + settlement valuation, posted-
  count lock, bookings in the scheduler and as busy time, posting stock/avg-cost/markup,
  posting delete unwinding stock and supplier debt, paid-posting guard, transfer
  crediting only what arrived + over-receive guard, transfer scanning, supplier CRUD +
  balance delete guard, price-list dry-run vs real import + match-key validation, PO
  reservation surviving into stock, delayed PO detection, label template validation, PDF
  generation, and that an internal write-off comment stays off the printout).
- Frontend: new `frontend/tests/stocktake_suppliers_labels.test.js` (jsdom, 22 checks).
  All **seven** jsdom suites green.
- One migration for all five builds; **zero schema drift**; ruff clean. 541 routes.

## [0.57.0] — build 90: asset warehouses + status-driven movement
- Every **client** and **technician** now has their own asset warehouse, created on
  first use, alongside company-wide internal ones (**Pending repair**, **Ready for
  pickup**).
- **The asset follows the job**: check-in moves it to Pending repair, assigning the
  order moves it into the technician's warehouse, marking it ready moves it to Ready
  for pickup, and closing the order sends it home to the client's warehouse. This runs
  off the same order-status hook that already drives payroll and notifications, so
  there's one place where "a status changed" means something.
- Movements are logged as `AssetMovement` rows, so an asset's whereabouts has a history
  rather than just a current value.

## [0.56.0] — build 89: per-warehouse actions + per-employee access
- A warehouse declares which operations it **permits at all**
  (`post / writeoff / transfer / sell / stocktake`) — a receiving bay can accept stock
  but refuse write-offs, and it refuses them from everyone, owner included.
- On top of that, **Settings → Warehouses → Access** grants rights per employee per
  warehouse. Three layers resolve most-specific-first: the warehouse's allowed actions,
  then an explicit per-employee grant, then (if there's no explicit row) the role's
  warehouse scope from build 83. Build 83's scope became the *fallback* rather than a
  second, competing system.

## [0.55.0] — build 88: write-off documents
- A write-off is now a **document**, not a fire-and-forget movement. **Deleting it
  restores the stock** — that's the entire point. Editing a line's quantity settles the
  difference against stock rather than requiring a compensating entry, so the books stay
  true instead of merely balanced.
- Lines carry comments that are **internal by default** and can be made client-visible.
- Writing off more than the warehouse holds is refused with the actual figure, not a
  generic error.

## [0.54.0] — build 87: returns to supplier
- `POST /api/stock-docs/returns` — the mirror of a posting. Stock leaves the warehouse
  and the money is credited back either to the **supplier's balance** or as a cash
  refund. Deleting the return puts the goods back and undoes the credit.
- A return that has **already been paid out can't be deleted** until its payment is
  removed — otherwise the ledger and the stock would disagree.

## [0.53.0] — build 86: Tasks, properly
- Tasks now carry the context the assignee actually needs — links to a **work order,
  client, lead, location and parent task** — plus an **event feed** with comments, built
  on the same feed machinery the work orders use.
- **On-time analysis**: completing a task records whether it beat its due date. That
  makes tasks payable — a new `task_closed` commission event reuses build 70's
  `on_time` / `overdue` execution terms unchanged, so an on-time close pays and a late
  one doesn't.
- **Internal notifications** on assigned / changed / completed, targeting *employees*
  (the rules engine previously only knew how to reach clients). Orderry's rule holds:
  **you're never notified about something you did yourself.**
- "My tasks" (assignee **or** author), overdue filter, due-date sort, and delete.

## [0.52.0] — build 85: bookings that connect to the rest of the app
- **`GET /api/bookings/availability`** — free slots computed from the employee's **work
  schedule** (build 73), with both existing **bookings and scheduled work orders**
  counting as busy, so the shop can't be double-booked from two different screens. A
  day off returns no slots and says why.
- **`POST /api/bookings/{id}/to-order`** — an appointment becomes the job: client, time
  and note carry over, the booking is marked done and linked. One order per booking.
- **Booking notifications finally fire.** `module="booking"` has existed in the rules
  engine since build 80 but nothing evaluated it; confirmations now send, and
  `queue-reminders` sweeps bookings as well as work orders (idempotently).

## [0.51.0] — build 84: warehouses are real entities
- **Settings → Warehouses.** A warehouse is now a first-class thing, not an implicit
  consequence of a location. Two kinds, following the industry template so the UI says
  *Devices* / *Vehicles* / *Tools* as appropriate:
  - **Product warehouses** hold shop inventory.
  - **Asset warehouses** hold the items being serviced (see build 90).
- **Reach**: a *location* warehouse is usable only from its location; a *company*
  warehouse is usable from anywhere.
- **Deleting a warehouse that still holds stock is refused** (with the actual balance)
  — deleting it would silently lose the goods. A location's default warehouse can never
  be deleted. Deletes are soft, so warehouses are **restorable**.
- **Compatibility shim**: every location keeps one default product warehouse, and the
  migration backfills it and re-points existing stock at it. Endpoints still accept
  `location_id` and resolve it to that warehouse, which is why 239 existing tests kept
  passing while stock became warehouse-addressed underneath.

### Tests (builds 84–90)
- Backend **261 passing** (was 239): `test_warehouses_tasks_docs.py` (22 — default
  warehouse per location, warehouse-addressed stock, product/asset creation,
  template-driven label, delete-blocked-with-stock + restore, default undeletable,
  disallowed-action 403, per-employee access override, write-off document +
  delete-restores-stock + line-edit-settles + over-write-off refusal, supplier return
  credits/removes/restores + paid-return guard, booking availability vs schedule and
  day-off, booking→WO + duplicate guard, idempotent booking reminders, task links +
  feed, on-time flags, task commission only when on time, delete + "my tasks", and the
  full asset-follows-the-job lifecycle).
- Frontend: new `frontend/tests/warehouses_tasks.test.js` (jsdom, 21 checks). All six
  jsdom suites green.
- One migration for all seven builds; **zero schema drift**; ruff clean on CI paths.

## [0.50.0] — build 83: record-level permission scoping
- Roles already gated **features** (`can_x`). They now also gate **records**
  (Orderry "access conditions"):
  - **Contacts: all / own** — "own" shows only the clients that employee manages.
  - **Work orders: all / own** — "own" shows only orders assigned to them
    (plus unassigned ones, so nothing is stranded).
  - **Warehouses: all / selected** — stock moves are refused outside the allowed
    warehouses (403).
  - **Own-compensation visibility window** (days) and **per-account** scoping.
- Filters are applied **in the query**, not after fetching, so **pagination and
  totals respect the scope** rather than showing counts the user can't open.
- Scoping limits *visibility*, not *association*: a scoped employee can still attach
  any client to an order — they just can't browse the whole contact book.
- **Default-allow**: no role, or scope left at "all", behaves exactly as before, so
  existing installs are unchanged (there's a test that proves it).
- New **EmployeeGroup** model + CRUD — groups are separate from roles (roles grant
  permissions; groups are for assignment/notification targeting).
- Staff → Roles now has an **Access conditions** section.

## [0.49.0] — build 82: CRM data ops
- **Client import** (`POST /api/clients/import`) with an explicit **column mapping**,
  match-on-phone-then-email, and **update-in-place instead of duplicating**
  (Orderry's "update by import"). **`dry_run`** returns exactly what *would* happen —
  the UI shows a preview before anything is written. Bad rows are reported per-row
  rather than failing the whole file, and an empty import cell never blanks an
  existing value.
- **Client merge** (`POST /api/clients/merge`) re-points devices, orders, invoices,
  payments and feedback onto the target, fills the target's blanks from the sources
  (never overwriting real values), sums balances, and only then removes the sources —
  so no history is orphaned.
- **CSV export** (`GET /api/clients/export/csv`), which respects the caller's
  contact scope.
- Clients page gains **Import** (paste CSV → map columns → preview → import),
  **Export**, and a **Merge** dialog.

## [0.48.0] — build 81: feedback report, chat → work order, client history tabs
- **Feedback report** (`GET /api/feedback/report`): average, score distribution, and
  a **real NPS** figure (%promoters − %detractors) — which is *not* an average and
  can't be derived from one. Unsubmitted invitations are excluded from the totals.
  (The 5-point / NPS / like modes and score-conditional band responses already
  existed — verified rather than rebuilt.)
- **Chat → work order** (`POST /api/chats/threads/{id}/to-order`): creates the client
  from the chat contact if there isn't one, seeds the complaint from the customer's
  first inbound message, and links the thread to the order so the conversation stays
  attached to the job. One order per thread (409 on a repeat).
- Client profile: **calls** and **feedback** history endpoints
  (`/{id}/calls`, `/{id}/feedback`) — the models existed but were never surfaced.

### Tests (builds 81–83)
- Backend **239 passing** (was 223): `test_scoping_crm_chat.py` (16 — feedback
  average/distribution/NPS/empty, chat→order incl. duplicate guard, import
  mapping/update-by-phone/dry-run/bad-rows, merge history move, CSV export,
  contact + order scope enforcement on list *and* detail, default-scope no-op,
  warehouse scope 403, role scope round-trip, employee-group CRUD).
- Frontend: new `frontend/tests/crm_scoping.test.js` (jsdom, 20 checks). All five
  jsdom suites green.
- One migration for all three builds; **zero schema drift**; ruff clean on CI paths.

## [0.47.0] — build 80: notification rules engine
- New **NotificationRule** model + `/api/notifications/rules`. A *rule* decides
  **whether and when** to send; a *template* stays **what** gets sent.
- Rules support: event triggers (created / status_changed / closed) scoped to a
  **status** and a **location**; **delayed sending** (e.g. ask for feedback 60 min
  after close); **working-hours-only** (a send that would land at 23:10 is held to
  the next working morning); and **appointment reminders** fired N minutes *before*
  an order's `scheduled_for`.
- Delayed/reminder sends are written to `NotificationLog` with `status='scheduled'`
  and a `send_at`; `POST /api/notifications/rules/run-due` (a worker tick) flushes
  the ones that have come due, so nothing depends on an in-process timer.
  `POST /api/notifications/rules/queue-reminders` scans upcoming appointments and is
  idempotent — no double reminders.
- The rules engine is deliberately **not** gated on a status's legacy
  `triggers_notification` flag (each rule carries its own status trigger; gating
  would mean enabling a notification in two places). Legacy templates keep the old gate.
- `GET /api/notifications/variables` — the template variable library grouped like
  Orderry's (Company / Client / Order / Employees / Finance / Date / Other), plus a
  variable auto-generated for **every custom field** the org has defined.

## [0.46.0] — build 79: markup auto-pricing
- `POST /api/inventory/parts/apply-markup` sets sell prices from cost + markup,
  using the **moving-average cost** by default so pricing tracks what stock actually
  cost rather than the last quoted price. Products with no cost basis are left alone.
- (FEFO expiry write-off and own/supplier warranty fields were already present —
  verified rather than rebuilt.)

## [0.45.0] — build 78: merge duplicate products
- `POST /api/inventory/parts/merge` folds up to 10 duplicates into one target:
  stock levels are summed per location, and movements, serialized units and batches
  are re-pointed. Blank fields on the target are filled from the sources
  (**real values are never overwritten**). Sources are **deactivated, not deleted**,
  so historical documents stay intact.
- A **serialized product can only merge with a serialized product** — mixing the two
  would break unit identity.

## [0.44.0] — build 77: Conversions UI
- **Inventory → Conversions** is now the full two-way conversion document log
  (it replaces the old one-way "assets that became products" list): filter by
  direction and date, click through to the product or asset, see who converted what
  and when, and see which documents have been **reversed**. There is deliberately
  **no delete action**.
- Conversion modals: **Product → Asset** prefills from the stock unit and can hand
  ownership to a client/supplier. **Asset → Product** surfaces **possible duplicate
  products** so you link one instead of creating a second, never auto-fills the SKU
  from the serial, and offers an opt-in "add to inventory as a serialized unit".

## [0.43.0] — build 76: Product ↔ Asset conversion
- New **AssetProductConversion** immutable document + `/api/conversions`.
  A **Product** is shop-owned inventory; an **Asset** is a physical item being
  tracked/serviced. The two concepts stay separate — conversion moves an item
  between them and records exactly what happened.
- **Product → Asset**: only for **serialized** products with a real serial (the
  serial is what proves the physical item matches inventory). A unit on an **open
  work order cannot be converted** (close the order or remove the line first).
  Converting a company unit **removes it from available stock**, links the unit to
  the new asset, and writes the conversion document.
- **Asset → Product**: creates *or links* a product (dedupe search runs first),
  preserves template/group/brand/model metadata, optionally creates a serialized
  stock unit if the asset becomes company inventory, and **never turns the serial
  into the SKU** without the user confirming.
- **Documents are immutable** — `DELETE` returns 405. Reversal is the *opposite*
  conversion, which writes a second document, marks the first `reversed`, and links
  the pair. The pair is the audit trail.
- New permissions: `can_convert_product_to_asset`, `can_convert_asset_to_product`.

### Tests (builds 76–80)
- Backend **223 passing** (was 198): `test_conversions.py` (12 — serialized-only,
  open-order guard, double-conversion guard, inventory effects, prefill, dedupe
  linking, optional stock unit, immutability, reversal-by-opposite-conversion,
  list filters, permission enforcement) and `test_inventory_notif_polish.py` (13 —
  merge folding/blank-fill/serialized-mismatch/cap, markup on both bases,
  rule delay → scheduled, run-due dispatch, immediate send, working-hours shift,
  reminder queueing + idempotency, location scoping, variable library).
- Frontend: new `frontend/tests/conversions.test.js` (jsdom, 19 checks). All four
  jsdom suites green.
- One migration for all five builds; **zero schema drift**; ruff clean on CI paths.

## [0.42.0] — build 75: work-order transfer + estimate approval timestamp
- **Transfer a work order between locations**: `POST /api/orders/{id}/transfer`
  moves an order to another of the org's locations (validated, logged to the feed).
- **Estimate approval is now timestamped.** Public estimate accept/decline with
  e-signature and auto-convert already existed; acceptance now also stamps
  `Order.estimate_approved_at`, so *when* the customer approved is auditable
  independently of whatever status the estimate later lands in. Exposed on the
  order serializer.

## [0.41.0] — build 74: scheduler free-slot search
- `GET /api/orders/scheduler/free-slots?date_str=&duration_min=&employee_id=&resource_id=`
  returns the open windows of at least `duration_min` on a day, sweeping around
  existing booked orders. Honours the employee's **planned shift** bounds from
  build 73 (falls back to 09:00–17:00), and returns **no slots** when the employee
  is off (day off / vacation / sick). Works per technician, per resource, or both.

## [0.40.0] — build 73: work schedules & timesheets
- New **WorkSchedule** model + `/api/schedules` CRUD: planned shifts (start/end)
  and whole-day absences (day off, vacation, sick). This is the *plan*; `TimeEntry`
  remains the *fact*, and payroll still runs off the fact (Orderry semantics).
- `GET /api/schedules/compare` returns **planned vs actual hours and the variance**
  for an employee over a range, plus the absences in that window — the check a
  manager makes before accruing payroll.

## [0.39.0] — build 72: exceptional commissions & coefficients
- **Exceptional-commission priority**: rules now carry a `rule_level`
  (product → category → profile/generic). The highest tier present wins and tiers
  **do not stack**, matching Orderry.
- New **EmployeeCoefficient** model + `/api/payroll/coefficients` (upsert/list/delete):
  a per-employee seniority multiplier scoped to a **product, service, or category**,
  held separately for the **work-order vs sales** context. Item-specific beats
  category; default 1. Applied on top of the winning rule.

## [0.38.0] — build 71: payroll calculation & accrual workflow
- `GET /api/payroll/calculation?month=YYYY-MM` — a **live, non-mutating preview**
  of every employee's month: base salary, commissions and hourly wages already in
  the ledger, computed total, and whether an accrual already exists.
- **Accrual guards** (Orderry): can't accrue the **current or a future month**
  (save a **draft** instead), and only **one accrual per employee per period** (409).
  Drafts are previews — they never post to the balance ledger.
- `GET /api/payroll/runs` and `POST /api/payroll/runs/{id}/pay` (payout draws the
  balance down; a draft must be accrued before it can be paid).

## [0.37.0] — build 70: payroll rule conditions & timing
- Commission rules gain **conditions**: restrict to a **work-order type**, and to an
  **execution term** (any / on-time / overdue / urgent).
- **Calculation timing**: `on_close` (default), `immediately` (any status change),
  or `on_status` (fires when the order reaches a chosen trigger status — pay before
  the job closes). Existing idempotency keys mean re-entering the trigger status, or
  later closing the order, **cannot double-pay**.
- **Cancel-if-deleted** (default on): archiving/deleting an order reverses its
  commissions as a current-period adjustment rather than rewriting history. Turn it
  off per rule to make a commission stick.
- Two new events wired through: `assignee_labor`, `assignee_product`.
- **Settings → Payroll**: the rule form now has a Conditions section (timing,
  trigger status, term, WO type, cancel-if-deleted) and the rules table shows a
  conditions summary per rule.

### Tests (builds 70–75)
- Backend **198 passing** (was 179): `test_payroll_depth.py` (11 — WO-type and
  urgent conditions, on-status timing, the no-double-pay guard, cancel-on-archive
  both ways, coefficient multiplication, coefficient upsert, current-month rejection
  + draft, duplicate-accrual 409 + payout, calculation preview) and
  `test_schedules_slots_estimate.py` (8 — schedule CRUD/validation, planned-vs-actual
  variance, free slots around bookings and on days off, transfer, estimate timestamp).
- Frontend: new `frontend/tests/payroll_conditions.test.js` (jsdom, 15 checks).
- One migration for all six builds; **zero schema drift**; ruff clean on CI paths.

## [0.36.0] — build 69: prod lint/runtime fixes, Copy Public Link, email + QBO OAuth

### Fixed — production-blocking lint/runtime issues
- **`cash.py` crash on card/cash payments** — `Organisation` was referenced but
  never imported (F821), so the default-cash-flow-item lookup 500'd. Imported it.
- **`assets.py` dropped the asset-type fallback** — a duplicated `asset_type` dict
  key (F601) overrode `d.asset_type or "Device"`. Removed the shadow key.
- **`catalog_products.py` import closure** — the per-row `g()` helper captured the
  loop variable `cells` (B023); bound it as a default arg.
- Removed a raft of dead `count`/`inv_count`/`cnt` count-queries (F841) across
  orders/inventory/leads/sales/transfers/v1/payments/reports — they were leftover
  round-trips (numbering is done by `next_number`). Whole-repo
  `ruff check --select F821,F601,B023,F841` is now clean.

### Added — Copy Public Link mints a token on demand
- `POST /api/orders/{id}/public-link` returns the tracking link, minting a
  cryptographically secure token (`secrets.token_urlsafe`) the first time it's
  requested — older/seeded orders without a token now work. Idempotent; org-scoped.
- Frontend "Copy public link" / "Copy tracking link" now call it and copy
  `/track/{token}`; the detail-view button always shows.

### Added — Gmail/Outlook email finished
- **Settings → Integrations** now has an **Email sending** section: per-provider
  Connect (gated on server creds), connected-accounts list, **Make default**, and
  **Disconnect**.
- Callback now **fetches the account's own address** from the provider (Google
  userinfo / Graph `/me`) and **encrypts tokens at rest** (Fernet via new
  `services/crypto.py`, keyed by `TOKEN_ENCRYPTION_KEY`; plaintext-tolerant so
  toggling the key never breaks the send path).
- New `POST /api/integrations/email/{id}/default`; disconnect promotes another
  account if the default is removed.
- **Work Order → Message customer** now sends through the connected OAuth account
  first and **falls back to SMTP** (response reports `delivery: oauth|smtp|simulated`).

### Fixed — QuickBooks OAuth
- **Callback works without a Bearer token.** The Intuit redirect carries no auth
  header, so the old callback 401'd. Context is now recovered from **HMAC-signed
  state** (`{conn_id}:{nonce}:{sig}`, verified against the stored nonce). Tampered
  state → 400.
- **Token refresh** before every push (60s skew), persisting rotated + encrypted
  tokens; access tokens are encrypted at rest.
- QBO card gains a **Sync logs** viewer over the existing `/logs` endpoint.

### Tests
- `test_public_link.py` (4), `test_qbo_oauth.py` (4), and 3 new `test_a6_email.py`
  cases (encryption + fetched sender, set-default, OAuth-first→SMTP). Suite **179**.
- Zero schema drift (no model changes); ruff clean on CI paths; frontend jsdom
  tests green.

## [0.35.0] — build 68: schedule-on-duplicate + Work-order scheduling fields

### Added — schedule a work order when you duplicate it
- **Duplicate now opens a dialog** with **Schedule from** / **Schedule to**
  (datetime) instead of silently cloning. Leave both blank for an unscheduled
  draft (the old behaviour), or set them to book the copy straight onto the
  calendar — handy for repeat/recurring jobs. Fields prefill from the source
  order's schedule so "duplicate and re-book" is one edit. `to` must be on/after
  `from` (validated client- and server-side).
- `POST /api/orders/{id}/duplicate` accepts optional `scheduled_for` /
  `scheduled_to` (both map to the order's existing `scheduled_for` /
  `scheduled_to` columns — **no schema change, zero drift**) and returns them.

### Added — Work order scheduling fields in the forms editor
- **Settings → Forms & types** has a new **Work order** entity row (alongside
  Client fields / Asset fields) that surfaces the two built-in scheduling
  fields — **Schedule from** and **Schedule to** — so they're visible and
  documented where every other form field is configured. They're first-class
  Order columns (used on the create modal, edit view, scheduler, and duplicate),
  so the section is read-only/built-in rather than a custom-field editor.

### Tests
- Backend: duplicate-with-schedule round-trip, unscheduled-by-default, and the
  reversed-range rejection (`test_wo_actions.py`). Suite **168**.
- Frontend: `frontend/tests/schedule_fields.test.js` (jsdom) — the duplicate
  modal renders + prefills the schedule inputs and POSTs them; the forms editor
  renders the built-in **Schedule from/to** section and exposes the Work order
  entity row.

## [0.34.0] — build 67: custom fields (Orderry-style), e2e spec, polish

### Added — custom fields on client & asset forms
- **Settings → Forms & types** now has **Client fields** and **Asset fields**
  editors (same drag-to-reorder builder as work-order types): text, textarea,
  number, select, date, checkbox, header. Backed by new org-level endpoints
  (`GET/POST /api/forms/entity-fields`, reorder; existing `PUT/DELETE /fields/{id}`).
- Those fields now render and save on the **client form**, the **asset (add-device)
  form**, and the **work-order create modal** (asset section) — so shop-defined
  fields are captured right where work orders are created. Values persist in new
  `custom_data` JSON on Client and Device (migration `d6189eae99e8`, zero drift)
  and are returned by the client/asset/order APIs.

### Fixed
- **Client edit 500** — a partial `PUT /api/clients/{id}` (e.g. rename) sent
  `consent_phone=None` and hit a NOT NULL constraint; partial updates now skip
  None consent/custom_data instead of wiping them.

### Added — tests & e2e
- Backend: `test_custom_fields.py` (CRUD, client/device/WO value round-trips, the
  partial-edit fix). Suite **165**.
- `tests-e2e/industry.spec.js` — Playwright flow (real browser, run in CI) that
  checks the create button label, template group + identifier, WO creation, and
  add-asset for phone (Ticket/IMEI) and auto (Order/VIN).

### Changed
- Small premium touch: custom-field groups render as a divided, labelled section.

## [0.33.0] — build 66: asset form on templates + legacy registry retired

### Changed
- **Client → Add asset form is now template-driven**, matching the work-order
  modal: a **Group** picker from the active template, brand/model suggestion
  inputs sourced from the template directories (free-text still allowed), a single
  identifier field whose label follows the group (VIN/IMEI/HIN/serial), and a
  **scan** button wired to `scan-resolve` (matches an existing asset or
  validates/prefills a new one). New devices are stamped with `template_key` +
  `asset_group_id` (`POST /clients/{id}/devices` now accepts both, defaulting to
  the org's active template).
- **Legacy template registry retired.** Deleted `INDUSTRY_TEMPLATES` /
  `template_for` / `identifier_kind_for` from `services/lookups.py` and removed the
  `/api/integrations/lookup/templates` endpoint — the industry registry now has a
  single source of truth in `services/industry.py`. The VIN/HIN/IMEI structural
  decoders remain (they back scanning). Removed the now-dead `__assetMeta` helpers
  from `orders.js`/`clients.js`.

### Tests
- New: legacy `/templates` endpoint returns 404; templates come from the new API;
  switching via `/api/staff/company` still works. Migrated device-create verified
  live (group + template + VIN persisted). Frontend create-modal UI test still
  green. Backend suite 162; no schema change (no drift).

## [0.32.0] — build 65: Industry templates Phase 5 (tests + cleanups)

### Added — UI-level tests
- **Browser-free create-modal test** (`frontend/tests/industry_modal.test.js`,
  jsdom) that loads the real `orders.js` and asserts the rendered work-order
  create modal is template-correct: phone → Phone/Tablet/Smartwatch groups + IMEI
  label + "New ticket" title + "+ Ticket" button; auto → Car/Truck/SUV/Van + VIN;
  marine → Boat/Trailer + HIN. This is the §11 rendering coverage that needed a
  DOM. Runnable via `npm run test:frontend` (jsdom added as a dev dependency).

### Changed — cleanups
- **Registry unified.** `/api/staff/company` validates `industry_template`
  against the new 9-key registry (+ legacy aliases); added a test that the legacy
  path accepts new keys (e.g. `mobile_phone`) and rejects unknown ones.
- **Brand/model DB uniqueness.** Added a unique constraint on asset models
  (`org, template, brand, name`) and a partial unique index on template-wide
  brands (`org, template, name` where `asset_group_id IS NULL`) so the NULL-group
  case is enforced at the database, not just the app layer. Migration
  `65f248d02285`; zero drift.

### Note
- The §11 data-layer checks were already covered (Phases 1–2, 28 tests); Phase 5
  adds the rendered-flow coverage + the two flagged cleanups. Backend suite: 162.

## [0.31.0] — build 64: Industry templates Phase 4 (work-order flow)

The template you pick in Settings now drives the actual work-order create flow.

### Added
- **Create button + modal wording follow the template.** The Work Orders page
  button reads "+ Ticket" for phone/computer and "+ Order" for every other
  industry; the modal title and submit button say "ticket" vs "work order"
  accordingly.
- **Asset section is template-driven.** The flat "Asset type" dropdown is replaced
  by the template's **Group** picker (Car/Truck/… for auto, Phone/Tablet/… for
  mobile, Boat/Trailer/… for marine). **Brand** and **Model** are now
  suggestion-backed inputs (datalists sourced from the template's directories,
  filtered by group → brand) while still allowing free-text entry.
- **Identifier field adapts to the group** — shows VIN for a car, IMEI for a
  phone, HIN for a boat — and the search button runs **scan-resolve**: it matches
  an existing asset (autofilling brand/model) or validates + prefills a new one.
- New work orders and their assets are stamped with `template_key` and
  `asset_group_id`; existing records keep their own template.

### Verified
- Live create path: switching the org to auto, creating an order with a car group
  + Toyota/Corolla + VIN stamps `template_key=auto` on both order and device,
  persists the VIN, and scan-resolve then matches it. Headless-DOM test confirms
  the identifier label + payload follow the group (car→VIN→asset_vin, boat→HIN).
  Backend unchanged (create endpoint already consumed these fields); suite 161.

## [0.30.0] — build 62: verified Actions menu, seamless dark surfaces

### Verified
- **Work-order Actions menu is clickable.** The menu portals to `<body>` with
  `position:fixed; z-index:99999`, escaping the modal's stacking context so the
  "AI summarize" button can no longer render over it or intercept clicks. Proved
  with a headless-DOM test of the real handler: opening portals the menu to body
  at z-index 99999, clicking **Duplicate** fires its handler, and the menu closes.
  (This shipped in build 60; earlier builds showed the overlap.)
- Researched Orderry's work-order Actions (Copy public link · Duplicate · Move to
  location · Delete, with Print/Message as separate buttons) — our set matches,
  plus the useful **Create purchase order** addition.

### Changed — dark-mode polish
- Modal footer no longer reads as a distinct grey bar — it now blends into the
  modal surface (transparent + hairline top border).
- The comment/feed composer gets subtle glass depth (soft gradient + inset
  highlight) instead of a flat grey block.

### Note
- The sidebar now shows the build number (**Internal beta · build 62**) so you can
  confirm which build is loaded after a hard refresh.

## [0.29.0] — Industry templates Phase 3 (Settings UI) + settings polish

### Added — Industry template Settings page
- New **Settings → Industry template** page (first user-visible part of the
  industry system). A clay/glass template picker for all 9 industries showing each
  one's asset noun, work-order button and group count, with the active template
  badged. Selecting a template shows a **live preview** — asset label, "+ New
  work order" wording, scan/identifier rule, and the full field + group chips —
  and switching prompts a confirmation that spells out "new records only; existing
  ones keep their template."
- A per-template **asset directory manager**: cascading Groups → Brands → Models
  columns (add/select/delete), plus **CSV import**. Everything is scoped to the
  selected template, so industries never mix. Backed by the Phase 2 endpoints,
  plus a new read-only `GET /api/settings/industry-template/describe` so any
  template can be previewed without switching.

### Fixed — settings styling
- **Finance & numbering** tables (numbering, legal entities, taxes) floated bare
  on the page and didn't match the app; wrapped them in clay/glass cards.
- **`.data-table`** gained the premium touches (tinted header, rounded top,
  row hover) so the Finance and Directories tables read like the rest of the app.
- Directory list rows now have consistent hover.

### Tests (+1, suite 161)
- `describe` previews any template (marine → Vessel/HIN) without changing the
  org's active template. Full flow (get/set/groups/brands/models/CSV) re-verified
  live. No schema change this release.

## [0.28.0] — Dark-mode grey surfaces + Actions dropdown fix

### Fixed
- **Light-grey blocks throughout dark mode.** Several surfaces hardcoded white
  "glass" fills with no dark override, so in dark mode they rendered as light-grey
  panels: the comment/feed composer and the History panel (both `.card-sm`), the
  modal footer bar, the feed tool buttons (Photo/File/Signature), the visibility
  toggle, and feed row dividers. Added dark surfaces for all of them, plus a
  broad sweep of the other white-hardcoded surfaces (detail toggles, category &
  column dropdowns, type/report chips, form-builder rows, scheduler cells, empty
  states, progress bars). The glass *variables* were already themed; these were
  the ones bypassing them.
- **Work-order Actions dropdown overlapped page content** (it sat at `z-index:40`
  and the tab-panel's "AI summarize" button bled over it, which also made the menu
  items hard to hit). Raised it to `z-index:1200` so it layers cleanly above the
  modal body; its background/text already theme correctly.

_Note: the Duplicate / Create-PO / Delete **logic** was fixed in 0.27.0; if those
still looked dead, it was this dropdown overlap plus running a build older than
0.27.0. No backend changes here — suite unchanged._

## [0.27.0] — Work-order actions: Duplicate, Create PO, Delete (archive)

The three Actions-menu items that were broken/stubbed now work end-to-end.

### Fixed / added
- **Duplicate** was creating an empty order (it dropped every service/product
  line). New `POST /api/orders/{id}/duplicate` deep-copies the client, asset,
  location, priority, complaint, type, industry template and **all lines** into a
  fresh draft that can be re-scheduled and worked. Payments/invoices/signature/
  schedule/status-history are intentionally not copied.
- **Create purchase order** was a stub that just opened Inventory. New
  `POST /api/orders/{id}/create-po` raises a real PO for the parts the work order
  needs but doesn't have in stock: it turns custom product lines into real catalog
  parts (so stock can be tracked), links the PO back to the work order
  (`source_order_id`), and — with *auto-send* — marks it ordered and notifies the
  supplier. A modal lets you pick the supplier and toggle send.
- **Delete** now **archives** instead of hard-deleting (it was also defined twice
  on the frontend, the second silently shadowing the first). `POST /api/orders/{id}/archive`
  requires a reason, refunds payments (ledger + refund record), and moves the
  order to an archive that stays visible to the owner with who/when/why. Plain
  `DELETE` is repointed to the same soft-archive path. A new **Archived** button
  on the work-orders toolbar lists them; archived orders are hidden from the
  normal lists but still openable.

### Schema
- Migration `d839ed931979`: `orders.archived/archive_reason/archived_at/archived_by_id`
  and `purchase_orders.source_order_id` (all nullable/additive). Pinned
  `foreign_keys` on the Order↔Employee `assigned_to` relationship (a second
  orders→employees FK was introduced by `archived_by_id`). Zero drift.

### Tests (+7, suite 160)
- Duplicate copies lines into a new draft; Create-PO links the PO and creates the
  missing part (and 400s when nothing needs ordering); archive requires a reason,
  hides the order, records who/why, keeps it openable, and refunds payments; plain
  DELETE soft-archives rather than destroying.

## [0.26.0] — Industry templates, Phase 2 (API layer)

Endpoints for picking an industry template and curating per-template asset
directories, plus template-aware scanning. New assets/orders are stamped with
the active `template_key`; existing records keep their own.

### Added
- **`GET/PUT /api/settings/industry-template`** — read the active template with a
  full descriptor (labels, fields, groups, scan rule) + the picker list; switch
  the org's template (validated), which seeds that template's defaults.
- **CRUD** for `/api/settings/asset-groups`, `/asset-brands`, `/asset-models`,
  and read for `/asset-identifier-rules` — every query scoped to
  `(org_id, template_key)`; brands optionally narrow to a group; deletes are soft.
  App-level dedupe guards the NULL-distinct uniqueness noted in Phase 1.
- **`POST /api/settings/asset-directory/import`** — CSV bulk-load of brands
  (and optional models/group) into one template, never leaking across templates.
- **`POST /api/assets/scan-resolve`** — picks the lookup kind from the template
  (+ group override), matches an existing asset for autofill, or structurally
  decodes a valid-but-new identifier for prefill. It runs only the relevant
  decoder (phone→IMEI, auto→VIN, marine→HIN), never all kinds on every scan.
- Asset and work-order creation now accept and stamp `template_key` /
  `asset_group_id`, defaulting to the org's active template.

### Tests (+12 endpoint tests, suite 153)
- Template get/set + seeding; unknown template rejected.
- Group/brand/model CRUD; duplicate + cross-template-brand-model rejections.
- The same brand name creates **separate** rows per template via the API; a
  phone-only brand never appears under auto.
- `scan-resolve` returns the correct kind per template and per group override
  (motorcycle→VIN vs bicycle→serial); matches an existing asset by VIN.
- A work order created under `auto` keeps `template_key=auto` after the org
  switches to `marine`; the next order picks up `marine`.
- CSV import is scoped to its template.

_Note: the schema is unchanged from Phase 1 (migration `9a6657b562a6`); zero drift._

## [0.25.0] — Industry templates, Phase 1 (data model + registry)

Foundation for per-industry asset directories. **Additive and behaviour-neutral**
— no endpoints or UI wired yet (those are Phases 2–4); existing data is untouched.

### Added
- **Four org-scoped, template-scoped tables** so industries never share asset
  data: `asset_groups`, `asset_brands`, `asset_models`, `asset_identifier_rules`.
  Every row carries `template_key`; brands/models optionally narrow to a group.
- **Columns** `devices.template_key`, `devices.asset_group_id`, `orders.template_key`
  — all nullable, so legacy assets/orders keep working and render via a fallback.
- **Registry** `backend/services/industry.py` — the code-level source of truth for
  the 9 templates (mobile_phone, computer, tools, small_engine, powersports, auto,
  bike, marine, general): per-template labels, work-order button text, asset groups,
  ordered field schemas, and template/group-aware identifier (scan) rules. Includes
  legacy-key aliasing (`generic→general`, `device_repair→mobile_phone`, …) and an
  idempotent, org-scoped `ensure_industry_seed()` that materialises default groups,
  identifier rules and a small starter brand/model set.
- **Migration** `9a6657b562a6` (autogenerated, batch-mode; schema builds clean from
  empty with zero drift).

### Guarantees proven by tests (+15, suite 141)
- Phone shows IMEI (not VIN/HIN); auto shows VIN (not IMEI); marine shows HIN.
- Groups never cross templates; brand/model lists are template-scoped.
- The same brand name (e.g. "Yamaha") exists as **separate** rows under
  powersports, marine, small_engine and bike — never one shared row.
- Per-group identifier overrides (phone→IMEI vs tablet→serial; motorcycle→VIN vs
  bicycle→serial; boat→HIN vs trailer→VIN). Seed is idempotent and org-isolated.

## [0.24.0] — Dark-mode polish + "Employee hours" fix

### Fixed
- **My-company tabs vanished when opening "Employee hours".** The tab-strip
  injector could short-circuit on the *previous* tab's leftover strip, then
  `setContent` wiped it — leaving the Employee-hours view with no tab strip and
  no way back. The strip is now (re)injected from `setContent` itself, so it is
  always present and always highlights the active tab. Reproduced and verified
  fixed in a headless-DOM test (jsdom): Dashboard→Employee hours→back all keep
  the strip; non-company pages correctly show none.
- **Timesheet page no longer blanks on a load error.** A failed timesheet fetch
  previously cleared the whole body; it now shows an inline message and keeps
  the filters, so nothing "disappears."
- **Grey filter chips looked flat/washed in dark mode.** `.chip` had no dark
  rule, so status/invoice/task filters rendered as pale-grey pills. Added a dark
  treatment on the elevation ramp (raised surface, cool hairline, readable text;
  active stays the indigo→violet gradient with high-contrast ink).
- **SKU pills (`kbd`) and priority labels were low-contrast in dark mode.** SKU
  chips now use a recessed dark well; Normal/Urgent/VIP now use theme accents
  instead of dark ink that vanished on the dark canvas.
- **Misaligned text in Settings › Finance & numbering and in Directories.** The
  `.data-table` class had *no* CSS, so headers collapsed ("Begins from"/"Example"
  ran together) and columns didn't line up. Added proper table styling
  (padded, aligned, uppercase headers, hairline rows) — theme-aware.

### Verified (no change needed)
- Time-based clock-in, work-order clock-in (switch), and clock-out all work;
  timesheet aggregation (total/job vs non-job/billable/utilization/overtime) and
  the order-time rollup are correct. Payments, reports, analytics, finance and
  cash read endpoints all return 200. Backend suite: **126 passed** (unchanged;
  edits were frontend/CSS only).

## [0.23.0] — Postings fix + nav cleanup + WO actions

### Fixed
- Postings 405: added `POST /api/inventory/postings` (bulk stock inflow —
  multi-line, purchase price + markup→sell, moving-avg cost). Modal rewired.
### Confirmed in place
- Sidebar: single "My company" entry + tabs; Assets removed (dupe of Inventory);
  bins moved to Products topbar.
- Work Order: Print / Message / Actions buttons.
- Tests +2 (suite 126). Docs: `docs/ux-nav-postings-fix.md`.

## [0.22.0] — A4 Xero · A7 VoIP/campaigns · SSO groundwork (gated seams)

### Added
- **A4 Xero** router `/api/integrations/xero/*` over the existing service (gated
  on XERO_CLIENT_ID/SECRET).
- **A7** VoIP `Call` + webhook (client-match by phone) `/api/comms/calls*`;
  marketing `Campaign` + segmented send `/api/comms/campaigns*`.
- **SSO** groundwork: `SsoConnection` + `/api/sso/{config,discover,authorize}`
  (OIDC/SAML config, domain routing, gated authorize URL; secrets redacted).
- Migration +3 tables +2 cols (schema 99). Tests +4 (suite 124).
  Docs: `docs/a4-a7-sso-seams.md`.

## [0.21.0] — Track B completion (B1 MFA, B3, B4, B5, B6)

### Added
- **B1 MFA**: vendored RFC-6238 TOTP, enroll/activate/disable + login gate
  (recovery codes). `/api/mfa/*`.
- **B3**: dependency-free rate-limit middleware (429), part/signature media
  offload pointers + `offload_if_enabled` helper.
- **B4**: `/metrics` (Prometheus text) + `/api/readyz`; MetricsMiddleware.
- **B5 white-label**: org branding (color/domain/email-from/hide-powered-by);
  `/api/branding` + public `/api/public/branding` resolver.
- **B6**: `/api/siem/audit.ndjson` (SIEM export) + `/api/bulk/{entity}`.
- Migration +9 cols (schema 95). Tests +6 (suite 117). Docs: `docs/track-b-completion.md`.

## [0.20.0] — B2 audit trail + GDPR

### Added
- Audit trail now live (`services/audit.py`): records order create/status-change
  and client create with field diffs; `GET /api/compliance/audit` (filterable).
- GDPR: `GET /api/compliance/gdpr/client/{id}/export` (Art. 15/20) and
  `POST .../erase` (Art. 17 — anonymizes PII, retains financial records). Both audited.
- Tests +3 (suite 111). No schema change. Docs: `docs/b2-compliance.md`.

## [0.19.0] — B3 media offload

### Added
- Media storage seam (`MediaBlob` + `services/media.py`): `MEDIA_BACKEND` db|local|s3.
  `POST /api/media`, `GET /api/media/{id}` (streams from any backend),
  `GET /api/media/status`, `POST /api/media/migrate-feed`. Feed uploads offload
  automatically when backend≠db; serializer returns media_url for offloaded items.
  Backward compatible (default db = inline, unchanged).
- Migration +1 table +2 cols (schema 95). Tests +4 (suite 108).
  Docs: `docs/media-offload.md`. (A5 AI verified already complete — no work needed.)

## [0.18.0] — A8 Fleet/B2B + A9 Business Insights

### Fixed
- Fleet overview `client_type` mismatch (queried "company", model said
  "organization") — now accepts both.
### Added
- `GET /api/analytics/business-insights` — owner KPI roll-up (revenue, close
  rate, avg ticket/turnaround, top techs & services).
- `GET /api/analytics/fleet/{client_id}/statement` — consolidated B2B billing
  statement across a fleet account's vehicles.
- Tests +3 (suite 104). No schema change. Docs: `docs/a8-a9-fleet-insights.md`.
  (Pass 1 of the A4–A9 Track-A sweep; A4/A5/A6/A7 follow as gated seams.)

## [0.17.0] — Wire-through: four settings now enforce

### Fixed / Added (logic-only, off-by-default)
- `asset_one_open_ticket`: order create blocks an asset already on an open ticket (409).
- Role `location_scope`: `check_location_access` enforces scoped roles at order
  creation (403); unscoped roles/owners unaffected.
- `rounding_enabled`: `_recalc_order` rounds order total to nearest 0.05 when on.
- `warranty_count_from`: warranty start honors closure|created policy.
- Tests +5 (suite 98). No schema change. Docs: `docs/wire-through.md`.

## [0.16.0] — Batch 2: Directories + notification events

### Added
- **Directories** (`Directory` + `DirectoryItem`): org-customizable reference
  lists feeding dropdowns app-wide; system lists seeded (asset_type, manufacturer,
  cancel_reason, lead_source, payment_method). `/api/directories` CRUD +
  `/by-key/{key}/items` runtime resolver. Settings → Directories UI.
- Notification template editor: added comment_added / ready_for_pickup /
  estimate_sent / invoice_sent trigger events.
- Migration +2 tables (schema 94). Tests +3 (suite 93).
  Docs: `docs/batch2-directories.md`.

### Note
Statuses/Forms/Notifications editors were already substantially built (corrects
the screenshot-based research). Remaining Batch-2 items (WO invoices tab, POS
tender, built-in-field forms config, wiring directories into live dropdowns) are
documented as deferred UI-parity.

## [0.15.0] — Batch 1 finish: Public Pages (1a/1b) + Finance UI (2a/2b)

### Added
- **1a** booking page: background-pattern picker + change-subdomain endpoint
  (`/api/staff/public-pages/subdomain`, unique, url-safe).
- **1b** order/estimate page: header + zero-price toggles
  (`oe_*` on Organisation) + real settings form (was text-only).
- **2a** Taxes UI: New-tax drawer + list/remove in Settings → Finance.
- **2b** default payment categories per doc type
  (`cash_category_{tickets,sales}_id` + `/api/staff/cash-categories`), wired into
  order/invoice payment recording.
- Migration +12 columns (schema 92). Tests +3 (suite 90).
  Docs: `docs/batch1-publicpages-finance.md`.

## [0.14.0] — Parity pack (P4) + RBAC v2 (P5)

### Added
- **P4 parity:** contact consent defaults + duplicate-phone/email guards (409),
  invoice backdating guard (422) + default due days, ticket visibility/warranty/
  one-open-ticket defaults, rounding + currency display settings — all via
  `/api/staff/company`. Commission engine gains `product_sold_no_deduction` and
  **assignee-variant** events (assignee_labor/assignee_product).
- **P5 RBAC v2:** permission catalog (66 perms × 8 modules), role templates
  (Full/Manager/Partner/Specialist), `Role` location scoping, `role_allows`
  deny-wins helper; `/api/settings/rbac/{catalog,roles}`. Backward compatible —
  flat perms + default-allow require_perm unchanged.
- Migration +17 columns (schema 92). Tests +8 (suite 87).
  Docs: `docs/parity-rbac.md`.

## [0.13.0] — Price lists (Build P3) + Alembic naming hardening

### Added
- **Price lists**: `PriceType` gains location scope; new `PriceTypeItem` explicit
  overrides; `Organisation.price_type_{tickets,sales,invoices}_id` + `price_decimals`;
  `Location.price_type_id`. Resolver: item override → cost×markup, and
  `resolve_doc_price` (location → org-default-per-doc-type → default).
  `/api/pricing/types/{id}/items`, `/api/pricing/defaults`. UI: default-price-list
  block in Settings → Prices & discounts. Migration +1 table +8 cols (schema 92).
  Tests +4 (suite 79). Docs: `docs/price-lists.md`.
### Fixed
- **Alembic naming convention** on Base metadata (ix/uq/ck/fk/pk) — batch-mode
  autogenerate no longer raises "Constraint must have a name"; 0 drift preserved.

## [0.12.0] — Numbering engine + legal entities + tax depth (Build P1+P2)

### Added
- **Numbering engine** (`NumberSequence` + `services/numbering.py`): token
  templates {D}{M}{Y}{C:n} with per-scope (org/location/entity) atomic counters;
  replaces hardcoded number formats at all 10 call sites. Backward compatible
  (default templates + seed-past-existing). `/api/settings/numbering` (+preview).
- **Legal entities** (`LegalEntity` + `Invoice.legal_entity_id`): CRUD with single
  default; entity-scoped invoice numbering. `/api/settings/legal-entities`.
- **Tax depth**: `Tax` + code/name_for_document/location scope; `TaxGroup`,
  `TaxRule` (ordered stack per doc family), `TaxException` (item/category);
  `services/tax.py::resolve_taxes` (inclusive/exclusive, precedence).
  `/api/settings/{tax-groups,tax-rules,tax-exceptions}`.
- UI: Settings → Finance & numbering. Migration +5 tables +6 cols (schema 91).
  Tests +8 (suite 75). Docs: `docs/numbering-entities-tax.md`.
### Fixed
- Numbering seeds its counter past existing documents to avoid UNIQUE collisions
  (caught by QA regression on the v1 order-create path).

## [0.11.1] — Product Blueprint 2026 (docs-only)

### Added
- `docs/PRODUCT_BLUEPRINT.md` — complete product audit & redesign: design
  thesis + principles (⌘K palette, two-click rule, drawers>pages>modals,
  saved-view-everything, status-as-engine, AI-drafts-human-commits, WCAG 2.2 AA),
  11-item IA (sidebar/Company/Settings split), global design system (SavedView
  model, token filters, bulk bar, peek drawers, keyboard map, federated search,
  automation recipes, mobile strategy), per-module blueprints for all 23 modules
  grounded in shipped code, 21-page Settings structure mapped to backing code,
  competitive teardown vs Orderry/RepairDesk/Shopmonkey/Jobber/Housecall Pro,
  and P0–P3 implementation priorities. No code changes.

## [0.11.0] — A2 engagement + multi-technician payroll

### Added
- **Gift cards** (issue/list/balance + order redemption with ledger),
  **coupons** (pct/fixed, windows, min-total, usage caps, one-per-order),
  **loyalty points** (auto-earn on paid intents, auto-tier standard/silver/gold,
  redemption) — all as negative fee lines so totals/invoices stay consistent.
- **Multi-technician work orders**: per-line Tech column + picker in the order/
  estimate UI (backend `OrderLine.technician_id` + payroll attribution already
  existed and is now reachable); `order_created` commission event wired.
- Order UI: Coupon / Gift card / Points actions.
### Fixed
- CommissionEngine invocation missing `org_id` (order_created path); loyalty
  hook `Client` import (tier auto-upgrade silently failing).
- Migration: +4 tables +2 org columns (schema 86). Tests +6 (suite 67).
  Research + docs: `docs/engage-and-payroll.md` (Orderry roles→payroll model).

## [0.10.0] — Payments breadth (Roadmap A1)

### Added
- **Square** gateway (Payment Links, sandbox/production) + signed webhook
  (`/api/payments/square/webhook`, HMAC fail-closed) → same idempotent
  reconciliation as Stripe.
- **SumUp** gateway (hosted checkout) + status poll
  (`POST /api/payments/intents/{id}/refresh`); the public pay page auto-polls.
- **Per-org provider selection**: `Organisation.payment_provider`
  (auto/stripe/square/sumup/simulated) — Settings → Integrations picker +
  `/api/payments/config` now reports availability; unconfigured choices fail
  cleanly (502), payment links report the intent's real provider.
- **Stripe Terminal slice**: connection-token + card_present intent endpoints
  (503-gated without keys) for in-person readers.
- Migration (+1 column, schema 82 tables). Tests +6 (suite 61).
  Docs: `docs/payments-providers.md`. Not live-fired (no keys in sandbox).

## [0.9.0] — Asset identification: VIN/HIN/IMEI lookups + industry templates

### Added
- **Lookups under integrations**: `/api/integrations/lookup/{vin,hin,imei,templates}` —
  real ISO-3779 VIN check-digit validation + keyless NHTSA vPIC live enrichment
  (offline-safe), USCG HIN structural decode, IMEI Luhn + TAC split (provider seam).
- **Identifier-aware asset forms**: work-order/estimate modal + client device modal
  switch the identifier field (VIN/HIN/IMEI/Serial) by asset type, with one-click
  lookup that auto-fills brand/model/year.
- **Industry template** (Settings → General): renames the asset entity app-wide
  (Assets/Vehicles/Vessels/Devices/Equipment/Tools/Appliances), Orderry-style, and
  drives each org's asset-type list.
- `Device.vin`/`Device.hin` columns (+ search), `Organisation.industry_template`;
  order create accepts `asset_vin/asset_hin/asset_imei`. Migration adds 3 columns
  (schema: 82 tables). Tests: +5 (suite 55). Docs: `docs/asset-identification.md`.

## [0.8.0] — Phase 3 hardening: security, GPS, offline conflicts, e2e

### Fixed
- **Critical:** `Employee.token_version` had been added to the `Organisation`
  model by mistake, so `employee.token_version` raised and **register/login 500'd**.
  Moved the column to `Employee` (+ migration). Login/refresh/logout-all now work.

### Added
- **Part 10 — lost-device logout**: `tv` JWT claim checked in
  `get_current_employee` (tokens minted before the feature stay valid),
  `POST /api/auth/logout-all-devices` bumps the version. Tests in
  `test_security_part10.py` (legacy-token compat, stale-tv rejection, logout-all).
- **Part 5 — GPS**: `TimeEntry.gps_lat/gps_lng` captured on clock-in (structured,
  no longer stuffed in the note); `/start` accepts them, `/switch` unaffected.
- **Part 4 — conflict resolution**: offline 409s are stored with the server's
  reason and surfaced in a review sheet (re-apply against latest / discard) via
  `RosPWA.getConflicts/resolveConflict`; conflict count in `RosPWA.status()`.
- **Part 11 — e2e**: `tests-e2e/` Playwright suite (manifest, SW scope, login,
  offline→queue→sync, navigation fallback) + Lighthouse audit spec +
  `.github/workflows/e2e.yml`. Runs in CI (no browser in the build sandbox).
- Migrations for `employees.token_version` and `time_entries.gps_lat/gps_lng`
  (schema total: 82). Suite now 50.

### Notes
- All Phase-3 parts (1–11) are ✅. Remaining before "done": run the e2e/Lighthouse
  suite in CI, and a live-fire push with real VAPID keys + a device.

## [0.7.1] — chat.message trigger + real-send proof

### Added
- **`chat.message` trigger**: inbound customer messages (demo inbound seam +
  Twilio webhook) notify the technician assigned to the client's most recent
  open order.
- **Push tests**: real dispatch proven via a stubbed `webpush` (correct
  subscription + VAPID params) and dead-subscription (410) pruning. `pywebpush`
  verified installable. Suite now 46.

## [0.7.0] — Phase 3: history + push triggers (Parts 7 & 9)

### Added
- **Notification preferences**: `NotificationPreference` model + `/api/tech/preferences`;
  muting a kind silences both in-app and push (`notify(respect_prefs=True)`).
- **Live push triggers**: `payment.received` (from payment reconciliation),
  `estimate.approved` (from public/portal accept), `booking.cancelled` (from
  booking cancel) — added to the existing `job.assigned`/`schedule.changed`.
- **Real Web Push**: `pywebpush` added to requirements; `_dispatch_push` sends
  when VAPID keys are set and prunes dead subscriptions, error-safe.
- **Customer & device history (mobile)**: `GET /api/tech/client/{id}/history`
  (repairs, invoices, warranty, outstanding balance, recent activity,
  recommendations) and `GET /api/tech/device/{id}/history`; reachable by tapping
  the customer/device in the order view. New in-app preferences screen.
- **Migration** `…notification preferences` (1 table; schema total: 82).
- **Tests**: `backend/tests/test_notifications_part79.py` (7); suite now 43.

### Notes
- `chat.message` trigger deferred: `ChatThread` has no assignee to target.
- Real push delivery still requires VAPID keys; simulated/logged without them.

## [0.6.0] — Phase 3: mobile work order + payments (Parts 3 & 8)

### Added
- **Checklists**: reusable `ChecklistTemplate`/`ChecklistTemplateItem` + per-order
  snapshot `OrderChecklistItem`; templates + order-checklist API
  (`/api/checklist-templates`, `/api/orders/{id}/checklist…`).
- **Completion gate**: closing a work order now enforces required checklist items
  + (opt-in) customer signature, at the status chokepoint; `WorkOrderType` gains
  `requires_checklist`/`requires_signature`. `POST /api/orders/{id}/complete`
  one-call completion.
- **Labor**: `GET /api/orders/{id}/labor` (time-clock aggregation); mobile
  start/stop timer bound to the order.
- **Parts picker**: catalog search → order line, in the mobile order view.
- **Attachments**: photo/video/PDF upload from the order view (feed, ~8 MB cap).
- **Mobile payments**: `GET /api/orders/{id}/invoice-preview`,
  `POST /api/orders/{id}/payment-link` (real QR via `segno`), public pay page
  `/pay/{intent_id}` + `GET /api/public/pay/{intent_id}`, on the existing
  PaymentIntent/Stripe seam.
- **Config**: `PUBLIC_BASE_URL` for absolute shareable links.
- **Migration** `…checklists and completion gate` (3 tables + 2 columns; schema
  total: 81).
- **Tests**: `backend/tests/test_work_order_mobile.py` (7); suite now 36.

### Notes
- Payment links use the simulated gateway's hosted page unless Stripe keys are
  set; QR encoding is real (segno). Parts link as custom lines (stock-product
  linkage is a follow-up). `pywebpush` remains optional (push simulated without
  VAPID keys).

## [0.5.0] — Phase 3: Mobile Technician Experience (foundation)

### Added
- **Installable technician PWA** at `/tech` — mobile-first shell, real app icons
  (192/512/maskable/apple-touch), Web App Manifest, install prompt.
- **Service worker** (`/sw.js`, root-scoped): app-shell precache, network-first
  API with cache fallback (assigned jobs open offline), stale-while-revalidate
  static, offline fallback page, version-update handling, Background Sync, and
  Web Push (`push` + `notificationclick`) handlers.
- **PWA runtime** (`pwa.js`): SW registration + update banner, install prompt,
  connectivity tracking, and an **AES-GCM-encrypted IndexedDB offline queue**
  with auto-sync, conflict handling, retry, and live sync status.
- **Technician dashboard** (`GET /api/tech/dashboard`): today / upcoming /
  completed / overdue jobs, quick stats, clock-in status, unread count — one
  round-trip, touch-optimised UI.
- **Staff notifications**: `StaffNotification` model + `/api/tech/notifications`
  (list / read / read-all); `backend/services/push.py::notify()` persists +
  dispatches Web Push (gated on VAPID; simulated otherwise).
- **Web Push subscriptions**: `PushSubscription` model + `/api/push/{vapid-public-key,
  subscribe,unsubscribe}`.
- **Live notification triggers**: order assigned to another tech → `job.assigned`;
  status change on an assigned order → `schedule.changed`.
- **Mobile work-order view** (compact, one-hand): customer info, current status +
  SLA flag, offline-capable status change, internal/customer notes, photo
  capture, and signature capture — all via the offline queue.
- **Time clock (mobile)**: clock in / break / resume / clock out via the existing
  timeclock engine, offline-capable, with best-effort GPS stamp in the note.
- **Barcode/QR scanner**: `BarcodeDetector` with camera-permission handling and a
  manual-entry fallback (the iOS path), with inventory lookup.
- **Auth refresh** (`POST /api/auth/refresh`): sliding session for the PWA.
- **Config**: `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_CLAIMS_EMAIL`,
  `REFRESH_TOKEN_EXPIRE_MINUTES`.
- **Migration** `c25e2eee420e`: `staff_notifications`, `push_subscriptions`
  (schema total: 78 tables).
- **Tests**: `backend/tests/test_technician.py` (6).
- **Docs**: `ARCHITECTURE.md`, `docs/mobile-technician.md`, this changelog.

### Notes
- Push and credential-gated integrations (Stripe/QBO/AI/Twilio) run simulated
  without keys, consistent with the existing seam pattern.
- PWA/SW/offline verified structurally (asset serving, MIME, SW scope,
  `node --check`); a browser + Lighthouse pass is documented in NEXT_TASK.md.

## [0.4.0] — Work Order Management
- Completed per-status **SLA automation** (`time_limit_min`): `backend/services/
  sla.py`, `GET /api/orders/sla/breaches`, `status-sla-sweep` job, detail flags.
- Verified the full WO surface (lifecycle, status pipeline with enforced
  transitions, automation triggers, estimates + public/portal approval,
  signatures, scheduling/calendar, attachments, timeline, warranty).
- Tests `test_work_orders.py` → 14; `docs/work-order-management.md`.

## [0.1.0–0.3.0] — Phase 0 production foundation
- Config hardening (prod fail-closed), async Alembic migrations, structured
  logging + request-context middleware, job runner + periodics, tenant
  isolation, CI (ruff + pytest + migration build), health endpoints.
