# Council seat: Product strategist / devil's advocate

Method: assumption testing plus an opportunity solution tree. Inputs: `reports/Tekly task manager redesign.md`, `reports/tekly-tasks-phases.html`, the four research notes, and a quick check of the backend (`services/automation.py`, notification rules, `sweep_status_sla`).

## Bottom line

The insight is right: shops don't need a to-do list, they need stalled jobs chased. The packaging is wrong. "Tasks follow the job" is mostly **delayed, self-cancelling work-order follow-ups**, and that belongs in Work Orders and Automations, not in a better Tasks page. Ship it as **"Follow-ups"**, a capability of the job. The demo surface should be the work order and a "Waiting" board, not a Tasks section. Keep Tasks as a plain secondary list for personal and ad-hoc items. Don't spend three phases rebuilding it into a Linear/Things clone.

The report also oversells how new this is. RepairDesk already sends automatic pickup and payment reminders. Jobber has internal quote reminders after N days plus client quote follow-ups. CellSmart nudges on overdue repairs and devices left on the shelf. RO App has per-status time limits. The customer-texting half is **table stakes that Tekly is missing**, not a differentiator. What is genuinely new is narrower: (a) one ladder that mixes customer texts with staff call steps and stops itself when the job moves; (b) "money waiting" as a live number; (c) clocks that pause while waiting on parts or the customer. That's enough to win a demo, but describe it accurately in sales material, or a RepairDesk switcher will catch it.

## 1. Differentiated, or just smarter statuses? Should Tasks stay top-level?

**Mostly smarter statuses plus automations.** In the opportunity tree, every high-value branch starts from a work-order state:

```
Outcome: more revenue recovered per shop (quotes approved, devices collected and paid)
├── Quotes go quiet            -> follow-up ladder on "Waiting for approval"   [work order]
├── Ready devices sit on shelf -> ladder on "Ready for pickup" + notice log    [work order]
├── Parts stall jobs           -> ETA check + "part arrived, resume" wake-up   [work order / inventory]
├── Owner can't see what's stuck -> Waiting board + money waiting + age chips  [work orders view]
├── Shift routines on a whiteboard -> repeating checklists                     [separate module]
└── Ad-hoc delegation          -> plain task list                              [Tasks]
```

Only the last two branches are really "tasks". The engine already exists in Work Orders territory: the status-change `automation.run`, per-status notification rules (B143/P4), `sweep_status_sla`, and the missed-call close-on-convert pattern. Phase 1 is a delay-and-cancel extension of those. It is not a Tasks feature.

**Recommendation:**
- Don't rename Tasks to "Next steps". A rename costs onboarding and docs and confuses Orderry switchers who look for "Tasks". Put the new value under a new, plain name: **Follow-ups** (Settings → Automations → Follow-ups, plus a card on the job).
- Make a **"Waiting" view of Work Orders** the owner's front door (columns: Approval · Parts · Ready · Supplier return, with age and money). It's a saved board view of jobs, not a task page.
- Keep **Tasks** in the sidebar but lower it, below Work orders and Inquiries. Its scope: personal and ad-hoc tasks, part requests, missed-call callbacks, plus a "My day" list that includes the follow-up steps assigned to me. A follow-up step can show up in My day; it should not *be* a Task record with its own life. One record per step, owned by the job.
- Test to settle this: if a shop switches off every follow-up recipe, does anyone still open Tasks daily? If not, Tasks is a utility, not a pillar, and should get utility-level investment.

## 2. Verdict per feature

