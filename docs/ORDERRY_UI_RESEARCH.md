# Orderry UI Research — Primary-Source Screenshot Analysis (Batch 1)

**Source:** 20 screenshots of the live Orderry app (app.orderry.com, July 2026) covering
Settings → Public pages, Finance, Prices & discounts, Integrations, General, and
Employees (Roles + Payroll calculation rules). This is a **living document** — further
screenshot batches will extend it. Every gap claim below is verified against the
RepairOS codebase (repairos-38), not guessed.

---

## 0. Global shell observations

**Primary left nav (14 items):** My company · Tasks (unread badge) · Inquiries ·
Bookings · Workflows · Sales · Invoices · Finance · Inventory · Contacts · Chats ·
Calls · Reports · Settings. Notable naming: *Workflows* = work orders/tickets,
*Inquiries* = leads, *Contacts* = clients.

**Settings sidebar (22 pages):** General, Employees, Locations, Inventory, Statuses,
External notifications, Internal notifications, Directories, Forms editor,
Print templates, Public pages, Finance, Prices and discounts, Marketing, Tags,
Ecommerce Connect (BETA), Chats, Telephony, Integrations, API, Subscription,
Referral program.

**Interaction patterns:** right-side **drawers** for create/edit (New tax, New role,
New commission, Gmail account) with the Create button pinned bottom-left; long
settings pages broken into sections, each with its **own Save button** (fragmented —
users must save per-section); inline info boxes deep-linking to related settings
("The ticket number template can be set on Settings > Locations"); "Selected N"
compact multi-selects for statuses; a persistent **Training** dropdown + help timer
in every page header; Intercom-style chat bubble bottom-right.

---

## 1. Settings → Public pages

Two tabs: **Online booking** and **Order / Estimate page**.

### 1a. Online booking tab
- **Appearance:** page URL `https://{sub}.orderry.page/booking…` with copy button +
  **Change subdomain**; share hint. Color picker; **background pattern** picker
  (decorative SVG patterns); **border radius** select ("Standard — 8px");
  Show language switcher; Display logo (+ logo preview). Save.
- **Settings:** Locations for online booking (**All | Selected**); Allow clients to
  book multiple services; Show employee selection step; **Consider orders when
  calculating available time slots** (capacity = bookings + work orders);
  Time slot select (60 min). Save.

### 1b. Order / Estimate page tab
- Same appearance block (independent color/pattern per page type).
- **Header block toggles:** Show company logo (+ Small/Big size), company name,
  address, email, description — each with live value preview beneath.
- **Products and services:** Show products and services in separate tables;
  Show products with a zero price; Show services with a zero price.
- **Estimates section:** *Allow clients to accept/decline* — **In statuses*** (multi),
  **Require client signature**, **If accepted, move to the status** (e.g.
  "Closed > Ticket created"), **If declined, move to the status** ("Dropped off >
  Rejected"). Save.
- **Tickets section:** identical accept/decline block (statuses "Approval";
  accepted → "Pending > Online Payment"; declined → "New > Assign technician").
- **Enable online payments:** In statuses* (multi); **If paid, move to the status**;
  **Company default merchant*** with note that each location can override the
  merchant on Settings > Locations. Save.

**Workflow insight:** the public page is a *status-pipeline actor* — client
accept/decline/pay actions are configured as status transitions, per status set.
This is the same "status-as-engine" thesis as our blueprint.

---

## 2. Settings → Finance

### 2a. Taxes
- Master **Enable** toggle ("tax application rules for automatic calculation in
  tickets, sales, and inventory postings" — note: taxes apply to **inventory
  postings** too, not just sales docs).
- **Recalculate taxes in open documents when settings change:** policy select
  ("Ask for every document" — presumably also always/never).
- **Taxes list** (tabs: Taxes | **Tax groups**): + Tax; columns Name / Rate /
  Locations (e.g. "13" 13.5% All).
- **New tax drawer fields:** Name*, **Name for document*** (print label), **Code***,
  **Type: Inclusive | Exclusive** (with explainer), Rate* %, **Locations: All |
  Selected**. Create.
- **General rules:** "Set order of tax applying… First tax has the highest
  priority" — an ordered default tax stack per document family (**Tickets and
  Sales** vs **Posting**), extendable via "+ Tax".
