# Council seat: shop owner / manager (6-person phone and laptop shop)

My lens: I pay per seat and I have 6 people, 2 of them part-time. I want fewer phone calls, fewer devices rotting on the shelf, and fewer quotes going cold. I don't want another screen my staff have to "keep up to date". If a feature needs someone to maintain it, it'll be dead within a month.

What Tekly already has that this proposal overlaps with (checked on `origin/roadmap/orderry-parity`):
- **Customer notification rules** (`backend/services/notification_rules.py`): status-change triggers, **delayed sends**, working-hours hold. "Ready text day 0, reminder day 3" is already possible with these.
- **Status automations** (`backend/routers/automations.py`): status_changed / estimate_approved / payment_received / order_overdue → create_task / assign / notify.
- **Per-status time limits plus an SLA sweep** (`sweep_status_sla`, `status_sla_overdue` on orders): this already *is* a "stuck in this status too long" engine.
- **Missed-call inquiries with an SLA and a self-closing call-back task** (`leads.py`).
- **Work-order checklists** (`ChecklistTemplate` / `ChecklistStep`, `requires_checklist` gating completion).
- **Purchasing cockpit** and part receiving (inventory/transfers), **home feed** open-task signal, the **Go "My tasks" tab** with date groups.

So a lot of "new" here is really *wiring existing pieces together*. That's good news, because it's cheaper, but it means some features must be built as **extensions of what we have, not parallel systems**.

## Verdicts

