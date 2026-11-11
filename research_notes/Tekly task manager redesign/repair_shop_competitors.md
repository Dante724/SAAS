# Task management in repair-shop and field-service software (researched 2026-11-11)

Scope: Orderry, RemOnline, RepairShopr, RepairDesk, CellSmart, RepairQ, Jobber, Housecall Pro, ServiceTitan. All findings come from public vendor help centers, marketing pages and review sites, read on 2026-11-11. Most help-center articles show no publish date. Where a date was visible it is noted.

## Orderry's task module (what to avoid copying)

### Takeaway
Orderry has a real, separate Tasks section, but it is basic: a list view with a personal agenda, a short form (title, description, due date, one or more assignees), links to orders, clients and leads, an event feed for comments and files, notifications on assign/change/complete, and an on-time vs late flag. It has no recurring tasks, and I found no kanban view, calendar view, priorities, checklists or status workflow beyond open/done. Its two distinctive ideas are the "one task for all" vs "one task for each" assignment modes and paying commission for completed tasks.

### Cited Findings
- **Purpose and placement.** The help center sells Tasks as a tool for managers to delegate work and monitor staff. Example uses: buying office supplies, reminding someone to call a client or bill an order, rent or tax reminders. — [Orderry help: General information about the Tasks section](https://help.orderry.com/en/articles/9130708-general-information-about-the-tasks-section)
- **Where tasks are created.** From the Tasks page, or from the activity feed of work orders, invoices, inquiries, quotes, or a contact's profile. Creating a task inside a record links it to that record. — [Orderry help: General information](https://help.orderry.com/en/articles/9130708-general-information-about-the-tasks-section); [Orderry help: How to add a new task](https://help.orderry.com/en/articles/9130710-how-to-add-a-new-task)
- **Form fields.** Task name (max 128 characters), Description, Due date, Assignee with a "+ Assignee" button for more, and an optional linked record. The article mentions no priority, observers/watchers, checklist, attachments or reminder fields. — [Orderry help: How to add a new task](https://help.orderry.com/en/articles/9130710-how-to-add-a-new-task)
- **Assigning several people.** With multiple assignees you pick a mode. "One task for all" makes one shared task: when one person completes it, it disappears from everyone else's panel and the credit goes to that person. "One task for each one" creates a separate copy per person. — [Orderry help: How to add a new task](https://help.orderry.com/en/articles/9130710-how-to-add-a-new-task)
- **Permission.** An employee can only be picked as an assignee if their role has "Can be an Assignee" enabled. Roles also control who can modify or complete tasks. — [Orderry help: How to add a new task](https://help.orderry.com/en/articles/9130710-how-to-add-a-new-task); [Orderry task manager page](https://orderry.com/online-task-manager/)
- **Overdue.** A missed due date marks the task overdue and shows a flashing red indicator in the side menu. — [Orderry help: How to add a new task](https://help.orderry.com/en/articles/9130710-how-to-add-a-new-task)
- **Done state.** Ticking "Completed" moves the task to a Done tab. Opening a completed task shows whether it was On Time or Overdue. — search summary of [Orderry help article 9130708](https://help.orderry.com/en/articles/9130708) (this exact wording was not in my direct fetch; treat as moderately confident)
- **Event feed.** Each task has a Task Event Feed that logs all employee actions. Comments, documents and images can be added there. Commenting works on desktop and mobile. — [Orderry help: General information](https://help.orderry.com/en/articles/9130708-general-information-about-the-tasks-section); [Orderry task manager page](https://orderry.com/online-task-manager/)
- **Views.** Marketing describes a "Convenient List View Instead of Sticky Notes Mess", a personal agenda sorted by due date and author, a counter of today's tasks, and filters by role, author or assignee. No kanban or calendar view is mentioned. — [Orderry task manager page](https://orderry.com/online-task-manager/)
- **Notifications.** People involved are notified by SMS, email or messenger when a task is assigned, changed or completed. — [Orderry task manager page](https://orderry.com/online-task-manager/)
- **Payroll link.** Incentives or commissions for closing tasks are calculated automatically through payroll setup. — [Orderry task manager page](https://orderry.com/online-task-manager/)
- **Audit.** The Employee Activity Log tracks deleted tasks and can restore them. — [Orderry task manager page](https://orderry.com/online-task-manager/)
- **Recurring tasks.** Not available. The FAQ says "It is not possible in the current version" and that support is planned. — [Orderry task manager page](https://orderry.com/online-task-manager/)
- **Mobile.** Tasks are "synced to your devices", and there is a free work order app for field teams. — [Orderry task manager page](https://orderry.com/online-task-manager/)
- **Reviews.** Orderry is rated 4.7 from 35 reviews on Capterra. The cons I found mention a dated design, a less intuitive UI, localization problems and a pricey top tier. None of them were about tasks. — [Capterra Orderry reviews](https://capterra.com/p/151174/Orderry/reviews/)

### Inferences
- Orderry's Tasks module reads as a generic "office to-do" bolted onto the CRM. Its examples (office supplies, rent) are not repair-workflow tasks. The repair job itself is tracked through work-order statuses, not tasks. That leaves room for a task model that is native to the repair workflow (bench handoffs, parts waits, customer callbacks).
- Things that are specifically Orderry and that we should not clone one-for-one: the "One task for all / One task for each one" naming and toggle, the "Can be an Assignee" role right, the flashing red side-menu overdue indicator, the "Task Event Feed" naming, and paying commission per closed task.
- Their weak points: no recurring tasks, no checklists or subtasks, no priority, no board or calendar, no automations that create tasks (for example from a status change), and a short 128-character title.

### Gaps
- I could not find screenshots or a changelog with dated Tasks updates. The Orderry help center articles show only "Updated over a week ago."
- I could not confirm whether notification channels are per-user configurable, or whether there is a calendar view of tasks inside Orderry's scheduler.

## RemOnline (same lineage as Orderry)

### Takeaway
RemOnline markets the same task capabilities as Orderry: deadlines, assign/complete notifications, commission for tasks, and activity tracking. I found no separate RemOnline task documentation. The help-center structure and feature claims look shared with Orderry.

### Cited Findings
- RemOnline's employee-management page says its team task tools let managers coordinate work, set deadlines, send notifications on assignment and completion, set up commission calculations and track progress in real time. It also mentions an activity log of employee actions and IP whitelisting. — [RemOnline employee management](https://remonline.app/features/employee-management)
- RemOnline announced clock-in/clock-out time tracking as "coming soon"; the article's date was not visible. — [RemOnline help: time tracking coming soon](https://help.remonline.app/en/articles/11186615-coming-soon-time-tracking-with-timesheet-recording-clock-in-clock-out)

### Inferences
- The overlapping feature claims (commissions on tasks, assign/complete notifications) point to a shared codebase between RemOnline and Orderry. Treat them as one product for competitive purposes.

### Gaps
- I did not verify RemOnline's task UI directly. I also did not confirm the exact corporate relationship between Orderry and RemOnline.

## How the others handle tasks, follow-ups, reminders, handoffs, waiting states and SLAs

### Takeaway
No repair-shop competitor I looked at has a strong task manager. RepairShopr has customer "Reminders" plus recurring tickets. RepairDesk and CellSmart lean on ticket status, assignment, private notes and automated customer SMS. RepairQ relies on custom statuses. Of the field-service tools, ServiceTitan (office-only tasks with subtasks, statuses, a source field and comments) and Housecall Pro (a new linked-tasks feature, July 2026) are the most developed. Jobber has no real to-do list. Waiting-on-parts and waiting-on-customer are handled as custom ticket statuses everywhere. I found no repair tool that pauses an SLA or turnaround clock based on status.

### Cited Findings

**RepairShopr**
- Reminders can be attached to a customer, assigned to a tech and given a due date. Upcoming reminders show on the welcome screen and in the user menu. Editing reminders was added later; before that you had to delete and recreate them. — [RepairShopr KB: create reminders](https://feedback.repairshopr.com/knowledgebase/articles/519504-create-reminders-in-repairshopr); [RepairShopr blog](https://www.repairshopr.com/?p=5948)
- Recurring tickets can serve as reminders for techs and for repeat jobs. — [RepairShopr Feature Friday: recurring tickets](https://www.repairshopr.com/blog/posts/feature-friday-recurring-tickets)
- Ticket settings can send diagnostic email reminders daily or hourly. Marketr sends automated follow-ups at 14 and 90 days, enabled "with one click". — [RepairShopr features](https://www.repairshopr.com/features)
- Users filed a feature request titled "Ticket reminders / notification for Stale tickets" on the RepairShopr UserVoice. I could not load the vote count or its status. — [RepairShopr UserVoice](https://feedback.repairshopr.com/forums/165658-general/suggestions/15972691-ticket-reminders-notification-for-stale-tickets)
- Many shops connect RepairShopr to external to-do apps through Zapier (Todoist tasks from new tickets, Things to-dos when tickets resolve). That points to unmet native task needs. — [Zapier RepairShopr+Todoist](https://zapier.com/apps/repairshopr/integrations/todoist/1734119/create-new-todoist-tasks-from-new-repairshopr-tickets); [Zapier RepairShopr+Things](https://zapier.com/apps/repairshopr/integrations/things/1618071/create-to-dos-in-things-when-tickets-are-resolved-in-repairshopr)

**RepairDesk**
- Ticket management covers filters by name, type, status and created date; scannable ticket labels; private notes on tickets; automatic repair-job notifications to technicians; and CSV export. — [RepairDesk repair ticket management](https://repairdesk.co/feature/repair-ticket-management)
- RepairDesk's own 2026 blog puts a number on manual follow-ups: part-delay follow-up calls (5 × 4 min = 20 min) and manual pickup reminders (5 × 3 min = 15 min). It pitches automation as the fix. These are vendor estimates. — [RepairDesk blog, 2026-06-11: Hidden cost of manual follow-ups](https://blog.repairdesk.co/2026/06/11/the-hidden-cost-of-manual-follow-ups-in-repair-shops/); [RepairDesk blog, 2026-05-20](https://blog.repairdesk.co/2026/05/20/the-real-reason-your-staff-spends-2-hours-a-day-on-the-phone/)
- I found no RepairDesk documentation for a standalone task or to-do module, ticket due-date alerts, or SLA timers. — searches noted under Gaps

**CellSmart POS**
- Each repair ticket gets a deadline for the assigned technician, and you are notified when the repair is complete. Assigning a ticket prompts the tech to start work, and notes can be added. Only the assigned tech can see the device passcode or pattern; other techs need manager or owner approval. — [CellSmart repair delay reminder](https://www.cellsmartpos.com/features/repair-delay-reminder)
- The "Repair Delay Reminder" is mostly customer-facing: SMS pickup and payment nudges after completion. CellSmart's own blog also mentions reminders when repairs take longer than they should and tracking devices left on the shelf for more than X days. — [CellSmart repair delay reminder](https://www.cellsmartpos.com/features/repair-delay-reminder); [CellSmart blog](https://www.cellsmartpos.com/blog/mobile-repair-service-management-software)

**RepairQ**
- Shops "create and track your unique workflow with statuses and forms". Qpop, a point-of-purchase app, captures estimates, authorizations and waiver signatures. — [RepairQ repair tracking](https://repairq.io/repair)

**Jobber**
- Jobber has no native to-do list. A community thread asks for "Adding a to-do task manager". A Jobber reply says some teams use "tasks on the schedule" as reminders, and one user tracked to-dos in Google Tasks. — [Jobber community thread](https://community.getjobber.com/discussions/online-booking-requests/adding-a-to-do-task-manager/9439) (403 when fetched directly; content is from the search summary)
- Automatic internal quote reminders appear on the schedule when a quote has gone unapproved for N days (adjustable). Client quote follow-ups (up to 2) and past-due invoice follow-ups (2) require the Connect plan or higher. Review requests send up to 2 follow-ups. — [Jobber help: reminders on the schedule](https://help.getjobber.com/en/articles/reminders-on-the-schedule/); [Jobber automations](https://help.getjobber.com/en/articles/automations/)

**Housecall Pro** (article dated 2026-07-13)
- Fields: title (required), description, a single assignee, due date, and "Linked to" (customer, job or estimate; leave blank for a stand-alone task). — [HCP help: Tasks on jobs and estimates](https://help.housecallpro.com/en/articles/15351133-tasks-on-jobs-and-estimates)
- Tasks appear in four places: a global list (top-right icon on web, dashboard on mobile, with All/My toggles); an Incomplete/Completed section on each job or estimate, sorted by due date with overdue dates in red; a Tasks card on the customer profile filtered by All/Customer/Job/Estimate (web only); and activity-feed entries. — same source
- Notifications: on assignment (not when you assign yourself), and a "due soon" alert at 5:00 AM local time on the due date, plus an email digest of that day's and overdue tasks. Tapping one opens the linked job. — same source
- Permissions: office staff can do everything. Field techs can create tasks and edit/complete their own, but cannot delete tasks or see other people's tasks in the main list; they can see all tasks on jobs they are assigned to. — same source
- Multiple assignees, comments and attachments are listed as "not yet supported / on the roadmap". Auto-generated service-agreement reminder tasks cannot be turned off, only filtered out. — same source; [HCP help: Tasks](https://help.housecallpro.com/en/articles/2810183-tasks)

**ServiceTitan** (doc updated 2026-11-04)
- Task Management is for office staff only; technicians cannot use it. It needs account configuration from Support and admin permission. — [ServiceTitan: Common use cases for task management](https://help.servicetitan.com/commercial/docs/common-use-cases-for-task-management)
- Fields: name, description, Source (for example "Facebook" for a review complaint), assignee, due date, a linked customer (not a location), work order or project, subtasks, and comments with attachments. Statuses are To Do → In Progress → completed, and closing requires a resolution note. The docs warn that leaving finished tasks open causes "inaccurate reporting". — same source
- Text/email alerts go to both reporters and assignees, with an in-app badge. Documented use cases: customer-complaint escalation, refunds routed to accounting, and warranty part returns. — same source
- A separate Follow Up screen covers unsold estimates and recurring service events that are due to be booked. Task conversations with @mentions are in private preview, and an Automations screen (follow-ups after a job closes, project-status reminders) is in Early Access for Roofing. — [ServiceTitan Follow Up screen](https://help.servicetitan.com/commercial/docs/use-the-follow-up-screen); [ServiceTitan Automations screen](https://help.servicetitan.com/release-hub/docs/set-up-and-monitor-your-own-automations-from-the-automations-screen); [ServiceTitan opportunity follow-up reminders (mobile)](https://help.servicetitan.com/docs/set-follow-up-reminders-for-opportunities-in-fma)

### Inferences
- **Handoffs between front counter and bench** are done everywhere through ticket assignment, status changes and private notes, not tasks. None of these tools model a handoff as a task with an acknowledgment step.
- **Waiting on parts or on the customer** is a custom ticket status (RepairShopr, RepairQ, RepairDesk). Nothing I found triggers a follow-up task when a part arrives or schedules a reminder after N days in "waiting on customer". Helpdesk tools like Freshdesk do pause SLA clocks on such statuses ([Freshdesk custom statuses](https://freshworks.com/freshdesk/helpdesk-customization/custom-ticket-statuses)), which repair tools lack. This is a clear opportunity.
- **Turnaround or SLA timers**: CellSmart's per-ticket deadline and "taking longer than it should" reminder came closest. No repair tool showed a visible countdown that pauses on waiting statuses.
- **Converging pattern (table stakes for 2026):** a task linked to a customer or job, one assignee, a due date, overdue shown in red, a My/All toggle, a notification on assignment, a morning digest, role-based visibility for techs, and an activity-feed log.
- **Differentiators only a few have:** subtasks, a source field and a resolution note (ServiceTitan); split vs shared multi-assignee (Orderry); commission per task (Orderry/RemOnline); auto-generated tasks from service agreements (HCP).

### Gaps
- I found no documentation for a RepairDesk task module, RepairDesk SLA/due-date alerts, or RepairQ waiting-state automation.
- I could not access the RepairShopr UserVoice vote counts.
- I did not find a kanban or board view of tasks in any of these tools. Absence is likely but not proven.

## What users complain about (tasks, to-dos, follow-ups)

### Takeaway
I found little direct review text about task features. The strongest signals are indirect: forum and feature requests (Jobber to-do manager, RepairShopr stale-ticket reminders), heavy use of Zapier to push tickets into Todoist, Things or Google Tasks, and vendors' own blog content about time lost to manual follow-ups.

### Cited Findings
- RepairShopr users praise the automatic follow-up emails (two in the first two weeks, then one at 90 days). Others complain that customization was "not as flexible as we needed" and about crashes. On G2, a reviewer called it "so customizable", which helps until fields are set up wrong. — [Capterra RepairShopr reviews](https://www.capterra.com/p/133945/RepairShopr/reviews/); [G2 RepairShopr reviews](https://www.g2.com/products/RepairShopr/reviews)
- Jobber users asked for a to-do/task manager and fall back on Google Tasks or schedule items. — [Jobber community](https://community.getjobber.com/discussions/online-booking-requests/adding-a-to-do-task-manager/9439)
- On G2, Housecall Pro reviewers cite "missing features, lacking customization" for scheduling (7 reviews). Connecteam's review says HCP handles basics well but has "significant shortcomings for team management". — [G2 Housecall Pro reviews](https://g2.com/products/housecall-pro/reviews); [Connecteam HCP review](https://connecteam.com/reviews/housecall-pro/)
- None of Orderry's Capterra cons were task-related; the complaints are design, intuitiveness, localization and price. — [Capterra Orderry reviews](https://capterra.com/p/151174/Orderry/reviews/)
- Zapier templates for Housecall Pro→Todoist/Any.do/Google Tasks and RepairShopr→Todoist/Things exist and are promoted. — [Zapier HCP→Todoist](https://zapier.com/apps/housecall-pro/integrations/todoist/1395241/create-todoist-tasks-from-new-completed-housecall-pro-jobs); [Zapier RepairShopr→Todoist](https://zapier.com/apps/repairshopr/integrations/todoist/1734119/create-new-todoist-tasks-from-new-repairshopr-tickets)

### Inferences
- The recurring pain is follow-ups falling through the cracks: stale tickets, unanswered quotes, parts delays, pickups. Generic to-do lists don't solve it. Shops want reminders that are generated automatically and tied to the ticket's state.
- Things users visibly value: automatic customer follow-ups (RepairShopr Marketr, Jobber quote/invoice follow-ups) and passcode access limited to the assigned tech (CellSmart).

### Gaps
- My Reddit searches (r/mobilerepair, r/msp) returned no usable threads. I found no Facebook group content. YouTube demos were not reviewed.
- I found no review text quantifying how much users love or hate a specific task module.

## Table stakes vs genuinely loved

### Takeaway
Table stakes: a task linked to a job or customer, an assignee, a due date, red overdue indicators, My/All views, assignment and due-day notifications, permissions that limit what techs see, and an activity log. Genuinely loved: automation that removes manual chasing (auto follow-ups, auto reminders on stale quotes or tickets). No competitor offers state-aware repair tasks (parts arrived, customer silent for N days, turnaround about to breach).

### Cited Findings
- The table-stakes set is consistent across [Orderry](https://help.orderry.com/en/articles/9130710-how-to-add-a-new-task), [Housecall Pro](https://help.housecallpro.com/en/articles/15351133-tasks-on-jobs-and-estimates) and [ServiceTitan](https://help.servicetitan.com/commercial/docs/common-use-cases-for-task-management).
- Praise centres on automation: RepairShopr's one-click follow-ups ([Capterra](https://www.capterra.com/p/133945/RepairShopr/reviews/)) and Jobber's automatic quote reminders ([Jobber help](https://help.getjobber.com/en/articles/reminders-on-the-schedule/)).

### Inferences
- Open space for Tekly, with nobody I found doing it well: tasks created automatically from ticket status (waiting on parts → follow up with the supplier in 2 days; waiting on customer → call in 24h); a turnaround timer that pauses on waiting states; a bench handoff that needs acknowledgment; recurring tasks (Orderry lacks them); subtasks/checklists in repair tools (only ServiceTitan has subtasks); multiple assignees with comments and attachments (HCP lacks them); and a board or calendar view of tasks (none found).

### Gaps
- No usage data or survey sizes the demand for each of these features.
