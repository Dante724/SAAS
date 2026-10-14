# RepairOS — Product Blueprint 2026
### A complete IA, UX, and workflow redesign for a premium repair & service SaaS

**Grounding.** This blueprint audits the *actual* shipped codebase (43 routers, 86 tables, ~343
API routes: work orders w/ SLA + checklists + per-line technicians, estimates + public approval,
configurable status pipelines with automation, deep inventory incl. serials/batches/bins/POs,
finance/cash/invoices, payroll commissions + timeclock, bookings/resources, leads, 2-way
SMS/WhatsApp, feedback/NPS, documents, webhooks/API, QBO, Stripe/Square/SumUp, gift cards/
coupons/loyalty, VIN/HIN/IMEI lookups + industry templates, technician PWA, staff push).
The redesign is therefore a **re-architecture of surface, not of capability** — most backend
capability exists; the wins are IA, speed, and interaction design.

**Competitive grounding (researched).** Orderry (status-automation depth, per-line payroll,
asset renaming per industry; dense, dated UI, settings sprawl). RepairDesk (repair-retail POS
strength; cluttered ticket UI). Shopmonkey (auto: workflow board + DVI; auto-only). Jobber
(clean and simple; features pushed to top tiers, modest depth). Housecall Pro (automation +
polished mobile; heavier setup, support gaps). None is keyboard-first; none has a real command
palette; none treats AI as a layer rather than a bolt-on. That is our lane.

---

## 1 · Product thesis & design principles

**Thesis.** "Linear-grade speed for the repair counter." Every high-frequency action (intake,
status move, add line, take payment, message customer) must be reachable in ≤ 2 interactions
from anywhere, with the keyboard alone.

**Personas.** Front-desk operator (intake, comms, payments — speed above all) · Technician
(bench/field, mobile PWA, offline) · Manager/Owner (pipeline, payroll, reports, settings) ·
Multi-location admin (cross-location visibility, central catalogs).

**Principles.**
1. **Command palette is the product.** `⌘K` does navigation, search, creation, and actions.
2. **Two-click rule.** List → record → action. Anything deeper becomes a palette action or inline edit.
3. **Drawers over pages, pages over modals.** Records open as right-side drawers from lists
   (context preserved); full pages only for workspaces (order detail, calendar, POS); modals
   only for confirmations and tiny forms.
4. **Everything is a view.** Every list = query + columns + sort + grouping, saveable, shareable,
   pinnable to the sidebar (Linear's model).
5. **Status is the engine.** The existing pipeline automation (notify/invoice/lock/warranty/SLA/
   webhook/commission) stays the single source of workflow truth; UI exposes it, never forks it.
6. **One industry template, everywhere.** The shipped `industry_template` renames Assets →
   Vehicles/Vessels/Devices across nav, forms, docs (Orderry does this at signup only; we make it
   switchable in Settings, live).
7. **Location is a lens, not a wall.** A global location switcher scopes every view; "All
   locations" is a first-class value for managers. Data model already scopes by org + location.
8. **AI assists, humans commit.** AI drafts (estimates, replies, summaries) always land as
   editable proposals with a visible "AI-drafted" chip. Never auto-send.
9. **Accessible by default.** WCAG 2.2 AA: full keyboard reachability, visible focus, 4.5:1
   contrast, reduced-motion support, ARIA landmarks per region.

---

## 2 · Information architecture

### 2.1 Sidebar (the daily surface — max 11 items + pins)

```
┌────────────────────────────┐
│ ◉ Org / Location switcher  │  ← org logo, location dropdown incl. "All locations"
│ 🔍 Search…          ⌘K    │  ← opens command palette
├────────────────────────────┤
│ ▸ Home            (g h)   │  Dashboard
│ ▸ Inbox           (g i)   │  Messaging: SMS/WhatsApp/email threads + approvals
│ ▸ Leads           (g l)   │
│ ▸ Work Orders     (g w)   │  incl. Estimates as a tab/view
│ ▸ Calendar        (g c)   │  scheduler + bookings + resources
│ ▸ Customers       (g u)   │  clients + their assets (Vehicles/Devices/Vessels)
│ ▸ Catalog         (g k)   │  Products · Services · Labor rates · Bundles
│ ▸ Inventory       (g v)   │  Stock · Warehouses/Bins · POs · Suppliers · Transfers · Counts
│ ▸ Money           (g m)   │  Invoices · Payments · POS/Sales · Cashboxes · Gift cards
│ ▸ Reports         (g r)   │
├─ Pinned views ─────────────┤  ← user-pinned saved views (e.g. "My overdue", "Awaiting parts")
├────────────────────────────┤
│ ▸ Company                  │  Employees · Payroll · Timesheets · Audit (people & org ops)
│ ⚙ Settings                 │
└────────────────────────────┘
```

**Sidebar vs Company vs Settings — the rule.**
- **Sidebar** = things you touch many times a day (operational nouns).
- **Company** = people & organizational operations touched weekly (Employees, Payroll,
  Timesheets, Schedules/Shifts, Announcements, Audit log *viewer*).
- **Settings** = configuration touched rarely (everything in §5). If a screen changes behavior
  rather than records data, it's Settings.

This collapses today's 15+ flat nav items (assets, bundles, bins, feedback, documents, chats,
notifications scattered) into 11 stable groups. Feedback/NPS lives under Reports → Customer
Experience; Documents become a capability (print/template picker on records) not a destination.

