# Price Lists (Build Phase 3)

Completes the pre-existing **PriceType** ("named markup over cost") into full
Orderry-style price lists: location scoping, explicit per-item overrides, and
per-document-type company defaults.

## Model
- `PriceType` + `location_scope(all|selected)` + `location_ids` (JSON).
- `PriceTypeItem(price_type_id, item_type[product|service], item_id, price)` —
  an explicit price that **overrides** the cost×markup derivation. Unique per
  (price_type, item_type, item_id).
- `Organisation.price_type_{tickets,sales,invoices}_id` (default list per doc
  type) + `price_decimals`. `Location.price_type_id` (per-location override).

## Resolution (in `backend/routers/pricing.py`)
- `resolve_price_for(kind, ref_id, price_type_id)`: **item override → cost×markup**
  (`avg_cost`/`cost_price` for products, `cost_price` for services), returns None
  to fall back to the item's standard price.
- `default_price_type_id(org, doc_type, location_id)`: **location override → org
  default for doc type → org default price type → None.**
- `resolve_doc_price(kind, ref_id, doc_type, location_id)`: convenience combining
  both — pick the list for the document, then price the item.

## API
- Price types: existing `/api/pricing/types` (now carries location scope).
- `GET/PUT /api/pricing/types/{id}/items` — explicit item overrides.
- `GET/PUT /api/pricing/defaults` — per-doc-type default lists + decimals.

## UI
Settings → Prices & discounts: existing price-type table + a new **Company
default price lists** block (Tickets/Sales/Invoices pickers + decimal places).

## Tests
`backend/tests/test_price_lists.py` (4): location scope roundtrip, cost×markup +
item override precedence, doc-type default resolution (tickets 2× vs sales 4×),
defaults + decimals roundtrip.

## Notes / next
Order/sale/invoice line creation currently resolves price via the **client's**
price type (existing behaviour, unchanged). Wiring `resolve_doc_price` as the
doc-level default when a line has no client-specific price is a small follow-up
(kept separate to avoid changing existing pricing behaviour in this phase).
