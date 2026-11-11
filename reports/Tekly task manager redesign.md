# Make tasks follow the repair job

Tekly should stop treating tasks as an office to-do list, which is what Orderry's version is. It should make them **the next step on a stalled repair job**. In practice that means four things. Tasks appear on their own when a job goes into a waiting state (quote sent, part ordered, device ready but not collected). They come back to the top of the list when something changes (the customer replies, the part arrives). They close themselves once the job moves on. And they reach the right person at the right moment without filling everyone's phone. None of the repair-shop or field-service tools in this research do this. Orderry's tasks are a basic list whose own help center gives examples like "buy office supplies" and "pay rent". Its task module has no priorities, no recurring tasks, no board, no calendar and no automatic tasks. In repair shops, follow-up work actually sits in ticket statuses, on whiteboards and in people's heads. Tekly already has the raw parts for something better: tasks linked to jobs, a status-change automation, missed-call tasks that already close themselves, a timer, and a mobile app with a "my tasks" tab. But the current screen is a flat table with a side drawer that copies Orderry's ideas. It cannot edit a task's title or due date, and it hides several real bugs. The plan below fixes those bugs first. It then ships the smallest change with the biggest payoff, automatic follow-ups on waiting jobs, before any new views.

## Tekly today is an Orderry-parity list with real holes

Tekly's task feature was built on purpose as "Orderry parity" in Build 86. The code says so in its opening comment, which lists Orderry's ideas: tasks linked to "what, when, where and for whom", an activity feed, and an on-time flag that makes tasks payable (`backend/routers/tasks.py:1-8`). It also adopted Orderry's rule that "you are never notified about something you did yourself" (`backend/services/notification_rules.py:709-718`). Each task is one row with a title, a description, **three statuses (to do, in progress, done)**, a priority, **a single due date**, a main assignee plus a list of extra assignees, and optional links to a job, client, inquiry, location or parent task (`backend/models/__init__.py:1752-1779`). It has no start date, no recurrence, no checklist, no reminder, no labels, no attachments, no time estimate and no watchers. A deleted task is gone for good.

On the web, staff get one Tasks page: a table with filter pills (All, To do, In progress, Done, Late) and a details panel that slides out from the right (`frontend-react/src/pages/tasks/TasksHub.tsx`). The panel can change the status, the assignees and the linked job, and it can post comments. It **cannot change the title, description, due date or priority**. It cannot delete a task, and it does not show subtasks, even though the server sends them (TasksHub.tsx:123-182). Staff cannot search, sort, or filter by person, job or priority, and the web has no "my tasks" view. Each job's screen shows a short task list with quick-add and tick boxes, but no due dates or details (`frontend-react/src/pages/orders/JobTasks.tsx`). Tekly Go, the mobile app, is better: it groups your tasks into Overdue, Today, Coming up and No date, shows a badge for what's due, and has a quick-add sheet with "Today / Tomorrow / Me" chips (`frontend-react/src/go/screens/Tasks.tsx`; `frontend-react/src/go/components/TaskSheet.tsx`). So web and mobile currently think about tasks in different ways. The web is organised by status, mobile by date, and mobile ignores "in progress" altogether.

Three things already create tasks automatically. A job status change can trigger a "Follow up on {job}" task. A missed call creates a "Call back {name}" task, due by the inquiry's response deadline and **closed automatically when the inquiry becomes a job** (`backend/services/leads.py:283-289, 346-438`). And a technician can request a part from Tekly Go (`tasks.py:139-178`). The missed-call flow is the seed of the whole redesign, because it already behaves the way every task should: created by an event, tied to a record, and closed when the situation resolves. What's missing is any reminder or overdue alert. "Overdue" is only worked out when someone opens the list, and nothing runs in the background to chase it (`backend/core/scheduled.py:17-280`).

### Bugs found in the current build

