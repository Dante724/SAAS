# Memberships — Product & Build Spec

*Status: design, pre-build. Output of a brainstorm with the owner (marine, auto, tech/electronics shops).*
*Target branch: `release/1.0`.*

---

## 1. Summary

A shop sells **service plans** — "Boat Care", "Auto Care", "Device Protect" — that a customer pays for **monthly or yearly**. A plan is a **list of included services with counts** ("Oil change × 3 a year", "Winterisation × 1", "10% off labour"), attached to a **specific boat, car or device**. When that asset comes in, the work order uses the plan up automatically: covered lines go to $0 and the plan shows what's left.

Everything a membership charges is an **ordinary Tekly invoice**, so sales, client balances, cash accounts and the Xero / QuickBooks sync work unchanged. A shop can optionally spread prepaid plan money over the months it covers.

Tekly reminds customers about renewals, unpaid months and included services they haven't used, and alerts the **assigned manager** when something needs a person. At checkout, a non-member sees what a plan would have saved them today, with one tap to join.

Decisions taken with the owner:

1. **A membership belongs to the asset** (boat / vehicle / device), not the person. It is shown on the customer's profile and puts an automatic, locked tag on the customer.
2. **Each plan can offer a monthly price, a yearly price, or both;** the customer picks when they join.
3. **Spreading prepaid revenue is a per-shop setting**, off by default.
4. **Plans are included-items lists**, used up on work orders. A flat discount is just one more line on the list.
5. **The checkout prompt** is the main way plans get sold.
6. **Roles control who can sell, set up, change and apply plans.**
7. No new top-level menu: plans are set up under **Catalog › Plans**; memberships are worked from **Contacts › Memberships**.

## 2. Goals and non-goals

**Goals**
- Recurring revenue that a small shop can sell, track and renew without spreadsheets.
- Members are recognised everywhere their asset appears — work order, checkout, self check-in — with no one having to remember.
- The books stay right: every charge is an invoice; the shop can see paid-vs-used per plan.
- Nothing lapses silently: customers and managers are reminded before money is lost.

**Non-goals for this build** (parked, see §15):
- Charging a saved card automatically (monthly plans use invoice + pay link in phase 1).
- Joining online from the portal without staff.
- Fleet / multi-asset plans (the data model allows them; the UI does not ship them).
- Member-only part prices, a separate points scheme (loyalty already exists).

## 3. Who uses it

| Who | Wants |
|---|---|
| Owner / manager | "Set up our plans once, see who renews this month, know if a plan makes money." |
| Counter staff / service advisor | "When a job is expensive, offer the plan and take the money in the same invoice." |
| Technician | "If the boat's on a plan, the haul-out should just say *covered*." |
| Assigned manager | "Tell me when one of *my* members hasn't paid or is about to lapse." |
| Customer | "Know what my plan covers, what's left, and when to book the included service." |

## 4. Concepts

### 4.1 Plan
What the shop sells. Belongs to the org; optionally limited to some locations and to one industry template / asset group ("Boat Care" only for vessels).

| Field | Meaning |
|---|---|
| name, description | Shown on invoices, the customer page and the checkout prompt |
| template_key, asset_group_ids | Which assets it can be attached to |
| price_monthly, price_yearly | Either may be empty (= not offered). At least one is required |
| tax_rule | The shop's normal tax rule for the plan line |
| active | Inactive plans can't be sold; existing memberships continue |
| commission | Optional, through existing commission rules (§10) |

### 4.2 Included item
One line of what the plan gives, per asset per plan year.

| Field | Meaning |
|---|---|
| kind | `service` (a catalog Service, with a count) · `discount` (percent off labour and/or parts) · `perk` (free text, e.g. "priority slot", not enforced) |
| service_id, qty | For `service`: which catalog service, how many per plan year |
| pct, applies_to | For `discount`: e.g. 10% on `labour` / `parts` / `both` |
| window | Optional seasonal window (e.g. Oct 1–Nov 30) — drives booking reminders (§7.6) |
| rollover | Unused count carries into the next plan year. Default **off** |

### 4.3 Membership
One plan on one asset (the data model allows a group of assets; §15).