### 2.2 Multi-location model
- Location switcher in the shell header scopes *every* list/report/dashboard; persists per user.
- Records carry their location badge; cross-location actions (transfer stock, reassign order)
  are explicit palette actions with confirmation.
- Central vs local catalogs: catalog items flagged `shared` (org-wide) or per-location price
  overrides (price-level table already exists — expose as "Location pricing" column set).
- Roles can be location-scoped (Settings → Roles): a tech at Location B never sees A's queue.

### 2.3 URL & object model
`/{location}/work-orders/{view}` · `/wo/WO-00042` (canonical short links) · every drawer has a
URL (`?peek=client:123`) so links, back button, and cmd-click all behave. Numbers (WO-, INV-,
EST-, PO-) are global search tokens.

---

## 3 · Global design system (applies to EVERY module; modules below note only deltas)

### 3.1 App shell
64px collapsible icon rail → 240px expanded sidebar; content max-width 1440px; right-side
**peek drawer** (480–720px) for record preview; secondary **activity drawer** on records
(timeline/comments). Top bar: breadcrumb, view controls, primary action, presence avatars.

### 3.2 Tables & views (the universal list spec)
- **Columns**: user-orderable, resizable, show/hide; per-view persistence (server-side
  `SavedView{module, filters, columns, sort, group_by, is_shared, pinned}` — *new table to add*).
- **Filters**: token-bar filters (`status:in-progress assignee:@me due:<today loc:main`),
  synced with a visual filter panel; every filter is URL-encoded (shareable).
- **Sorting**: multi-key (shift-click headers). **Grouping**: by any enum (status, assignee,
  location) → collapsible sections with aggregates (count, Σ total).
- **Saved views**: per-user + shared; defaults per module ship pre-made ("My open", "Overdue",
  "Unassigned", "Awaiting approval"). Pinnable to sidebar.
- **Bulk actions**: checkbox column + sticky action bar (assign, status, tag, print, export CSV,
  message, delete-with-guard). Keyboard: `x` select, `shift-x` range, `esc` clear.
- **Row interactions**: click → peek drawer; `enter` → full page; `space` → quick preview;
  right-click → context menu (same items as `…` menu): Open, Open in new tab, Copy link,
  Copy number, quick status, Assign, Print/PDF, Duplicate, Archive/Delete (guarded).
- **Density toggle** (comfortable/compact) and sticky header/first column.

### 3.3 Forms, drawers, wizards
- Creation = **drawer form** with progressive disclosure (required strip on top, "More" sections
  collapsed). `⌘Enter` saves, `⌘Shift-Enter` saves+new, `esc` prompts if dirty.
- Inline edit everywhere on detail pages (click-to-edit fields — already partly built in order
  lines; make universal). Autosave with per-field optimistic UI + undo toast (7s).