| Phase | Feature | Verdict | Reason |
|---|---|---|---|
| 0 | Fix invalid `"open"` status, priority enum | KEEP | Data corruption. Non-negotiable. |
| 0 | Notify and pay all assignees | KEEP (notify) / KEEP but decide explicitly (pay) | Broken promise. The payroll split rule needs an owner decision, not a silent default. |
| 0 | Delete legacy `/api/staff/tasks` | KEEP, do first | No permission checks: a security hole. Hours of work. |
| 0 | Editable panel, delete to 30-day bin | KEEP | Parity basics. Embarrassing in a demo today. |
| 0 | Server paging, search, "Mine" default | KEEP | Cheap, and stops slowness from reaching big shops. |
| 0 | Visibility permission | KEEP | Part-timers reading the owner's to-dos is a sales objection. |
| 0 | Screen-reader label | KEEP | Trivial. |
| 0 | Settle the Go Tasks tab | KEEP, decide now | Rename it "My day"; it costs nothing to decide. |
| 1 | Ready-not-collected ladder | KEEP, the #1 build | Directly recovers cash. Easy to explain. Customer-side parity with RepairDesk plus the staff-step twist. |
| 1 | Quote-not-answered ladder | KEEP | Recovers revenue. Jobber shows shops want it. |
| 1 | Preview "would affect N jobs / £X" before switching on | KEEP, the demo moment | Fixes the trust risk and shows £ on screen in one click. Best sales asset in the plan. |
| 1 | Next-step card plus sent timeline on the job | KEEP | This is where the feature lives. Stops double-texting. |
| 1 | Morning digest email | LATER (Phase 2) | Nice, but it's a new channel to tune and staff ignore email. Ship the in-app count and badge first; add the digest once there's content worth sending. |
| 1 | Day-30 storage notice step | MERGE into the ready ladder as an optional, off-by-default step with a logged notice | Legal exposure varies by country and state. Don't make it a headline. |
| 2 | Shared "My day" (web = Go) | KEEP, slimmed | One model on web and mobile is real value. Scope it to Today / Upcoming / Catch up only. |
| 2 | Separate "show from" and "due" dates | CUT for manual tasks / KEEP internally | Power-user concept; counter staff won't use two dates. Follow-ups need it internally (next step date versus customer deadline); hide it in the UI. |
| 2 | Snooze that wakes on activity | MERGE | The wake-up is just the follow-up ladder reacting to events (reply, part received, paid). Build it into recipes, not as a general snooze engine. A manual "snooze until date" is enough for personal tasks. |
| 2 | Calm "Catch up" with Move all to today | KEEP | Cheap, and better than red-wall overdue. |
| 2 | Quick-add chips (date, @person, #job) | KEEP small | Go already has chips; parity on web. Skip natural-language parsing. |
| 3 | Counter queue with rotating "on counter" owner | LATER, needs proof | Assumes shops have a dedicated counter role and rota. Most 1–3 person shops don't. Needs interviews. |
| 3 | Bench queue | MERGE into the existing tech queue / Go tech tablet | Technicians already work from assigned jobs. "Part arrived, resume" is a job flag, not a new queue. |
| 3 | Handoff Accept | LATER | Adds friction in small shops. Valuable at 5+ staff. Test before building. |
| 3 | Waiting board with age chips and money waiting | KEEP, move to Phase 1.5 | The owner's screen and the screenshot that sells. It's a grouped query over work orders. Cheap. |
| 3 | Shop TV / wall view | LATER, then a cheap read-only mode of the Waiting board | Real "whiteboard replaced" story, but don't build a separate surface. A full-screen toggle is enough. |
| 3 | Parts-ordered recipe | KEEP (Phase 2) | Strong revenue and call-deflection case. Depends on PO/received events being reliable. |
| 3 | Supplier-return (RMA) recipe | LATER | Real deadlines, but a niche frequency and it needs RMA data Tekly may not model. Interview first. |
| 3 | Pausing turnaround clock | MERGE into `sweep_status_sla` (exclude waiting statuses) | It's a work-order SLA feature. Small change, B2B selling point. Don't put it in Tasks. |
| 4 | Shift routines / repeating checklists | KEEP, but as its own small module, LATER | A real need (the whiteboard by the register), and a clear win over Orderry, which has no recurring tasks. But it isn't the job-follow-up story; don't let it delay Phase 1. |
| 4 | Two-person sign-off (cash count) | LATER | Gimmick risk. Do a single tick with a name and time first. |
| 4 | Job sign-off / QC checklist | MERGE into the work order (it's the job's QC, not a task) | Already in the blueprint as an automation action. |
| 4 | Owner view of completed routines | KEEP with routines | That's where the value of routines is. |
| 5 | Cmd/Ctrl+K | CUT from this plan | App-wide concern; owners won't buy it. Do it at platform level if ever. |
| 5 | AI suggestions (assignee, due, duplicate) | CUT | No evidence of need. Recipes already set the assignee and date. This is the "AI that runs your shop" the report itself warns against. |
| 5 | Voice drafts on Go | CUT for now | 62% usable rate. Gimmick in a demo, frustrating in a shop. |
| — | Rename Tasks to "Next steps" | CUT | Switching cost with no payoff. Call the new capability "Follow-ups". |
| — | Watchers table, handoff `acknowledged_at` | LATER | Build only when Accept handoffs are proven. |

**Redundancy flags:** snooze-wake vs recipe wake rules (same thing); bench queue vs tech queue and Go tablet (same thing); shop TV vs Waiting board (same thing); show-from/due vs recipe step dates (same thing); job QC checklist vs task checklist (different owners, so keep them apart). **Gimmicks:** voice, AI suggestions, Cmd+K, two-person tick, "on counter" rotation for tiny shops.

## 3. Riskiest assumptions, ranked, with the cheapest test

1. **Shops actually lose material money to unanswered quotes and uncollected devices, and chasing recovers it.** (Revenue thesis. The only number in the research is one extreme Denver anecdote.)
   *Test (1 day, no code):* run a SQL query on beta shops' data: jobs in "Ready for pickup" over 3 days and "Waiting for approval" over 2 days, with values. If the median shop has under ~£300 waiting, the "money waiting" story is weak; pivot the pitch to "fewer status calls".
2. **Shops will let Tekly text their customers automatically.** (Trust and brand fear. Many owners want to control the voice.)
   *Test (concierge, 2 weeks):* for 3 beta shops, send the owner a daily list "these 6 jobs would get a reminder today, approve?" by hand or with a script. Measure the approve rate and how many edits they make to the wording. Low approval means ship staff call steps first and customer texts as opt-in.
3. **Staff will act on the staff-side steps (call tasks) rather than ignore them.** (Adoption. This is the part that is actually differentiated.)
   *Test:* with Phase 0 shipped, create the follow-up tasks via the existing `create_task` automation on a 1-day delay (or have a beta owner do it manually for a week). Track the share completed within 24h. Under 50% means the tasks are noise; lean on customer automation and the board.
4. **The owner looks at a Waiting/money board daily.** (Is this the right home screen?)
   *Test:* a static mock or screenshot of their real numbers, emailed each morning for 2 weeks. Track opens and replies. If owners open it, build the live board. If not, the digest is enough.
5. **A separate counter role and rota exists.** (Phase 3 premise.)
   *Test:* 5 calls of 15 minutes each with beta shops. "Who answers the phone and texts customers? Same person every day?" If most are 1–3 person shops, cut the counter queue and handoff Accept.
6. **Shops want routines in software instead of on the whiteboard.**
   *Test:* ask beta shops for a photo of their whiteboard or opening checklist. No checklist anywhere means no routine module.
7. **Supplier RMAs are frequent and painful enough to need a recipe.**
   *Test:* ask how many returns per month. Fewer than about 5 means a manual task is enough.

Do tests 1, 2 and 5 before any Phase 1 UI work. All three are cheap and could re-scope it.

## 4. Positioning and demo

**One sentence vs Orderry:** *Orderry gives you a to-do list; Tekly chases every stalled repair for you, texting the customer, putting the call on the right person's list, and stopping by itself the moment the job moves, so quotes get approved and devices get collected and paid.*

**Three features that make the demo land:**
1. **"Preview before you switch on": "This would chase 4 devices (£1,240) and 2 quotes today."** Their own money, on screen, before they commit. The single strongest moment.
2. **The job's Follow-up card and timeline.** Ready text sent, day 3 reminder, call on Priya's list today. Then mark it paid and watch the step close itself. That shows "no double entry, no double texting" live.
3. **The Waiting board with money waiting and age chips (full-screen as the shop TV).** "Your whiteboard, kept up to date for you." Visual, easy for the owner to grasp, and it screenshots well for the website.

Not in the demo: My day, snooze, Cmd+K, AI, voice, routines. They're fine features, but they don't sell the switch.

## Recommended re-sequence

1. Phase 0 fixes (unchanged; legacy API first).
2. **Follow-ups v1:** ready-not-collected and quote-not-answered ladders built on the automation, notification-rule and SLA engine; preview; job card and timeline. Storage notice off by default.
3. **Waiting board** with money waiting and full-screen mode, plus pausing SLA clocks.
4. Parts-ordered recipe with "part arrived, resume" wake; slim My day on web and Go; morning digest.
5. Routines module (only if test 6 passes); counter queue and handoffs (only if test 5 passes).
6. Cut AI, voice and Cmd+K from this initiative.
