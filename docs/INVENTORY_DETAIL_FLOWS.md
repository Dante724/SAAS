# Inventory detail flows — Orderry vs Tekly (stock · asset · PO · postings)

*Deep research (our Orderry screenshot/help corpus + code trace) + council brainstorm (product ×2 · tech-PM · senior-architect). Market: US · LATAM · EU. Note: live Orderry re-verification was blocked this session (help center is JS/login-gated, web-fetch timed out); Orderry facts below are from our prior primary-source capture in `docs/ORDERRY_UI_RESEARCH.md` + `PRODUCT_BLUEPRINT.md`.*

The scheme underneath all four is the same **document-driven stock ledger**: quantities are never edited directly — every change is a document (posting, write-off, transfer, stocktake, sale) that writes an immutable `StockMovement`, and stock is the sum of movements. We already use this model; the gaps are what each *detail screen surfaces* and a few purchasing depths.

---

## 1. Opening an existing STOCK / product

**Orderry — flow & scheme.** Click a product → card with: identity (name/SKU/barcode/category), **per-warehouse stock breakdown**, cost/price/markup, **serial-number list** (if serialized), **movement history** (every in/out with document link), **linked purchase orders**, reservations against open tickets, and "used in" (documents that consumed it). Serial tracking is per-item; money fields (cost/purchase price) are permission-scoped.

**Tekly — what we have.** `ProductDrawer` tabs: **General specs**, **Stock & pricing**, **Variants** — plus `serials_in_stock`, and now the **Compatible devices** editor (144jt). Backend already exposes far more than the drawer shows: `/parts/{id}/movements`, `/parts/{id}/batches`, `/parts/{id}/stock` (per-warehouse), `/parts/{id}/purchase-orders`, `/reservations`. We also have batch/expiry, moving-average cost, bins.

**Gap = surfacing, not capability.** The product drawer doesn't show the movement history, the linked-PO list, or reservations — all of which already exist as endpoints. Add two tabs — **Movements** (the audit trail) and **Purchasing & reservations** (linked POs + who's holding stock) — and the product card matches/beats Orderry with zero new backend.

---

## 2. Opening an existing ASSET (device/vehicle/…)

**Orderry — flow & scheme.** Asset card = the item's life story: identity (type + VIN/IMEI/Serial), owner (client) with link, **full service-history timeline** (every ticket/visit), **warranty status**, custom fields, photos/files, prior-visit count.

**Tekly — what we have.** `assets/{id}` returns the asset + **owner + owner balance inline**, its **orders list**, and actions: `/movements`, `/transfer`, `/write-off`, `/convert-to-product`. Identifier-aware form (VIN/HIN/IMEI/Serial + lookup), per-asset noun by industry template, image, prior-visits badge on the WO.

**Gaps.**
- **Warranty status** on the asset card — we store `warranty_days`/`supplier_warranty_days` on parts and capture serials, but the asset card doesn't compute "in/out of warranty" from the last qualifying repair. High-value, low effort.
- **Files/photos tab** on the asset (intake photos, documents) — we have media infra; surface it here.
- **Service-history timeline** — we have the orders list; upgrade to a dated timeline (visits + notes + outcomes), reusing the feed we built for clients.
- **Asset custom fields** on the detail (form is identifier-aware; ensure custom fields render/edit on the card).

---

## 3. PURCHASE ORDERS

**Orderry — flow & scheme.** Create PO (supplier + expected date + lines) → send → **receive (full or partial)** → stock posts in at cost. Reorder suggestions feed PO creation; supplier price lists; tax can apply.

**Tekly — what we have (strong, often ahead).** `PurchaseOrder` (supplier, location, po_number, status, `on_credit`, `received_cost`, `expected_at`/`ordered_at`/`received_at`, **supplier_invoice_no/date**, **charges** + **charges_to_cost** = landed cost, notes) with `POLine` carrying **qty_ordered/qty_received** (partial receive) and **reserved_order_id** (links a PO line to the WO waiting on it). Endpoints: create, **group POs**, send, receive (updates WO pending-stock + landed cost into avg cost), **reorder-list / reorder-forecast → auto-create POs / digest**. `Supplier` already has **`currency`** and **`lead_days`**.

