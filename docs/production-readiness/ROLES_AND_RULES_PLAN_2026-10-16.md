# Roles, data rules, workflow rules and read rules: plan

From a three-way council (roles · data and workflow rules · security and read rules),
16 Oct 2026. Every finding below was checked against the current code on `security/phase-4`.

## What you asked about first

- **Recommendations on/off for customers:** already exists. Settings › Public pages › "Customers can approve extra work you recommend". When it's off, the tracking page hides them and the server refuses decisions.
- **Recommendations in roles:** already exists. "Create recommendations on work orders" and "Send recommendations to clients".
  - Missing: "Approve on the customer's behalf" (a phone approval), logged with who approved it.

## The real problem: roles look complete but mostly aren't enforced

- **Default-allow.** A role loses a permission only if someone switches it off by hand (`services/auth.py` `has_perm`). Every new permission added later is silently granted to every existing restricted role.
- **45 of 106 permissions are never checked.** Switching them off does nothing, and you can't tell. They include:
  - refunds;
  - deleting or voiding invoices and editing issued invoices;
  - editing closed jobs;
  - changing prices and seeing costs;
  - exporting sales or payroll.
- **3 permissions are checked but aren't in the role editor**, so they can never be turned off: discount approval, adding products, adding services.
- **Roles can widen themselves.** Anyone allowed to manage roles can edit their own role. There is also a second, unvalidated copy of the role editor in `routers/staff.py`.
- **Costs leak.** Every job's lines send cost price to every role (`routers/orders.py:319`). The same goes for catalog, inventory and supplier screens.
- **Money screens are open to any login:**
  - finance summary;
  - P&L;
  - account balances on the dashboards;
  - custom reports, which include customer phone and email.
- **"Own jobs only" isn't really own.** Techs also see every unassigned job (the whole intake queue). Bulk actions skip the check entirely.
- **Rules live only in scattered router code. The database enforces almost nothing:**
  - no CHECK constraints;
  - invoice numbers aren't unique;
  - two cash drawers can be opened at once;
  - stock can go negative in some screens and not others.
- **Workflow holes:**
  - issued invoices can still change;
  - archived jobs can still be edited;
  - a locked status blocks line edits only;
  - approved recommendations can be deleted, leaving an orphan line.

## Plan, in four phases (each one shippable and tested)

### Phase 1: Roles you can trust (security first)

1. **Deny new permissions by default** for restricted roles. Existing roles are migrated so nothing changes for them today. The owner keeps everything.
2. **Stop self-widening.** A role edit can't grant more than the editor has, nobody edits their own role, and the duplicate role editor is removed.
3. **Gate the money screens:** P&L, finance summary, dashboard balances, custom reports (and their cost and contact columns), and exports.
4. **Hide costs and profit** from roles without "see purchase price / profit", in one central place rather than per screen.
5. **Make the role editor honest:**
   - add the 3 missing permissions and "Approve recommendations on the customer's behalf";
   - add a test that fails if a permission exists but nothing checks it, or the reverse;
   - show unchecked permissions as "coming soon" until they're wired.

**Phase 1 status (2026-10-16): built, tests pass.**
- Roles are snapshotted to explicit allow-lists (migration `rp144p8`, plus a startup self-heal); a permission added later starts off. The legacy `{"all":"write"}` shop-creator role keeps everything.
- Role edits: nobody can edit their own role, an edit can't grant a permission or widen a scope the editor lacks, and a new role is capped at the editor's own access. `/staff/roles` now goes through the same code as `/settings/rbac/roles`.
- Money screens: P&L needs "see profit", the finance summary needs "view finance", the dashboard needs "view reports", Home hides money tiles without "view finance"/"view balance", and custom reports drop cost and contact columns.
- Costs: one middleware strips cost/margin fields from staff API responses for roles without "View the purchase price". Product, service, part, variant and order-line saves from those roles ignore cost fields, and their new lines take the catalog cost. A save that doesn't send a cost no longer resets it to 0.
- Role editor: 4 permissions added, 38 unwired ones marked "Coming soon" (no switch, not counted), and `test_permission_catalog.py` fails if the list and the code drift apart.
- The product Excel export leaves the cost column empty for those roles.
- Not covered yet: other file exports (CSV/Excel) and the product import aren't checked for cost fields; the coming-soon permissions themselves still need wiring (Phase 4).

### Phase 2: The workflow rule book (`services/rules.py`)

One place for "is this allowed right now?", called by every write. Each rule gets a test.

- A locked or archived job can't be edited (all fields, not just lines).
- An issued or paid invoice can't change. Corrections go through a credit note.
- A sent or approved recommendation can't be deleted.
- A job can't close with money owed unless the shop allows it.
- Stock below zero follows one shop setting everywhere.
- The open money bugs: coupon-to-cash, pay links after settling, till close missing cash, PO over-receipt, and refund/archive races.

### Phase 3: Database rules (constraints the database itself enforces)

Each comes with a clean-up query run first:

- invoice numbers unique per shop;
- one open drawer per location;
- amounts, quantities and balances never negative;
- coupon uses never above the limit;
- PO received never above ordered;
- coupon and status keys unique per shop;
- status values limited to known ones;
- tenant columns NOT NULL;
- the missing `(org_id, …)` indexes.

### Phase 4: Finish the role model

- Wire the remaining unchecked permissions to their screens.
- Role templates: Owner, Manager, Front desk, Technician, Bookkeeper, Read-only.
- A per-role maximum discount.
- Settings split by area (billing, payments, messaging, integrations, workflow).
- Location limits applied to reports and money screens.
- An audit log of every export, and export rate limits.
- 2FA required for roles that see payroll, balances or exports.
- Customer links that expire after pickup and can be revoked.
- Retention rules for old customer data.

## Suggested start

Phase 1, items 1–5. It closes the biggest real risks (techs seeing costs, payroll and balances, and roles widening themselves) without changing anything for the owner or for any existing role today.
