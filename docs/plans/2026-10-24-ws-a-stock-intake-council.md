# Council note — Workstream A: stock intake and catalog (24 Oct 2026)

Sprint plan: `2026-10-24-orderry-jun-jul-sprint.md` (A). Gap items 16b, 19, 16a, 13b, 3, 21.
Base: `roadmap/orderry-parity`. Files owned: inventory, postings, purchase orders, catalog products, assets.

## What we're building
| P | Item | Where |
|---|---|---|
| P0 | Bulk IMEI/serial entry at intake — posting review step and PO receiving; scan one after another or paste many; one serial per unit | `services/serial_intake.py`, `POST /inventory/serials/check`, posting commit, PO receive; `SerialEntry` component |
| P0 | Active / written-off filter on assets with counts, remembered | `GET /assets?state=` + `counts`; AssetsHub tabs |
| P1 | Product type (standard / serialized / expiry-tracked) in posting import; preview flags rows that still need serials | paste column "Type", preview + commit |
| P1 | Import products into a purchase order through the same paste/map pipeline; match by SKU / barcode / code / MPN; offer to create missing ones | `POST /inventory/parts/resolve-import`; NewPODrawer "Paste from Excel" |
| P1 | Custom product line on a job with "Save to catalog" (name, SKU, barcode) | `LineIn.catalog_sku/catalog_barcode`; WODrawer "Custom item" |
| P2 | One search box finds an asset by plate, IMEI, serial, VIN or HIN | `GET /assets?search=`, `POST /assets/identify` |

## PM
- The shop's real pain: a box of 20 phones arrives and there was no screen to put the IMEIs in. Scanning must be fast: scan → Enter → next, focus stays in the box, a counter shows "12 of 20".
- Wording is plain: "Already in stock", "Sold before — check it's a trade-in or return", "Check digit doesn't match — probably a typo".
- Not in scope: expiry write-off action, per-warehouse replenishment, PO tax/payments (later list).

## Engineer
- One check function used by the live check endpoint, posting commit and PO receive, so the screen and the server never disagree.
- Normalise via `services/identifiers.py` (IMEI digits only; serial upper-case, no spaces). Stored serials are the normalised value.
- Rules:
  - **Block**: same serial twice in one intake; serial already in stock (in_stock or allocated) in this shop; serial count ≠ qty; fractional qty on a serialized product; serialized product received with no serials.
  - **Warn only**: IMEI checksum/length wrong (customer at the counter — consistent with the asset rule); serial seen before but sold / written off / converted (trade-ins and returns are real).
- Posting commit reuses `apply_receipt_line(serials=...)`, which already creates one `SerializedItem` per unit. PO receive already passes serials; it now validates them first.
- Expiry-tracked rows can carry a batch expiry date into the FIFO layer.
- Assets "active" = everything not written off. Counts come from one grouped query.
- Plate search reads `custom_data` keys `plate`, `plate_registration`, `registration` with the cross-dialect JSON accessor (SQLite and Postgres).
- PO import adds no new PO endpoint: preview reuses `/inventory/postings/preview`, missing products are created by `resolve-import`, and lines go through the existing create/edit PO calls (org checks already there).
- No migration needed: every column already exists.

## Tester
- Pytest: Luhn pass/fail, normalisation, in-batch duplicate, in-stock duplicate (blocked at commit and receive), sold → warning only, count mismatch, serialized-without-serials blocked, cross-tenant serial not leaked or blocking, preview `needs_serials`, product type on new products, asset filter counts + state, plate/IMEI search with spaces, resolve-import creates missing and matches barcode, save-to-catalog SKU/barcode + permission.
- Vitest: serial list parsing (newline/comma/space, scanner Enter), local duplicate + Luhn flags, paste "Type" column mapping.
- Browser: posting with serials, PO receive scanning, assets filter, custom line — light and dark.

## Security
- Every lookup is scoped to `current.org_id`; the check endpoint never reveals another shop's serials (a serial in another org reads as new).
- Check endpoint needs `can_manage_inventory` and caps input (500 serials, 120 chars each).
- Save-to-catalog now needs `can_create_products` (product) or `can_edit_labors` (service) — previously anyone who could add a custom line could create catalog items.
- resolve-import needs `can_manage_inventory` + `can_create_products` when it creates anything; MPN uniqueness reuses `_checked_mpn`.
- Search input goes through bound parameters only (no raw SQL); LIKE wildcards in user input are escaped.
