# Numbering Engine · Legal Entities · Tax Depth (Build Phases 1 & 2)

## Phase 1A — Numbering engine
Document numbers are produced from a **token template** against a per-scope
counter, replacing the previously hardcoded `f"INV-{n:05d}"` at all 10 call sites
(orders×2, cash×2, inventory, leads, sales, transfers, v1, payments).

Tokens: `{D}` day · `{M}` month · `{Y}` year · `{C:n}` zero-padded counter.
Example `INV-{Y}-{C:4}` → `INV-2026-0001`.

- **Model** `NumberSequence(org_id, doc_type, scope_key, template, next_value)`,
  unique on (org, doc_type, scope_key). `scope_key` ∈ `org` | `loc:{id}` |
  `entity:{id}`.
- **Service** `backend/services/numbering.py`: `next_number(db, org_id, doc_type,
  *, location_id=None, entity_id=None)` resolves entity → location → org, allocates
  and renders atomically inside the caller's transaction. `render()` and
  `peek_example()` are pure. **Backward compatible:** with no config, the built-in
  default template reproduces the historical format, and a new org sequence is
  **seeded past existing documents** (`_existing_max`) so it never collides with a
  UNIQUE document number.
- **API** `GET/PUT /api/settings/numbering`, `POST /api/settings/numbering/preview`.
  PUT rejects templates without a `{C}` counter (422).

## Phase 1B — Legal entities
- **Model** `LegalEntity(org_id, name, address, tax_id, details, is_default)`;
  `Invoice.legal_entity_id`. Exactly one default (setting one clears others).
- **API** `GET/POST/PUT/DELETE /api/settings/legal-entities` (soft delete).
- When an invoice carries a legal entity, `entity:{id}` numbering is used if
  configured (via Phase 1A resolution order).

## Phase 2 — Tax engine depth
- **Tax** extended: `code`, `name_for_document` (print label), `location_scope`
  (all|selected) + `location_ids`. Mode `included` (inclusive) vs `imposed`
  (exclusive) already existed.
- **TaxGroup**, **TaxRule** (ordered default stack per `doc_family`
  tickets_sales|posting; priority asc = highest precedence), **TaxException**
  (per labor/service/product/category → specific tax or exempt).
- **Service** `backend/services/tax.py::resolve_taxes(...)` — exception precedence
  (item → category → default stack), inclusive/exclusive math, location scoping.
  Returns per-tax amounts + total + exempt flag.
- **API** `/api/settings/{tax-groups,tax-rules,tax-exceptions}`; extended
  `/api/catalog/taxes` create/update to carry the new fields.

## UI
Settings → **Finance & numbering** (`settings_finance.js`): numbering table with
live preview + per-row save; legal-entity add/list/remove; tax-stack picker for
Tickets & Sales.

## Tests
`backend/tests/test_numbering_entities_tax.py` (8): token render, default=legacy
format, set+preview+422 guard, scope isolation, entity single-default + soft
delete, tax extended-fields roundtrip, inclusive/exclusive stack math, exception
precedence.

## Notes / next
Posting-family stack, per-location numbering UI, and per-legal-entity numbering
rows are supported by the backend and are UI follow-ups. Price lists = Build
Phase 3.