- **Wizards** only for: onboarding, data import, payroll run, stocktake, campaign send.
  Always: left step rail, save-and-exit, resumable drafts.
- Validation: inline on blur, summarized on submit, server errors mapped to fields; never lose
  input.

### 3.4 Empty / loading / error (every list & page)
- **Empty**: icon + one-line promise + primary CTA + "Import" secondary + 30-sec doc link.
  First-run empties may include sample-data offer (uses existing SEED machinery, org-scoped).
- **Loading**: skeleton rows matching final layout (no spinners in content), 150ms delay before
  showing; background refetch keeps stale data visible (SWR pattern — the tech PWA already
  does network-first-with-cache; adopt app-wide).
- **Errors**: inline retry card with request-id (middleware already emits `x-request-id` — show
  it); toasts only for action failures; full-page error only on shell failure. Offline banner
  reuses the PWA connectivity component.

### 3.5 Keyboard model (global)
`⌘K` palette · `g` then letter = go-to (map in §2.1) · `c` new record in current module ·
`/` focus filter · `j/k` row nav · `enter/space` open/peek · `x` select · `e` edit ·
`a` assign · `s` status · `m` message customer · `p` take payment · `.` context menu ·
`?` shortcut overlay · `esc` close layer. All remappable (Settings → Preferences).

### 3.6 Search (global + palette)
- **Palette (⌘K)**: fuzzy across commands + records (orders, clients, assets by any identifier —
  serial/IMEI/VIN/HIN —, invoices, products by SKU/barcode, employees); scoped prefixes
  (`>` commands, `#` numbers, `@` people, `?` help). Recent + frequency ranking.
- **Backend**: one `/api/search?q=` federated endpoint (new) fanning to existing per-module
  search params; later swap to SQLite FTS5/Postgres tsvector without API change.
- **Scanner input**: hardware barcode scanners type-into-palette; a scanned identifier resolves
  to product/asset/order instantly (lookup endpoints already exist).

### 3.7 Permissions surface (mechanics in Settings §5.3)
Every action renders from capability checks: hidden if no read, disabled+tooltip("Requires
Finance role") if no write. Location-scoped roles filter data server-side (org filter exists;
add location claim). Sensitive reveals (cost/margin columns, passcodes) are per-role toggles.

### 3.8 AI layer (per-module features listed in modules)
One pattern everywhere: **"✦ Assist" split-button** on records + palette verbs. Drafts render in
a review sheet (accept/edit/regenerate), stamped "AI-drafted" in the timeline. Org-level AI
settings (§5.19) gate everything; no key → buttons hidden. Existing endpoints (summary,
estimate-draft, reply-suggest) plug straight in; add transcription + item-recognition per
roadmap A5.

### 3.9 Automations (engine exists; give it a face)
Settings → Automation is a **recipe builder** over existing triggers: status-entered,
SLA-breached, payment-received, estimate-approved/declined, booking-created/cancelled,
inbound-message, stock-below-min, warranty-expiring, birthday. Actions: notify (template),
webhook, assign, add checklist, create task/PO draft, apply tag, schedule follow-up. Each
recipe: enabled toggle, run log, test-fire. (Status-level flags remain visible on the pipeline
editor and read "Managed by pipeline".)

### 3.10 Mobile & tablet strategy
- **Technician PWA** (shipped) stays the field surface; extend, don't fork.
- **Responsive desktop web**: tables → card lists < 900px; drawers become full-screen sheets;
  bottom action bar replaces top-right buttons; calendar collapses to day/agenda.
- **Tablet/counter mode**: POS and intake get a touch-first layout ≥ 48px targets (POS page
  already leans this way; formalize breakpoint).

### 3.11 Reports pattern
Every module's list has "Σ" summary row + "Open as report" (sends current view's query to
Reports with chart). Reports = saved queries + visualizations + schedule-to-email (SMTP exists).

---

## 4 · Module blueprints (deltas from §3)

