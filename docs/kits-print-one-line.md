# Kits print as one line, extras still shown

**Ask (a shop):** service kits pull parts from inventory. The customer's invoice should show only
the kit's name and price, but parts a tech adds on top must still show. Orderry's workaround
(hide every kit item) hides the extras too.

## Council (shop owner, tax/accounting, engineer, tester, security)

| Voice | Position | Outcome |
|---|---|---|
| Shop owner | Fixed package prices; real usage still tracked for stock and profit. | Fixed kits keep their price exactly. Stock, cost and profit stay per part. |
| Engineer | Keep money on the real component lines (design A), not a separate non-billed "materials" model (6–9 days, high risk, changes revenue reporting). | **Design A + price lock.** Only presentation and price locking change. |
| Tax / accounting | The tax summary must split by rate. India composite supply / HSN is its own project. | Folding never merges rates; `{Tax Summary}` is built from the real lines. GST package tax is a follow-up. |
| Security | One server-side choke point, leak tests, audit, an itemised copy on request, a region warning where itemising is law. | `kit_print.customer_lines()` is the only way customer surfaces get lines. Leak test over every surface. Audited changes. Role permissions. |
| Tester | Characterisation tests before building. | `test_kit_print_baseline.py` was written and run against the old code first. |

## Design

- A kit still explodes into real `order_lines`. Each line is tagged: `bundle_group_id`, `bundle_name`,
  `bundle_print_mode`, `bundle_price_locked`, `bundle_qty`, `bundle_std_price` (migration `kit1p01`,
  all nullable, no backfill). `bundles.print_mode` defaults to `itemized`, so no shop sees a change
  until it turns the setting on.
- **Exact price.** `kit_print.spread()` divides a fixed kit total in proportion to standard price.
  It works in cents, and when the quantities share a common factor it works per kit, so "2 kits"
  is exact. The remainder goes on one line.
- **Price lock.** When a tech changes a part's quantity, adds a part inside the kit, or removes one,
  the locked total is spread again over the lines that are left. A removed part's stock comes back.
  You can't change one line's price inside a fixed kit. A manager changes the kit's price on the
  job with a reason, and that change is audited.
- **One fold.** `customer_lines(lines, itemised=False)` folds each kit per job item. On a partial
  invoice it folds only the lines that invoice bills. Lines outside a kit, and kits from before this
  change, come back untouched. It is used by invoice detailed mode (and so the invoice PDF, the
  invoice email, the e-invoice, the public pay page and invoice templates), job and estimate
  documents and customer messages (`documents._resolve`), and the track page and portal.
- **Modes.** "Item by item" / "One line" / "One line and what's included". The included list holds
  names only: never price, cost, qty or SKU.
- **Itemised copy.** `?itemised=true` on the invoice print and document render endpoints, or
  `itemised: true` on print-view. A customer can always get one.
- **Permissions.** `can_manage_bundles` covers the kit's own print setting and parts.
  `can_change_kit_print` covers a kit's print setting on one job. `can_change_kit_price` (opt-in)
  covers changing the locked price. `can_print_itemised_copy` covers the itemised copy. Each can be
  switched off per role.
- **Region warning.** Vehicle-repair shops and US shops see a reminder before choosing one line.
  Kits never default to one line.

## Follow-ups (out of scope)

- India GST package tax (composite supply at one rate, HSN/SAC codes). Tekly has no HSN/SAC fields yet.
- Consumables tracked outside kits without being billed.
- QBO: roll a kit up to one description.
- An optional, never-automatic tool to tag old "(Kit)" lines.
- `/api/v2/bundles` should expose `print_mode` once API v2 lands (it is not on this branch).
- A Tekly Go badge on kit lines once Go is on this branch (the order API already returns the tags).