**Gaps.**
- **Multi-currency purchasing** — `Supplier.currency` exists but PO lines/receipt should price in the supplier's currency and convert to base at receipt (store base in `avg_cost`, keep original on the PO). The RegionPack currency layer (144jr) is the natural home. Global-market essential.
- **Multi-supplier catalog** — one part, several suppliers with their SKU/price/lead-time (`part_suppliers`), so PO creation picks the best source. Today `Part.default_supplier_id` is single.
- **PO status granularity + approval** — surface draft → sent → partially received → received → closed; optional over-threshold approval.
- **Attach the supplier invoice file** (we store the number/date; add the document).

---

## 4. POSTINGS (goods-in)

**Orderry — flow & scheme.** A posting is a stock-in document: pick warehouse + supplier + reference, add lines with purchase price; **tax applies to postings** (its own tax stack, separate from sales); posting raises quantity + sets cost, writes movements, and can carry a reason.

**Tekly — what we have.** `/inventory/postings` — bulk inflow with per-line purchase price + **optional markup% that auto-sets sell price**, updates **moving-average cost**, records a **movement per line**, ties to a supplier + note/reference. Plus `stock_docs`: **write-offs** (with reasons + suggestions), **returns**, and a separate postings surface.

**Gaps.**
- **Tax on postings** — the tax engine has a `posting` doc-family (per `BUILD_ROADMAP_PARITY.md` `TaxRule.doc_family=posting`), but the posting create path doesn't apply it. Wire posting tax so landed cost/valuation is tax-correct where postings are taxed (EU/LATAM).
- **Reverse / correct a posting** — write-offs are editable; a posting should be reversible (mirror the transaction correction pattern) so a mis-receipt doesn't require a manual counter-write-off.
- **Attach document** to a posting (supplier delivery note).

---

## 5. Council brainstorm

### 🧭 Product (two angles)
- **"Surface what we already store" is the cheapest win.** The product and asset cards under-show data we already compute (movements, linked POs, reservations, warranty). This is a UX/exposure gap, not an engineering one — the fastest way to visibly out-detail Orderry.
- **Repair-native depth is the moat.** Asset **warranty intelligence** (scan serial → in/out of warranty + full history) and product **"where used / compatibility"** turn generic stock cards into repair-shop tools Orderry doesn't have.

### 🏗️ Tech-PM — build order (impact × effort)
1. **Product drawer: Movements + Purchasing/Reservations tabs** — pure FE over existing endpoints. (Now, S)
2. **Asset card: warranty status + files + service timeline** — mostly FE; warranty calc is small BE. (Now/Next, S–M)
3. **Multi-currency PO receipt** — BE (price in supplier currency, FX→base at receive) reusing RegionPack. (Next, M)
4. **Multi-supplier catalog** (`part_suppliers` + best-source pick in PO create). (Next, M)
5. **Tax on postings** — wire the existing `posting` tax-family into the posting path. (Next, S–M)
6. **Posting reverse/correct + attach document**. (Later, M)

### 🔒 Senior-architect — integrity guards
- Every new surface stays **read-through the movement ledger** — don't add a second source of truth. Movements/postings/receipts must each write `StockMovement`.
- **Multi-currency**: convert at receipt, store base in `avg_cost`; never mix currencies in valuation; keep the original currency + rate on the PO for audit.
- **Reversal safety**: a posting reverse must write a compensating movement (not delete history) and refuse if the received stock has already been consumed below the reversal qty.
- **Serial correctness**: serial uniqueness per-org; stocktake already flags `serial_mismatch` — keep receipts binding serials the same way.

---

## 6. Recommended plan
**Now (surface + repair-native, low risk, no schema churn):**
1. Product drawer **Movements** tab + **Purchasing & reservations** tab (existing endpoints).
2. Asset card **warranty status** chip + **Files** + **service-history timeline**.

**Next (global purchasing depth):**
3. Multi-currency PO receipt (RegionPack FX). 4. `part_suppliers` multi-supplier sourcing. 5. Tax-on-postings.

**Later:** posting reverse/correct + document attach · supplier price-list import · PO approval threshold.

## 7. One-liner
Our inventory *engine* already beats Orderry; the detail **screens** just don't show everything the engine knows. Ship the surfacing wins first (product movements/POs/reservations, asset warranty/timeline) for immediate visible parity-plus, then add the global purchasing depth (multi-currency + multi-supplier + posting tax) that turns a strong module into the moat.