| Field | Meaning |
|---|---|
| plan_id, client_id, device_id | The plan, the paying customer, the asset |
| billing | `monthly` · `yearly` (chosen at join) |
| price | Locked at join (plan price changes affect renewals only) |
| starts_on, current_period_end, renews | Plan year and billing period |
| status | `active` · `paused` (unpaid, in grace) · `lapsed` · `cancelled` |
| manager_id | Assigned manager (defaults to the client's manager, else the seller) |
| sold_by_id, sold_on_invoice_id | For commission and audit |

### 4.4 Period
One billing period (a month or a year), with the invoice that charges it. `paid` when that invoice is paid.

### 4.5 Usage
One row each time an included item is used: membership, item, order line, qty, value given (the list price that was waived). Drives "what's left" and the profitability report.

## 5. Data model

New tables (all org-scoped, every query filtered by `org_id`):

- `membership_plans` — §4.1.
- `membership_plan_items` — §4.2, FK plan.
- `memberships` — §4.3. Index `(org_id, status, current_period_end)` for the daily job.
- `membership_assets` — `(membership_id, device_id)`. Phase 1 writes exactly one row; fleet plans later write several.
- `membership_periods` — `(membership_id, period_start, period_end, invoice_id, paid_at)`.
- `membership_usage` — `(membership_id, plan_item_id, device_id, order_line_id, qty, value, used_at)`. Unique on `order_line_id` so a line can't be counted twice.

Changes to existing tables:
- `tags`: add `system` (bool, default false) and `source` (e.g. `plan:<id>`). System tags can't be edited, attached or removed by hand.
- `order_lines`: add `membership_id` (nullable). Set when a line is covered or discounted by a plan.
- `organisations` settings (existing settings JSON): `membership_spread_revenue` (bool, default false), `membership_grace_days` (default 7), `membership_lapse_days` (default 30), `membership_renewal_notice_days` (default 30).
- Role permission catalog: four new permissions (§10).

One Alembic migration, single head.

## 6. Where it lives in the app

| Place | What's there |
|---|---|
| **Catalog › Plans** | Plan list and editor: prices, included items, windows, rollover, which assets. "Start from a template" offers 2–3 starter plans per industry template (§6.1) |
| **Contacts › Memberships** (new tab next to Clients / Suppliers) | Every membership, filterable: *Renewing in 30 days*, *Unpaid*, *Paused*, *Lapsed this month*, by plan, by manager. Row actions: send pay link, renew, pause, cancel |
| **Client profile** | A *Memberships* card: each plan, its asset, status, renews on, what's left. The tier badge shows "Member" |
| **Asset (boat/car/device) page** | The plan on this asset, included items with remaining counts, usage history |
| **Work order** | A plan banner on the asset ("Boat Care · 1 haul-out left") and covered lines marked "Covered by Boat Care" |
| **Checkout / POS** | The join prompt (§7.3) |
| **Settings › Memberships** | The four shop settings in §5 |
| **Self check-in** | Members are greeted: "Your Boat Care covers this visit" (data comes from the device match the check-in already does) |

### 6.1 Starter plans
Shipped with industry templates, editable, never auto-created:
- **Marine — Boat Care:** spring launch × 1 (window Mar–May), winterisation × 1 (window Oct–Nov), haul-out × 1, 10% labour, priority slot (perk).
- **Auto — Auto Care:** oil change × 3, tyre rotation × 2, pre-trip inspection × 1, 10% labour.
- **Tech — Device Protect:** screen repair × 1, battery health check × 2, loaner device (perk), 15% labour.

## 7. Flows

### 7.1 Set up a plan
Catalog › Plans › New (or *Start from a template*). Name, which asset types, monthly and/or yearly price, included items. Saving creates nothing else; a plan is only sold through §7.2/7.3.

### 7.2 Sell a plan (staff)
From a client profile, an asset page, a work order or the POS: *Add plan* → pick plan → monthly/yearly → asset (pre-filled from context) → manager (defaults per §4.3).
- Adds a **plan line** to the current invoice (or creates one): "Boat Care — yearly — *Sea Breeze*, $449".
- The membership is created as `active` from today; the first period is linked to that invoice.
- Covered items on the *same* work order are applied straight away (§7.4).
- The system tag "Boat Care member" is attached to the client.

### 7.3 Checkout prompt (non-member)
When an invoice for a non-member asset is being taken payment for, Tekly works out, per eligible plan:
`saving today = value of lines this plan would cover or discount`
and shows the prompt **only if** `saving today ≥ 25% of the plan's yearly price` *or* the job includes a windowed item. Wording: "Boat Care would save $90 today and covers your winterisation ($280). $449/yr or $45/mo." One tap = §7.2 on this invoice, savings applied. "Not now" hides it for this client for 90 days. Requires *Sell plans* (§10); others see nothing.

### 7.4 Using benefits on a work order
When a line is added for an asset with an active membership:
- If the line's service matches a `service` item with remaining count → the line is marked **covered**: unit price stays visible, `discount = 100%`, `discount_paid_by = company`, `membership_id` set. A usage row is written when the order is closed (not when the line is added), so removed lines don't consume the plan.
- If a `discount` item applies → the plan discount is applied to eligible lines (it doesn't stack with a larger manual discount; the larger wins).
- Applying is automatic for users with *Apply plan benefits*; for others the line shows "Covered by plan — ask a manager" and stays full price.
- **Server-side check** on save and on close: a line can only carry `membership_id` if the plan lists that service (or discount), the membership is `active` for that asset, and count remains. Nobody can make a line free just by sending a `membership_id`.
- `paused` memberships show the plan but don't cover: "Pay $45 to apply your plan" with a pay-link button.

### 7.5 Billing
**Yearly:** one invoice per plan year. Renewal invoice is created `renewal_notice_days` before the end and sent with a pay link; paying it starts the next year on the old end date (no gap, no overlap).

**Monthly (phase 1):** the daily job creates the month's invoice on the period start and texts/emails the pay link. Paid → period `paid`. Unpaid → §7.7.

**Switching** monthly → yearly (or back): from the membership row, *Change billing*. The unused part of the current paid period is credited on the next invoice. Requires *Change a membership*.

### 7.6 Reminders for included items
For items with a `window`: 14 days before the window opens, the customer gets "Your winterisation is included in Boat Care. Book your slot: <booking link>". If nothing is booked or done 14 days before the window closes, the assigned manager is alerted. Unused non-windowed items: 30 days before the plan year ends, "1 tyre rotation left — use it before 12 March".

### 7.7 Missed payments (monthly or renewal)
| Day after the invoice is due | What happens |
|---|---|
| 0 | Pay link sent |
| 3 | Reminder to the customer |
| `grace_days` (7) | Status `paused` (benefits stop, plan still shown). Manager alerted: "Call Sam — Boat Care unpaid" |
| `lapse_days` (30) | Status `lapsed`; the system tag is removed; final message to the customer with a re-join link |
Paying at any point before lapse restores `active` immediately. The shop sets both day counts.

### 7.8 Cancel, refund, transfer
- **Cancel** (needs *Change a membership*): ends at period end by default, or now. Cancelling a yearly plan *now* proposes a refund of `unused months × monthly-equivalent − value already used`, never below 0, through the existing refund flow (needs the existing refund permission).
- **Transfer to another asset** (customer replaced the car): moves the membership; usage history stays with the plan year.
- **Asset sold:** when an asset's owner changes, Tekly asks "Move Boat Care to the new owner?" — yes moves the membership and billing to the new client; no cancels at period end.

## 8. Accounting

- **Every charge is an invoice** with a plan line (`kind = "membership"`, `source_id = plan id`). Payments, cash accounts, client balances, receipts and the existing Xero / QuickBooks push work unchanged.
- **Income category:** plan lines post to a "Membership income" sales category, separate from repairs. For Xero/QBO, the shop maps it to an income account once (same mapping screen as other categories).
- **Covered work:** covered lines appear on the work order and invoice at list price with a 100% plan discount, so the invoice shows the value given and totals $0 for those lines.
- **Spread revenue (setting, off by default):** when on, a yearly payment is recognised over the 12 months. Tekly records the unrecognised part as *plan money received in advance* and moves 1/12 into membership income each month. The Finance view shows "Prepaid plans not yet earned: $12,400". With Xero/QBO connected, the monthly move is pushed as a journal. Monthly plans are the same either way.
- **Refunds** on cancel use the existing refund + credit note flow.
- **Tax** uses the plan's tax rule on the plan line; covered lines carry no tax because their total is $0.
- **Commission** on plan sales uses existing commission rules (new rule target: plan sales — fixed amount or % of the first period).

## 9. Notifications

New events for the existing notification / automation rules, each with a default message the shop can edit:

| Event | Customer gets | Staff alert |
|---|---|---|
| `membership.joined` | Welcome + what's included | — |
| `membership.renewal_due` | Renewal invoice + pay link | Manager, in the weekly summary |
| `membership.payment_due` / `payment_missed` | Pay link / reminder | Manager at grace day |
| `membership.paused` / `lapsed` | Notice / re-join link | Manager |
| `membership.item_window` | "Book your included service" | Manager if not booked 14 days before the window closes |
| `membership.items_expiring` | "1 left, use before …" | — |

Weekly summary to users with *Manage plans*: renewing this month, unpaid, lapsed, new members, plan revenue.

## 10. Roles and permissions

New permissions:

| Permission | Allows | Typical role |
|---|---|---|
| `can_sell_memberships` | Add a plan to an invoice / the checkout prompt | Front desk, service advisor |
| `can_manage_membership_plans` | Create and edit plans; Settings › Memberships | Owner, manager |
| `can_change_memberships` | Pause, cancel, switch billing, transfer, extend dates | Manager |
| `can_apply_membership_benefits` | Plan coverage applies automatically on their work orders | Technicians, front desk |

Reused: `can_refund_card_payments` / refund permissions (cancel with refund), `can_approve_discount` (selling a plan below its price), `can_see_profit` (profitability report, paid-vs-used), `can_view_client_info` (plan price and payment history; without it, staff see only the "Member" badge), `can_create_payment_links` (sending pay links).

All new write routes go through `require_perm(...)` and are covered by the route permission gate test.

## 11. Reports

**Plan profitability** (needs `can_see_profit`), per plan and per member, for a chosen period:
- money in (paid plan invoices; recognised revenue if spreading is on),
- value given (usage rows at list price + plan discounts),
- margin and "uses its plan" ratio,
- members vs non-members: visits per year, average spend outside the plan, retention.

## 12. API

Staff (`/api/memberships`):
- `GET/POST/PUT /plans`, `GET /plans/templates`
- `GET /` (filters: status, plan, manager, renewing_within), `GET /{id}`
- `POST /` (sell), `POST /{id}/billing`, `/pause`, `/resume`, `/cancel`, `/transfer`, `/pay-link`
- `GET /eligible?order_id=` (checkout prompt numbers)
- `GET /reports/profitability`

Existing endpoints extended: order line save/close (coverage check, §7.4), client and device detail (membership block), self check-in public page (member greeting, no plan prices exposed).

Daily job (existing scheduler): create monthly invoices, renewal invoices, apply grace/lapse, send item reminders, post monthly revenue moves when spreading is on. Idempotent per day.

## 13. Self check-in tie-in

When a check-in matches an asset with an active membership, the confirmation says "Your Boat Care covers this visit" (no prices), and the Walk-ins row shows a *Member* badge. Converting the check-in applies coverage per §7.4.

## 14. Testing

Backend:
- Plan CRUD + validation (at least one price; item counts ≥ 1).
- Sell yearly / monthly → invoice line, membership, period, system tag.
- Coverage: covered only for listed services with count left; count used on close, not on add; second close doesn't double-count; a forged `membership_id` is rejected; paused doesn't cover.
- Discount item vs manual discount (larger wins).
- Daily job: monthly invoice creation, renewal invoice timing, grace → paused, lapse → lapsed + tag removed, payment restores active. Idempotent re-run.
- Cancel with refund maths; transfer; owner change prompt.
- Spread revenue on/off: monthly recognition amounts sum to the paid amount.
- Permissions: each new route denied without its permission; route gate test passes.
- Tenant isolation on every list/detail route.

Frontend:
- Plan editor, Contacts › Memberships filters, client and asset cards, covered lines on the work order, checkout prompt thresholds and 90-day snooze, settings page.

## 15. Phasing

**Phase 1 (this build):** everything above, with monthly billing via invoice + pay link.

**Phase 2:** saved-card auto-charge for monthly plans (card on file with the payment provider, retries, card-expiring notices); joining from the customer portal.

**Phase 3:** fleet plans — one membership over several assets (`membership_assets` already allows it), shared or per-asset counts, per-asset pricing, one bill.

## 16. Open questions

1. Should the checkout threshold (25% of the yearly price) be a shop setting? *Proposed: fixed in phase 1, revisit with data.*
2. Do plan years follow the join date (proposed) or a fixed season (e.g. marine Apr–Mar)?
3. Should a cancelled member's system tag change to "Former member" (useful for win-back campaigns) rather than disappear?
