# Numbering Engine + Legal Entities (Phase 1) & Tax Depth (Phase 2)

## Phase 1 — Document numbering
Template tokens resolve against a per-scope atomic counter at document creation:
`{D}` day, `{M}` month, `{Y}` year, `{C:n}` zero-padded counter (`{C}` = unpadded).
Example `INV-{Y}-{C:4}` → `INV-2026-0007`.

- **Model** `NumberSequence(org_id, doc_type[order|invoice|inquiry|sale|po|transfer],
  scope_key['org'|'loc:{id}'|'entity:{id}'], template, next_value)`.
- **Service** `backend/services/numbering.py::next_number(db, org_id, doc_type,
  location_id=?, entity_id=?)` — resolves scope (entity → location → org), lazily
  creates a sequence **seeded past existing documents** so a fresh counter never
  collides with legacy count-based numbers, increments atomically, renders.
- **Wiring** all 10 previously-hardcoded sites (orders×3, invoices×3, PO, sale,
  transfer, v1 order) now call `next_number`; built-in defaults reproduce the old
  formats (`WO-{C:5}`, `INV-{C:5}`, …) so existing installs are unchanged. The
  estimate path keeps its `E` prefix.
- **API** `GET/PUT /api/settings/numbering` (+ `/numbering/preview`); template must
  contain a counter token (422 otherwise); `begins_from` seeds the counter.

## Phase 1 — Legal entities
- **Model** `LegalEntity(org_id, name, address, tax_id, details, is_default,
  active)`; `Invoice.legal_entity_id`, `Location.legal_entity_id`.
- **API** `GET/POST/PUT/DELETE /api/settings/legal-entities`. First entity is
  auto-default; setting a new default clears the others (exactly one). Delete is a
  **soft-delete** when invoices reference the entity (history preserved).

## Phase 2 — Tax engine depth
- **Models** `Tax` extended with `code, name_for_document, location_scope[all|
  selected], location_ids`; `TaxGroup`; `TaxRule(doc_family[tickets_sales|posting],
  tax_id, priority)` — the ordered default stack; `TaxException(doc_family,
  target_type[labor|service|product|category], target_id, tax_id|exempt,
  taxation_system)`.
- **Service** `backend/services/tax.py::resolve_taxes(...)` → `{taxes:[{tax_id,
  name,rate,mode,amount}], tax_total, exempt}`. Precedence: exception (product →
  category) beats the default stack; honours `mode` (`included` = tax contained in
  price, extracted; `imposed` = added on top); respects location scope.
- **Auto-apply** order lines with no explicit tax get the org's default
  Tickets&Sales stack stamped on (single effective rate/mode) via
  `_auto_apply_tax` in `add_line`, so the Tax settings actually drive taxation.
- **API** `/api/settings/taxes`, `/tax-groups`, `/tax-rules` (ordered),
  `/tax-exceptions`.

## Tests
`backend/tests/test_numbering_entities_tax.py` (8): token render incl. unpadded
`{C}`, default = legacy format, set+preview, per-scope isolation, legal-entity CRUD
+ single-default, tax extended-fields round-trip, tax stack + inclusive/exclusive
math, exception precedence + exemption.

## Caveats
Atomic increment is `flush`-based (fine for sqlite dev + single-writer);
Postgres production should use `SELECT … FOR UPDATE` on the sequence row (noted for
B3 scale work). Per-location/entity numbering scopes are modelled and resolved but
the Settings UI currently edits the org scope; location/entity scope is API-set.
