# Automated customer follow-ups in repair-shop and field-service software (market scan, Nov 2026)

Scope: Orderry / RO App, RepairDesk, RepairShopr, CellSmart, RepairQ, mHelpDesk, Jobber, Housecall Pro, ServiceTitan, Tekmetric, Shopmonkey, AutoLeap.
Method: about 20 web searches and fetches. Most sources are vendor help centers and marketing pages; pricing comes mostly from aggregators, which often disagree with each other. "Unknown" means I found no source. It does not mean the feature is missing.

## Comparison matrix

Legend: Y = documented, P = partial / manual / add-on / only via Zapier, N = documented absence, ? = no source found.

| Capability | Orderry / RO App | RepairDesk | RepairShopr | CellSmart | RepairQ | mHelpDesk | Jobber | Housecall Pro | ServiceTitan | Tekmetric | Shopmonkey | AutoLeap |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Status-change SMS/email to customer | Y [1] | Y [5] | Y (Zapier + automations) [8] | Y [11] | Y [12] | ? | Y [13] | Y [15] | Y | Y | Y | Y |
| Uncollected "ready for pickup" reminders (repeat after N days) | P: one delay after trigger; no repeating or "X days in status" reminder documented [2] | P: blog says it re-sends if still uncollected; interval and config not documented [6] | Y: "Ticket Automations" fire when a ticket sits N days in a status (e.g. 7 days in "waiting for pickup") [8][9] | P: one-shot ready text [11] | ? | ? | n/a | n/a | n/a | n/a | n/a | n/a |
| Abandoned-device / storage-fee notice ladder | N/? | N: blog only advises "set a storage policy" [7] | P: could be built from N-day automations [9] | ? | ? | ? | n/a | n/a | n/a | n/a | n/a | n/a |
| Quote/estimate auto follow-up after N days | N: estimate status triggers only, no estimate reminders [2] | P: "approval requests" reminders mentioned in blog [7] | P: diagnostic email reminders daily or hourly [10] | ? | ? | ? | Y: Quote follow-ups for "awaiting response" quotes, plan-gated [13] | Y: Pipeline automates follow-up when a customer doesn't act [15] | Y: Marketing Pro unsold-estimate campaigns [17] | Y: declined-work follow-up (Marketing add-on) [19] | Y: declined-service follow-up (CRM Essentials add-on) [21] | Y: deferred-service reminders [22] |
| Approve quote from link | Y: public page, accept/decline/pay updates status automatically [1] | Y: "approval requests" [7] | ? | ? | ? | ? | Y: client hub [14] | Y | Y | Y: line-item approve and decline by text link, e-signature [20] | Y | Y |
| Deposit collected on approval | P: pay link on public page [1] | ? | ? | ? | ? | ? | Y: "Approve and Pay Deposit" [14] | ? | ? | ? | ? | ? |
| Customer self-service status page or tracking link | Y: public work-order page [1] | Y: Repair Tracker widget (ticket ID + last name) and Customer Portal [23] | Y: customer portal, with per-ticket hide-status option [10] | P: ambiguous [11] | Y: Qsite portal [12] | Y: portal shows in-flight job status [24] | Y: client hub [14] | Y | Y | Y | ? | ? |
| Evidence that the status page cuts calls | N: vendor claim only | P: vendor says it "cuts down on follow-up calls", no numbers [23] | N | N | N | N | N | N | N | N | N | N |
| Customer messages and internal staff tasks in one sequence | P: same automation engine can message clients and employees and create tasks, but no single sequence builder documented [1] | N | P: an automation can email and change status together [9] | N | N | N | ? | P: Pipeline workflows | P | N | N | N |
| Sequence auto-stops when the job moves on | ? (only "skip if time already passed") [2] | ? | P: status-based, so it stops when the status changes (inferred) | ? | ? | ? | Y (implied: only "awaiting response" quotes) [13] | ? | Y: stop trigger "Job Booked" [17][18] | ? | ? | ? |
| "Revenue recovered" / follow-up results reporting | N | N | N | N | N | N | ? | P: estimate-conversion dashboards [16] | Y: revenue attributed per campaign [17][18] | ? | ? | ? |
| SLA / turnaround timer that pauses in waiting states | ? | ? | P: SLA timers that can be paused manually, per ticket or globally; no auto-pause by status found [25] | ? | ? | ? | n/a | n/a | ? | ? | ? | ? |
| Owner "money waiting / WIP aging" dashboard | ? | ? | ? | ? | ? | ? | ? | P | P | ? | ? | ? |
| Shop TV / wallboard | ? | ? | ? | ? | ? | ? | n/a | n/a | ? | ? | ? | Y: Work Board fullscreen "perfect for display on a shop TV" [22] |
| Price (monthly, approx.) | ? | $99 Essential / $149 Growth / Advanced on quote [26] | ? (portal listed on "Big Chain" plan) [26] | ? | ? | ? | Core ~$49; quote follow-ups on Grow per vendor announcement, aggregators conflict [13][27] | $79 / $189 / $329 (Basic / Essentials / MAX) [28] | Quote only; Marketing Pro is an add-on | $199–$439/shop; marketing is an add-on, price unknown [29] | ~$179–$475 (aggregators); CRM Essentials is an add-on [21][29] | $179–$449 [29] |