| # | Bug | What the shop sees | Where |
|---|---|---|---|
| 1 | Automatic "follow-up" tasks are saved with status `"open"`, which isn't a valid status | A blank status badge. The task only shows under "All" and "Late" and is missing from To do and In progress | `backend/services/automation.py:93-99` vs `tasks.py:25`; display at `TasksHub.tsx:110` |
| 2 | Extra assignees are never notified | Add three techs to a task and only the first one hears about it | `notification_rules.py:710-757` reads only `assigned_to_id` |
| 3 | Only the first assignee is paid task commission | Multi-person tasks pay one person | `backend/services/payroll.py:95-122` |
| 4 | An old duplicate task API is still live, with **no permission checks**, no notifications and no payroll | Nothing visible, but it is a security and data-integrity gap | `backend/routers/staff.py:683-740` |
| 5 | Priority is stored as text but sent as numbers (0/1/2), and older code writes "normal"/"high" | Has already crashed task creation in production once; filtering and sorting stay unreliable | `models/__init__.py:1770-1774` |
| 6 | The panel can't edit title, description, due date or priority, and has no delete | Typos and wrong dates are stuck unless you remake the task | `TasksHub.tsx:123-182` |
| 7 | The list loads every task in the shop at once. "Mine" is filtered after loading everything. No paging on the server, no search | Gets slow for busy shops over time | `tasks.py:54-87` |
| 8 | Every employee can see every task | A part-timer can read the owner's private to-dos | `tasks.py:54-87` (no visibility check) |
| 9 | Tasks are permanently deleted, with no undo or restore | Accidental deletes are unrecoverable (Orderry can restore them) | `tasks.py:295-305` |
| 10 | Notification errors are silently swallowed | Lost alerts are invisible | `tasks.py:381-386` |
| 11 | The "remove assignee" buttons are labelled "Close" for screen readers | Accessibility defect | `TasksHub.tsx:145,196` |
| 12 | The mobile specs say to remove Tekly Go's Tasks tab, but the shipped app has one | An unresolved product decision | `docs/mobile/tekly-go-pwa-plan.md:130-131` vs `go/components/TabBar.tsx` |

Bugs 1 to 5 corrupt data or break promises the screen already makes, so they should be fixed whatever is decided about the redesign.

## Every competitor stops at the to-do list

