# Council: inventory identity (bins, serials, barcodes) and import templates — 25 Oct 2026

Inputs: code audit of Tekly (branch `claude/orderry-ws-a-stock`, PR #21) and Orderry help-centre / changelog research (help.orderry.com, orderry.com/whats-new, checked 25 Oct 2026). Competitor notes: RepairDesk, RepairShopr.

## Verdict in one line
Tekly is **ahead of Orderry at stock intake** (live IMEI checks, sold-before warnings, AI invoice reading, paste-and-match into postings and POs). It is **behind on the basics underneath**: nothing in the database guarantees a barcode, SKU, bin or serial is unique; barcodes are random; bins are half-built and have a real bug; and product import has no button, no template and no error report. Fix the foundation first, then the import centre. After that, Tekly beats Orderry on every line of the table below.

## Where we stand vs Orderry

| Area | Orderry | Tekly today | Gap |
|---|---|---|---|
| Bin setup | Bins per warehouse, default bin, "add one more", rename, delete-if-empty | Add / list / delete; keyed by location, `warehouse_id` never set; rename can create duplicates | **Behind** |
| Bin barcodes and labels | Auto-generated (editable), print button | Random 12-character code, not unique, no print, no scan | **Behind** |
| Stock in several bins | Yes, per-bin quantities, choose a bin when using stock | One bin per product per location; a bin transfer creates a second stock row that **breaks the next posting** | **Behind + bug** |
| Serial entry at intake | Paste up to 200, skip duplicates, highlight invalid | Paste/scan up to 500, Luhn check, in-stock block, sold-before warning, cross-line check | **Ahead** |
| Serial generator | Template `text{C:n}`, start number, "generate missing serials" in file posting | Assets: random `DEV-XXXXXXXX`, no collision check; products: none | **Behind** |
| Serial uniqueness | Unique per unit | App check plus a lock; no database rule | Behind (risk) |
| Unit passport and serial labels | Per-unit history, supplier warranty, print label | No passport screen; `{Product Serial Number}` label variable is empty | **Behind** |
| Barcode formats | EAN-13, EAN-8, UPC-A, Code128; several barcodes per product; unique | EAN-13, UPC-A, Code128, QR (no EAN-8 rendering); one barcode per product; not unique | Behind |
| Barcode generation | Generate on card; **bulk** "products without barcode → Add barcode" | One product at a time; fully random digits, no internal prefix, no collision check | **Behind** |
| SKU / product code | SKU or code usable as barcode | No SKU generator; the "unique identifier" setting is saved but never used | Behind |
| Product import | Strict template download, 2,500 rows, match by name | Endpoint only, **no button, no template**, no preview, one bad cell aborts the file | **Behind** |
| Posting import | Mapping saved per supplier, 250 lines | Paste with auto-mapping, AI photo/PDF reading, preview, 500 rows | **Ahead** (no file upload or per-supplier mapping yet) |
| PO import | Excel/CSV, product type | Paste, match by SKU/barcode/MPN/name, create missing | Level (file upload missing) |
| Client import | Not documented | Backend has dry run and row errors; UI ignores both, no template | Behind ourselves |
| Asset / service import | Not documented / — | None | Gap |
| Stocktake | By bin, blind scan, export → fill → import, upload levels | Scan count, no bin scan, the same serial scanned twice counts twice, no count import | **Behind** |
| Expiry | Per batch, FEFO, expiring report, write-off from report | Batch expiry at intake (new), FEFO write-off suggestions | Level |

## What the council says

**PM (shop owner's voice)**
- "When a box of phones arrives, I want to scan it in, print a sticker for every unit and shelf, and later find any phone in two seconds." That is the whole story. Bins, serials, barcodes and labels are one feature to a shop owner, not four.
- The import button is the first thing a switching shop touches. If moving from Orderry or RepairDesk takes an afternoon of fixing spreadsheet errors, we lose them on day one. **The template download is the onboarding.**
- Beat Orderry where it is rigid: Orderry demands its exact columns and matches on name, so renaming a product creates a duplicate. We accept the shop's own sheet, map the columns, remember the mapping, and match on the identifier the shop chose (SKU, barcode or name).

**Engineer**
- **Fix first:** the bin-transfer bug. Two stock rows for one product and location make `stock_posting.apply_receipt_line` crash on the next receipt. Move to per-bin stock rows and keep (part, location, bin) unique.
- **Uniqueness in the database, not just in code.** Partial unique indexes per shop:
  - barcode (on a new `product_barcodes` table, so a product can have several);
  - SKU, case-insensitive, active products only;
  - bin name per warehouse, and bin barcode per shop;
  - serial while in stock or reserved.

  Each migration first runs a dry-run report of existing duplicates and refuses with a clear list instead of failing half-way.
- **One code generator.** Extend `services/numbering.py`, which already does `{C:n}` counters with collision skipping:
  - **barcodes:** internal EAN-13 in the GS1 in-store range (prefix 20–29) plus a per-shop counter and the check digit, so codes are valid, scan everywhere and never collide with real manufacturer barcodes;
  - **SKU template** (e.g. `{CAT}-{C:5}`), serial template (Orderry-style `text{C:n}`), bin codes and asset serials.
- Wire up the dormant `inv_unique_identifier` setting. It decides the import match key and which field must be unique.
- Retire the old `/api/print/labels` PDF path (unused) or fold it into the HTML label renderer, so there is one label system. Fill in the empty `{Product Serial Number}` and `{Product Bin Location}` variables.

**Tester**
- Tests for every uniqueness rule:
  - concurrent creation of the same barcode, SKU or serial (one wins, one gets a 409);
  - renaming a bin to an existing name;
  - generated codes stay unique after 10,000 generations;
  - EAN-13 check digits always valid.
- Tests for every import:
  - the template round-trips (download → fill → import → export matches);
  - every row error is reported (wrong type, missing required, unknown category, duplicate key) without aborting good rows;
  - dry run writes nothing;
  - upsert by each identifier choice;
  - size caps;
  - Excel quirks: leading zeros on barcodes stored as numbers, decimal commas, BOM in CSV, dates as serial numbers.
- Stocktake: scanning a bin sets the context; the same serial twice counts once.

**Security**
- Imports are the biggest attack surface in this area:
  - keep the zip-bomb and row caps in `core/spreadsheet.py`;
  - neutralise formula injection on export (`=`, `+`, `-`, `@` prefixes) so a malicious product name can't run in the owner's Excel;
  - parse in a size-limited, no-macro way (openpyxl read-only, no `.xlsm`).
- Scope every import, match and uniqueness check to the shop. A barcode taken in another shop must not be revealed or blocked.
- Imports that create products need "Create products"; updating prices needs "Edit products"; cost columns are dropped for roles that can't see cost.
- Keep an audit row per import (who, file name, counts) and an **undo last import** window, which RepairDesk offers as rollback.

**Designer (UX)**
- One **Import** button per list (Products, Services, Clients, Assets, Stock count). It opens a three-step drawer: **1 Download template / upload file → 2 Check columns (auto-mapped, remembered) → 3 Review (green: will create, blue: will update, red: needs fixing; download errors as a file) → Import.**
- The template is a real XLSX, not a bare CSV:
  - an *Instructions* sheet;
  - required columns marked;
  - dropdowns for Unit, Category, Product type and Yes/No;
  - one example row;
  - barcode and SKU columns pre-formatted as text so leading zeros survive.

  CSV is accepted too.
- Labels: a "Print labels" action anywhere you see units, bins or products, with a preview and a quantity per item.

## Plan (Now / Next / Later)

| # | Item | Size | Why first |
|---|---|---|---|
| **Now — foundation** | | | |
| 1 | Fix bin-transfer stock-row bug; per-bin stock rows; set `warehouse_id` on bins | M | Live data-corruption risk |
| 2 | Uniqueness: duplicate report + partial unique indexes (barcode, SKU, bin, in-stock serial); wire `inv_unique_identifier` | M | Everything else relies on it |
| 3 | Code generator: GS1 in-store EAN-13 barcodes with check digit and collision skip; SKU, serial and bin templates in Settings | M | Ends random codes |
| 4 | Bulk "generate barcodes for products without one"; several barcodes per product | S–M | Orderry parity |
| **Next — import centre** | | | |
| 5 | Import drawer + XLSX/CSV templates for Products (with opening stock, bin, serials) | M | Switching shops start here |
| 6 | Same pipeline for Services, Clients (use the existing dry run), Assets, Suppliers | M | One pipeline, five lists |
| 7 | File upload (XLSX/CSV) for postings and POs, with mapping saved per supplier; generate missing serials | S–M | Beats Orderry's 250-line, fixed template |
| 8 | Undo last import, audit row, formula-safe export | S | Safety |
| **Later — finish the experience** | | | |
| 9 | Bins: edit, print bin labels, scan a bin anywhere (stocktake, transfer, putaway), per-bin quantities on product card | M | Orderry parity+ |
| 10 | Serial labels and unit passport (history, supplier warranty, where it is now) | M | Orderry parity |
| 11 | Stocktake: by bin, per-serial de-dup, count export/import, code/MPN match | M | Orderry parity |
| 12 | EAN-8 rendering; retire the old PDF label path | S | Clean-up |

**Cut line:** items 1–5 are the commitment, about 1.5 sprints for one session. Items 6–8 come next if there is capacity. Items 9–12 are the following sprint.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Existing shops already have duplicate SKUs, barcodes or serials, so unique indexes fail | Migration blocked | Duplicate report first; index only active rows; show the owner a "fix these" list; don't auto-merge |
| Changing barcode generation breaks printed labels already in use | Labels stop scanning | Never rewrite existing codes; only new ones use the GS1 range |
| Excel mangles barcodes (scientific notation, lost zeros) | Wrong matches | Template columns formatted as text; importer re-pads and validates check digits; warns on 1.23E+12 |
| Workstreams B/C touching inventory files at the same time | Merge conflicts | Do this after PR #21 merges; own the inventory, bins and catalog files |

## Questions for the owner
1. **Match key for imports:** default to SKU (best practice) or name (what Orderry does)? Council recommends SKU, falling back to barcode, then name.
2. **Barcode format** for generated codes: EAN-13 (prints on every label printer, scans at any till) or Code128 (shorter, allows letters)? Council recommends EAN-13 in the 20–29 in-store range.
3. Start **Now** items straight after PR #21 merges, as a new workstream session?

---

# Build council: "Now" items 1–4 — 25 Oct 2026

Base: `claude/orderry-ws-a-stock` (PR #21 not merged yet). Branch: `claude/inventory-identity-foundation`.

## Decisions

**1. Stock rows per bin.** A stock row is keyed **(product, location, warehouse, bin)**; "no warehouse" and
"no bin" are values of the key, not wildcards. One unique index enforces it
(`uq_stock_levels_key` on `part_id, location_id, coalesce(warehouse_id,''), coalesce(bin_id,'')`).
All stock reads and writes go through one helper, `services/stock_rows.py`:
- `totals(part, location)` — available / reserved summed over every row at the location (what every
  availability check uses; before this, a second row made `scalar_one_or_none` crash);
- `add(part, location, qty, bin=…)` — atomic increment of the exact row, created if missing;
- `draw(part, location, qty, bin=…)` — guarded decrements: the named bin first, then the unbinned row,
  then other bins (largest first); any shortfall the caller allowed lands on the unbinned row;
- `reserve(part, location, delta)` — reservations live on the unbinned row (reserved is a location-level idea).
Bin transfer moves quantity between two exact rows; the location total never changes. Bins get
`warehouse_id` (the location's default product warehouse, or the one chosen). Renaming a bin to a name
already used in the same warehouse is refused with a 409.

**2. Uniqueness in the database.** Migration `ii144a01` runs a duplicate report first and **refuses with a list**
(per shop: the value and how many products/bins/units share it) instead of failing half-way. No auto-merge.
Indexes (all per shop, partial):
- `product_barcodes` (new table, several barcodes per product): unique `(org_id, barcode)`; backfilled from
  `parts.barcode`, which stays as the product's *main* barcode and is kept in sync;
- SKU: unique `(org_id, lower(sku))` among active products;
- bin name: unique `(location_id, coalesce(warehouse_id,''), lower(name))` among active bins; bin barcode: unique
  `(org_id, barcode)` among active bins;
- serial: unique `(org_id, upper(replace(serial_number,' ','')))` among `in_stock` / `allocated_to_order` units —
  per shop, the same rule and the same normalised form the intake check already uses.
- `inv_unique_identifier` (name | sku | barcode | code) now does something: the chosen field must be unique
  among active products (checked on create, edit, variants and import), and it is the import match key.
  SKU and barcode are always unique regardless; the setting adds name or code on top.
- A database clash (two saves at once) comes back as a plain **409** with a sentence, never a 500.

**3. One code generator** (`services/numbering.py`):
- barcodes: EAN-13 `2` + `0–9` (GS1 in-store range 20–29), per-shop counter, check digit, skips any code
  already in use in the shop. The shop picks the prefix digit (default `20`). Code128/QR keep a counter code.
- templates (Settings → Inventory): SKU (`{C:5}`, `{CAT}` = first 3 letters of the category), product serial
  (`SN{C:6}`, Orderry-style `text{C:n}`), bin code (`BIN-{C:4}`) and asset serial (`{T}-{C:6}`, `{T}` = first 3
  letters of the asset type). Each has a counter in `number_sequences`; skip-if-taken like invoices.
- **Existing codes are never rewritten.** Only empty fields get a code.

**4. Bulk barcodes + several barcodes per product.** `POST /api/catalog/products/barcodes/generate-missing`
(`{ids?}`; default all active products without a barcode, capped at 5,000). Stock tab gets
"Generate missing barcodes". Products can hold extra barcodes (`GET/POST/DELETE /api/catalog/products/{id}/barcodes`).
Every scan path matches main *and* extra barcodes: inventory scan, `/api/scan` resolve, sales scan, stocktake scan,
transfer scan, catalog search.

## Engineer
- The crash class is "two rows for one (part, location)". Fix it at the key, not per call site, and make
  every call site use the helper so it cannot come back. Existing duplicate rows (from the bug) are merged
  in the migration only when they share the full key (same bin) — that is lossless.
- Postgres and SQLite both support the partial and expression indexes; the dialect test covers them.

## Tester
- Bin transfer then a receipt: no crash, totals right, bin split right. Transfer more than the bin holds: 409.
- Sale / work-order / write-off after a transfer draws across bins without going negative per bin.
- Duplicate barcode / SKU / serial / bin name: app 409; forced DB insert: IntegrityError mapped to 409.
- Migration pre-flight: seeded duplicates → upgrade refuses and prints the list.
- EAN-13: 10,000 generations unique, all check digits valid, all in 20–29; skips a taken code.
- Templates render; existing codes untouched by bulk generate; another shop's barcode never blocks or leaks.

## Security
- Every uniqueness check and lookup is scoped by `org_id`; a code used in another shop is neither blocked nor
  revealed. Bulk generate needs "Manage inventory", ids filtered to the caller's shop, capped.
- Template input is length-limited and only known tokens are rendered (no format-string injection).
- Error messages name the conflicting product only within the same shop.

## Review outcome (security + tester reviewers, 25 Oct 2026)

Fixed in this branch:
- **Security (medium):** a stocktake line could name another shop's bin and create stock in it (raw insert bypasses the
  ORM tenant guard). Every stocktake bin is now checked against the shop and location; bin totals join on the shop.
- **Security (low):** reserve-stock checked ownership after revealing quantities; reservation release is now a guarded
  UPDATE; the asset-serial preview runs one query, not up to 1,000; a lost race in a clamped draw no longer 500s.
- **Tester (high):** `draw` retried on numbers cached in the session and failed every retry under concurrency →
  rows are re-read fresh (`populate_existing`); `allow_short` never raises.
- **Tester (medium):** stock-document lines naming a bin ignored it when the warehouse also had a tagged row; a
  warehouse stocktake seeded one line per bin row and scans landed on the first → one line per product per count
  (per bin for a bin count), scans match on the bin.
- **Tester (low):** merge keeps the source's barcodes scanning (moved to the target); SKU scan matching is
  case-insensitive like the index; running out of codes is a plain 409, not a 500.

Known, left for the "Later" items:
- Reversals (sale void, transfer cancel, job un-use) put stock back on the unbinned row, not the bin it came from.
  Location totals are right; the bin split drifts until item 9 records which bin each draw came from.
- `PostingLine` / `PurchaseReturnLine` (stock documents) don't store a bin, so deleting such a document returns stock
  unbinned.
- No UI yet for "generate serials" at intake (endpoint is in place — item 7).
