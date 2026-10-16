# ADR-144po: Purchase orders on the work order

**Status:** Accepted
**Date:** 2026-10-16
**Deciders:** Shop owner (product), engineering

## Context

A job that needs parts the shop doesn't have gets a supplier purchase order from the work order ("Raise PO"). Before this change:

- The linked PO showed as a full-width amber "Waiting on parts" band under the header. Raising POs and reserving stock lived in a card on the Services & products tab. That made three places to look and a lot of permanent screen space.
- Only open POs were listed. Once a PO arrived it disappeared from the job, so there was no history.
- When the parts arrived, the job's product lines kept their **pending stock** (units the job uses but stock didn't have). Someone had to press "write off pending stock" on each line. The only automatic step was a "parts received, job can resume" alert, and only when the receipt pushed global on-hand over the need.
- Changes to the PO (sent, partly received, cancelled, new expected date) were never told to the job's people.

## Decision

1. **One "Parts & purchase orders" panel, opened from the work order's ⋯ menu.** It lists every PO linked to the job (open and finished), line by line (ordered, received, still to come), the stock held for the job, and the "Raise PO" and "Reserve stock" actions. The amber band is replaced by a small header chip, "Waiting on parts", that opens the same panel.
2. **Auto write-off on receipt.** When a PO linked to a job is received, the received units first clear that job's pending stock for the same part. This is the same posting as the manual button, which stays as a fallback. Units left over are reserved for the job, as before, when the PO line was reserved for it. Otherwise they land as free stock.
3. **Linked jobs hear about PO changes.** Sending, receiving (part or full), cancelling, a status/stage change, and a new expected date each:
   - add a line to the job's activity feed;
   - send an in-app notification to the job's assignee and manager (kind `inventory.po_update`, which they can mute).

4. **The shop chooses.** Settings → Inventory & warehouses → "Parts that arrive for a job" is either **Write off automatically** (the default) or **Staff write it off**. In manual mode, arriving parts land in stock. The notification then says they're ready to write off, and the panel lists what the job still owes, each with a **Write off** button.

## Options considered

### A. Auto write-off on receipt (chosen)
| Dimension | Assessment |
|---|---|
| Complexity | Low: reuses the existing write-off posting |
| Correctness | Stock moves exactly when it physically arrives for that job |
| Risk | A receipt at a different location from the job must not touch it (guarded) |

Pros: no forgotten clicks, inventory is right the moment goods land, matches what the shop asked for.
Cons: a receipt can't be "held back" from a job, though staff can still adjust the line afterwards.

### B. Reserve on receipt, write off when the job closes
Pros: reversible until close.
Cons: needs a second step at close, and stock reads "reserved" for the whole job. It's also different from how lines that had stock behave (deducted when added).

### C. Keep notify-only (status quo)
Pros: nothing to build.
Cons: pending stock gets forgotten, so on-hand drifts from reality. This is the reported problem.

## Guard rails

- Never touches estimates, archived jobs, or jobs in a closed/dropped status.
- Only when the PO's location is the job's location.
- Only lines for the same part with `pending_stock > 0`, and never more than was received in this receipt.
- Each write-off is a normal `use` stock movement with ref `order`, and is logged on the job's feed ("3 × Screen written off from PO-0012").

## Consequences

- Easier: the job always shows its parts story in one place. Inventory is right without a manual step.
- Harder: receiving is now a cross-domain write (inventory → orders). It's kept in one helper (`services/po_links.py`), so the receive route stays readable.
- Revisit: when partial receipts are split across several jobs, per-job priority. Today the order is the PO's own reserved job first, then the job it was raised from.

## Action items

1. [x] `services/po_links.py`: `linked_order_ids`, `apply_receipt_to_orders`, `notify_po_change`.
2. [x] Hook it into PO receive, send, cancel, status, workflow and edit (expected date).
3. [x] `GET /orders/{id}/purchase-orders` returns all linked POs with line detail. `incoming-po` stays for the chip.
4. [x] WO drawer: ⋯ menu item, panel, and the header chip. Remove the band and the Services-tab card.
5. [x] Tests: write-off on receipt, estimate untouched, notification sent, manual mode.
6. [x] `organisations.inv_po_to_job` (migration `pj144p9`) and the settings choice.