- **Exceptions** (checkbox-gated): per **labor/service/product or category**,
  tabs Tickets and Sales | Posting; rows show Name / Type (Category) /
  **Taxation system** — i.e. a category can carry its own tax code/exemption.

### 2b. Payment (cash-flow) categories
- +Category; table Name / **Direction (Income | Expense)** badges. 16 defaults:
  Client's payment for service, goods · Other money income · Prepayment · Refunds
  from the supplier (Income); Advertising · Clients refund · Other spendings ·
  Fuel/transport · Telephone/internet · Utility costs · Earnings (dividends) ·
  Taxes · Wages and salaries · Suppliers of goods and spare parts · Payout ·
  Rental payment (Expense).
- **Default categories** per document type (Tickets, Sales) to auto-label payments.

### 2c. Rounding
- Enable toggle: "automatic rounding adjustments for all or specific documents."

---

## 3. Settings → Prices and discounts

- **Prices:** Number of decimal places (2/3/4/5) with live example. Save.
- **Service and product prices** (tabs Services | Products): **named price lists**
  with **Markup %** and **Locations** scope (row: "Base price / 0 / All") — i.e.
  price levels are derived from base via markup, per location.
- **Company default prices** per document type: Tickets, Sales, Invoices each pick a
  price list; per-location overrides live on Settings > Locations.
- **Discounts:** default discount type toggle **Discount | Company discount**, with
  the payroll rule spelled out: *"Employee wages calculation is based on the amount
  after discount. This way the company discount decreases the piecework employee
  wages."* (Mirror-image of our `discount_paid_by=company` which keeps tech
  commission whole — Orderry's "Company discount" reduces wages; their plain
  "Discount" is client-paid. Same axis, inverse naming — worth a UX-copy decision.)

---

## 4. Settings → Integrations (catalog + drawer pattern)

Sections and cards (✓ = connected badge in the shots):
- **Emails:** Gmail, Outlook. Gmail drawer: "Add up to 10 accounts", + Account,
  table Email / Sender name / Status, empty state with guidance. (Send-as
  identity for notifications; per-account sender names.)
- **Chats** (two-way): Instagram, Facebook, WhatsApp by Twilio, SMS…
- **Messengers** (one-way): WhatsApp ✓, Viber.
- **VoIP:** Aircall, Twilio Voice, Zadarma ("display client info on incoming call").
- **Payments:** Stripe ✓, Square ✓, SumUp.
- **Invoicing:** Xero (2-way), QuickBooks Online (2-way), Xero (deprecated),
  QuickBooks (deprecated) ✓ — deprecation handled by parallel cards.
- **Marketplaces:** Custom Integration With Online Stores (stock-level upload).
- **Online Connectors:** Make, Zapier, Google Forms, Facebook Lead Ads, LiveChat.
- **Other:** **VIN Lookup ✓, Vessel HIN Lookup, IMEI Lookup** (sold as integration
  cards — exactly where we put ours), **Order Status Widget ✓** (embeddable "track
  your order" widget for the shop's own site), **Orderry Companion Plugin** (Chrome
  extension for bulk creation & status editing).

---

## 5. Settings → General (one long page, many sections)

- **Company info:** Name*, Address, Billing email*, Company description,
  Additional info ("use in documents and printing templates"), Logo. Save.
- **Legal entities:** "+ Legal entity"; table Name / Address; a **radio marks the
  default**; used to issue invoices with entity details (3 in demo: ProEngine
  Repairs, ToolCare Service Center…).
