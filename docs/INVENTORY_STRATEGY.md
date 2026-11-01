# Tekly Inventory — how Orderry does it, what we have, what to build

*Research + council review (product brainstorm · tech-PM · senior-architect). Market: US · LATAM · EU repair shops. Pre-launch, multi-tenant.*

---

## 1. How Orderry Inventory works (flow · scheme · why)

Orderry's inventory is a **document-driven stock ledger**. Nothing changes a quantity directly — every quantity change is the side-effect of a *document*, and each document writes an immutable **stock movement**. That's the whole scheme, and it's why the numbers are auditable.

**The documents (the "why" in parentheses):**
- **Posting** (goods-in / receipt) — brings stock in at a cost price; tax can apply to postings. Raises quantity + sets/updates cost.
- **Write-off** — removes stock with a **reason** (damaged, lost, internal use…). Lowers quantity.
- **Movement / Transfer** — moves stock between locations/warehouses (out of A, into B).
- **Stocktake** (physical count) — count the shelf, system computes the **variance** vs book quantity, then posts the correcting write-off/posting. Reconciliation, not direct edit.
- **Reservation** — soft-holds stock for an open ticket so it can't be double-sold.
- **Sale / ticket deduction** — selling a product or adding it to a ticket **deducts from stock**; Orderry also allows "**add product without deduction**" (customer-supplied part) and "**add custom product outside Catalog**" (one-off, not a catalog item).
- **Return / refund to supplier** — sends stock back, reverses the posting.
- **Purchase order** → received into a Posting. Reorder suggestions feed PO creation.

**Supporting scheme:** Products (with **serial-number tracking**, categories, variants), **Bundles**, per-location **warehouses**, **suppliers**, and a **marketplace stock-upload** for online stores. Money-data (purchase price / cost / profit) is permission-scoped per role.

**Why document-driven:** it makes stock a *derived* value (sum of movements), gives a full audit trail, supports multi-location, and lets permissions gate who can post/write-off/see cost. It's the correct model — and it's the one we already use.

---

## 2. What Tekly has today (verified in code)

We are **at or above Orderry parity** on the core. Our inventory is the same document-driven ledger:

| Orderry capability | Tekly | Where |
|---|---|---|
| Postings (goods-in, cost, tax) | ✅ postings + receive | `inventory.py /postings`, `/po/{id}/receive` |
| Write-offs with reasons | ✅ | `stock_docs.py /write-offs` (+ suggestions) |
| Transfers between locations | ✅ + scan + receive | `transfers.py` |
| Stocktake (count → variance → adjust) | ✅ | `sales.py /stocktake` |
| Reservations | ✅ | `inventory.py /reserve`, `/reservations` |
| Sale/ticket deduction | ✅ | `orders.py` (writes off on product lines) |
| Add product **without deduction** | ✅ `pending_stock` | `orders.py` (role-gated) |
| Custom product outside catalog | ✅ free-form line | invoice/WO lines |
| Returns / refunds to supplier | ✅ | `returns.py`, `stock_docs.py /returns` |
| Purchase orders (create/send/receive) | ✅ + **group POs** + **charges/landed cost** | `inventory.py /po*` |
| Reorder suggestions | ✅ **+ velocity forecast + auto-PO + digest** | `/reorder-list`, `/reorder-forecast`, `/reorder-digest` |
| Suppliers | ✅ | `suppliers.py` |
| Products: categories, variants, serial | ✅ + **barcode gen/scan, CSV import/export** | `catalog_products.py` |
| Bundles | ✅ | `bundles.py` |
| Warehouses | ✅ **+ bins + per-warehouse access** | `warehouses.py` |
| Movements audit trail | ✅ | `StockMovement` |

**We already exceed Orderry** on: **batch/expiry tracking**, **landed cost** on POs, **velocity-based reorder forecast + auto-PO**, **asset↔product conversions**, **bins**, **variants**, **CSV import/export**, and **in-app barcode generation**. Stock is derived from `StockMovement`; `StockLevel` carries `qty_available`/`qty_reserved` per location/warehouse/bin.

