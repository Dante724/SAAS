# Tekly — Build Roadmap: Orderry-Parity Settings Batch

**Scope.** This is the *execution* plan for the gaps surfaced in
`docs/ORDERRY_UI_RESEARCH.md`, grounded in the actual codebase (repairos-38: 43
routers, 86 tables, 343 routes). Each phase is one shippable tarball with the
standard gate (migration 0-drift · tests · lint · QA 26/47/70 · clean-extract).
Ordered by value-for-effort. Phases are independent unless a dependency is noted.

Legend: **M** models · **E** endpoints · **F** frontend · **T** tests · **Mig**
migration. Effort: S ≤ ~1 pass, M ≈ 1 full pass, L ≈ 2 passes.

---

## Phase 1 — Numbering Engine + Legal Entities  *(effort M · do first)*

**Why first.** Numbering is currently hardcoded in **10 call sites** (`f"INV-
{n:05d}"`, `WO-`, `PO-`, `S-`, `T-`) — brittle, not per-org, not configurable.
Legal entities are a hard requirement for tax-serious markets and are the parent
for per-entity invoice numbering. Cheap, high-leverage, unblocks Phase 2.

### 1A. Numbering engine
How it works: a template string with tokens resolves to a document number at
creation time, against a per-scope counter that increments atomically.

- Tokens: `{D}` day, `{M}` month, `{Y}` year, `{C:n}` zero-padded counter of n
  digits. Example `INV-{Y}-{C:4}` → `INV-2026-0001`. (Matches Orderry exactly.)
- **M** `NumberSequence(org_id, doc_type[order|invoice|inquiry|sale|po|transfer],
  scope_key, template, next_value, updated_at)`. `scope_key` = `"org"` |
  `"loc:{id}"` | `"entity:{id}"` so the same doc_type can number differently per
  location or legal entity.
- **Service** `backend/services/numbering.py`:
  `async def next_number(db, org_id, doc_type, *, location_id=None,
  entity_id=None) -> str`. Resolution order: entity → location → org → built-in
  default template. Atomic increment via `SELECT … FOR UPDATE` (Postgres) /
  serialized write (sqlite dev); idempotent within a transaction.
- **E** `GET/PUT /api/settings/numbering` (list templates, set template +
  begins-from per doc_type/scope). `require_perm("can_manage_settings")`.
- Replace all 10 hardcoded sites with `await next_number(...)`. Keep the old
  format as the *default template* so existing installs are unchanged.
- **F** Settings → **Numbering** page: per-doc-type template input + "begins
  from" + live example (render the token expansion client-side, like Orderry's
  "Example: inquiry 015").
- **T**: token expansion (each token + combined), counter increments & pads,
  per-scope isolation, begins-from seed, default-template fallback = current
  format. **Mig**: +1 table.

### 1B. Legal entities
- **M** `LegalEntity(org_id, name, address, tax_id, details_json, is_default,
  active)`. `Invoice.legal_entity_id` (nullable FK). `Location.legal_entity_id`
  optional (default entity per location).
- **E** `GET/POST/PUT/DELETE /api/settings/legal-entities`; exactly one default
  enforced (setting one clears others). Invoice creation accepts `legal_entity_id`
  (falls back to org default entity).
- Wire into invoice creation: entity stamps its details onto the invoice for
  print, and **its** numbering scope (`entity:{id}`) is used if present (depends
  on 1A).
- **F** Settings → Company Profile: Legal entities table (+ add, radio default),
  matching Orderry's screen. Invoice create/edit: entity picker.
- **T**: CRUD + single-default invariant, invoice uses entity numbering when set,
  falls back to org numbering otherwise. **Mig**: +1 table +2 cols.

**Deliverable:** repairos-39. **Docs:** `docs/numbering-and-entities.md`.

---

## Phase 2 — Tax Engine Depth  *(effort M · depends on nothing; better after P1)*

**Why.** We have a flat `Tax(name, rate, mode, is_default)`. Orderry's tax system
drives correct invoicing: per-document tax label, code, location scope, **tax
groups**, an ordered **priority stack** per document family, and **category
exceptions**. This is the difference between "a tax field" and "compliant
invoicing."

- **M extend** `Tax`: `+ code, name_for_document, location_scope(all|selected),
  location_ids_json`. **M new** `TaxGroup(org_id, name, member_tax_ids_json)`;
  `TaxRule(org_id, doc_family[tickets_sales|posting], tax_id, priority)` (the
  ordered stack — "first tax has highest priority"); `TaxException(org_id,
  doc_family, target_type[labor|service|product|category], target_id, tax_id|
  exempt, taxation_system)`.
- **Service** `backend/services/tax.py`:
  `resolve_taxes(db, org_id, *, doc_family, line) -> [applied_taxes]` — checks
  exceptions (most specific: product → category → default stack), respects
  inclusive vs exclusive (`mode`), returns the tax breakdown for a line.
  Org setting `tax_recalc_policy(ask|always|never)` for open-document changes.
- **E** `GET/POST/PUT/DELETE /api/settings/taxes` (drawer fields: name,
  name_for_document, code, type incl/excl, rate, locations); `…/tax-groups`;
  `…/tax-rules` (ordered); `…/tax-exceptions`. Line/invoice totals call
  `resolve_taxes` instead of a single rate.
- **F** Settings → Taxes: Taxes | Tax groups tabs, New-tax drawer, General-rules
  ordered stack (Tickets&Sales / Posting), Exceptions table. (Matches the
  screenshots field-for-field.)
- **T**: inclusive vs exclusive math, priority ordering, exception precedence
  (product beats category beats default), location scoping, recalc policy.
  **Mig**: +3 tables +4 cols.

**Deliverable:** repairos-40. **Docs:** `docs/tax-engine.md`.

