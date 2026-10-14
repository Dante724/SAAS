# Parity Pack (P4) + RBAC v2 (P5)

## Phase 4 — Parity pack
Cheap, verified Orderry-parity settings — each a field + guard.

**Contacts:** `Organisation.{contact_consent_phone_default, contact_consent_email_default,
prohibit_dup_phone, prohibit_dup_email}` + `Client.{consent_phone, consent_email}`.
Client create enforces dup guards (409) and applies consent defaults from org policy.

**Invoices:** `prevent_backdating_invoices` (422 when now precedes the last invoice's
issue date, via `_check_backdating`), `default_due_days`.

**Workflows/tickets:** `default_comment_visibility`, `default_file_visibility`,
`warranty_count_from(closure|created)`, `asset_one_open_ticket`.

**Finance/display:** `rounding_enabled`, `currency_display_style(symbol|code|iso)`,
`currency_position(before|after)`.

**Commission events (payroll engine):** the per-line loop now also fires
`product_sold_no_deduction` (products added without stock deduction) and
**assignee-variant** events — when the order's assignee differs from the line's
performing tech, `assignee_labor` / `assignee_product` accrue to the assignee.
Mirrors Orderry's event catalog (performer vs assignee, deducted vs not).

All settings ride `GET/PUT /api/staff/company`. Migration: +15 org/client columns.

## Phase 5 — RBAC v2
Backward compatible: `Role.permissions` stays a flat `{code: bool}` dict and
`require_perm` keeps **default-allow** (deny only on explicit `False`). v2 adds
structure without breaking existing roles.

- **Permission catalog** (`backend/services/permissions.py`): 66 permissions across
  8 modules (General, Workflows, Catalog, Sales, Invoices, Finance, Payroll,
  Settings) — the transcribed Orderry roles tree as the spec. `ALL_CODES` validates.
- **Templates:** Full access / Manager / Partner / Specialist (deny-lists).
- **Location scoping:** `Role.{location_scope, location_ids}` + `in_location_scope()`
  helper (all-scope or no-location => allowed).
- **`role_allows(role, code)`** — deny-wins evaluation for both legacy flat perms
  and v2 matrix.
- **API** `/api/settings/rbac/{catalog,roles}`: catalog+templates, role CRUD with
  matrix + template seed + scope; unknown codes → 422; role ops gated
  `can_manage_roles`.

## Tests
`backend/tests/test_parity_rbac.py` (8): dup-phone 409 + dup-email-off default,
settings roundtrip (backdating/warranty/asset/rounding/currency), assignee-variant
commission split (10.00 performer + 5.00 assignee), permission catalog, role from
template + 422 validation, location-scope roundtrip, legacy flat-perm deny-wins.

## Notes / next (UI)
The RBAC catalog endpoint powers a searchable role matrix (planned Settings →
Roles & Permissions v2 UI, beating Orderry's unsearchable tree). Wiring
`in_location_scope` into `require_perm` for location-gated actions is a small
follow-up (helper is ready; no endpoint currently enforces it to avoid changing
behaviour mid-phase).