**Bottom line:** this is not a parity gap module. It's a *lead* we should sharpen and make correct-everywhere.

---

## 3. Council review — what actually needs doing

### 🧭 Product (brainstorm): where the differentiation is
Orderry treats inventory as generic warehouse stock. A **repair shop's** inventory has two things a generic tool ignores, and we already hold the data for both:
1. **Parts↔device compatibility.** `Part.compatibility_json` exists but isn't surfaced. "Which parts fit this device?" on the work order — pick the compatible screen/battery in one tap — is a repair-shop killer feature Orderry has no answer for.
2. **Serial + warranty intelligence.** We flag `serialized` and can serialize, but the *lifecycle* (capture serial at receive → bind to the sale → scan serial to pull warranty + repair history) is the high-value loop. This is what turns "stock" into "traceability."

Plus one **assembly** gap: bundles are price groupings, not true kits — selling a kit should optionally **consume component stock**. Repair shops that build refurb units need this.

### 🏗️ Tech-PM: build order (impact × effort, all buildable now)
1. **Compatibility on the WO** (Now, low effort) — surface `compatibility_json`: on a work order's device, filter the parts picker to compatible parts. Data + a filter; no schema change. Highest repair-specific payoff.
2. **Multi-supplier sourcing** (Now/Next, medium) — today `Part.default_supplier_id` is single. Real shops buy one part from several vendors at different prices/lead times. Add a `part_suppliers` table (supplier, supplier SKU, price, lead days) and let PO creation pick the best source. Global-market essential.
3. **Serial lifecycle** (Next, medium) — capture serials on receive, bind on sale, `/scan` a serial to return warranty + history. Extends existing serialize + scan.
4. **Multi-currency purchasing** (Next, medium) — PO in supplier currency, landed cost converted to base via the RegionPack/currency layer. Needed for US/LATAM/EU shops importing parts.
5. **True kits/assemblies** (Later) — bundle option "deduct components on sale."
6. **Inventory notifications** (Now, tiny) — wire the existing reorder digest + low-stock into the notifications engine (region channel).

### 🔒 Senior-architect: correctness/risk to guard
- **Movements are the source of truth — keep every quantity change going through a document + `StockMovement`.** The one real risk is `/parts/{id}/adjust` and merge/bulk paths writing `StockLevel` without a matching movement — audit these so stock stays reconstructable (they're the classic "phantom quantity" bug).
- **Concurrency on deduction/reservation** — two tickets grabbing the last unit. Confirm reserve/deduct is transactional (row lock or conditional update), especially now it's multi-tenant + multi-location.
- **Multi-currency + landed cost** must not silently mix currencies in `avg_cost`; convert at receipt time and store base-currency cost.
- **Serial uniqueness** must be per-org (mirror the order-code fix), not global.

---

## 4. Recommended plan

**Now (sharpen the lead, repair-specific, low risk):**
1. Parts↔device **compatibility filter** on the work-order parts picker.
2. **Inventory notifications** — low-stock / reorder digest into the notifications engine.
3. Senior-arch **audit**: every `StockLevel` write has a `StockMovement`; reservation/deduction is race-safe.

**Next (global-market depth):**
4. **Multi-supplier** sourcing (`part_suppliers` + PO best-source pick).
5. **Serial lifecycle** (receive → sale → scan-for-warranty), serial unique per org.
6. **Multi-currency purchasing** with FX landed cost.

**Later:** true kits/assemblies (component deduction) · marketplace/e-commerce stock sync (integrations) · point-in-time valuation report.

---

## 5. One-line verdict
Inventory is our **strongest** module and already beats Orderry on batches, landed cost, forecasting, conversions, and bins. Don't rebuild it — **make it repair-native** (compatibility + serial/warranty), **global-ready** (multi-supplier + multi-currency purchasing), and **provably correct** (movement/reservation audit). That's how we turn a parity module into a moat.
