# UI / UX audit — what looks sloppy or "AI-built"

Date: 15 Oct 2026 · Branch `security/phase-4` · Demo shop "Fix-It Pro Repairs" on http://127.0.0.1:8000

How it was checked: every main page opened in Chrome (Home, Inquiries, Appointments, Chats,
Contacts, Work orders + job drawer, My work, Timesheets, Tasks, Sales, Invoices, Catalog,
Inventory, Finance, Marketing, Reports, Settings, the customer booking page and the customer
tracking page), plus a scan of all 88 screen files for the patterns below. Settings pages that
were redesigned this week (Statuses, Checklists, General, Team, Notifications, Directories,
Locations, Finance & numbering, Document templates, Public pages) are not repeated here.

---

## Part A — Things that are wrong (fix first)

These are what a shop owner notices in the first five minutes and what makes software feel
untrustworthy. Most are small.

| # | Where | What's wrong |
|---|-------|--------------|
| A1 | Top bar, **every page** | The search box placeholder "Search orders, clients, serials…" wraps onto two lines. |
| A2 | Inventory, Sales (POS), tracking page | **Rupee sign next to dollar amounts.** Inventory columns say "Cost (₹)" and "Sell price (₹)" above $ values. The POS shows "Discount (₹)". The customer tracking page groups numbers the Indian way (en-IN). Five more places fall back to ₹ when no currency is set. |
| A3 | Work orders list, tracking page, Contacts, Reports | **Internal names shown instead of real names.** Job rows say "Est Pending" and "Dropped", but your status is called "Not collected". The tracking page history says "dropped" in lower case. Contacts tiers show "vip", "standard", "corporate". Reports show the role as "technician". |
| A4 | Home vs Finance vs Reports | **Numbers disagree between pages.** Home "This month → Revenue $0", Finance "Revenue (paid) $9,400". Reports "Total orders —" while there are 6 jobs. |
| A5 | Job drawer → Services & products | "Subtotal (0 items) $0" right above "Grand total $1,800". |
| A6 | Inventory | Margin shows "—" on every product even though cost and price are both filled in. Every product is "Uncategorized". |
| A7 | Customer tracking page | A dropped job shows the progress bar still on step 1 "Received". The total appears twice ("Estimated total $8,000" and a big "Total $8,000"). |
| A8 | Tasks | The filter has both **"Completed"** and **"Done"**. |
| A9 | My work | The page has no title or top padding; three cards sit in a narrow column stuck to the top. |
| A10 | Marketing | The campaigns table only fills half the width; the "New campaign" button floats above it. |
| A11 | Appointments | In the empty state, the calendar icon sits top-left while the text is centered. |
| A12 | Dates and times | **Four formats are mixed.** "05:27 AM" with a leading zero. "09:00–18:00" (24-hour) on the booking page. "10/29/2026" next to "15 Oct" on the tracking page. Plus unstyled browser date boxes ("mm/dd/yyyy") on Finance. |
| A13 | Invoices | A $0 balance is shown in red. The list has no customer or date column, so you can't tell invoices apart. |
| A14 | Chats | Demo data has the same message sent twice. Avatars use the first two letters of the first name ("NE" for Neha Gupta), not initials ("NG"). |

## Part B — Why it looks "AI-built" (the pattern problem)

None of these are bugs on their own. Together they are the look people recognise as generated:
every page built from the same kit, with no point of view.

**B1. The same KPI tile row on every page.**
Home has 8 tiles, Finance 5, Reports 5, Timesheets 5 and Work orders 5. Each tile has an
UPPERCASE label, a number, and a pastel icon square in the corner. On Reports each tile also
has a coloured gradient underline.
Real products show 1–3 numbers that matter on that page, and make them do something (click
to filter). The Statuses page and the Notifications journey strip are the model to copy.

**B2. UPPERCASE micro-labels everywhere: 185 uses in 35 files.**
Examples: "WORK ORDERS TODAY", "CASH ACCOUNTS", "ON THE CLOCK", "PROGRESS HISTORY". Table
headers too ("ACTION", "ACTIONS").
Use sentence case at normal weight; reserve caps for nothing.

**B3. Monospace for money, phone numbers, times and IDs: 338 uses in 42 files.**
It makes the app look like a terminal: "$9,400", "+91-9111222333", "0h 0m", "60 min".
Use the normal font with `tabular-nums` for columns of numbers, and keep mono only for codes
people type (SKU, serial).

