# Workstream B — profit, dropped jobs and reports (Orderry June–July sprint)

Council note written before building (24 Oct 2026). Scope: gaps #1, #2, #4, #5, #6, #7 of
`orderry-jun-jul-2026-gap.md`. Not in scope: stock/postings/PO/assets (A), inquiries/telephony/
contacts/Go/public API (C).

## PM — what the owner gets
1. **Dropped jobs** (customer never came back, refused the repair…). Moving a job into a
   "dropped" status (e.g. *Not collected*) now:
   - asks for a reason, and refuses without one (the reason feeds the reports);
   - skips the close checks that only make sense for finished work (required checklist items,
     customer signature, "fill in before closing" fields, unwritten-off stock). Checks the owner
     put on the dropped status itself (needs a comment / needs payment / needs the checklist)
     still apply — those were chosen on purpose;
   - writes off what the customer still owes, so the customer is not left with a fake debt and
     the job stops showing a balance. Money already paid stays paid. Reopening the job undoes
     the write-off;
   - doesn't auto-draft an invoice;
   - is kept out of job profit: it is reported on its own line.
2. **Total profit** (Reports → Total profit): one number for the period — jobs + dropped jobs +
   counter sales, after tax and commissions — by location and by technician, with the change
   against the previous period of the same length, and a click-through to the jobs and sales
   behind each number.
3. **Unsuccessful jobs profit** (the old "Cancellations by reason" upgraded): one row per
   dropped job with paid, written off, cost, commission, tax and profit; filter by job type and
   status; totals by reason stay.
4. **Report filters**: product turnover "with movement / without movement / all"; closed jobs
   "hide zero amounts"; the report, dates, location and these filters are remembered per user.
5. **Job fields on invoices**: job number, fault, technician notes and every job custom field
   become invoice template variables; on an invoice that bills several jobs each row can show
   its own job's number and fault.
6. **Continue as a linked job**: from any job, start a new job for the same customer and
   device, linked both ways ("Continued from" / "Continued in").

## Engineer — how
- **One profit function**: `backend/services/profit.py::job_figures()` is the only place job
  profit is computed. Job profitability, Total profit and Unsuccessful jobs all call it, so they
  can't disagree. Per job:
  - `charged` = job total − credit notes; for a dropped job, only the money actually kept
    (net paid, capped at that) — never the written-off part;
  - `tax` = job tax × charged / total; `revenue` = charged − tax;
  - `parts_cost` = Σ qty × cost of product/part lines; `labour_cost` = Σ clocked hours × the
    snapshot cost rate (same as job profitability today);
  - `commission` = net of the payroll ledger rows for the job and its lines (accruals,
    claw-backs, reversals);
  - `profit` = revenue − parts − labour − commission.
  Counter sales: revenue = sale total, cost = FIFO cost stamped on the sale, voided sales out
  (sales carry no tax or commission today). Period = closed date for jobs, sale date for sales.
- **Schema** (one migration after the head): `orders.written_off` (money, default 0) and
  `orders.continued_from_order_id` (FK orders, indexed). Models and migration match (drift gate).
- `order_money.balance_due` subtracts `written_off`. The contact ledger reconciles a dropped
  job to `total − written_off` instead of the total, so no debt is created. Reopen → 0 as today.
- Report filters ride on `/reports/run/{key}` as optional query params (`movement`, `hide_zero`,
  `type_id`, `status`) that only the reports that use them read. Remembered with the existing
  per-user prefs store (`listview:reports`), no new server table.
- Invoice variables: `_resolve_invoice` adds the job fields from the invoice's first job; the
  rows of `display_lines` carry their `order_id` so per-row `{Row Order Number}`/`{Row Order
  Fault}` resolve on grouped invoices.

## Tester — what proves it
- Dropping: no reason → 422; with reason → status changes even with an undone required
  checklist item, a missing signature and pending stock; balance due 0; written_off = what was
  owed; client ledger shows no debt; reopening restores the balance; no invoice drafted.
- Profit reconciliation: a mixed period (closed jobs with tax, labour and commission, a dropped
  job with a part payment, a sale, a voided sale) — Total profit's job totals equal the sum of
  Job profitability's rows; dropped job counts only kept money; the grand total = jobs +
  dropped + sales; previous period numbers and delta are right; location and technician
  filters narrow every bucket.
- Permissions: Total profit and Unsuccessful jobs need "See estimated profit"; cost and profit
  fields are removed for roles without "View the purchase price"; a location-limited role only
  sees its branch; another shop's jobs never appear.
- Filters: turnover without movement lists active products with no use in the period; closed
  jobs hide zero totals; unknown filter values are ignored.
- Invoices: variables resolve; per-row order data on a two-job invoice; HTML in job fields is
  escaped.
- Linked job: copies customer/device/location, links both ways, other shop's job → 404, a role
  limited to own jobs can't continue a colleague's job.
- Frontend (vitest): profit trend maths, filter persistence, reason required in the dialog.

## Security
- Every new query is org-scoped; drill-down lists only ids the caller's location scope allows.
- Profit endpoints require `can_view_reports` + `can_see_profit`; the cost-redaction middleware
  still strips cost/profit keys for roles without `can_view_purchase_price` (keys are named so it
  catches them: `cogs`, `profit`, `margin_pct`, …).
- Rendering an invoice through a template now checks `can_view_invoices` (it didn't), since it
  now carries job notes and custom fields.
- Template values are HTML-escaped as everywhere else in the renderer.
- Write-off and continue are audited (who, when, amount/reason).
- Dropping keeps the role's transition rules and `can_edit_closed_ticket` for reopening.

## Not doing (noted for later)
- Sales tax and sales commissions don't exist in the data model; the report shows them as 0 and
  says so.
- Pending stock on a dropped job is left as it is (inventory is workstream A).
