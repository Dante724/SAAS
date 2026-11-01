# Navigation cleanup + Work Order actions + Postings fix

## Sidebar (Orderry parity) — already in place
- "My company" is a single sidebar entry; Dashboard / Employee hours / Services /
  Products / Bundles are horizontal tabs under it (not separate sidebar links) — a
  `setContent` wrapper prepends the tab strip on those routes.
- Bins & serials moved out of the sidebar into the Products topbar.
- **Assets removed** from the sidebar — assets live under Inventory → Devices, so
  the standalone link was a duplicate.

## Work Order — Print / Message / Actions — already in place
The order detail header carries three controls matching Orderry:
- **Print** → document picker (asset labels, invoice, ticket, any template) →
  generates + prints.
- **Message** → email or SMS the customer (subject + body).
- **Actions** → Copy public link · Duplicate · Create purchase order · Delete.

## Postings — 405 fixed (the real bug)
The "New posting — bulk stock inflow" modal had no matching backend route (405
Method Not Allowed). Added `POST /api/inventory/postings`:
- Multi-line: receive several products at once.
- Per line: purchase price (required) + optional markup% that auto-sets the new
  sell price.
- Updates stock qty, moving-average cost, and records a `receive` movement per line.
The Postings modal is now a proper bulk-inflow form (product + qty + purchase +
markup, running line table with new-sell preview and total cost) hitting that
endpoint.

## Tests
`backend/tests/test_postings.py` (2): bulk posting creates stock + cost + markup
(300 → sell 450), validation returns 422 not 405. Suite 126. No schema change.

## Note on the screenshots
The Tekly screenshots showed an older build (pre-nav-cleanup sidebar). The nav
and Work-Order buttons were already delivered; this pass fixes postings and wires
the richer bulk modal.