### 4.1 Home (Dashboard)
**Purpose**: 8am answer to "what needs me today". **Sidebar**: first item.
**Pages**: single, role-aware. **Layout**: greeting + location scope; KPI strip (Today's
revenue, Open WOs, Overdue, Awaiting approval, Unread inbox — existing report endpoints);
**"Needs attention" feed** (SLA breaches — endpoint exists; unapproved estimates aging;
unpaid invoices > X; low stock); My schedule (today's calendar lane); Recent activity.
Owner variant adds trend sparklines + multi-location comparison strip.
**Modals**: KPI drill = opens filtered view (no dashboard-local modals).
**AI**: "Morning brief" — one-paragraph digest of anomalies (queue up 2× vs avg, tech out sick
conflicts). **Mobile**: KPI cards stack; feed first. **Empty**: onboarding checklist (connect
payments, add first WO, invite tech) with progress.

### 4.2 Inbox (Messaging)
**Purpose**: every conversation (SMS/WhatsApp/email-when-connected) + approval events in one
triage surface — the module competitors bury. **Sidebar**: #2 with unread badge.
**Pages**: Inbox (list+thread split), Thread. **Layout**: 3-pane — filters rail (Mine/Unassigned
/All, channel chips) · thread list (client, snippet, WO chip, age) · conversation (bubbles,
internal-note toggle, right context card: client, open WOs, balance, quick actions).
**Drawers**: client peek; "Link to work order" picker. **Fields**: reply box w/ templates (`;`
snippet expander), attachments, schedule-send. **Bulk**: assign, close, mark read.
**Shortcuts**: `r` reply, `⇧i` internal note, `e` archive/close, `l` link WO. **AI**: suggested
reply (endpoint exists), thread summary, sentiment flag, auto-link intent→lead. **Automations**:
inbound after-hours auto-reply; unanswered > 1h escalation. **Empty**: "Connect a number"
(Twilio) CTA. **Permissions**: `can_message_clients`; assign requires manager.
**Mobile**: exactly WhatsApp-like; swipe to close/assign.

### 4.3 Leads
**Purpose**: pre-order pipeline → convert to WO/estimate (conversion endpoints exist).
**Sidebar**: yes. **Pages**: Board (default) + Table + Lead page. **Board**: kanban by stage,
WIP counts, drag = stage change (mirrors WO board component). **Drawer**: quick-create
(name/phone dedupes live against clients, source, interest, value, assignee). **Context menu**:
Convert → Estimate/WO/Booking, Message, Schedule call, Mark lost (reason picker feeds report).
**AI**: lead scoring (recency/engagement heuristic first), first-touch draft. **Automations**:
new-lead SLA (untouched 15min → notify), source auto-tag from booking/chat. **Reports**:
funnel conversion, source ROI, lost reasons. **Empty**: web-booking + chat wiring CTA.

### 4.4 Customers (Clients + Assets)
**Purpose**: CRM + owned assets, renamed by industry template (Vehicles/Devices/Vessels…).
**Sidebar**: yes. **Pages**: Clients list, Client 360, Assets list (label from template),
Asset detail. **Client 360 layout**: header (name, tags, balance chip, loyalty tier ✦points,
portal-link button) · left: profile/custom fields · center tabs: Timeline (feed), Work orders,
Estimates, Invoices & payments, Assets, Messages, Files · right: quick actions (New WO,
Message, Take payment, Book). **Forms**: client (type person/company, contacts[], addresses[],
tax id, discount level, marketing opt-ins); asset form is **identifier-kind aware** (shipped:
type → VIN/HIN/IMEI/Serial + Lookup autofill). **Bulk**: tag, export, message-campaign (perm-
gated), merge (new: dedupe wizard). **Context**: call (telephony seam), copy portal link.
**AI**: duplicate detection, "client brief" summary before a call. **Reports**: LTV, repeat
rate, balance aging. **Empty**: import wizard (CSV mapping — exists in import seam) front and
center. **Permissions**: PII export/erasure = admin (GDPR tooling per roadmap B2).

### 4.5 Work Orders (+ Estimates as sibling view)
**Purpose**: the core workspace. **Sidebar**: yes; Estimates = tab (same engine,
`is_estimate`, distinct pipeline — already modeled).
**Pages**: Views (Table/Board/**Split**), WO page, Print/PDF, Public tracking (exists).
**Board**: columns = pipeline statuses (colors/limits from Settings), card: number, client,
asset chip, assignee avatars (multi via line techs), due/SLA ring, ⏱ badge when timer running.
Drag = status (server transition rules can 403 → snap-back with reason toast — enforcement
exists).
**Split view** (new, killer): list left, full order right — zero-click triage.
**WO page layout**:
```
Header: WO-00042 · [status pill ▾ w/ allowed-only menu] · SLA ring · client · asset · ⌘K hints
Actions: ✦Assist ▾ · Message · Take payment ▾ (link/QR/terminal) · Print ▾ · …
Left rail: Details (inline-edit: type, priority, assignee, schedule, due, tags, custom fields)
Center tabs:
  Overview  — complaint/diagnosis (AI-draft), Checklist (gate indicator), Lines table
             (desc | Tech ▾(shipped) | qty | price | disc | tax | total; catalog picker ⌘;
              serials/batches sub-picker; margin col perm-gated)
  Activity  — unified timeline (comments int/ext, photos, files, status log, SLA, payments)
  Financial — invoice preview, payments, gift/coupon/points applied (shipped actions)
  Time      — labor entries per line/tech (endpoint exists), start/stop
Right: customer card + asset history capsule (past WOs, warranty status)
```
**Modals/drawers**: New WO (drawer: client typeahead-or-create → asset pick/create w/ identifier
lookup → complaint → type/schedule; AI "draft estimate lines from complaint" — endpoint
exists); Payment sheet (amount, method incl. gift card/points, link+QR, terminal); Decline-
reason; Completion gate errors render as an actionable checklist (gate shipped).
**Estimates delta**: Accept/Decline hero on public page (exists), "Convert" CTA, expiry field +
auto-expire automation, versioning (new: `revision_of_id` — P2).
**Shortcuts**: `s` status, `a` assign, `t` start/stop timer, `p` payment, `l` add line,
`⌘p` print. **AI**: summary for handoff (exists), estimate-line drafting (exists), repair-time
prediction from history (P2), photo-based device recognition (roadmap A5).
**Automations**: full existing engine surfaced (invoice/notify/lock/warranty/SLA/commission);
add "parts-arrived → notify tech + status" recipe (pending-stock receive exists as trigger
point). **Reports**: cycle time by status, tech throughput, first-time-fix.
**Mobile**: technician PWA is the answer (shipped: offline, checklist, parts, signature,
payment link).

### 4.6 Calendar
**Purpose**: scheduler for WOs + bookings + resources; dispatch. **Sidebar**: yes.
**Pages**: Day/Week/Month/**Tech-lanes (dispatch)**/Resource view; Booking settings live in
Settings. **Layout**: left mini-cal + unscheduled tray (drag onto grid = schedule PATCH,
exists); lanes = employees or resources; conflict shading; working-hours from shifts.
**Drawer**: event peek (WO capsule, travel-time est P2). **Context**: reassign, duplicate,
convert booking→WO (exists). **Shortcuts**: `d/w/m` views, arrows navigate, `⌘drag`
duplicate. **AI**: "find best slot" (duration × tech skill-tag × load). **Automations**:
reminder cadence (exists via templates), no-show follow-up. **Mobile**: agenda list; tech PWA
shows *my* day. **Empty**: enable online booking CTA (public page exists).

### 4.7 Catalog (Products · Services · Labor · Bundles)
**Purpose**: what you sell. **Sidebar**: one item, four tabs (today they're scattered).
**Pages**: per-tab lists + item page. **Item form**: shared core (name, SKU/code, category,
tax class, price levels grid incl. per-location, cost, images) + type extras: product (barcode,
serialized flag — irreversible, warn; batch flag; min-stock per warehouse), service (default
duration, required checklist template link, exceptional commission — feeds payroll priority),
labor (rate type hourly/flat), bundle (component picker w/ auto-deduct flag — exists).
**Bulk**: price update wizard (%, rounding), category move, export/import. **Context**:
duplicate, view stock, price history. **AI**: description generation (roadmap A5), price
suggestion from cost+market band, photo→attributes. **Reports**: assortment/ABC (exists),
margin by category. **Empty**: import + "generate starter catalog for {industry}" (AI, from
industry template).

### 4.8 Inventory (Stock · Warehouses · Transfers · Counts · POs · Suppliers)
**Purpose**: physical truth. **Sidebar**: one item, six tabs.
**Stock list**: product × warehouse grid, on-hand/reserved(pending WO lines — exists)/min,
below-min filter chip; serial/batch drill-in (exists). **Warehouses**: cards + bin tree editor
(bins exist). **Transfers**: draft→in-transit→received flow (exists) with scan-to-add.
**Counts (stocktake)**: wizard — scope (warehouse/category) → count sheet (scanner-first,
keyboard `+1` on scan) → variance review → post adjustments (exists). **POs**: list + PO page
(supplier, expected, lines w/ received qty, partial receive, "create from low-stock" generator —
exists), receive flow updates WO pending-stock (exists) and can auto-price via markup rules.
**Suppliers**: list + card (terms, lead time, catalog mapping). **Barcode**: global scan
behavior (§3.6). **Automations**: below-min → draft PO recipe; PO received → notify waiting
WOs. **AI**: reorder-point suggestions from velocity; receive-by-photo (invoice OCR, P3).
**Reports**: valuation (exists), turns, dead stock, shrinkage from counts.
**Permissions**: adjustments/counts = inventory role; cost visibility toggle.

### 4.9 Money (Invoices · Payments · POS/Sales · Cashboxes · Gift cards)
**Purpose**: all inbound money in one place (today split across cash/sales/payments).
**Tabs**: Invoices (aging chips 0/30/60/90, send/remind, partial payments — model exists),
Payments (all tenders incl. provider intents; refund flow exists), POS (touch grid, cart,
tender split: card/cash/**gift card/points** — redemption endpoints exist, receipt print),
Cashboxes (accounts + shift open/close with over/short), Gift cards (issue/list/balance —
exists; POS sale of cards = A2 tail). **Payment sheet everywhere** (same component as WO):
provider auto per org (Stripe/Square/SumUp — shipped), link + QR, terminal button (Stripe
Terminal slice shipped). **Automations**: overdue reminder ladder (3/7/14d), payment-received
(exists→ push/webhook/loyalty). **AI**: dunning-tone drafts, anomaly flags (refund spikes).
**Reports**: revenue by day/location/tech, AR aging, tender mix, fees by provider.
**Permissions**: refunds > X require manager PIN (P1 add).

### 4.10 Company (people ops)
**Employees**: directory (role, locations, rates, active), profile with **Payroll rules tab**
(Orderry-parity: base salary + commission rules list w/ event/basis/coefficient — engine
shipped; this UI formalizes it), documents, PIN/passcode. **Timesheets**: timeclock review +
approvals (exists), GPS chips (shipped), exceptions filter. **Payroll**: period picker →
calculation table (base + commissions expandable to source docs — ledger is idempotent &
traceable) → adjustments (bonus/penalty) → **Run wizard** (draft → review → accrue → mark
paid via cashbox) — engine + run model exist; wizard is UI. **Schedules/Shifts**: week grid.
**Audit viewer**: filterable AuditLog (model exists; coverage expansion = roadmap B2).
**Permissions**: Payroll visible only w/ finance; employees see self.

### 4.11 Reports
**Purpose**: question-answering, not dashboards-for-show. **Layout**: gallery of report cards
by domain (Sales, WOs, Inventory, People, Customer Experience incl. NPS/feedback — exists) →
report page = filter bar + chart + table + export + **schedule** (email via SMTP) + "save as
view". **Builder (P2)**: pick entity → measures/dimensions → chart. **AI**: "Ask reports"
natural-language → saved-query proposal (never raw SQL execution without review).
**Permissions**: per-report-domain visibility.

### 4.12 Customer Portal (public)
Exists (track/approve/pay/book/portal). Redesign: unified `/p/{token}` shell — status stepper,
timeline (client-visible), estimate approve/decline w/ signature, invoice + **Pay now**
(providers shipped), booking reschedule, message-the-shop thread (writes to Inbox), branding
from Settings (logo/colors — fields exist). Add portal analytics (opens, approval time).

---

## 5 · Settings — complete structure

Left rail with sections; every page = form with autosave + "Changes saved" toast; dangerous
zones separated; every setting searchable from ⌘K (`>settings: taxes`).

| # | Page | Contents (key fields/actions) | Backing |
|---|---|---|---|
| 1 | **Company Profile** | name, contacts, address, logo, currency, timezone, **industry template** (live-renames assets), fiscal year | exists (staff/company) |
| 2 | **Locations** | list + form (name, address, hours, phone, tz), default location, per-location numbering prefix | exists + prefix (P1) |
| 3 | **Roles & Permissions** | role list; matrix editor (resource × action), **location scope**, sensitive-data toggles (costs, payroll), templates (Owner/Manager/Tech/Front-desk); RBAC v2 = roadmap B1 | JSON roles exist |
| 4 | **Users** | invites, role+locations, MFA status, sessions ("sign out everywhere" — token_version shipped), deactivate | exists+B1 |
| 5 | **Notifications** | per-event channel matrix (customer: SMS/email/WhatsApp; staff: push/in-app — prefs shipped), quiet hours | templates+prefs exist |
| 6 | **Templates** | message templates w/ variables + preview; document templates (invoice/WO/label) w/ editor; estimate T&Cs | exists (documents) |
| 7 | **Automation** | recipe list (§3.9), run log, test fire | engine exists; recipes UI new |
| 8 | **Pipelines & Statuses** *(add)* | WO + estimate pipeline editors: reorder, colors, transition matrix, per-status automation flags + SLA minutes | fully exists |
| 9 | **Integrations** | provider cards: QBO (exists), Xero (roadmap), Stripe/Square/SumUp (+org provider picker — shipped), Twilio, Email (roadmap A6), AI, Zapier (roadmap) — each: status, connect, health, last sync log | exists+roadmap |
| 10 | **API & Webhooks** | API keys (exists: create/rotate/last-used), webhook endpoints + delivery log + retries (exists), events catalog doc | exists |
| 11 | **Taxes** | tax classes/rates, per-location defaults, inclusive/exclusive rules | exists |
| 12 | **Numbering** | per-doc masks (WO-{seq}), next-number, location prefixes | partial → P1 |
| 13 | **Payment Methods** | provider selection (shipped), terminal pairing, cash-rounding, surcharges, gift-card & **loyalty settings** (earn rate/point value — fields shipped; UI here) | shipped fields |
| 14 | **Branding** | portal/pay-page logo+colors (fields exist), email footer, receipt header | exists |
| 15 | **Security** | password policy, MFA enforcement (B1), session TTL, IP allowlist (B3), SSO/SCIM (B1) | roadmap |
| 16 | **Audit Logs** | link to Company viewer + retention setting + SIEM export (B6) | model exists |
| 17 | **Data Import & Export** | CSV wizards (clients/catalog/inventory), full org export (B2), scheduled backups info | partial |
| 18 | **Backup & Restore** | PITR/runbook status (deployment-level), export snapshots | doc'd (B3) |
| 19 | **Feature Flags** | org-level toggles (beta modules), plan gating display | new (simple JSON) |
| 20 | **AI Settings** | enable/disable per feature, model tier, data-sharing consent, monthly usage meter | key exists |
| 21 | **Billing & Subscription** | plan, seats, invoices, payment method (Stripe customer portal embed) | new |

---

## 6 · Competitive teardown (per major area)

**Navigation/IA** — *Orderry*: complete but 15-item flat nav + settings sprawl; *RepairDesk*:
module overload; *Jobber*: admirably simple but shallow (features hidden in top tiers);
*HCP*: good defaults, setup-heavy; *Shopmonkey*: focused board, auto-only. **Ours**: 11 groups +
pinned saved views + ⌘K covers both simple (Jobber-like day one) and deep (Orderry-like) without
menu sprawl. **Why superior**: navigation scales by *views*, not by adding nav items.

**Work-order workflow** — *Shopmonkey* does the best board+DVI; *Orderry* the best status
automation; *Jobber/HCP* strong quote→job→invoice linearity. Weaknesses: none offer split-view
triage, allowed-transition guarded drag, per-line multi-tech visible on cards, or a completion
gate. **Ours** ships all four on an automation engine equal to Orderry's — plus keyboard-first.

**Estimates/approvals** — *Jobber/HCP*: polished client-facing quotes (their best feature);
*Orderry*: functional but dated public page. **Ours**: parity on hosted approve+sign+auto-
convert (shipped) + portal thread + pay-now on the same token; gap to close is quote *layout
polish* and versioning (P2) — be honest, Jobber's quote templates still look better today.

**Scheduling/dispatch** — *Jobber*: built-in routing (their differentiator); *HCP*: strong
dispatch. **Ours**: tech-lane dispatch + booking engine exist; **routing is a real gap** —
roadmap item (Maps ETA + stop ordering) before courting field-service verticals.

**Inventory** — *RepairDesk/Orderry*: deepest among competitors; *Jobber/HCP*: thin. **Ours**
already exceeds Jobber/HCP (serials, batches, bins, POs, counts, WO reservation) — the win is
scanner-first UX and low-stock→PO automation, which none make one-click.

**Payroll/commissions** — *Orderry* is the only serious one (per-line, coefficients).
**Ours**: engine parity shipped (incl. per-line techs + clawbacks); the differentiator is
**traceability UX** — every payroll number expands to its source document (idempotent ledger
makes this trivial; Orderry buries it).

**Messaging** — *HCP*: best automated comms; *Orderry*: has chats but siloed. **Ours**: Inbox
as first-class triage + AI replies + WO-linking beats both; email channel (A6) needed for full
parity with HCP sequences.

**Mobile/field** — *HCP/Jobber*: polished native apps. **Ours**: offline-first PWA with
encrypted queue + conflict resolution — objectively stronger offline story; be honest that
native-app polish/push reliability on iOS (web push limits) trails; mitigation: installable
PWA + APNs wrapper only if metrics demand (P3).

**AI** — all five bolt on isolated AI bits (HCP marketing copy, Jobber quote helper).
**Ours**: a consistent Assist layer with human-commit and audit stamps — more trustworthy and
more useful because it's grounded in the org's own data (history-based drafting).

---

## 7 · Implementation priorities (mapped to codebase)

**P0 (weeks 1–4) — the feel:** app-shell rebuild (rail, location switcher, peek drawer),
`⌘K` palette + `/api/search` federated endpoint, SavedView model + universal table component,
WO split view, keyboard map, empty/skeleton system. *(Frontend-heavy; backend adds: SavedView
table, search endpoint, location claim in JWT.)*
**P1 (5–8):** Inbox redesign, New-WO drawer flow, Payment sheet unification (providers shipped),
Settings IA + Pipelines editor + Payment/Loyalty settings pages, per-location numbering,
refund PIN guard, Company→Payroll rules tab + Run wizard (engine shipped).
**P2 (9–14):** Automation recipe builder over existing triggers, Reports gallery + scheduler,
estimate versioning + quote-template polish, dispatch lanes + "find best slot", dedupe/merge
wizard, portal unification `/p/{token}`.
**P3 (15+):** Report builder, routing/ETA, invoice OCR receive, native wrapper (only if PWA
metrics demand), franchise console (roadmap B5).

**Scalability recommendations** (aligns with ROADMAP B): media → object storage (biggest risk,
B3), cursor pagination + FTS index, SavedView + notification fan-out on the durable queue (B3),
RBAC v2 with location scope (B1), audit-everything middleware (B2), OpenTelemetry + SLOs (B4),
design-token theming for white-label (B5). Feature-flag the new shell (§5.19) to migrate users
progressively — never a big-bang UI cutover.

---

*Every spec above that says "exists/shipped" was verified against the running code; everything
else is scoped to P-levels. The moat: keyboard-first speed + views-not-menus IA + traceable
automation + honest offline — none of the five competitors has more than one of these.*