Orderry's Tasks section is a manager's delegation list. Its help center offers buying office supplies, calling a client or paying rent as typical uses ([Orderry help](https://help.orderry.com/en/articles/9130708-general-information-about-the-tasks-section)). The form has a name (max 128 characters), a description, a due date and one or more assignees. With several assignees you choose between "one task for all" and "one task for each one" ([Orderry help](https://help.orderry.com/en/articles/9130710-how-to-add-a-new-task)). A missed date makes **a red indicator flash in the side menu**. The marketing promises a "convenient list view instead of sticky notes mess", and **recurring tasks are "not possible in the current version"** ([Orderry task manager](https://orderry.com/online-task-manager/)). I found no kanban board, calendar, priorities or checklists. RemOnline advertises the same features, commissions for tasks and assign/complete alerts, which suggests the same product underneath ([RemOnline](https://remonline.app/features/employee-management)). Orderry's reviewers complain about dated design and a hard-to-use interface, not about tasks ([Capterra](https://capterra.com/p/151174/Orderry/reviews/)). That tells us tasks aren't where shops expect Orderry to shine, not that the tasks are good.

The rest of the field is thinner still. RepairShopr has customer "reminders" and recurring tickets ([RepairShopr KB](https://feedback.repairshopr.com/knowledgebase/articles/519504-create-reminders-in-repairshopr)). Its users have filed a request for alerts on stale tickets ([UserVoice](https://feedback.repairshopr.com/forums/165658-general/suggestions/15972691-ticket-reminders-notification-for-stale-tickets)), and many shops push tickets into Todoist or Things through Zapier ([Zapier](https://zapier.com/apps/repairshopr/integrations/todoist/1734119/create-new-todoist-tasks-from-new-repairshopr-tickets)). RepairDesk has no to-do module and relies on automatic customer texts. Its own blog puts a figure on what manual chasing costs, for example **20 minutes a day on part-delay calls alone** (a vendor estimate) ([RepairDesk blog](https://blog.repairdesk.co/2026/06/11/the-hidden-cost-of-manual-follow-ups-in-repair-shops/)). Jobber users ask for a to-do list and fall back on Google Tasks ([Jobber community](https://community.getjobber.com/discussions/online-booking-requests/adding-a-to-do-task-manager/9439)). Housecall Pro added tasks linked to jobs in July 2026, with one assignee, a 5 a.m. "due today" alert and a daily email. **Multiple assignees, comments and attachments are still "on the roadmap"** ([Housecall Pro help](https://help.housecallpro.com/en/articles/15351133-tasks-on-jobs-and-estimates)). ServiceTitan has the most complete module, with subtasks, a "source" field, a required resolution note and comments with attachments, but **only office staff can use it** ([ServiceTitan](https://help.servicetitan.com/commercial/docs/common-use-cases-for-task-management)).

The gap they all share matters most for Tekly. Everywhere, "waiting on parts" and "waiting on customer" are just ticket statuses. I found **no repair tool that creates a follow-up when a job enters a waiting state, wakes it when the part arrives, or pauses a turnaround clock while the shop is waiting on someone else**. Helpdesk tools like Freshdesk pause their response-time clocks on waiting statuses ([Freshdesk](https://freshworks.com/freshdesk/helpdesk-customization/custom-ticket-statuses)), and IT-support workflows send a reminder after a ticket sits idle 8.5 hours in "Waiting for customer" ([Kaseya workflow docs](https://docs.nhmlac.org/technical/acme/processes/kaseya-workflows)). The closest a repair tool gets is RO App's per-stage "status time limit" ([RO App](https://roapp.io/apps/staff/)) and CellSmart's per-ticket deadline with a "taking longer than it should" nudge ([CellSmart](https://www.cellsmartpos.com/features/repair-delay-reminder)). Today's table stakes are: a task linked to a job or customer, an assignee, a due date, red overdue, My/All toggles, alerts on assignment, a morning digest, and limits on what technicians can see. Tekly already has most of these. Being better means building the layer above.

## Shop staff need waiting jobs chased, not another list

In a small shop, the "tasks" that matter are nearly all **a job stuck in a waiting state that needs an owner and a next-action date**. Repair boards across the industry use the same stages: waiting for approval, waiting for parts, sometimes waiting on outside work ([Aptean](https://aptean.com/en/insights/blog/organize-your-repair-shop-with-planning-boards); [RAD Repairs](https://radrepairs.com/documentation/tickets.html)). Devices go uncollected for three reasons: the quote was never accepted, the repair took too long, or nothing was paid upfront. The suggested fix is a same-day "ready" message, a reminder a week later and **a weekly review of the Ready list** ([RepairFlow](https://repairflow-site.pages.dev/blog/stop-losing-money)). Supplier warranty returns have hard deadlines. Some suppliers want damage reported **within 3 days** of delivery and charge **20–50% restocking** if a returned part tests fine ([Phone LCD Parts](https://www.phonelcdparts.com/warranty-return-policy); [Ting Mobile](https://phones.tingmobile.com/pages/warranty-returns)). The second group is shift routines. RepairDesk's playbook lists a two-person cash count at opening, a printer test, a display reset, a **daily stock count**, missed calls returned **within 15 minutes**, and three review requests a day, **tracked on a whiteboard by the register** ([RepairDesk playbook](https://blog.repairdesk.co/2025/08/21/10-daily-tasks-for-successful-shop-management-download-free-playbook/)). When a software vendor sends its own customers back to a whiteboard, the software's task tools have failed.

The evidence also explains why task modules go unused. The real follow-ups already live in ticket statuses, so typing a separate task is double entry. Automatic status triggers that don't fit a shop's process get distrusted; one RepairShopr user complained that a parts comment flipped the ticket to "waiting on parts" when it shouldn't have ([RepairShopr feedback](https://feedback.repairshopr.com/forums/165658-general/suggestions/6565393-customize-the-triggers-that-affect-statuses)). And the counter person serving walk-ins isn't looking at a "welcome screen". The whiteboard wins because everyone can see it without logging in ([Fullbay](https://fullbay.com/blog/front-shop-back-shop-hut)).

Each role needs something different. **The front counter** owns customer contact: quotes awaiting approval, devices ready but not collected, callbacks when a part arrives, and missed calls. Technicians get pulled mid-repair to answer status calls and then miss other calls ([RepairDesk](https://blog.repairdesk.co/2026/06/05/how-to-spend-less-time-on-calls-and-start-fixing-more-things/)). **Technicians** need a protected bench queue, "part arrived, resume" flagged automatically, a sign-off checklist on completion, and very few alerts. **The owner** needs to see what is stalled across everyone, how long it has been stuck, and whether routines happened. **Part-timers** need their shift checklist and nothing else. The money matters too. "Is my device ready?" is said to be the most common inbound call, costing **over 2 hours a day** of staff time once quote and parts calls are added (RepairDesk's illustrative figure, not a survey) ([RepairDesk](https://blog.repairdesk.co/2026/06/05/how-to-spend-less-time-on-calls-and-start-fixing-more-things/)). Customers expect speed: about **80% of iPhone owners would repair or replace within a day or two** ([9to5Mac/CIRP](https://9to5mac.com/2023/04/05/how-fast-repair-replace-iphone-ipad-mac-poll/)). Chasing uncollected devices also carries legal risk. A Denver shop that charged $25 a day in storage fees and filed **85+ lawsuits** saw a judge call its fee "unconscionable" ([Yahoo/Denver7](https://www.yahoo.com/news/colorado-electronics-repair-shop-filed-130200275.html)). Published shop policies range from about 30 to 90 days before a device counts as abandoned ([Prime Tech Support](https://primetechsupport.com/pages/repair-terms-and-conditions); [MDrepairs](https://mdrepairs.com/terms-conditions)), and the rules differ by state and country. Pickup reminders should therefore be configurable steps with a logged record of each notice, not a fee machine.

One caveat: this research found almost no first-hand shop-owner voice. Reddit and Technibble blocked access, so the role needs above are inferred from vendor playbooks and shop policies. A handful of interviews with Tekly beta shops should confirm the priorities before Phase 2.

## Borrow the calm patterns, skip the "autonomous AI"

Linear, Things, Todoist, Superhuman, Asana and Sunsama, the tools people most admire, share a few patterns that suit a repair counter.

**Separate "when I'll look at it" from "when it's due."** Things treats a start date as the day you begin and keeps deadlines separate ([Cultured Code](https://culturedcode.com/things/support/articles/2803579/)), and Todoist added a separate deadline in 2024. That makes "snooze" simple: push the start date. **Snooze that wakes on activity** is the most praised way to put something off. Linear cancels a snooze the moment there's new activity ([Linear docs](https://linear.app/docs/snooze)). Superhuman reminders return only "if no reply", and a reply cancels them ([Superhuman](https://help.superhuman.com/hc/en-us/articles/43822934607251-Remind-Me)). In a repair shop that becomes "remind me Friday if the customer hasn't approved the quote", which then disappears on its own when they approve.

**A shared triage queue with a rotating owner.** Linear's Triage holds work nobody has claimed yet, with one-key Accept, Decline, Duplicate and Snooze, plus a "who's on triage today" rotation that skips people who are off ([Linear Triage](https://linear.app/docs/triage)). That maps neatly onto "who's on the counter today".

**A "new for you" landing zone.** Anything assigned to you in Asana appears under Recently Assigned, and tasks move up to Today automatically one day before they're due ([Asana](https://asana.com/features/project-management/my-tasks)). Handoffs can't go unnoticed.

**Calm overdue handling.** Todoist puts a one-click "Reschedule" button next to the Overdue section ([Todoist](https://todoist.com/help/articles/205325931)), and Sunsama rolls unfinished work forward in an end-of-day review ([Sunsama](https://help.sunsama.com/docs/evening-daily-planning)). Compare that with Orderry's flashing red light.

**Fast capture.** Todoist's quick-add understands phrases like "every Monday", highlights what it understood, and lets you tap a wrongly read word to undo it ([Todoist](https://todoist.com/help/articles/set-a-recurring-due-date-YUYVJJAV)). Linear's Cmd/Ctrl+K menu reaches every action by name ([Linear design study](https://blakecrosley.com/guides/design/linear)).

**One inbox, few push alerts.** Everything goes to an in-app inbox, push alerts are configurable per event, and since September 2026 Linear splits the inbox into Priority and Other ([Linear changelog](https://linear.app/changelog/2026-09-03-priority-inbox)). A Duke study found that **batching notifications three times a day** left people less stressed and more productive ([Business Insider](https://www.businessinsider.in/smartphone-notifications-are-becoming-dangerous-and-theres-an-easy-fix-that-could-vastly-improve-your-life/amp_articleshow/63762019.cms)).

There are also things to avoid. Height marketed itself as "autonomous project management" and **shut down on 24 September 2025** ([AlternativeTo](https://alternativeto.net/news/2025/3/height-project-management-tool-to-shut-down-by-september-2025)). Motion's automatic calendar scheduling saves time but asks for required fields on every task and a lot of setup, and its 2026 redesign drew complaints ([Upbase](https://upbase.io/blog/motion-app-review/)). Voice capture is promising for technicians with their hands full, but Todoist's version still **produced a usable task only about 62% of the time** in late 2025 ([Pulse](https://www.pulse.bot/ai/news/building-todoist-ramble-how-doist-turned-voice-braindumps-into-real-time-task-capture-e753990f-8836-4153-b9fa-067f7ce5b301/)). Voice should create drafts for review, never finished tasks. Tekly should also skip streaks, shame counters, required time estimates, and anything sold as "AI that runs your shop". The AI pattern that holds up is Linear's: **suggest, give a reason, let a human accept**, with a per-team switch between hidden, shown and auto-applied ([Linear Triage Intelligence](https://linear.app/docs/triage-intelligence)).

## The redesign: "Next steps" that follow the job

### Core concept

Rename the idea in the product from "Tasks" to **"Next steps"**. Every open job either is moving or has a next step with an owner and a date. Next steps come from three places. **Automatic** ones are created by job events and close themselves when the event resolves. **Routine** ones come from shift and weekly checklists that repeat. **Personal** ones are typed in by hand, like today's tasks. This is the one idea Orderry, RepairShopr, RepairDesk and Housecall Pro all lack, and Tekly's missed-call flow already proves it works.

Each next step carries a few things. A **"show from" date** and an optional **due date**, kept separate. A **wake-up rule**, so it comes back to the top when the customer replies, the part is received, the payment lands or the job status changes. A **reason line written in shop language** ("Quote sent 2 days ago, no reply · £180"). And **one-tap actions** that do the real work, such as "Text customer", "Mark part received" or "Call", instead of just ticking a box.

### Views

| View | Who | What it shows |
|---|---|---|
| **My day** (web and Go) | Everyone | New for you (handoffs) · Today · Upcoming · Later. Overdue sits at the top under a calm header with "Move all to today / pick a date". Same model on web and mobile. |
| **Counter queue** | Front desk | Customer-contact next steps across the shop: quotes awaiting approval, devices ready and waiting, parts arrived, missed calls (15-min target). Unclaimed items have one-click Take / Assign / Snooze / Dismiss. Shows who's "on counter today" (rotating, skips staff who are off). |
| **Bench queue** | Technicians | Their jobs in order, with "part arrived, resume" pinned to the top and a sign-off checklist on completion. No admin noise. |
| **Shop board / wall view** | Owner, plus a TV by the bench | The whiteboard replaced: columns by waiting type (Approval · Parts · Ready for pickup · Supplier return) with age chips (1d / 3d / 7d / 30d) and a **"money waiting" total**, meaning open quote value plus unpaid ready jobs. Read-only and readable from across the room. |
| **On the job** | Everyone | The existing job task panel, upgraded with due dates, the reason line, and the job's automatic next steps. |

Keep a full searchable list for power users, with filters for person, job, client, priority and source, but it becomes the back-office view, not the front door. Following Tekly's layout rule, page actions sit on the left (New next step first) and search and filters on the right.

### Automation (the differentiator)

Shops get a short list of ready-made, switchable "follow-up recipes". Each recipe sets its own timing and escalation steps, and each one closes itself when the condition clears. **Quote sent, no reply** → counter follow-up at 24h, a second at 72h, closed on approval or decline. **Part ordered** → supplier check at the supplier's ETA; when the part is received, a "resume" step for the job's technician and a "tell the customer" step for the counter. **Ready, not collected** → day 0 message, a reminder on day 3 and day 7, and a formal notice step at the shop's configured abandonment day, with every notice logged. **Supplier return opened** → steps for reported, RMA number received, shipped with tracking, and credited, with the supplier's deadline. **Missed call** → keep the existing flow. **B2B jobs** get a turnaround clock that **pauses while waiting on the customer or on parts**. Every recipe shows a preview of what it would have created in the last 30 days before the shop turns it on. Because recipes are switchable and previewed, they avoid the "wrong trigger" frustration RepairShopr users describe.

### Notifications

Send a push alert only when a next step is assigned or handed to you, when someone @mentions you, or when one of yours is about to breach a customer-facing deadline. Everything else goes to an in-app inbox, which marks items done automatically when they resolve. A **morning digest** ("Today: 5 next steps, 2 overdue, 1 handed to you, £640 waiting on approval") goes out at a time each staff member picks. All assignees get notified, not just the first. Alerts can be snoozed. And Orderry's flashing red light should not be copied.

### Tekly Go mobile

Settle the open decision: **keep the tab, but rename it "My day"** and make it the same model as the web view. Tekly Go already has the date groups, the badge and offline ticking. Add snooze (swipe), the one-tap actions ("Mark part received" scans the part), push links that open the right job, and later voice capture that creates drafts for review. Part-timers see only their shift checklist.

### Phased build order (smallest high-impact first)

| Phase | What ships | Why this order | Rough size |
|---|---|---|---|
| **0. Fix the foundations** | Bugs 1–11 in the table above: valid statuses, notify and pay all assignees, delete the legacy API, priority as a proper list, editable panel with delete/restore, server-side filtering and paging, a basic visibility permission | Data must be trustworthy before automation builds on it | Days |
| **1. Ready-not-collected + quote-no-reply recipes, morning digest** | Two automatic, self-closing recipes on the existing status-change automation, plus a daily digest | The smallest change that visibly does what no competitor does, and it goes after money directly | 1–2 weeks |
| **2. My day, start vs due, snooze-that-wakes** | Shared web and Go "My day", a "show from" date, wake-on-activity snooze, calm overdue rescheduling, quick-add with chips (date, @person, #job) | Gives people one place to look, and web and mobile finally match | 2–3 weeks |
| **3. Counter queue + shop board** | Triage queue with rotating counter owner, wall/TV board with age chips and money waiting, parts-ordered and supplier-return recipes | Replaces the whiteboard; reuses Phases 1–2 | 3–4 weeks |
| **4. Routines** | Repeating shift and weekly checklists by role, sign-off checklists on jobs, owner view of completed routines, B2B pausing turnaround clock | Second group of needs; checklists are already in the product blueprint | 2–3 weeks |
| **5. Assistive extras** | Cmd/Ctrl+K menu, AI suggestions (assignee, due date, duplicate) with reasons and an on/off switch, voice drafts on Go | Polish once the core is used; never "autonomous" | Ongoing |

Sizes are rough guesses for planning. Phase 1 should follow the infrastructure work now in progress, in line with the rule that new features wait until the backend is solid. Each visible change in Phases 2–3 should go through the usual before/after screenshot review.

### For the developers: data-model changes

These changes are listed against the current `Task` model (`backend/models/__init__.py:1752-1779`).

Make `status` an enum: `todo | in_progress | waiting | done | cancelled`. Migrate stored `"open"` rows to `todo` and change `automation.py:93-99` to write a valid value. Make `priority` an enum (`low | normal | high | urgent`) and migrate the 0/1/2 and "normal"/"high" strings. Add `start_at` (show from), keeping `due_at`. Add `updated_at`, `archived_at` (soft delete; restore within N days) and `completion_note`.

Add `source` (`manual | recipe | routine | part_request | missed_call`), `recipe_key` and `source_ref` (an entity type plus id) so automatic tasks are de-duplicated and can be closed on resolve. Generalise the existing missed-call pattern in `leads.py:283-289, 422-423` (update-instead-of-duplicate, auto-close on convert) into a shared helper. Add `wake_on` (a JSON list of events such as `customer_replied`, `part_received`, `payment_received`, `order_status_changed`) and wire it into the event feed, so matching events clear the snooze and bump the task.

Replace the `assigned_to_id` + `assignee_ids` split with a `task_assignees` join table holding a `role` (`owner | helper`) and `acknowledged_at` (handoff acknowledgment). Add a `task_watchers` table. Then update `notify_task` (`notification_rules.py:710-757`) and `accrue_for_task` (`payroll.py:95-122`) to read the join table and decide explicitly who gets commission on shared tasks. Name the shared-versus-copy choice in Tekly's own words, not Orderry's "one task for all / for each one".

Add `task_checklist_items` and `routine_templates` (role, recurrence rule, time of day, checklist), plus a daily job that creates each day's routine instances. Add a scheduled sweep in `backend/core/scheduled.py` for due-soon/overdue alerts, the morning digest and recipe escalation steps. Pausable turnaround clocks reuse the existing `sweep_status_sla` approach, with "waiting" statuses excluded from elapsed time. Server-side filtering, text search and cursor paging go on `GET /api/tasks`, which today loads everything and filters `mine` in Python (`tasks.py:54-87`), plus a role permission for "see everyone's tasks". Finally, delete `/api/staff/tasks` (`backend/routers/staff.py:683-740`); no frontend calls it.

## Conclusion

The competition sets a low bar here. Orderry's task feature is the weakest part of a product its reviewers already find dated, and no repair tool links follow-ups to the state of the job. If Tekly keeps refining its current Orderry-shaped list, the best it can be is a tidier Orderry. If it makes next steps a by-product of the job, it gets a feature the rest of the market doesn't have, and one that shows its value in pounds: quotes chased and devices collected. The highest-leverage move is also the cheapest, because the missed-call flow already proves Tekly's engine can create a task, de-duplicate it and close it on its own. Two more recipes and a morning digest give a sales demo something no competitor can show.

The main risk is trust, not engineering. Shops abandon automatic triggers that fire at the wrong time, and pickup-chasing has legal limits. So every recipe should be switchable, previewed before it goes live, and logged, and Tekly should talk to a few beta shops before building Phase 3. The current bugs make that more urgent. Today an automatic task can show up with no status, and two of three assignees get no alert. Phase 0 matters because the whole redesign depends on staff believing that what the screen shows is true.

---

## Council and market verdict (added 2026-11-11)

Four council seats (shop owner, counter and bench staff, architect, product strategist) reviewed every feature. Two more research passes then checked competitors' follow-up automation and the money and legal evidence. Notes: `research_notes/Tekly task manager redesign/council/`, `market_followups.md`, `market_value.md`.

### What changed our mind
- **About 40% of the plan rebuilt things Tekly already has.** That includes:
  - automation rules and customer notification rules, with delayed sends and working hours;
  - per-status time limits (`sla.py`);
  - the Sense morning brief and the Sense quote follow-up agent;
  - the "parts received, job can resume" alert;
  - `/orders/board`, Go Today and `/my-work`;
  - the ⌘K palette and work-order checklists;
  - the public tracking page with quote respond, a deposit link and messages.
- **"No competitor does this" was overstated.** Tracking links, approving a quote from a link, pay links and status texts are table stakes, and Tekly has them. Nobody documents five things:
  1. a multi-step pickup chain that stops by itself;
  2. one sequence that mixes customer messages with staff call steps;
  3. a "money recovered" report;
  4. turnaround clocks that pause by themselves while waiting;
  5. a view of money waiting, sorted by how long it has waited.
- **Nobody buys repair software for its task list.** Shops switch for support, a modern app, workflows, POS and inventory, and integrations. Task value has to show up as customer outcomes such as approved quotes and collected, paid devices.
- **Three real bugs block any "after N days" follow-up:**
  - task alerts never reach the bell or push;
  - time-based automations only run when someone triggers them by hand;
  - delayed customer messages aren't re-checked against the job's status before sending, so a reminder can reach someone who already collected.

### Final plan
| Phase | Ships | Built on |
|---|---|---|
| 0 Foundations | Task bugs (`open` status, priority type, editable panel, soft delete, "Mine" + search, notify and pay every assignee). Task alerts through bell/push. Scheduled automation sweep. Status check before a delayed message sends. Delete the legacy `/api/staff/tasks`. Duplicate guard (`source_ref`). | Existing tasks, `push.notify`, `scheduled.py`, `notification_rules` |
| 1 Follow-ups | Two presets, off by default: **ready but not collected** (text day 3, call step day 6, printable legal notice later) and **quote not answered** (text 24 h, call step 72 h). A "Next steps" card on the job, with history. Preview before switching on. Steps close themselves when the job moves. A **money recovered** report. | AutomationRule + NotificationRule pairs (no new engine), dry-run, Sense quote agent |
| 2 Waiting board | A view of money waiting by age (ready not collected, quotes waiting), turnaround clocks that pause in waiting statuses, and a full-screen wall mode (no money shown). | `/orders/board`, per-status SLA |
| 3 My day | One role-aware My day on web that matches Go Today: catch-up group, quick-add chips, part-arrived pinned. A tasks section in the Sense morning brief. | Go Today, `/my-work`, Sense brief |
| 4 After beta interviews | Shift routines (on `ChecklistTemplate`), a supplier-return recipe, paying several assignees, the counter-on-duty filter. | Existing checklist model |

**Cut:**
- the rename to "Next steps" (only the job card uses that name);
- AI assignee suggestions (a fixed rule does it: the job's tech);
- voice drafts;
- two-person ticks;
- a separate "show from" date field;
- the handoff Accept button (opening or starting the job counts as accepting);
- per-task checklists;
- a new ⌘K (it exists);
- a separate morning email for techs;
- rotating counter owner.

**Rules for automatic messages:**
- Keep service and marketing messages separate, with no promotions in service texts.
- Respect opt-outs and quiet hours (8am–9pm local).
- A text is a nudge, not a legal abandonment notice. Generate a printable notice and log when it was sent.
- Cap any storage fee and show it up front.

**Test before building the Phase 1 screens:**
1. Query beta shops' data for jobs ready more than 3 days and quotes waiting more than 2 days, with their value.
2. Run a 2-week trial with 3 owners approving a daily reminder list.
3. Five 15-minute calls on how the counter and bench are staffed.

**Positioning:** "Orderry gives you a to-do list. Tekly chases every stalled repair for you, and stops the moment the job moves."
