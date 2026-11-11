# Best-in-class task/work management UX patterns (Linear, Things 3, Todoist, Asana, Height, Motion, Sunsama, Akiflow, Superhuman, Notion, ClickUp)

Scope: concrete, borrowable patterns for a small-business staff task manager (Tekly, repair-shop context). Research date: Nov 2026. Sources are mostly vendor docs plus reviews; vendor marketing claims are flagged.

## Capture: quick-add, natural-language dates, command palettes, add-from-anywhere

### Takeaway
The best tools make capture a single text field that parses dates, recurrence, people and lists inline (highlighting what it parsed, with a one-tap "undo the parse"), plus a Cmd/Ctrl+K palette that exposes every action by name. Voice "braindump to tasks" (Todoist Ramble, GA Jan 2026) is the newest capture surface with real usage numbers.

### Cited Findings
- Todoist Quick Add parses natural language like "every Monday" / "every weekend"; the parsed date is automatically recognised, highlighted, and added to the task — [Todoist help: recurring dates](https://todoist.com/help/articles/set-a-recurring-due-date-YUYVJJAV)
- Supported patterns include "every day/daily", "every weekday/workday", "every other day", "every fri at noon", and bounded ranges ("everyday from 10 May until 20 May"); you cannot combine two "every" patterns — [Todoist help](https://todoist.com/help/articles/set-a-recurring-due-date-YUYVJJAV)
- False-positive guard: if a word like "monthly" in a task title gets highlighted as a date, click/tap the word to disregard it; smart date recognition can be turned off. Complex schedules fall back to a structured picker (Date > Repeat > Custom) — [Todoist help](https://todoist.com/help/articles/set-a-recurring-due-date-YUYVJJAV)
- Todoist Ramble (voice-to-tasks): public beta 19 Nov 2025, GA 21 Jan 2026; user speaks freely and a structured task list builds in real time, responding to corrections mid-speech; 38 languages, all platforms — [TechCrunch](https://techcrunch.com/2026/01/21/todoists-app-now-lets-you-add-tasks-to-your-to-do-list-by-speaking-to-its-ai/); [Todoist help: Ramble](https://www.todoist.com/sv/help/articles/from-voice-to-tasks-ramble-july-1-BmcE0Kq8U)
- Ramble beta: ~76,000 users ran ~290,000 sessions in three weeks; task-creation success rose from ~40% (Oct 2025) to ~62% (Dec 2025) — i.e. roughly 4 in 10 sessions still failed to produce a usable task at that point — [Pulse/Doist build writeup](https://www.pulse.bot/ai/news/building-todoist-ramble-how-doist-turned-voice-braindumps-into-real-time-task-capture-e753990f-8836-4153-b9fa-067f7ce5b301/); a Product Hunt commenter reported it missed "by 2pm" — [Product Hunt](https://www.producthunt.com/p/todoist/ramble-by-todoist)
- Linear: Cmd/Ctrl+K opens a command menu giving access to every action by name; it is introduced as the primary navigation pattern during onboarding, before users create content — [Blake Crosley design study of Linear](https://blakecrosley.com/guides/design/linear); [Supademo Linear onboarding](https://supademo.com/content/plg-onboarding-gallery/linear)
- Linear pairs the palette with mnemonic single-key shortcuts (J/K move, S status, number keys for priority) — [techinterview frontend design of Linear](https://www.techinterview.org/post/3233475370/frontend-system-design-build-linear-project-management/)
- Critique of copycat palettes: a good Cmd-K stays keyboard-navigable after opening; weak ones "dump you back into mouse targets" — [swyx: Keyboard First](https://www.swyx.io/keyboard-first)
- Akiflow's system-wide command bar parses natural language including duration, e.g. "Call John tod 3pm for 1h" sets date, time and 1h block; system-wide capture is desktop only. Shortcut reported as Cmd+K by some sources, Cmd+E by others (conflict) — [The Business Dive Akiflow review](https://thebusinessdive.com/akiflow-review); [Akiflow command bar](https://akiflow.com/command-bar-feature/)
- Superhuman reminders use the same palette: Cmd+K > Remind Me (or H), then type the day/time in natural language; Tab reveals more options — [Superhuman help: Remind Me](https://help.superhuman.com/hc/en-us/articles/43822934607251-Remind-Me)

### Inferences
- For Tekly: one global quick-add (keyboard shortcut on desktop, FAB on mobile) with inline parsing of date/time/recurrence, "@person" assignment and "#record" linking (e.g. a work order number), with parsed tokens shown as removable chips. This is the single highest-leverage "feels premium and fast" pattern.
- Always provide a structured fallback picker next to the NL field; NL alone is error-prone (Todoist itself keeps both).
- Voice capture is promising for hands-busy technicians at the bench, but 2025 success rates (~62%) suggest it should be "draft tasks for review", never auto-commit.
- Cmd-K palette is table stakes for desktop power users but small-shop staff are often mouse/touch users; it should be additive, not the only path.

### Gaps
- No primary Things 3 Quick Entry (autofill from Mail/Safari) details were fetched in this pass.
- No quantitative evidence found on how much NL parsing increases task creation versus a form.

## Organisation: Inbox/Today/Upcoming/Someday, triage inbox, My Work vs team, smart views

### Takeaway
Two complementary models dominate: Things' time-horizon buckets (Today / This Evening / Upcoming / Anytime / Someday, driven by a start date) for personal focus, and Linear's team Triage inbox (accept / decline / duplicate / snooze, with rotating owner and auto-routing rules) for incoming work that nobody has claimed yet. Asana's My Tasks bridges them with a "Recently assigned" landing zone and auto-promotion by date.

### Cited Findings
- Things 3: Today = only to-dos you'll work on today; optional "This Evening" section at bottom of Today; Upcoming = scheduled/future items plus repeating to-dos, deadlines and calendar events; Anytime = Today plus everything without a start date; Someday = on-hold ideas that do not appear in Anytime or Upcoming — [Cultured Code support](https://culturedcode.com/things/support/articles/4001304/); [Apple App Store story on Things](https://apps.apple.com/us/story/id1437936658)
- Setting a start date moves a to-do to Upcoming; "Clear" returns it to Anytime; the lists are mutually exclusive — [Cultured Code support](https://culturedcode.com/things/support/articles/4001304/)
- Linear Triage: a team inbox for issues created by integrations or people outside the team; excluded from normal views. Actions: Accept (optionally comment, move to default status), Decline (cancel with optional explanation), Duplicate (merge into existing, moving attachments/requests), Snooze (hide until a time or until new activity, whichever first; hidden for everyone), or comment and leave in Triage to ask creator for info. Shortcuts: G T open, 1 accept, 2 decline, 3/MM duplicate, H snooze — [Linear docs: Triage](https://linear.app/docs/triage)
- Linear Triage rules (Business/Enterprise) run top-down when issues enter Triage and set team, status, assignee, label, project or priority; conflicts are shown in UI — [Linear docs: Triage](https://linear.app/docs/triage)
- Linear "Triage responsibility": nominated members notified or auto-assigned in rotation, skipping inactive / out-of-office members; does not balance workload; team can see who is currently on triage duty — [Linear docs: Triage](https://linear.app/docs/triage)
- Linear My Issues has four tabs: Assigned, Created, Subscribed, Recent activity — [Linear docs: My Issues](https://linear.app/docs/my-issues)
- Asana My Tasks default sections: Recently Assigned, Today, Upcoming, Later; any task assigned to you lands in Recently Assigned; tasks auto-promote to Upcoming one week out and Today one day out (tasks without due dates stay put); custom rules can move tasks between sections based on due date or completion — [Asana My Tasks feature page](https://asana.com/features/project-management/my-tasks); [Asana: 4 ways to master My Tasks](https://asana.com/resources/asana-tips-my-tasks.md)
- Asana My Tasks rules reportedly run at midnight, so a new rule doesn't apply until then (community report) — [Asana forum](https://forum.asana.com/t/asana-my-tasks-section-do-today-do-tomorrow-do-this-week/145260)
- Akiflow's core is a "universal inbox" pulling tasks from many tools into one triage view (reviewers say 30+; vendor says 3,000+ via integrations — conflicting) — [Business Dive review](https://thebusinessdive.com/akiflow-review); [Akiflow universal inbox](https://akiflow.com/universal-inbox)

### Inferences
- For Tekly: a per-staff "My work" with Today / Upcoming / Later (+ optional Someday) driven by a start ("when") date, plus a "New for you" / Recently-assigned landing zone so handed-off tasks are always noticed.
- A shop-level "Unassigned / Triage" queue (tasks auto-created by events, customer requests, tasks created by others for the shop) with one-key Accept/Assign, Decline, Merge, Snooze maps well to a front desk; a rotating "on triage today" owner is a cheap, high-value borrow.
- Team view = same data grouped by person, so managers see load; Linear explicitly does not balance load, which is an opening.

### Gaps
- Linear Cycles were not researched in depth this pass (likely less relevant for small shops than time-horizon buckets).
- No usage data comparing Things-style buckets vs Asana-style sections.

## Time: snooze/defer, start vs due, time-blocking, auto-scheduling, recurrence, overdue without shame

### Takeaway
Separate "when I'll start/see it" from "when it's actually due" (Things When vs Deadline; Todoist added a separate Deadline in 2024). Snooze that auto-cancels on new activity (Linear, Superhuman "if no reply") is the most praised defer mechanic. Overdue is best handled by a one-click bulk "Reschedule" and a daily review ritual that rolls work forward (Sunsama), not by an ever-growing red list. Fully automatic AI scheduling (Motion) is polarising.

### Cited Findings
- Things: a start date "merely indicates the day on which you want to begin work"; Deadlines are separate, and tasks with a deadline stay active in Anytime; for multi-day work, set a start date several days before the deadline — [Cultured Code support](https://culturedcode.com/things/support/articles/2803579/)
- Todoist introduced a separate "deadline" field alongside the do-date (community discussion, 2024) — [r/todoist via redlib](https://redlib.cypherpunktools.com/r/todoist/comments/1feddc5/early_thoughts_on_new_deadline_feature_a_true)
- Linear snooze (H) hides a notification/issue until a time; if new activity occurs before then you get notified and the snooze is cancelled. Reminders are separate: snooze hides, reminders don't — [Linear docs: Inbox](https://linear.app/docs/inbox); [Linear docs: Snooze](https://linear.app/docs/snooze)
- Superhuman reminders default to "if no reply": the thread returns to the top of the inbox at the set time only if nobody responded; a reply cancels the reminder — [Superhuman help: Remind Me](https://help.superhuman.com/hc/en-us/articles/43822934607251-Remind-Me); also "Auto Reminders" configurable in settings — [Superhuman: Reminders on Autopilot](https://help.superhuman.com/hc/en-us/articles/45270478397203-Reminders-on-Autopilot)
- Todoist overdue: a "Reschedule" button next to the Overdue section reschedules them in bulk (web and mobile) — [Todoist help: dates](https://todoist.com/help/articles/205325931); its 2016 "Smart Schedule" suggested new dates for all overdue tasks in one action, editable/rejectable — [MacStories](https://www.macstories.net/news/todoist-launches-smart-schedule-an-ai-based-feature-to-reschedule-overdue-tasks/)
- Sunsama: daily planning ritual (morning or evening) and a "Today in review" step where unfinished tasks are rolled to tomorrow; settings choose auto-rollover at midnight vs a prompt to pick which move — [Sunsama help: Evening daily planning](https://help.sunsama.com/docs/evening-daily-planning); [Sunsama help: Daily planning](https://help.sunsama.com/docs/daily-planning)
- Reviewers credit Sunsama for making deferral part of the routine; one notes the system breaks if the ritual is skipped — [Asian Efficiency review (2026)](https://www.asianefficiency.com/schedule-management/sunsama-review/); Sunsama now also offers "Autoplan" that drafts the day for review/undo — [Sunsama help](https://help.sunsama.com/docs/daily-planning)
- Things persistence: once a When date is reached, the task stays in Today until completed (community description) — [MPU forum](https://talk.macpowerusers.com/t/q-for-things-3-users-the-when-start-date/19654)
- Motion auto-schedules tasks (with deadline, priority, duration) into open calendar slots and reshuffles when meetings move; most-cited G2 praise is time saved — [G2 Motion](https://www.g2.com/products/motionapp); criticisms: heavy setup and mandatory fields on every task, mobile lags desktop, cost ($19–$49/mo quoted, conflicting), and a 2026 report that the redesigned AI agenda felt useless with constant manual moves — [Upbase review](https://upbase.io/blog/motion-app-review/); [ClickUp Motion review](https://clickup.com/en-US/blog/143115/motion-review)
- Akiflow time-blocking: drag inbox items onto calendar, which blocks time on the connected calendar; reviewer found auto-scheduling still needed manual adjustment — [Business Dive review](https://thebusinessdive.com/akiflow-review)
- Asana auto-promotion by date (week out to Upcoming, day out to Today) is a quieter form of "scheduling" — [Asana My Tasks](https://asana.com/features/project-management/my-tasks)

### Inferences
- For Tekly: two date fields (Start/"Show from" and Due), with Due optional. Snooze = set Start date; auto-unsnooze when the linked record changes (customer replied, part arrived) — the Linear/Superhuman "wake on activity" rule fits repair shops very well (e.g. "remind me if customer hasn't approved quote by Friday").
- Overdue handling: group under a calm "Overdue" header with one-click "Move all to today / pick date", plus an optional end-of-day "wrap up" prompt that rolls unfinished items forward. Avoid red badge counts that only grow.
- Skip Motion-style full auto-scheduling for staff tasks: heavy input burden and polarised reviews; small shops lack per-task duration estimates. Lightweight "plan my day" suggestions with review/undo (Sunsama Autoplan, Todoist Smart Schedule) are the safer AI borrow.
- Recurrence: support NL phrases + "every N days after completion" vs fixed schedule (Todoist distinguishes "every" vs "every!" — not verified in this pass, see gaps).

### Gaps
- Did not verify current Todoist syntax for completion-based recurrence ("every!") in this pass.
- No measurement found on whether auto-rollover improves completion or just hides slippage.

## Collaboration: assignment, handoff, mentions, watching, activity, linking to records

### Takeaway
The repeatable pattern is: assign one owner, auto-subscribe involved people, route all updates for subscribed items into a single inbox with snooze, show a chronological activity log on the item, and let tasks link to (or live inside) the business record they concern.

### Cited Findings
- Linear: subscribe with Shift+S, unsubscribe Cmd/Ctrl+Shift+S; all subscribed issue events go to Inbox (you can't filter what reaches Inbox, only which events trigger other alerts like email/push); subscribed issues listed under My Issues > Subscribed — [Linear docs: Inbox](https://linear.app/docs/inbox); [Linear docs: Notifications](https://linear.app/docs/notifications)
- Linear inbox caps at 2,000 open notifications and auto-archives beyond — [Linear docs: Inbox](https://linear.app/docs/inbox)
- Linear Triage "comment and leave in Triage" lets the triager ask the creator for info without accepting — [Linear docs: Triage](https://linear.app/docs/triage)
- Asana: anything assigned to you appears in Recently Assigned so handoffs aren't missed — [Asana My Tasks](https://asana.com/features/project-management/my-tasks)
- ClickUp distinguishes lightweight Reminders (private by default) from Tasks; reminders can be "delegated" into someone else's personal inbox — [ProcessDriven: ClickUp reminders vs tasks](https://processdriven.co/hub/clickup-reminders-vs-clickup-tasks)
- Notion: task databases relate to projects/clients/team databases, with rollups aggregating across relations; one reviewer claims performance degrades above ~5,000 records (single unverified source) — [ClickUp Learn: Notion for task management](https://clickup.com/learn/topic/task-management/tools/notion/); [project-management.com](https://project-management.com/clickup-vs-notion/)

### Inferences
- For Tekly: every task should optionally link to a record (work order, customer, invoice, purchase order) and appear in that record's sidebar; the record page is where staff already are, so tasks "in context" beat a separate tool.
- A "delegated reminder" (lightweight nudge to a colleague, not a full task) is a good fit for small teams ("Ask Sam to call the customer at 3").
- Auto-subscribe creator + assignee + @mentioned; handoff = reassign with required short note that lands at the top of the new owner's "New for you".

### Gaps
- Did not research Asana/Linear comment threading or "handoff note" UX specifically; no sources on read receipts for task handoff.

## Automation & AI: auto-created tasks, suggestions, summaries

### Takeaway
The successful AI pattern is "suggest with reasons, human accepts" (Linear Triage Intelligence), configurable per team as hidden / shown / auto-applied. The fully "autonomous" pitch (Height) did not survive: Height shut down on 24 Sep 2025. Rule-based routing and event-created tasks remain the dependable automation layer.

### Cited Findings
- Linear Triage Intelligence suggests assignee, labels, duplicates and related issues, each with a short explanation; per-team setting to show, hide or auto-apply suggestions; extra guidance can be set at workspace/team level; Business/Enterprise — [Linear docs: Triage Intelligence](https://linear.app/docs/triage-intelligence)
- Linear also offers cheaper search-based "quick suggestions" in the composer and property menus on all plans — faster but less thorough — [Linear docs: Triage Intelligence](https://linear.app/docs/triage-intelligence); build writeup — [Linear: How we built triage intelligence](https://linear.app/now/how-we-built-triage-intelligence)
- Linear Triage rules auto-set properties for incoming issues; integrations (Slack, Sentry) create issues into Triage automatically — [Linear docs: Triage](https://linear.app/docs/triage)
- Height ("autonomous project management": AI bug triage, backlog pruning, auto-updated specs; Height 2.0 Oct 2024) announced sunset March 2025 and stopped operating 24 Sep 2025; no official reason given — [AlternativeTo news](https://alternativeto.net/news/2025/3/height-project-management-tool-to-shut-down-by-september-2025); [Shortcut: Height alternatives](https://www.shortcut.com/blog/alternatives-to-height-app/)
- Motion rebranded its agents as "AI Employees" (reviewer observation) — [ClickUp Motion review](https://clickup.com/en-US/blog/143115/motion-review)
- Superhuman "Auto Reminders" set follow-up reminders automatically — [Superhuman help](https://help.superhuman.com/hc/en-us/articles/45270478397203-Reminders-on-Autopilot)
- Sunsama Autoplan drafts a day plan the user reviews or undoes — [Sunsama help](https://help.sunsama.com/docs/daily-planning)

### Inferences
- For Tekly, the most valuable automation is deterministic: auto-create tasks from business events (ticket awaiting approval 48h, part received, device ready but not collected, invoice overdue) and auto-close them when the condition resolves. That's the shop equivalent of Linear integrations-into-Triage.
- AI should be suggestion-with-reason (assignee, due date, duplicate) with a per-shop "suggest / auto-apply / off" switch, mirroring Linear.
- Height's failure is a cautionary data point against marketing the product as "autonomous".

### Gaps
- No public adoption/accuracy metrics for Linear Triage Intelligence were found.
- Did not find independent evaluation of AI task summaries.

## Mobile and notifications: useful not noisy

### Takeaway
Leaders converge on: one in-app inbox for everything, separate channel controls (push/email) per event type, snooze on notifications, and now automatic prioritisation (Linear Priority inbox, Sept 2026). Batched/digested delivery has modest research support.

### Cited Findings
- Linear Priority inbox (changelog 3 Sep 2026): inbox split into Priority and Other tabs so urgent items (e.g. a review blocking a release) aren't buried; Linear chooses Priority contents by default, users can pick which sources appear or build a filter, or opt out — criteria not published — [Linear changelog: Priority inbox](https://linear.app/changelog/2026-09-03-priority-inbox)
- Linear: everything goes to Inbox, while push/email/Slack triggers are configurable per event type — [Linear docs: Notifications](https://linear.app/docs/notifications)
- Duke University study (APA conference; secondhand coverage): batching phone notifications three times a day made participants happier, less stressed, more productive and more in control — [Business Insider](https://www.businessinsider.in/smartphone-notifications-are-becoming-dangerous-and-theres-an-easy-fix-that-could-vastly-improve-your-life/amp_articleshow/63762019.cms)
- Vendor claims (unverified): digests yield 35% higher engagement than individual alerts (attributed to Braze); users getting >10 notifications/week twice as likely to disable notifications (attributed to Localytics) — [Courier blog](https://www.courier.com/blog/how-to-reduce-notification-fatigue-7-proven-product-strategies-for-saas); guidance to put low-priority updates in an in-app centre or digest, not push — [SuprSend](https://www.suprsend.com/post/notification-batching-and-digest)
- Mobile weaknesses cited in reviews: Motion's mobile lags desktop; Akiflow's mobile beta / command bar desktop-only — [Upbase Motion review](https://upbase.io/blog/motion-app-review/); [Business Dive Akiflow review](https://thebusinessdive.com/akiflow-review)

### Inferences
- For Tekly: push only for (a) assigned to me, (b) @mention, (c) my task due soon / customer-blocking; everything else in-app inbox + an optional morning digest ("Today: 5 tasks, 2 overdue, 1 handed to you"). Notifications should be snoozable and auto-clear when the task is done.
- Mobile must at minimum: view Today, complete, snooze, quick-add (voice later), and handle push deep links into the task.

### Gaps
- Linear's Priority classification criteria unpublished.
- No peer-reviewed CHI/CSCW study comparing batched vs immediate work notifications was located.

## Widely praised vs gimmicky (evidence)

### Takeaway
Praised and durable: NL quick add (Todoist), start-vs-deadline split and calm time buckets (Things), keyboard palette + triage inbox (Linear), "if no reply" reminders (Superhuman), daily plan/review ritual (Sunsama). Contested or gimmicky: fully autonomous/auto-scheduling AI (Height shut down; Motion polarising), sprawling universal-inbox integrations at high price (Akiflow), and over-customisable all-in-one workspaces (ClickUp/Notion) for small teams.

### Cited Findings
- Things 3 widely reviewed as "beauty and delight in a task manager" — [MacStories review](https://www.macstories.net/reviews/things-3-beauty-and-delight-in-a-task-manager/); design study of its focused simplicity — [Blake Crosley on Things](https://blakecrosley.com/guides/design/things)
- Motion: praised for time saving, criticised for setup burden, mandatory fields, price; "is it a gimmick" framing appears in reviews — [Upbase review](https://upbase.io/blog/motion-app-review/); [G2](https://www.g2.com/products/motionapp)
- Height shut down despite "autonomous" positioning — [AlternativeTo](https://alternativeto.net/news/2025/3/height-project-management-tool-to-shut-down-by-september-2025)
- Akiflow criticised on price ($19–$34/mo quoted, conflicting), shortcut learning curve, mobile — [Saner.ai Akiflow reviews](https://blog.saner.ai/akiflow-reviews); [usecarly](https://www.usecarly.com/blog/akiflow-review/)
- Sunsama ritual praised but fragile if skipped — [Asian Efficiency](https://www.asianefficiency.com/schedule-management/sunsama-review/)
- Linear's keyboard-first approach copied industry-wide (Vercel, GitHub, Slack, Raycast palettes) — [DEV Community](https://dev.to/thekitbase/cmdk-is-the-new-hamburger-menu-why-command-palettes-are-taking-over-saas-81d)

### Inferences
- Borrow list for Tekly (priority order): 1) NL quick-add with chips + structured fallback; 2) Start vs Due + Today/Upcoming/Later buckets; 3) snooze that wakes on record activity / "if no reply"; 4) shop triage queue with one-key actions and rotating owner; 5) event-generated tasks that self-close; 6) bulk reschedule overdue + optional end-of-day wrap-up; 7) single inbox + Priority/Other split + morning digest; 8) Cmd-K palette; 9) AI suggestions with reasons, opt-in.
- Avoid: auto-scheduling into calendars, mandatory duration fields, gamified streaks/shame counts, and "autonomous AI" branding.

### Gaps
- Most review sources are SEO/comparison blogs of mixed quality; few independent usability studies exist for these tools.
- Notion and ClickUp were covered only lightly (relations, reminders); their task UX is less often cited as best-in-class.
