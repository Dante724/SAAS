# Council seat: front-counter staff + bench technician

Who's speaking: **Priya** (front counter: phone, intake, quotes, pickups, payment, usually serving a walk-in while doing all of that) and **Leo** (bench: hands in a phone, gloves/ESD strap, looks at a screen between steps, hates being pulled away).

What we care about: fewer clicks, fewer pings, no nagging, no typing the same thing twice. Our test for every feature: *does it remove a thing I currently do, or add one?*

## Bottom line

The core idea is right. Tasks that make themselves from job status and close themselves are the first task feature that removes work instead of adding it. The plan's problem is **too many places to look**. Counting the proposal as written, a counter person could have: My day, Counter queue, Shop queues board, Next-step card on the job, the job task panel, the full list, the inbox, the morning email, the Checklists page and the shop TV. That's ten surfaces for about 15 things a day. We want **two**: *my list* and *the job*. Everything else should be a filter or a layout of the same data, for the owner.

---

## Verdict table

| # | Feature | Verdict | Reason (daily use) |
|---|---|---|---|
| **Phase 0** | | | |
| 0.1 | Valid status for auto tasks (+ "Auto" tag) | **KEEP** | Right now auto tasks vanish from the filters. A task you can't find is worse than none. |
| 0.2 | "Mine" as default + search by text/job/customer | **KEEP** | First thing we'd use. Searching "Jane" or "1042" is how we actually think. |
| 0.3 | Links show job + customer together | **KEEP** | Saves a click into the job just to see who to call. |
| 0.4 | Editable panel (title/due/priority/notes) + delete to 30-day bin | **KEEP** | Retyping a task to fix a typo is the definition of double entry. |
| 0.5 | Notify + pay every assignee | **KEEP** | But notify once per assignment, not per edit. More assignees should not mean more pings per change. |
| 0.6 | Delete legacy API, priority enum, paging, visibility permission | **KEEP** | Invisible to us, which is what we want. Visibility: the part-timer shouldn't see owner to-dos. |
| **Phase 1** | | | |
| 1.1 | Follow-up recipes (ready-not-collected, quote-no-reply) | **KEEP** | The single best thing in the plan. It replaces the whiteboard and my memory. Must close itself: if I still have to tick "done" after the customer collects, it's double entry. |
| 1.2 | Next-step card on the work order | **KEEP, MERGE** with the job's task panel (0.3/"On the job") | One "Next steps" block on the job, not a card *and* a task list. Auto and manual steps in the same block. |
| 1.3 | Timeline of what was sent | **MERGE** into the Next-step card | It's the card's history, not a separate thing. It stops two of us texting Jane on the same day, so it must be there, but as 3–4 lines inside the card, not a new tab. |
| 1.4 | Preview before switch-on | **KEEP (owner only)** | We never see it. It protects us from a Monday morning of 40 bogus tasks and angry customers. |
| 1.5 | Morning digest email | **CUT the email / MERGE** into the top line of My day | Nobody at the counter reads email at 8:00; we open Tekly. Put the same summary as a strip at the top of My day. Owner can opt in to the email. Techs: off by default, and no £ figures for them. |
| **Phase 2** | | | |
| 2.1 | My day: Today / Upcoming / Later | **KEEP** (this is *the* list) | One place to look, same on web and Go. Upcoming and Later should be collapsed by default; I only work Today. |
| 2.2 | Catch-up group with "Move all to today" | **KEEP** | Calm, one click. Much better than a red wall. Don't add a counter or streak. |
| 2.3 | Snooze until an event (reply / part arrives) | **KEEP, mostly automatic** | Recipe steps should already wake on their event without me setting it. Manual "until customer replies" is fine as an extra on the snooze menu. Swipe on Go. |
| 2.4 | Separate "show from" and "due" dates | **MERGE** into snooze | Two date fields on every task is a form I won't fill. Show only Due. "Show from" gets set by Snooze, and by recipes. Don't put both fields on the create form. |
| 2.5 | Per-task checklist | **CUT for manual tasks / MERGE** with recipe steps and the job sign-off checklist | The RMA example (photos sent, RMA number, posted back) is the supplier-return recipe's steps. The QC example is the job sign-off. A generic checklist on any task is to-do-app sprawl and a third place for ticks. |
| 2.6 | Quick add with chips (date, @person, #job) | **KEEP, MERGE** with 5.1 | Same feature twice. Ship one quick add. |
| **Phase 3** | | | |
| 3.1 | Counter queue | **MERGE** into My day as a "Counter" filter/group | For the person on the counter, the counter queue *is* their day. Make unclaimed customer-contact steps appear in My day for whoever is on counter, with Take/Snooze on hover. No separate page. |
| 3.2 | Rotating counter owner | **LATER** (make it simple) | Most shops have 1–2 counter people. Default: unclaimed counter steps go to everyone with the counter role. Add rotation when a 5+ staff shop asks for it. Setting up a rota is admin nobody does. |
| 3.3 | Bench queue | **MERGE** into My day for techs (sorted by promised time) | A tech's My day *is* the bench queue. "Part arrived, resume" pinned on top is the only extra. |
| 3.4 | Handoff "Accept" | **KEEP the signal, CUT the button** | An extra tap on every handoff, every day. Treat opening the job, starting the timer, or changing status as Accept. Keep a "New for you" marker until then, and tell the counter if nobody has touched it in X minutes. Keep "Not now / hand back". |
| 3.5 | Turnaround clock pauses on waiting | **KEEP, no input from us** | Fair to techs: we stop looking late while the part's in the post. Driven by status only. Show it as a quiet chip, not a countdown. B2B only unless the shop turns it on. |
| 3.6 | Owner stuck-jobs view | **KEEP (owner only), MERGE** with Shop TV | Same data, two layouts. We don't need the money totals on our screens. |
| 3.7 | Shop TV | **LATER, MERGE** with owner view as a "wall" layout | Nice whiteboard replacement, but it's the owner board in big type. Keep £ off the TV: customers can see into the back room in many shops. Show job counts and late jobs only. |
| **Phase 4** | | | |
| 4.1 | Repeating shift checklists | **KEEP** | Real job, kills the whiteboard by the till. Part-timers only need this. Show today's list inside My day, not on a separate Checklists page. Remove items that duplicate recipes ("Return missed calls", "Check pickups over 3 days"): those already show in My day. A checklist that points to another list is the same work twice. |
| 4.2 | Two-person steps | **CUT** (or LATER as an optional "checked by" name) | Two people logging in on one till to tick one box adds friction. If the owner wants it, let the person ticking pick the second name from a dropdown. |
| 4.3 | Templates with examples | **KEEP (owner only)** | We never touch it. Ship 3 examples ready to switch on so nobody builds from scratch. |
| **Phase 5** | | | |
| 5.1 | Natural-language quick add with chips | **MERGE** with 2.6, then **KEEP** | Typing "call jane tmrw 3pm 1042" is the fastest way for the counter. One quick add, one chip parser, in ⌘K and on the + button. |
| 5.2 | AI assignee suggestion with reason | **CUT** (replace with a plain rule) | In a 3–6 person shop, the assignee is obvious: the job's tech, or whoever's on counter. A fixed default rule does it with zero UI. An AI box with a "Why" line is noise to read on every task. |
| 5.3 | Voice drafts on Go | **LATER** | Appealing for a tech with hands full. But a draft you must review later is double entry, and ~62% accuracy means fixing 4 in 10. Revisit when accuracy is better; tie it to a job ("note on this job"), not a free task. |

---

## Redundancy calls

| Overlap | What we'd do |
|---|---|
| **My day vs Counter queue vs Bench queue** | One list: **My day**. It changes with role: the counter person also sees unclaimed customer-contact steps; a tech sees their jobs by promised time with "part arrived" pinned. "Everyone's day" is an owner/lead filter on the same screen. No separate Shop queues page for staff. |
| **Morning digest vs owner stuck-jobs vs Shop TV** | One data set, three layouts at most, and only one is ours. Staff: a summary strip at the top of My day. Owner: stuck-jobs view (+ opt-in email). TV: a large-type layout of the owner view, with no £ shown, later. |
| **Per-task checklist vs shift checklists vs job sign-off** | Two kinds, not three. **Routine checklists** (shift/weekly) and **job steps** (recipe steps + QC sign-off on the job). No free checklist on random tasks. |
| **Next-step card vs job task list vs timeline** | One **Next steps** block on the job: current step on top with its action buttons (Text / Call / Log call / Snooze), sent-history lines under it, manual steps below. |
| **Phase 2 quick-add chips vs Phase 5 NL quick add** | Same feature. Ship once, in Phase 2. |
| **Show-from date vs snooze** | Same thing. Snooze sets show-from; never expose two date fields by default. |
| **Inbox vs My day** | Watch this. If auto-done items clear from the inbox *and* My day, fine. If the inbox becomes a second to-do list, cut it to "mentions + handoffs" only. |

## Simplest combined design (what staff actually get)

1. **My day** (web + Go, same model). Top strip: today's summary (replaces the email). Groups: New for you, Catch up, Today, then Upcoming/Later collapsed. Role-aware: the counter sees unclaimed customer contacts; techs see bench jobs with "part arrived" pinned; part-timers see today's shift checklist and nothing else. Today's shift checklist appears as one collapsible row.
2. **Next steps on the job.** One block: auto step + action buttons + sent-history + manual steps + sign-off checklist at completion.
3. **One quick add** (+ button and ⌘K) with removable chips.
4. **Owner-only:** recipe settings with preview, checklist templates, stuck-jobs view (TV layout later), full searchable list.

That's two staff surfaces instead of ten.

## Clicks and noise to watch

- **Pushes.** Only for: handed to me, @mention, my customer deadline about to breach. Never for recipe steps created on schedule, never for checklist due, never "overdue" pings. Techs get pushes for handoffs only, and nothing during an active timer except a customer-waiting handoff.
- **Notify-all-assignees** shouldn't multiply pings: one notification per person per assignment, no pings on edits.
- **Accept button** adds a tap per handoff. Infer it.
- **Two-person tick** adds a second login. Cut.
- **Show-from + due on the create form** adds two fields. Hide show-from.
- **"Log call" after calling.** If I tap Call in Tekly (or Text), log it automatically and ask only "Answered? / No answer" with one tap. Don't make me open a form.
- **Recipe steps that ask the counter to "call" on day 6 after the system already texted on day 3**: fine, but don't create a call task if the customer replied to the text. Wake-on-reply must cancel the pending step, not just bump it.
- **Money on staff screens.** £ waiting is the owner's motivation, not ours. Showing it on the counter card is fine for prioritising; leave it off tech screens and the TV.
- **Renaming "Tasks" to "Next steps".** Fine in the job block. Call the nav item **My day**, so there's one name for one place.
- **Snooze menu**: max 5 options, event options only when relevant to that job (no "part arrives" on a job with no part ordered).
