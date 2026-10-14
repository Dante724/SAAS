# Batch 1 Finish — Public Pages (1a/1b) + Finance UI (2a/2b)

Closes the remaining gaps from the first research batch (the 20 settings screens).

## 1a — Online booking page
- **Background pattern** picker (`Organisation.page_background_pattern`:
  none|topo|gears|dots|waves) + **Change subdomain** (`PUT /api/staff/public-pages/
  subdomain`, url-safe 3–40 chars, uniqueness 409). Booking appearance UI already
  existed; these complete parity with Orderry's booking tab.

## 1b — Order / Estimate public page
- Header + price-row toggles on `Organisation`: `oe_show_logo` + `oe_logo_size`
  (small|big), `oe_show_name/address/email/description`, `oe_separate_tables`
  (products & services in separate tables), `oe_show_zero_price_products/services`.
  Served via the extended `GET/PUT /api/staff/public-pages`. The order/estimate tab
  in the Public Pages settings UI is now a real form (was explanatory text only).

## 2a — Taxes UI
- Settings → Finance & numbering now has a full **Taxes** section: New-tax drawer
  (name, name-for-document, code, inclusive/exclusive, rate, default), list, and
  remove — on top of the tax-depth backend (groups/stack/exceptions) shipped in P2.

## 2b — Default payment categories
- `Organisation.cash_category_{tickets,sales}_id`; `GET /api/staff/cash-categories`
  + `PUT /api/staff/cash-categories/defaults`. Order/invoice payments now use the
  org's default category when set (falling back to the "Sales" category), so the
  setting actually labels recorded payments. UI block in the Finance settings page.

## Tests
`backend/tests/test_batch1_publicpages_finance.py` (3): 1a pattern + subdomain
(valid/short/space 422/409), 1b order-estimate toggles roundtrip + untouched
default preserved, 2b category listing + default set. Migration +12 columns
(schema 92). Suite 90.

## Notes
Background patterns and the OE toggles are stored + API-exposed; rendering them on
the actual public booking/track pages (consuming the flags in the public HTML) is
a thin follow-up on the public-page templates.
