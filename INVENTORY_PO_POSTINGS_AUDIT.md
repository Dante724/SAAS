# Purchase Orders & Stock Postings — Accuracy Audit + Plan

**Scope:** Why POs and postings feel "not accurate," what's actually broken in code, and how to make
receiving/costing *better than Orderry* — reviewed through three lenses (product brainstorming,
tech‑PM, senior architect/QA).

Evidence is cited as `file:line` from the live tree. Nothing below is speculative — every claim was
traced in the running code.

---

## 1. How it works today (traced)

There are **three ways stock cost/quantity moves in** and they don't agree with each other:

| Path | Endpoint | Qty | WAC basis | Cost layers (FIFO) | Supplier balance | Reservations |
|------|----------|-----|-----------|--------------------|------------------|--------------|
| Manual posting | `POST /inventory/postings` (`inventory.py:262`) | ✅ per‑location | **per‑location** `prev_qty` (`:295`) | ❌ none | ✅ (`:313`) | ❌ |
| PO receive (real) | `POST /inventory/po/{id}/receive` (`inventory.py:908`) | ✅ | **global** `sum(all locations)` (`:944`) | ✅ `StockItemBatch` + serials (`:963`) | ✅ credit + landed cost (`:987`) | ❌ ignores `reserved_order_id` |
| PO receive (dead) | `POST /suppliers/purchase-orders/{id}/receive` (`suppliers.py:307`) | ❌ **none** | — | ❌ | ❌ | creates `StockReservation` but never touches `qty_reserved` |

Reservations themselves have a **second, separate** implementation:
`POST /inventory/reserve` (`inventory.py:487`) correctly bumps `sl.qty_reserved` **and** writes a
`StockReservation` with an expiry, and an expiry sweep releases them (`:462‑473`). The PO‑line
`reserved_order_id` mechanism (`suppliers.py:259` reserve / `:307` receive) is a parallel universe that
never meets it.

---

## 2. Accuracy defects (ranked, with evidence)

### P0‑A — You cannot receive a PO from the app at all
`InventoryHub.tsx:356` renders a **"View Details" button with no `onClick`**, and there is **no PO
detail drawer anywhere in the React app** (`grep` for `/po/`, `receive`, `PODrawer` → 0 hits in
`frontend-react/src`). So the entire sophisticated backend receive pipeline — WAC, landed cost, FIFO
batches, serials, partial receipt, supplier balance — is **unreachable**. POs are created, then stay
open forever; the only way to get stock in is a *manual posting* disconnected from the PO.
**This is the "not accurate" the user is feeling:** ordered ≠ received ≠ on‑hand, permanently.