| # | Feature | Verdict | Why (owner seat) |
|---|---|---|---|
| P0 | Bug fixes (valid status, notify/pay all assignees, kill legacy API, priority enum, editable panel, server paging, visibility permission) | **KEEP, do it now** | Unpaid commission and a part-timer reading my notes are trust killers. Non-negotiable, and it's days of work. |
| P0 | Soft delete / 30-day bin | **KEEP** | Cheap insurance. Folds into P0. |
| P0 | "Mine" default + search | **KEEP** | That's the only reason anyone opens the list. |
| P1 | Follow-up recipes (ready-not-collected, quote-no-reply) | **KEEP, but MERGE** into the existing automations and notification rules | The money feature. But the customer *texts* already exist as delayed notification rules. A recipe should be a **preset bundle**: existing delayed SMS rule + a staff call task + auto-close when the status changes. Don't build a second texting engine, or my customers get double texts. |
| P1 | Supplier-return (RMA) recipe | **KEEP (move up to P1)** | 3-day damage windows and 20–50% restocking fees are real money I lose today. Higher value than the TV. |
| P1 | Part-ordered recipe ("part arrived → resume / tell customer") | **KEEP, MERGE** with the purchasing cockpit / part receiving | The trigger must be the existing "receive part" action, not a new button. |
| P1 | "Next step" card on the work order | **KEEP** | This is where my staff already live. Strictly better than a separate Tasks page. |
| P1 | Timeline of what was sent | **MERGE** with the existing order activity feed / NotificationLog | The order already has a timeline. Show recipe steps there; don't add a second one. |
| P1 | Preview before switching on | **KEEP** | It's the thing that stops me getting angry calls from customers on day one. Cheap, as it's a dry-run query. |
| P1 | Morning digest email | **MERGE** with the home feed / dashboard; email for owner only, opt-in for staff | My techs won't read an 8am email. I will. Staff get it as the top of My day, not in their inbox. |
| P2 | My day: Today / Upcoming / Later | **KEEP, slimmed** | Mostly a port of what Go already does. Web needs to match. Do this, but don't spend 3 weeks on it. |
| P2 | Catch-up group, "Move all to today" | **MERGE** into My day | It's just the overdue section with one button. Not a feature, a detail. |
| P2 | Snooze until event (customer replies, part arrives) | **LATER** | Clever, but the recipes already auto-close on those events. Manual snooze-until-event is a power-user toy. Revisit if beta shops ask. |
| P2 | Separate show-from vs due dates | **CUT** | Two dates per task is how my staff end up ignoring both. A plain snooze (pick a date) covers 95% of it. |
| P2 | Per-task checklist | **MERGE** with existing work-order checklists (`ChecklistTemplate`) | We already have checklists. Reuse that component; don't create a second checklist model. |
| P2 | Quick-add chips (date, @person, #job) | **KEEP (simple version)** | Go already has Today/Tomorrow/Me chips. Bring the same to web. No NLP needed for this. |
| P3 | Counter queue | **MERGE** into My day as a "Counter" filter (customer-contact steps, unclaimed) | Useful, but in a 6-person shop it's the same 2 people. One filtered list plus a Take button, not a new page. |
| P3 | Rotating "on counter today" owner | **CUT (or tie to the rota/clock-in if it exists)** | I already know who's on the counter. If anything, auto-assign to whoever is clocked in at the front, with no rotation settings to maintain. |
| P3 | Bench queue | **MERGE** with the work-order list filtered by "my jobs" plus Go's tech view | Techs already have their jobs list. Pin "part arrived, resume" there. No new screen. |
| P3 | Handoff Accept / Not now | **LATER** | It solves a real handoff gap in big shops. In mine, Priya shouts across the room. Extra click for every job = staff skip it. |
| P3 | Turnaround clock that pauses on waiting statuses | **MERGE** with the existing per-status time limits / `sweep_status_sla` | It's a config flag ("this status doesn't count"), not a feature. Good for B2B contracts, so it's cheap to add there. |
| P3 | Owner stuck-jobs view (money waiting, past promised, oldest) | **KEEP, MERGE** with reports/dashboard | This is the one screen I'd open daily. Build it as a dashboard/report card on top of status SLA data, not as part of Tasks. "£ waiting on customers" is the killer number. |
| P3 | Shop TV / wall view | **LATER** | Nice demo, but few 6-person shops will mount a screen. Revisit after the owner view exists, since it's the same data in big type. |
| P4 | Repeating shift checklists (opening/closing) | **KEEP, small** | Real need, and Orderry can't do it. But keep it simple: repeat daily/weekly, assign to a role, tick. Reuse the checklist model. |
| P4 | Two-person steps (cash count) | **CUT** | Edge case. Cash drawer counts belong in the cash module (it has its own open/close). Two names on a tick box don't prove anything. |
| P4 | Checklist templates / ready-made examples | **MERGE** with existing `ChecklistTemplate` | Already exists for work orders. Add 3 starter templates (open, close, weekly stock) and stop there. |
| P4 | Owner view of completed routines | **MERGE** into the owner stuck-jobs view | One owner screen, not two. |
| P5 | Natural-language quick add with chips | **LATER** | Fun, but my staff type "call jane" and pick a date. Chip-based quick add (P2) gets 90% of it. |
| P5 | AI assignee suggestion with reason | **CUT** | In a 6-person shop the answer is always "the tech who has the job". That's a default rule, not AI. Don't charge me for it. |
| P5 | AI due-date / duplicate suggestions | **CUT** | Recipes already dedupe via `source_ref`. |
| P5 | Voice drafts on Go | **LATER** | 62% accuracy means more cleanup than typing. Revisit when it's free and good. |
| P5 | Cmd/Ctrl+K create | **KEEP if near-free** | The ⌘K box exists. Adding "create task" to it is fine; don't build a parser for it. |
| — | Rename "Tasks" → "Next steps" | **CUT** | Every shop calls them tasks. Renaming confuses migrating Orderry users and our own help docs. Put "Next step" on the job card only. |
| — | Notification rules (push only on assign/@mention/breach, rest to inbox) | **KEEP** | Fewer pings = staff don't mute Tekly entirely. |

## Redundancy flags

1. **Recipe texts vs notification rules.** Both send delayed customer SMS on status change. One engine only, or customers get two texts. The recipe should configure notification rules, not duplicate them. Also check that existing delayed sends **cancel when the status moves on**. If they don't, that's a bug to fix in P0/P1.
2. **Recipes vs status automations.** `create_task` on status_changed already exists. Recipes = automations + auto-close + escalation steps. Extend `AutomationRule`; don't make a parallel "recipes" table that shows up in a different settings page.
3. **"Stuck too long" vs status SLA.** Per-status `time_limit_min` and `sweep_status_sla` already flag stuck jobs. The owner view, age chips, the pausing clock and the TV all read the same data, so build them on it.
4. **Timeline vs order activity feed.** Same thing twice.
5. **Per-task checklist, sign-off checklist and routine templates vs `ChecklistTemplate`.** Three proposals, one existing model.
6. **Bench queue vs work-order list "my jobs" and Go.** Same list.
7. **Counter queue vs My day and the inquiries hub.** Missed calls already live in Inquiries with an SLA. Don't show them in three places.
8. **Morning digest vs home feed.** Same counts, different delivery.
9. **Snooze-until-event vs recipe auto-close.** Overlapping wake/close mechanisms. Pick one mental model.

## Top 5 by real value to my shop

1. **Ready-not-collected recipe** (merged with notification rules, preview before switch-on, logged notices). Shelf space and cash on the shelf.
2. **Quote-not-answered recipe.** Every cold quote is lost revenue, and right now nobody chases them.
3. **Owner "money waiting / stuck jobs" view** on the dashboard. One number I'd look at every morning.
4. **Phase 0 fixes** (commission to all assignees, visibility, editable tasks). Without them, staff won't trust anything else.
5. **Supplier RMA recipe with deadlines.** Missed 3-day windows and restocking fees are pure loss.

(Close 6th: the Next-step card on the work order, which is the delivery surface for 1, 2 and 5.)

## Missing features I'd pay for

- **"Is my device ready?" deflection**: a customer status link or auto-reply to inbound SMS with the live job status. The report says this is the #1 inbound call (2+ hrs/day). Tasks don't fix that; deflection does. Check whether the portal links already cover it and push them in the ready/quote texts.
- **Quote approval by link** (approve/decline from the SMS) so the "quote not answered" task closes itself without a call.
- **Abandoned-device workflow end state**: after the final notice, one click to mark abandoned / move to resale stock, with the notice log attached for legal cover.
- **Per-recipe results report**: "this month the pickup recipe recovered £X / Y devices collected after a reminder". That's what proves the feature is worth my subscription, and it's what sales should demo.
- **Supplier warranty tracking on the part itself** (supplier, purchase date, claim window), so the RMA recipe knows the deadline without anyone typing it.

## Bottom line

Ship P0 plus two recipes built **on top of** notification rules and automations, plus the Next-step card and an owner money-waiting card. That's roughly 3 weeks and covers 80% of the value. Everything with "queue", "TV", "AI" or "two dates" in the name waits until beta shops ask for it by name.