- **Regional settings:** Country (*"determines the set of available services on the
  Integrations page, date and time format, and number display"*); Timezone;
  Currency; **Display currency as** ($ | US$ | USD) × (Before | After the amount)
  with example; **Documents language** (print language) explicitly separate from
  per-user interface language.
- **Inquiries:** numbering template with tokens **{D} {M} {Y} {C:n}** (counter with
  n digits) + **"begins from"** seed + live example ("inquiry 015"); Default
  inquiry type.
- **Bookings:** Hide unscheduled employees (Day view); Hide not-working employees
  (sick leave, vacation, day off) (Day view).
- **Workflows (ticket settings):** **Count warranty from** (Closure date | …);
  info-link that the *ticket* number template lives on Settings > Locations
  (per-location numbering!); **Use an asset in one open ticket only** (prohibit an
  asset in two open tickets); **Default visibility for comments** (Private/Public —
  public shows on public pages); **Default visibility for uploaded files** (same,
  "Use with caution").
- **Sales:** Make the Client field required; Make the Ad Campaign field required;
  note that per-form required fields are configured in **Settings > Forms editor**.
- **Contacts:** Show the Middle name field; **Phone:** consent-by-default to
  notifications (else manual confirmation), **Prohibit creating a contact with an
  existing phone number** (dup guard); **Email:** same two toggles.
- **Invoices:** numbering template ({C:3}, begins from, example) **plus a
  per-legal-entity numbering table** (entity → template → counter start; entity
  template overrides the standard one); **Invoice creation settings:** Default
  legal entity, **Default due date** (Custom, N days, auto-calculated),
  **Prevent backdating invoices** (issue date must be ≥ previous invoice's);
  **Invoice payments:** Enable online payments…

---

## 6. Settings → Employees → Roles (full permission catalog)

Tabs: **Employees | Roles | Payroll calculation rules.** Demo roles: Full access,
Manager, Partner, Specialist. "New role" = right drawer, Title* + a **hierarchical
checkbox tree** (~120 permissions). Transcribed catalog:

- **General:** make payments due to client's balance · view purchase price of
  products · set discount in documents · set the discount type · set cost price in
  documents · see estimated profit · **Display in the Managers list** · **Display in
  the Assignee list** (role controls dropdown membership!) · send SMS · see an
  asset cost · configure exceptional commissions for payroll · view their own
  compensation.
- **My company:** Dashboard · Employee hours (view hours / edit schedule / edit
  timesheets) · Labors and services (view/create/edit/delete + categories) ·
  Products (create / **enable serial-number tracking for existing products** /
  edit / edit categories / delete) · Bundles (view/create/edit/delete + categories).
- **Tasks:** create · be an Assignee · view all employees' tasks · edit all.
- **Inquiries:** see · create · change status · edit · delete.
- **Bookings:** be assigned · view · create · edit · delete.
- **Workflows (tickets):** create tickets · create estimates · transfer tickets ·
  delete tickets · assign managers and technicians · view the client info · view
  the asset info · view prices of labors/services/products · view the "Invoices and
  payments" tab · see calls · print documents · edit "General info" fields · edit
  fields on the "Services and products" tab · add services · **add custom services
  outside Catalog** · add products without deduction · add products · **add custom
  products outside Catalog** · create assets directory items · edit the services
  price · edit the price of products · set the custom price for products · **edit
  the closed ticket** · create/edit/delete **shared filters** · create assets ·
  create payment links · create purchase orders.
- **Sales:** create · delete · sell services · edit price · export.
- **Invoices:** view · create · edit · delete · **edit issued invoices**.
- **Finance › Transactions:** create incomes · transfer money · create expenses ·
  **create cash incomes/transfers/expenses retroactively** · reprint the fiscal
  check · view the cash flow · view cash flow **for any period** · correct account
  transactions · delete account transactions · print/export the cash flow · view
  the account balance · create/edit/delete accounts · **select any employee from
  the list**. **Payment links:** create · delete. **Refunds:** create returns ·
  delete refunds · edit price. **Balances:** see · export. **Payroll calculation:**
  print · export. **Payroll accruals:** view · create (accrue payroll / create
  penalties / create bonuses) · print · export · delete.

**Design takeaways:** permissions are *verb-level per module*, including
view-scoping of money data (cost/profit/purchase price), dropdown membership,
period-scoped finance visibility ("for any period"), and edit-after-close rights.
Our RBAC-v2 (Roadmap B1) matrix should adopt this verb×module granularity and the
"display in X list" idea.

---

## 7. Employees → Payroll calculation rules

- **Commissions:** "+ Commission", Event filter, table of rules; migration note
  banner about new payroll capabilities.
- **New commission drawer — Event* catalog (grouped):**
  - **Task:** For creating a task (visible as the drawer's selected value —
    commissions can attach to task work too).
  - **Booking:** For creating a booking · For closing a booking.
  - **Ticket:** For creating a ticket · For closing a ticket · **To technician for
    service or labor in ticket** · **To technician for product deducted from
    stock** · **To technician for product added without deduction** · **To
    assignee** · **To assignee for service or labor** · **To assignee for product
    deducted from stock** (list continues below fold).
- **Payrolls:** summary table of base salaries + configured commissions per
  employee.

**Mapping to RepairOS:** we implement order_created / order_closed /
labor_performed / product_sold keyed to the per-line technician with assigned-tech
fallback. Orderry splits **performer vs assignee** into *separate rule events* and
distinguishes **deducted vs non-deducted product** commissions, plus **booking**
events. Cheap, high-parity extension: add `product_sold_no_deduction`,
`booking_created/closed`, and assignee-variant events to our EVENTS registry.

---

## 8. UX patterns worth copying vs beating

**Copy:** status-driven public-page actions; drawer-based creation everywhere;
info-boxes that deep-link to the *other* settings page involved; live examples next
to numbering/currency inputs; All|Selected location scoping as a standard control;
connected-state badges on integration cards; grouped event dropdowns.

**Beat (blueprint already targets these):** per-section Save buttons (→ autosave or
one sticky save bar); the 22-item flat Settings sidebar with duplicated names vs
main nav (Finance, Inventory, Chats appear in both — confusing; → our 3-way
sidebar/Company/Settings split); "General" as an unbounded scroll of 8 unrelated
sections (→ one page per concern + ⌘K jump); permission tree with no search,
templates, or diff view (→ RBAC v2 matrix with search + role templates).

---

## 9. Gap analysis vs RepairOS (verified against code)

| Feature (from screenshots) | RepairOS status | Notes / priority |
|---|---|---|
| Public booking page appearance (color/radius/logo/lang, slot, multi-service, consider-orders) | ✅ have | `Organisation.page_*`, `booking_*` fields already mirror 1a |
| Public order/estimate accept-decline as status transitions + signature | ✅ have | `OrderStatus.shows_accept`, public.py apply-decision, `requires_signature` |
| Online payment per-status + "if paid move to" | ◑ partial | pay links exist; no per-status gating / paid-transition setting — small add |
| Per-location default merchant | ❌ missing | provider is org-level only (A1 tail) |
| Background-pattern picker / Change subdomain / header-field toggles | ❌ missing | cosmetic tail for public pages |
| Tax registry | ◑ partial | `Tax(name, rate, mode[incl/excl], is_default)` exists; **missing:** code, name-for-document, location scope, **tax groups**, priority stack per doc family, **category exceptions**, recalc policy — P1 for invoicing markets |
| Cash-flow categories + defaults per doc type | ✅ have | `CashFlowItem`; defaults-per-doc-type worth adding (small) |
| Rounding adjustments setting | ❌ missing | P2 |
| Decimal places / currency display style | ❌ missing | org-level display prefs, P2 |
| **Price lists** (named levels, markup %, per-location, default per doc type) | ❌ missing | single price per item today — **P1**, big seller for multi-location |
| Default discount type + wage interaction | ✅ have (inverse naming) | `discount_paid_by client|company` per line; add org default toggle |
| Email connectors (Gmail/Outlook, 10 accounts, sender names) | ◑ partial | SMTP only; OAuth connector scoped in ROADMAP A6 |
| VoIP cards (Aircall/Twilio/Zadarma) | ❌ missing | A7 |
| Make/Zapier/Google Forms/FB Lead Ads/LiveChat | ❌ missing | A7 (webhooks/API exist as DIY) |
| Order Status Widget (embeddable) | ◑ partial | public track page exists; embeddable widget variant missing |
| Companion browser extension (bulk ops) | ❌ missing | P3 |
| Legal entities (+ default, per-entity invoice numbering) | ❌ missing | **P1** for tax-serious markets |
| **Numbering templates** {D}{M}{Y}{C:n} + begins-from, per doc type, per location/entity | ❌ missing | hardcoded `INV-{n:05d}` etc. — **P1**, cheap |
| Regional: documents language ≠ UI language | ❌ missing | P2 (i18n groundwork) |
| Contacts consent defaults + duplicate prohibition (phone/email) | ❌ missing | P2, easy |
| Middle-name toggle / required-field config per form | ◑ partial | `CustomField.required` exists; per-built-in-field required config missing |
| Warranty count-from / asset-in-one-open-ticket / default comment+file visibility | ◑ partial | warranty_days + visibility flags exist per item; org **defaults** + one-open-ticket guard missing (small) |
| Prevent backdating invoices / default due date / default entity | ◑ partial | due dates exist; backdating guard + defaults missing (small) |
| Roles: verb-level permission tree (~120), Managers/Assignee list membership, edit-closed, shared filters | ◑ partial | flat JSON perms + require_perm today; full matrix = Roadmap **B1 RBAC v2** — this catalog is the spec |
| Commission events: booking events, assignee-vs-performer variants, product w/o deduction | ◑ partial | 4 events live; extend EVENTS registry — cheap |
| Tags / Marketing / Referral program / Ecommerce Connect settings pages | ❌ missing | P3 (Marketing=A7 campaigns) |

---

## 10. Recommended build order from this batch

1. **Numbering templates + Legal entities** (P1, small-medium): token engine
   ({D}{M}{Y}{C:n}, begins-from), org+location+entity scoping, wire orders/invoices/
   inquiries; LegalEntity model + default + invoice attachment + per-entity numbering.
2. **Tax depth** (P1): code/name-for-document/location scope on Tax, TaxGroup,
   priority stack per doc family, category exceptions, recalc policy setting.
3. **Price lists** (P1): PriceList(name, markup_pct, locations) + per-doc-type org
   defaults + per-location override + resolution order in pricing.
4. **Small parity pack** (P2, one pass): contacts consent+dup guards, invoice
   backdating guard + defaults, org defaults for comment/file visibility +
   warranty-from + asset-one-open-ticket, payment-category defaults per doc type,
   commission-event extensions, currency display + decimals + rounding.
5. **RBAC v2** (B1): use §6 catalog as the permission spec.

*Next batches will extend this file (expected: Locations, Statuses editor,
Notifications, Forms editor, Print templates, Inventory settings, Directories,
main-module screens).*

---

# Batch 2 — operational screens + config editors (24 more screenshots)

Correcting the record: the first analysis covered only the 20 Settings screenshots.
These 24 (the June-12 set + the 13:02:23→13:04 run) are the **main working screens
and the remaining config editors** — the parts that actually matter for day-to-day
UX. Transcribed below with gap calls against repairos-41.

## B2.1 Work Orders / Workflows (list + detail)
- **List:** dense table — WO number, client, asset, status pill, assignee,
  created/deadline, total. Left status-group filter rail; top search + saved
  filters + bulk toolbar; "+ Work order" primary.
- **Detail:** left = General info (client, asset, complaint, dates, assignee,
  manager); center = **Services & products** table (labor/parts lines with
  qty/price/discount/tech, add-line row); right/tabs = comments feed, files,
  invoices & payments, history. Status changer up top; print/actions menu.
- *Ours:* we have order list + split detail + line table w/ tech column
  (shipped). **Gaps:** saved-filter rail polish, the invoices&payments tab
  layout, per-line discount column visibility parity.

## B2.2 Sales (POS)
- Counter-sale screen: product search/scan → cart lines → totals → tender.
- *Ours:* Sale/SaleLine + POS exist. **Gap:** gift-card/loyalty tender in the POS
  surface (A2 tail), receipt options.

## B2.3 Contacts / Client 360
- Client card: contact details, balance, assets, order/sale history, documents,
  consent flags, loyalty. *Ours:* covered; consent flags now backed (P4) but no UI.

## B2.4 Statuses editor  *(config)*
- Status list grouped by stage (New / In progress / Closed…), each with a color,
  a group, terminal flag, SLA/time-limit, and **automation hooks** (notify on
  enter, move-after). Drag-order within a group.
- *Ours:* OrderStatus model + transitions + SLA sweep exist. **Gap:** the visual
  editor (grouping, drag-order, per-status notify/automation config UI).

## B2.5 Notifications editor (External / Internal)  *(config)*
- **External:** client-facing templates per event (status change, ready for
  pickup, estimate, invoice) × channel (SMS/email/WhatsApp), with variable tokens
  and per-status triggers. **Internal:** staff alerts (assignment, comment, SLA).
- *Ours:* NotificationPreference + channel adapters + inbound chat exist. **Gap:**
  the **template editor** (per-event, per-channel bodies with tokens) and the
  event→template mapping UI. This is a real missing surface, not just a flag.

## B2.6 Directories  *(config)*
- Custom reference lists (asset types, manufacturers, models, cancel reasons,
  lead sources…) editable as directories, feeding dropdowns app-wide.
- *Ours:* some enums are hardcoded. **Gap:** a generic **Directory** model +
  editor so dropdowns are org-customizable (Orderry-style).

## B2.7 Forms editor  *(config)*
- Per-entity (order/inquiry/contact/product/asset) field layout: reorder, mark
  required, hide, add custom fields.
- *Ours:* CustomField + required flag exist. **Gap:** the layout/required editor
  UI and applying it to built-in fields.

## B2.8 Print templates  *(config)*
- WYSIWYG-ish document templates (work order, estimate, invoice, receipt) with
  tokens + logo/entity details.
- *Ours:* DocumentTemplate + GeneratedDocument exist. **Gap:** editor depth +
  token palette parity.

## B2 gap summary (net-new vs already-tracked)
Net-new surfaces these 24 revealed that we had NOT captured before:
1. **Notifications template editor** (per-event × per-channel bodies + tokens) — real gap.
2. **Statuses visual editor** (grouping, drag-order, per-status automation/notify).
3. **Directories** (org-customizable dropdown lists) — new model needed.
4. **Forms editor UI** (built-in field required/hide/reorder).
5. Work-order **Invoices & payments tab** + per-line discount column parity.
6. POS **gift-card/loyalty tender** surface.

Already tracked (half-done/UI-missing from Batch 1 audit): asset-one-open-ticket
enforcement, role location-scope enforcement, rounding math, warranty count-from,
and the absent P4/P5 UI.

## Process note
44 screenshots were provided; 20 reached the assistant inline in the first two
messages, 24 (this batch) were on disk but only analyzed here. Future batches:
assume all uploaded images should be viewed from /mnt/user-data/uploads even if
not pasted inline.