### P0‑B — Split‑brain receive endpoint
`suppliers.py:307` `receive_po` marks the PO received and creates reservations **but posts no stock, no
movement, no cost, no balance.** If anything calls it (it's a live route), the PO flips to "received"
while inventory never moves — silent, invisible data loss. Two endpoints named `receive_po` with
opposite behavior is a loaded gun.

### P0‑C — WAC is computed two different ways
Postings weight against **per‑location** quantity (`inventory.py:293‑296`); PO receive weights against
**global on‑hand across all locations** (`inventory.py:944‑950`). The same product receives a
*different average cost* depending on which door the stock came through. `avg_cost` is a single global
column, so the per‑location basis in postings is simply wrong for a global average.

### P1‑D — Reservations don't survive receiving
`receive_po` (`inventory.py:908`) increments `qty_available` but never honors a PO line's
`reserved_order_id`, and never calls the working reserve path. Goods a customer's job was waiting on
land as **free stock** and can be sold out from under the job. Orderry explicitly reserves
"products… still on the way" — we have the field and don't use it.

### P1‑E — Only PO receipts create cost layers
`create_posting` (`inventory.py:262`) never creates a `StockItemBatch`, but `receive_po` does
(`inventory.py:963`). So FIFO/exact‑COGS at fulfillment works for PO‑sourced stock and silently falls
back to average for posting‑sourced stock. Mixed, unpredictable COGS.

### P1‑F — PO total excludes charges
`create_po` sets `total = sum(qty*cost)` (`inventory.py:635`) with no freight/charges; the PO list
"Total" column (`InventoryHub.tsx:353`) therefore understates the real payable whenever charges exist.

### P2‑G — Fragmented PO lifecycle & creation
Three PO creators: `POST /inventory/po` (jumps straight to `status="ordered"`, `:634`), the reorder
forecast (creates `draft`), and the new WO `create-po`. Plus a separate `/po/{id}/send`. No single
state machine (draft → ordered/sent → partial → received/closed → cancelled), so status is set
ad‑hoc in each path.

---

## 3. Three‑lens review

**Product (brainstorming lens).** The job‑to‑be‑done is *"the number on the shelf matches the number on
the screen, and I know what each unit cost me."* Today the loop is broken at the last step (receive),
so trust collapses — once a user sees one wrong on‑hand, they stop trusting all of it. The wedge that
beats Orderry isn't more features, it's **truthful receiving with a visible paper trail**: every stock
number links back to the document (PO/posting/write‑off) that moved it.

**Tech‑PM (delivery lens).** Sequence by trust‑recovery, not by size. P0 (reachable, correct receive +
one costing path) restores the core loop in days and is the thing the user actually asked about.
Reservation‑on‑receive and unified cost layers (P1) are the "better than Orderry" differentiators and
should ship right after, each behind tests. P2 (state machine, three‑way match) is hardening. Ship in
three thin vertical slices, each deployed and gated.

**Architect / QA (correctness lens).** Root cause is **duplicated posting logic with no single source
of truth.** Fix = one `post_stock()` service that every path (posting, PO receive, WO consume,
write‑off, transfer, return) calls, which atomically does: qty ± , `StockMovement`, `StockItemBatch`
(in or out), WAC (one global basis), reservation honoring, and ledger. Delete the dead `suppliers.py`
receive. This is the lazy *and* correct fix: fewer lines, one place to test, no more divergence. Guard
every write path with the org/record checks already established.

---

## 4. Plan

### P0 — Restore the loop (make receiving real & reachable)
1. **One posting service** `backend/services/stock_posting.py::post_receipt(...)` — extract the
   `receive_po` body (WAC on **global** basis, `StockMovement`, `StockItemBatch`, serials, landed
   cost, supplier balance) and have `create_posting` call it too. One WAC, one layering rule.
2. **Delete** `suppliers.py:307` `receive_po` (dead + wrong); keep the single inventory receive.
3. **PO detail drawer (FE)** — wire "View Details" → drawer with header, lines, charges, and a
   **Receive** action (full or per‑line qty, optional bin/serials) → `POST /inventory/po/{id}/receive`;
   show `qty_received/qty_ordered` and status. This is the missing 80%.

### P1 — Beat Orderry
4. **Reserve‑on‑receive:** in `post_receipt`, if a PO line has `reserved_order_id`, create a real
   reservation via the working reserve path (bump `qty_reserved` + `StockReservation`) so incoming
   goods stay earmarked. (Orderry reserves incoming; we'll do it *and* keep exact cost layers.)
5. **Unify cost layers:** postings create `StockItemBatch` too (falls out of step 1 for free).
6. **PO total includes charges** in `create_po`/serializer so the list total is the true payable.

### P2 — Harden
7. **PO state machine** (draft → ordered/sent → partial → received → closed/cancelled) as one helper;
   all three creators route through it.
8. **Three‑way match** badge on receive: PO qty vs received qty vs supplier‑invoice qty/price, flag
   variances — a genuine step beyond Orderry's post‑and‑done.

### Verification (every slice)
`pytest` the posting service (WAC across two locations, landed‑cost split, partial receipt, reserve
survival), `tsc` + build, live‑verify a full create‑PO → receive → on‑hand loop, and re‑run the
inventory/ledger suites (currently 32 green) so nothing regresses.

---

## 5. "Better than Orderry" scorecard

| Capability | Orderry | RepairOS today | After P0–P1 |
|---|---|---|---|
| Post PO to stock | ✅ one click | ❌ unreachable | ✅ + per‑line/partial |
| Cost method | Moving average | WAC (2 inconsistent) | **WAC + FIFO layers, one basis** |
| Landed cost in unit cost | limited | ✅ (PO only) | ✅ everywhere |
| Reserve incoming goods | ✅ | field exists, unused | ✅ honored on receive |
| Serial/lot on receipt | ✅ | ✅ (PO only) | ✅ everywhere |
| Three‑way match | ❌ | ❌ | ✅ (P2) |
| Every stock number ↔ its document | partial | partial | ✅ full trail |

Sources: [Orderry Purchase Orders](https://orderry.com/purchase-orders/), [Orderry Inventory: General Information](https://help.orderry.com/en/articles/9134169-inventory-general-information), [What is a purchase order](https://help.orderry.com/en/articles/12088203-what-is-a-purchase-order-and-how-to-create-one), [Product Write‑Off](https://help.orderry.com/en/articles/9134200-product-write-off).