## Is automated pickup follow-up (multi-step, stop conditions, abandonment) table stakes?

### Takeaway
A single "ready for pickup" message on a status change is universal. Repeating "still not collected after N days" reminders exist only in a rough form: RepairShopr's N-days-in-status automations and RepairDesk's vaguely documented resend. No product I found documents a configurable multi-step ladder that runs from ready, through reminders, a storage-fee notice and a final notice, to abandoned, and stops automatically when the device is paid for or collected.

### Cited Findings
- Orderry notifications fire on "Work order created", "status changed", and "status changed to [X]". Reminders run only relative to a scheduled date and time. Delayed sending is a one-time delay after the trigger (e.g. an SMS "1 hour after closing a Work Order"). The article does not describe "X days after status" or repeating reminders, and it describes no estimate reminders. — [1 Orderry help: SMS and email notifications](https://help.orderry.com/en/articles/9134442-sms-and-email-notifications-for-clients)
- RO App automations: SMS, email or Telegram, or the client's original chat (Viber, WhatsApp, Instagram), go to clients and employees on events. Automations can create tasks, generate public pages and payment links, and accept, decline or pay on the public page, which updates status. Scheduled messages are possible (a review request "one week after"). Stop conditions are documented only for stock reservations, and no plan tier is stated. — [2 RO App automations](https://help.roapp.io/en/articles/15820286-ro-app-automations-streamline-daily-workflows-easily)
- RepairDesk's blog says it sends automated pickup reminders when a completed ticket stays uncollected and resends after a certain period. The interval is not specified. — [5/6 RepairDesk blog, manual follow-ups](https://blog.repairdesk.co/2026/06/11/the-hidden-cost-of-manual-follow-ups-in-repair-shops/)
- A RepairDesk article on late pickups covers storage only as advice: "set a storage policy... share it at drop-off, repeat it in ready messages". Abandoned devices are not mentioned. — [7 RepairDesk blog, no-shows and late pickups](https://blog.repairdesk.co/2026/05/26/7-ways-to-cut-no-shows-and-late-pickups-in-a-busy-repair-shop/)
- RepairShopr "Ticket Automations": e.g. a ticket in "waiting for customer" that sits untouched for N days triggers an email and a status change. A user set up 7-day idle reminders for "waiting for pickup". — [8 RepairShopr blog](https://repairshopr.com/?p=6029); [9 RepairShopr feedback forum](https://feedback.repairshopr.com/forums/165658-general/suggestions/15972691-ticket-reminders-notification-for-stale-tickets)
- CellSmart: a one-shot text when the device is fixed, with an option to send it to an alternate number if the customer left the SIM in the device. — [11 CellSmart repair alerts](https://www.cellsmartpos.com/blog/2018/04/30/repair-alerts)
- RepairQ: an automated notification system with SMS and email per location, driven by ticket statuses and purchase-order statuses. — [12 RepairQ](https://repairq.io/repair)
- Real shop policies show what an abandonment ladder looks like: a $5/day fee from day 31, a final reminder at 30 days, and the device treated as abandoned 10 days after the final reminder. Another shop uses 90 days. — [Sour Apple Repair policy](https://sourapplerepair.reamaze.com/kb/policies/device-abandonment-policy); [MD Repairs terms](https://mdrepairs.com/terms-of-service)
- Legal risk: a Colorado shop charging up to $25/day storage had 47 suits thrown out. The judge called the fees "unconscionable". — [Denver7](https://www.denver7.com/news/investigations/customers-pay-colorado-electronics-repair-shop-sues-dozens-of-its-online-customers)

### Inferences
- Table stakes: a status-change notification, with a template, by SMS or email.
- Differentiated: a real escalating ladder with paid/collected stop conditions, configurable storage-fee notices with sensible defaults and caps, and an "abandoned" end state. The Colorado case argues for capped, clearly disclosed fees with a notice trail, which software can produce automatically.

### Gaps
- RepairDesk's exact reminder settings (interval, count) are not in its help center results.
- RepairQ and CellSmart docs on repeating reminders were not found.

## Quote/estimate follow-ups, approve-from-link, deposit

### Takeaway
Field-service (Jobber, Housecall Pro, ServiceTitan) and auto-repair tools (Tekmetric, Shopmonkey, AutoLeap) all have automated estimate and declined-work follow-ups. Most sit on a higher tier or a paid add-on. Device-repair tools (Orderry, RepairDesk, RepairShopr, CellSmart) have approve-from-link but weak or undocumented automatic quote chasing. That gap is an opening in Tekly's own vertical.

### Cited Findings
- Jobber Quote follow-ups: automatic emails and texts to prospects whose quotes are in the "awaiting response" status, on a schedule you choose. The vendor announcement says Grow plan, while aggregators conflict. Manual quote reminders (calendar only) are on all plans. — [13 Jobber](https://academy.getjobber.com/updates/announcing-quote-follow-ups/) (now redirects); [Jobber automations](https://www.getjobber.com/features/automations/)
- Jobber client hub: with Jobber Payments the button becomes "Approve and Pay Deposit", with an e-signature and the option to request changes. — [14 Jobber quote approvals](https://help.getjobber.com/hc/en-us/articles/115012715008)
- Housecall Pro Pipeline: "automate following up with leads from estimate to invoice", with custom follow-up emails to unresponsive leads. — [15 Housecall Pro sales pipeline automation](https://www.housecallpro.com/resources/sales-pipeline-automation-for-contractors/)
- ServiceTitan Marketing Pro: an unsold-estimate campaign stops when the job is booked ("stop trigger... Job Booked"), and revenue is then attributed to the campaign. An AI SMS Agent follows up on non-converted customers. — [17 ServiceTitan blog](https://www.servicetitan.com/blog/webinar-recap-ai-marketing-automation-crawl-walk-run); [18 Marketing Pro overview](https://help.servicetitan.com/docs/overview-using-servicetitan-marketing-pro)
- Tekmetric: the customer gets a text link and can approve or decline individual line items with a digital signature. Declined jobs are logged. The Marketing add-on automates declined-service follow-ups. — [20 Tekmetric digital signature](https://support.tekmetric.com/hc/en-us/articles/4421125978391-Digital-Signature-Authorization-Process); [19 Tekmetric DVI](https://tours.tekmetric.com/answers/sell-more-work-with-automotive-digital-inspection-software)
- Shopmonkey CRM Essentials sends a follow-up months after a declined service: "set it up once and it runs on its own". — [21 Shopmonkey](https://www.shopmonkey.io/demo-crm-essentials)
- AutoLeap campaigns include deferred-service reminders. — [22 AutoLeap CRM](https://autoleap.com/features/crm/)

### Inferences
- Approve-from-link is table stakes across all categories. Tekly must have it, including line-item approve and decline.
- Approve plus deposit in one step is common in field service (Jobber) and rare in device repair. That makes it a good, cheap differentiator.

### Gaps
- Tier placement for Jobber is contradictory across sources.
- No documented number of follow-up steps for any vendor.

## Customer status page and call reduction

### Takeaway
A self-service status page is table stakes: RepairDesk, RepairShopr, RepairQ, Orderry/RO App, Jobber and mHelpDesk all have one. No vendor publishes measured call-reduction data. Only qualitative claims exist.

### Cited Findings
- RepairDesk Repair Tracker widget: the customer enters ticket ID and last name to see status and download invoices. It is embeddable per location. The vendor says it "cuts down on follow-up calls". There is also a separate Customer Portal. — [23 RepairDesk help](https://help.repairdesk.co/portal/en/kb/articles/repair-tracker-widget)
- RepairQ Qsite portal: real-time repair status. — [12 RepairQ](https://repairq.io/repair)
- mHelpDesk portal: service requests, history, bill pay, and status for in-flight jobs. — [24 mHelpDesk](https://mhelpdesk.com/features/customer-portal)
- RepairDesk's blog says staff spend "2 hours a day on the phone". This is a vendor claim with no method given. — [RepairDesk blog](https://blog.repairdesk.co/2026/05/20/the-real-reason-your-staff-spends-2-hours-a-day-on-the-phone/)

### Inferences
- A no-login, single-link tracking page sent automatically in every message is the expected baseline. A visible timeline with an ETA and "what we're waiting on" would go beyond it.

### Gaps
- No independent statistics on call reduction.

## Mixed customer and staff sequences, auto-stop, and results reporting

### Takeaway
Nobody in the device-repair segment documents one sequence that mixes customer messages and internal staff tasks and auto-stops when the job moves on. ServiceTitan is the only vendor that documents both a stop-on-conversion trigger and revenue attribution, and it does this for marketing campaigns, not job operations. "Revenue recovered" reporting is absent in device-repair tools.

### Cited Findings
- RO App's engine can send to clients and employees and create tasks, but each automation is a single trigger-to-action rule. — [2](https://help.roapp.io/en/articles/15820286-ro-app-automations-streamline-daily-workflows-easily)
- ServiceTitan: stop trigger set to Job Booked, and the dashboard shows revenue, bookings and sold jobs by campaign. — [18](https://help.servicetitan.com/docs/overview-using-servicetitan-marketing-pro)
- Housecall Pro: dashboards with "converted estimates". — [16 Housecall Pro advanced reporting](https://housecallpro.com/features/advanced-reporting)

### Inferences
- A per-job follow-up sequence in Tekly would be genuinely differentiated: customer SMS on day 0, 3 and 7, a staff "call them" task on day 10, and a storage notice on day 30, all cancelled when the device is paid or collected. A "recovered: $X from N devices collected after reminder" report would also be new.

### Gaps
- Housecall Pro Pipeline stop conditions were not found.

## SLA timers, WIP / money-waiting dashboards, shop TV, tiers

### Takeaway
SLA pause is generic helpdesk territory. RepairShopr has SLA timers with a manual pause only. Repair-specific "money waiting / WIP aging" dashboards are not documented by any vendor I checked. A shop TV display is documented only by AutoLeap.

### Cited Findings
- RepairShopr: an SLA index with a "Status" column. You can pause individual SLA timers or pause all SLAs globally, which is manual. — [25 RepairShopr SLA](https://www.repairshopr.com/blog/posts/feature-friday-business-hours-major-service-level-agreement-revisions)
- Helpdesks such as Desk365 and Jira Service Management auto-pause SLAs per status ("Waiting for customer"). This is a known pattern that hasn't reached repair POS tools. — [Desk365](https://help.desk365.io/en/articles/how-to-pause-the-sla-timer/)
- AutoLeap Work Board fullscreen mode is "perfect for display on a shop TV". — [22 AutoLeap](https://autoleap.com/?p=107525)
- Pricing: RepairDesk $99 / $149 plus Advanced on quote [26 G2](https://www.g2.com/products/repairdesk-repairdesk/pricing); Housecall Pro $79 / $189 / $329 [28 HCP llm-info](https://www.housecallpro.com/llm-info/); Tekmetric $199–$439 per shop, AutoLeap $179–$449, Shopmonkey ~$179–$475, all from aggregators [29 xpay Tekmetric](https://www.xpay.sh/saas-pricing/tekmetric/), [xpay AutoLeap](https://www.xpay.sh/saas-pricing/autoleap/), [toolradar Shopmonkey](https://toolradar.com/tools/shopmonkey/pricing); Jobber Core ~$49, with higher tiers conflicting [27 getonecrew](https://www.getonecrew.com/post/jobber-pricing).

### Inferences
- Pattern: the advanced follow-up features (Jobber quote follow-ups, HCP MAX, Tekmetric Marketing, Shopmonkey CRM Essentials, ServiceTitan Marketing Pro) are gated on higher tiers or paid add-ons. Including them in a base plan is a pricing differentiator.

### Gaps
- ServiceTitan pricing is quote-only. Orderry, RepairShopr, CellSmart and RepairQ prices were not verified this pass.

## Conclusion for Tekly

**Table stakes (must have):**
1. Status-triggered SMS, email and messenger notifications with templates.
2. A no-login customer tracking link or status page.
3. Approve or decline an estimate from a link with an e-signature (line-item approval is expected in auto repair).
4. A pay link in the "ready" message.
5. Appointment reminders.

**Rare (a few have it, usually on higher tiers or add-ons):**
1. Automatic quote or declined-work follow-ups after N days (Jobber, HCP, ServiceTitan, Tekmetric, Shopmonkey, AutoLeap; weak in device-repair tools).
2. "N days stuck in status" reminders (RepairShopr).
3. Approve plus deposit in one step (Jobber).
4. Stop-on-conversion plus revenue attribution (ServiceTitan only, marketing campaigns).
5. A shop TV board (AutoLeap).
6. SLA timers (RepairShopr, manual pause).

**Nobody documents:**
1. A multi-step pickup and abandonment ladder (ready, reminders, storage-fee notice, final notice, abandoned) with automatic stop on paid or collected.
2. One sequence that mixes customer messages and staff tasks and is cancelled when the job moves on.
3. A "revenue recovered by follow-ups" report for repair shops.
4. Turnaround timers that auto-pause in waiting states (waiting for parts or for customer approval) in a repair POS.
5. An owner "money waiting / WIP aging" view (finished but uncollected value, quotes awaiting approval, by age).
6. Published call-reduction metrics from a tracking page.

These are where Tekly can credibly differentiate, ideally included in the base plan rather than sold as an add-on.