**B4. Tiny text: 9–10px is used 382 times.**
It is hard to read at a counter or on a tablet. The minimum should be 11px, with 12px for
anything a person needs to read.

**B5. Gradients and heavy weights.**
There are 74 gradient backgrounds (buttons, tiles, avatars) and 42 `font-black` uses. Both
read as template-y. Use one flat brand colour for primary buttons and semibold at most.

**B6. Generic filler copy.** Examples:
- "Overview — Key performance metrics. Data may be delayed a few minutes."
- "This month at a glance"
- "Your books are done when the day is done — hand your accountant one clean file."
- "Workshop Central" under the logo
- "Acme Repairs" as the sign-up placeholder

Say something specific to the shop, or say nothing.

**B7. Decoration standing in for content.**
- Inventory: every product has a random-coloured 4-letter chip ("KBMB", "BATT", "PAST").
- POS: every product shows the same box icon, with an unlabelled green "25" bubble (that's
  stock).

Use a real photo or a neutral placeholder, and label the stock.

**B8. Jargon from other tools.**
"SLA overdue", "Straight / OT", "Utilization", "Provided services (Ready+Closed)", "Base labor
fee", "Product / Stock", "Approve range" (shown even when there is nothing to approve).

**B9. Every table has its own row action.**
Work orders uses a chevron, Catalog an "Edit" link, Inventory a "⋮" menu, Invoices a printer
icon, Contacts a chevron. Pick one pattern: the whole row opens the record, and "⋮" holds
extra actions.

**B10. One page looks like a different product.**
The customer booking page is dark, while the tracking page, the app and the documents are
light. Customers see both pages.

**B11. Small tells.**
- A "✓" inside the dialog title "Drawer balanced ✓".
- A sparkle icon next to "QUICK" in Chats.
- "Est Pending" style capitalised keys.
- Code comments that name Orderry and Stitch (not visible to users, but worth cleaning
  before the repo is shared).

## Part C — Ease-of-use gaps

- **Home** doesn't open with today's work. "Needs you" is good, but it sits above 8 equal
  tiles. Lead with today's jobs (due today, ready for pickup, waiting on customer).
- **Reports** opens on "Payroll summary". Most owners want "How did we do this week".
- **Empty pages** (Appointments, Marketing, My work) say "nothing yet" but not why you'd use
  them or the one step to start.
- **POS**: you can't tell stock from price at a glance, and the discount field has no currency
  or % choice.
- **Tasks**: due times are all the same seeded "05:27 AM", and there's no "late" colour.

---

## Suggested order of work

1. **Bug sweep (Part A)**, about a day. It is mostly one-line fixes plus the number
   mismatches A4–A6, which need a look at how each total is calculated.
2. **One shared "page header + numbers" component**, replacing the tile rows (B1). Apply it to
   Home, Finance, Reports, Timesheets and Work orders. This is the biggest single change in
   how the app looks.
3. **Global style pass (B2–B5)**:
   - sentence-case labels;
   - normal font with tabular numbers;
   - 11px minimum text;
   - flat buttons.

   It is mechanical but touches many files, so do it file by file with a screenshot check.
4. **Copy pass (B6, B8)** with one plain-words glossary, and one table-row pattern (B9).
5. **Customer pages**: make the booking page match the tracking page (B10), and fix A7.

The worst files by these markers are listed below. Each count is roughly 2× uppercase +
2× heavy weight + 2× gradient + tiny text + monospace.

| File | Score |
|------|-------|
| `pages/reports/ReportsHub.tsx` | 94 |
| `pages/orders/WODrawer.tsx` | 88 |
| `pages/inventory/InventoryHub.tsx` | 85 |
| `pages/inventory/InventoryDrawer.tsx` | 85 |
| `pages/settings/SettingsHub.tsx` (the parts not yet redesigned) | 77 |
| `pages/orders/ClientDrawer.tsx` | 71 |
| `pages/finance/FinanceHub.tsx` | 57 |
| `pages/orders/WorkOrdersHub.tsx` | 53 |
| `pages/cash/CashPanel.tsx` | 49 |
| `components/Shell.tsx` (sidebar and top bar, seen on every page) | 44 |