---

## Phase 3 — Price Lists  *(effort M · builds on existing `resolve_price_for`)*

**Why.** Single price per catalog item today. Orderry has named **price lists**
(markup % over base, scoped per location) with **defaults per document type**
(Tickets/Sales/Invoices) — the top multi-location seller in the batch. We already
have a `resolve_price_for` hook in `orders.py`, so this slots in cleanly.

- **M** `PriceList(org_id, name, markup_pct, location_scope, location_ids_json,
  is_base)`; `PriceListItem(price_list_id, item_type[service|product], item_id,
  price)` for explicit overrides (else derive base × (1+markup)). Org defaults:
  `price_list_tickets_id / _sales_id / _invoices_id`; per-location override on
  `Location`.
- **Service** extend `resolve_price_for(db, org_id, item, *, doc_type,
  location_id)`: pick the price list (location override → org default per
  doc_type → base), then explicit item price → base×markup. Decimal-places from
  org setting (Phase 4).
- **E** `GET/POST/PUT/DELETE /api/settings/price-lists` (+ items); org defaults
  in `/api/staff/company`.
- **F** Settings → Prices & discounts: price-list table (name/markup/locations),
  company-default pickers per doc type, decimal-places control. Order/sale/invoice
  line creation resolves via doc_type + location.
- **T**: base×markup derivation, explicit override wins, per-location selection,
  per-doc-type default, resolution order. **Mig**: +2 tables +N cols.

**Deliverable:** repairos-41. **Docs:** `docs/price-lists.md`.

---

## Phase 4 — Parity Pack  *(effort M · one pass, many small wins)*

Bundle the cheap, verified gaps that each need only a field + a guard:

- **Contacts:** `Org.contact_consent_phone_default / _email_default`,
  `prohibit_dup_phone / _email`; enforce on client create (409 on dup when set).
- **Invoices:** `prevent_backdating` guard (issue_date ≥ previous), `default_due_days`,
  `default_legal_entity_id` (Phase 1B).
- **Workflows/tickets:** org defaults `default_comment_visibility`,
  `default_file_visibility`, `warranty_count_from(closure|created)`,
  `asset_one_open_ticket` guard.
- **Finance:** payment-category defaults per doc type (Tickets/Sales); `rounding`
  enable + policy; currency **display style** (symbol/code × before/after) +
  **decimal places** (shared with Phase 3).
- **Commission events:** extend the EVENTS registry with `product_sold_no_deduction`,
  `booking_created`, `booking_closed`, and assignee-variant events
  (`assignee_service`, `assignee_product`) — matches Orderry's event dropdown.
- **F:** fold into the relevant Settings sections; each is a toggle/select.
- **T:** one focused test per guard (dup 409, backdating 409, one-open-ticket
  409, a new commission event fires). **Mig:** +~12 cols, 0 new tables.

**Deliverable:** repairos-42. **Docs:** append to `engage-and-payroll.md` +
`docs/settings-parity.md`.

---

## Phase 5 — RBAC v2  *(effort L · the permission catalog is the spec)*

**Why.** Today: flat JSON perms + default-allow `require_perm`. The transcribed
~120-permission Orderry tree (in the research doc §6) is the exact target.

- **M** `Permission` catalog (code, module, label), `Role.permissions_json` →
  normalized `RolePermission(role_id, perm_code, allow)`; deny-wins evaluation;
  **location-scoped roles** (`RoleScope`); role templates (Full/Manager/Partner/
  Specialist as seeds).
- **Compatibility shim:** map existing flat perms → new codes so current roles
  keep working (no breakage). Verb×module granularity: view-scoping of money data
  (cost/profit/purchase price), "display in Managers/Assignee list", edit-closed-
  ticket, shared-filters, period-scoped finance visibility.
- **E** `/api/settings/roles` v2 (matrix get/set), permission catalog endpoint.
- **F** Settings → Roles & Permissions: searchable matrix (not a flat 120-item
  scroll), role templates, per-module group toggles — **beating** Orderry's
  unsearchable tree.
- **T:** deny-wins, location scope, shim keeps a legacy role's effective perms
  identical. **Mig:** +3 tables.

**Deliverable:** repairos-43+. **Docs:** `docs/rbac-v2.md`.

---

## Dependency graph & sequencing

```
P1 Numbering+Entities ──▶ P2 Tax depth (entities optional but natural)
        │                      │
        └──────────────▶ P3 Price lists ──▶ P4 Parity pack (shares decimals/rounding)
                                                   │
                                                   ▶ P5 RBAC v2 (independent; do anytime)
```

Recommended order: **P1 → P2 → P3 → P4 → P5.** P5 (RBAC) is independent and can be
pulled forward if enterprise identity (Roadmap B1) becomes the priority.

## Cross-cutting conventions (every phase)
- New settings endpoints live under `/api/settings/*`, gated
  `require_perm("can_manage_settings")`; reads gated to viewers.
- Every provider/seam stays **gated** — absent config ⇒ safe default, never a 500.
- Backward compatibility is mandatory: current installs must behave identically
  until a setting is explicitly changed (default templates, shim, org defaults).
- Gate per tarball: `ruff` clean (gate scope), full `pytest`, alembic empty→head
  **0 drift**, QA 26/47/70, clean-extract verify. Update CHANGELOG + CHECKLIST +
  a `docs/*.md` page.

## Out of scope here (tracked elsewhere)
UX re-architecture (P0 shell/⌘K/SavedView) → `PRODUCT_BLUEPRINT.md`; payments
live-fire, e2e CI run, media offload, Gmail/Outlook connector → `ROADMAP.md` /
`NEXT_TASK.md`. Later research batches may add Locations/Statuses-editor/
Notifications/Forms-editor phases to this file.
