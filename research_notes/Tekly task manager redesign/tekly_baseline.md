# Tekly task manager: current baseline (codebase study)

All citations are to `origin/roadmap/orderry-parity` at commit `6af48719` (2026-11-11, "Merge pull request #194 ... fix/list-search-boxes"), read with `git show origin/roadmap/orderry-parity:<path>`. Note: the local branch `roadmap/orderry-parity` is stale (`1a96a43b`, 2026-11-01), so the remote-tracking ref was used. Sources are written as `path:line`. Nothing was checked out or modified.

## 1. Where tasks are modelled, and what fields exist

### Takeaway
There is one flat `tasks` table with a three-state status, a numeric-as-text priority, one due datetime, a primary assignee plus a JSON list of extra assignees, and optional links to a job, client, lead, location and parent task. Comments live in the shared event feed. There is no recurrence, checklist, reminder, label, attachment, estimate or start-date field.

### Cited Findings
**Model**: `Task` in `backend/models/__init__.py:1752-1779` (section header "TASKS & COMMS", line 1748-1750):
- `id`, `org_id`, `title` String(500), `description` Text (lines 1754-1757).
- `assigned_to_id` FK employees: the "primary owner" (1758). `assignee_ids` JSON list: "multiple technicians (primary owner stays assigned_to_id)" (1759).
- `created_by_id` FK employees (1760).
- Links: `order_id` (indexed, 1761), `client_id` (1762), `lead_id` (1763), `location_id` (1764), `parent_task_id` self-FK for subtasks (1765). All added in "Build 86".
- `completed_on_time` Boolean: "on-time analysis" (1766).
- `status` String(30), default `"todo"` (1767). `priority` String(20), default `"normal"` (1768). A `@validates` hook turns ints into strings because "The tasks API sends numbers (0, 1, 2); the column is text ... creating any task 500'd in production" (1770-1774).
- `due_at`, `completed_at`, `created_at` DateTime (1775-1777). There is no `updated_at` and no start date.
- Relationships: `assigned_employee` and `order` (1778-1779).

**Migrations:**
- Original table: `backend/migrations/versions/03851168a43b_initial_schema.py:1224-1242`. It has title, description, assigned_to_id, created_by_id, order_id, status, priority, due_at, completed_at and created_at.
- Build 84-90: `d1fa0ef2213c_build84_90_warehouses_tasks_returns_.py:197-208` adds client/lead/location/parent_task links, indexes and FKs.
- Build 144ex: `e1g3i5k7m9o1_144ex_task_assignees.py:18` adds `assignee_ids` JSON.
- Build 144x: `f6b8d0c2e4a7_b144x_clock_toggles_task_timer.py:21-22` adds `time_entries.task_id`. Its FK is `ondelete="SET NULL"` because "tasks are hard-deleted; keep the time (payroll history)" (`backend/models/__init__.py:2421`).
- `aqp01_hot_fk_and_list_indexes.py:26` indexes `tasks.order_id`.

**API**: `backend/routers/tasks.py`, prefix `/api/tasks` (line 23). Its docstring calls it "Build 86 — Tasks, properly" and frames it against Orderry (1-8).
- `STATUSES = ["todo", "in_progress", "done"]` (25).
- `GET /api/tasks` (54-87) filters by `status`, `assigned_to_id`, `order_id`, `client_id`, `mine` and `overdue`. `mine` is filtered in Python after loading every task in the org (78-80). The sort is open before done, then due_at ascending (82-83). There is no pagination and no text search. Every employee can list every task in the org. No permission scopes visibility.
- `_ser` (28-46) computes `overdue` at read time (30) and returns `assignee_names`, `author_name`, all link ids and `completed_on_time`.
- `POST /api/tasks` (114-136) uses an Idempotency-Key (`idem_begin`, 126-129). `TaskIn` (100-111) accepts title, description, assigned_to_id, assignee_ids, priority int (default 0), due_at, order_id, client_id, lead_id, location_id and parent_task_id.
- `POST /api/tasks/part-request` (139-178) is Tekly Go's "order a part" flow. It creates a priority-1 task titled "Order part: ... for {order_number}" and assigns it to the job's manager.
- `GET /api/tasks/{id}` (200-220) adds `order_number`, `client_name` and a `subtasks` array.
- `PATCH /api/tasks/{id}` (223-292). `TaskPatch` cannot change lead, location or parent (223-232). On `done` it sets `completed_at` and `completed_on_time = due_at is None or completed_at <= due_at` (271-274), writes a feed line ("Task completed (late)"), sends a notification and calls payroll `_pay` (275-278). Reopening clears both fields (279-281).
- `DELETE /api/tasks/{id}` (295-305) hard-deletes the row.
- `GET /{id}/feed` and `POST /{id}/comments` (309-342) use the shared `EventFeedItem` with `entity_type="task"`. Comments are plain text, with no attachments or @mentions.
- Permissions: assigning anyone other than yourself needs `can_assign_staff` (350-353). Edit, complete or delete is limited to the creator, an assignee or a `can_assign_staff` holder (356-359).

**Legacy duplicate API**: `backend/routers/staff.py:683-740` (prefix `/api/staff`, line 18) still exposes `GET/POST /api/staff/tasks` and `PATCH /api/staff/tasks/{id}`. These have no permission checks, no feed, no notifications and no payroll, and parse dates with `datetime.fromisoformat` (`TaskIn` and `TaskUpdate` at 92-101). No frontend file calls `/staff/tasks`; a grep of `frontend-react/src` found only `/tasks` callers.

**Fields that do NOT exist**: recurrence, checklist items, reminders or reminder offsets, labels/tags, attachments on tasks, estimated time, a start date, watchers, a task type or category, and archive or soft-delete. Searches of the model, the router and the migrations found none of them.

### Inferences
- Subtasks are modelled (`parent_task_id`, a `subtasks` array in GET) but cannot be created from any UI (see section 2), so in practice they are an API-only feature.
- `priority` is a string column that the API treats as 0/1/2, while the legacy paths write strings: "normal" (staff.py, comms.py) and "high" (leads.py). A redesign should normalise this to an enum.

### Gaps
- I did not verify how `EventFeedItem` handles files. The Build 86 docstring says "event feed for comments and files" (`tasks.py:5-6`), but the task router has no upload endpoint.

## 2. Frontend screens and components that show tasks

### Takeaway
There are three web surfaces: a single `/tasks` table page with a detail drawer, an inline task list inside the work-order drawer, and a dashboard "home feed" counter. Tekly Go adds a grouped "my tasks" tab and a quick-add sheet. There is no kanban, calendar or "my day" planner view, and the list cannot be filtered by person, job or priority.

### Cited Findings
**Web `/tasks` page**: `frontend-react/src/pages/tasks/TasksHub.tsx` (218 lines). The route is at `frontend-react/src/App.tsx:19,65`. It sits in the sidebar group "Workshop", next to Work orders, My work and Timesheets (`frontend-react/src/components/Shell.tsx:133-139`).
- Toolbar: a "New task" primary button on the left, then filter pills "All · To do · In progress · Done · Late" (TasksHub.tsx:83-90). Filtering is client-side (72). It loads all tasks in one call (39) and pages them client-side at 50 per page (74-78, 118).
- Table columns: done checkbox · Task · Status (badge) · Links (only a WO badge) · Assigned to (first 2 names + "+N") · Due (red when overdue) (93-115). Priority, client and creator are not shown. There is no sorting UI and no search box.
- Empty state: "No tasks here." with a "New task" button and, when filtered, a "Show all tasks" button (99-105). Loading uses `SkeletonRows` (98).
- Detail drawer (right side, 480px, 123-182) contains:
  - status buttons To do / In progress / Done, plus a "Start timer" button when the `time_clock` feature is on (`hasFeature`, 26; button 138; POST `/timeclock/start?switch=true {task_id, category:"task"}`, 56-59);
  - a read-only description (140);
  - an assignee checkbox list (141-155);
  - a "Linked work order" `JobPicker` (156-159);
  - a read-only client name (160);
  - an "Activity & comments" feed with a comment input (162-178).
- The drawer cannot edit the title, description, due date or priority. It has no delete and does not show subtasks, even though the API returns them.
- New task drawer (440px, 185-214) asks for title*, a multi-assignee checklist, Priority (Normal/High/Urgent → 0/1/2), "Link to work order", and Due (datetime). The footer has the primary button on the left (211). There is no description field and no client/lead link.

**Inside a work order**: `frontend-react/src/pages/orders/JobTasks.tsx` (87 lines), rendered in `WODrawer.tsx:731` between `JobItems` and `IntakePhotos`. The header reads "Add task · Tasks · N open / All done" (46-51). Quick-add is an inline title plus a single-assignee select (52-63). Each row has a checkbox, the title, an "Overdue" flag, an avatar and an assignee select (68-82). There is no due date, priority or detail view. The empty state reads "No tasks on this job yet." (65). Tests are in `JobTasks.test.tsx`. The audit item "J4 — Tasks aren't shown on the job. You link a task by typing the job id" (`docs/audits/2026-10-30-estimate-to-invoice-flow.md:62`) motivated this component.

**Dashboard / home feed**: `GET /home-feed` (`backend/routers/reports.py:1541`) counts open tasks, all of them or only the user's own when the role's `order_scope=="own"` (1590-1593). It emits "N open tasks" linking to `/tasks` (1611). The action label is "Open" (`frontend-react/src/pages/company/CompanyHub.tsx:132`). Owners can switch the "Open tasks" signal on or off per role (`TeamSettings.tsx:26`; migration `hf144ds01_role_home_feed.py:4`). The dashboard stats endpoint also returns `pending_tasks` (`reports.py:93-96,122`).

**Timer integration**: the Shell clock widget names a running timer by its task title (`Shell.tsx:743-749`), and timeclock entries carry `task_id`/`task_title` (`backend/routers/timeclock.py:209-214, 249-253, 291, 323-349`).

**Tekly Go (mobile PWA)**:
- `frontend-react/src/go/screens/Tasks.tsx` (133 lines) shows "mine" (`/tasks?mine=true`, 90). Tasks are grouped Overdue / Today / Coming up / No date / Done using `bucket()` (21-28, 107-129), and Done is collapsed behind "Show done tasks", capped at 20 (124-128). A row has a large check circle, the title, the due time ("Overdue ·" in red), "From X"/"For Y", and an "Open job" link (58-79). Ticking a task goes through the offline queue (96-105). The empty state reads "No tasks · Tasks assigned to you, and ones you make with +, show up here." (119).
- The tab badge counts tasks due today or overdue (`dueCount`, `useTaskBadge`, 30-48; `GoApp.tsx:56,85`; `TabBar.tsx:8,32`).
- `frontend-react/src/go/components/TaskSheet.tsx` (96 lines) creates a task: title; When chips Today / Tomorrow / No date / Other date, where the quick choices mean 18:00 local (14-22, 70-83); Who chips, which are "Me" plus others only with `can_assign_staff` (43-46, 84-91). It is opened from the "+" `NewMenu.tsx:21,44` and from a job's ··· menu (`go/screens/Job.tsx:430,459`). Part requests use `PartRequestSheet.tsx`.

### Inferences
- The web list has no filter by assignee, job or client, even though the API supports `assigned_to_id`, `order_id`, `client_id` and `mine`. A "My tasks" view exists only on mobile.
- The web and mobile UIs model different things. Web is status-centric (todo, in_progress, done). Mobile is date-bucket-centric and ignores `in_progress`.
- Loading all tasks without a limit (CHANGELOG.md:2471 confirms that "/tasks ... take no limit (return all)") will not scale for busy shops.

### Gaps
- No kanban, calendar or Gantt task views were found anywhere in `frontend-react/src`. Their absence is confirmed by search, not just unverified.
- I did not check whether client or inquiry (lead) detail screens show the tasks linked to them. A grep for `/tasks` callers found only TasksHub, JobTasks and the Go files, so most likely they do not.

## 3. What looks copied from or modelled on Orderry

### Takeaway
The task module was explicitly built as "Orderry parity" in Build 86. The pieces taken from Orderry are its context links, the on-time completion flag that drives payroll, the rule never to notify the person who acted, and the internal Tasks notifications tab. The UI itself is Tekly's own table plus drawer.

### Cited Findings
- The router docstring says "Orderry's tasks carry the context the assignee actually needs — *what, when, where and for whom* — so a task links to a work order, a client, a lead, a location and a parent task, and has an event feed ... Completion records whether it landed **on time**, which is what makes tasks payable" (`backend/routers/tasks.py:1-8`).
- "Orderry's rule: you are never notified about something you did yourself." (`tasks.py:379-380`; `backend/services/notification_rules.py:709-718`)
- The Orderry research doc lists "Tasks (unread badge)" as the second item in Orderry's 14-item nav (`docs/ORDERRY_UI_RESEARCH.md:13`). Orderry's task permissions are "create · be an Assignee · view all employees' tasks · edit all" (`ORDERRY_UI_RESEARCH.md:195`), and its commission events include "Task: For creating a task" (`ORDERRY_UI_RESEARCH.md:233-234`).
- The CHANGELOG for Build 86, "[0.53.0] Tasks, properly", covers the links, the event feed, on-time analysis, the `task_closed` commission, internal notifications, and "My tasks" / overdue / due sort / delete (`CHANGELOG.md:8853-8866`).
- The notifications hub has Internal tabs "Workflows · Inquiries · Tasks" and staff variables for Tasks "like Orderry's per-tab catalog" (`CHANGELOG.md:4341-4383`).
- `REACT-PARITY-MAP.md:17` marks tasks as migrated to React.

### Inferences
- Tekly already differs from Orderry on permissions. Orderry has a "view all employees' tasks" permission; Tekly has no view restriction at all, only `can_assign_staff` for assigning and editing. Tekly also has no unread-badge equivalent for tasks on the web nav, while Orderry's nav shows one.
- Tekly pays commission for *closing* a task on time (`task_closed`). The research doc shows that Orderry's event is "For creating a task". This is a deliberate divergence, or the research was incomplete.

### Gaps
- No docs/*orderry* file describes Orderry's task screen layout (list vs kanban, fields). The UI research batch covers settings, finance and similar screens, not the Tasks screen, so a direct UI-copy comparison is not possible from the repo.

## 4. Automation, SLA/overdue logic, notifications, mobile

### Takeaway
Tasks are created automatically by three paths: workflow automation on job status change, missed calls / inquiries, and Go part requests. Overdue is computed only at read time; there is no overdue sweep, reminder or escalation. Notifications fire only on assigned, changed and completed, and only to the primary assignee and the creator.

### Cited Findings
- **Status-change automation**: `orders.py:1950-1956` calls `automation.run(..., "status_changed", order=order)`. The `create_task` action creates a task titled `cfg.title` or "Follow up on {order_number}", assigned to the configured employee or the job's tech and linked to the order, client and location. **It sets `status="open"`** (`backend/services/automation.py:93-99`), which is not in `STATUSES` (`tasks.py:25`). The action is listed in `automations.py:34`. Tests are in `backend/tests/test_automations.py:27,50` ("Ready → task").
- **Missed call → inquiry + "Call back {name}" task**: `backend/services/leads.py:346-438`. The task is assigned to the client's manager or the first person on duty, given priority from the legacy config (default `"high"`), and due at `lead.sla_due_at` (400-421). A repeat call updates the existing open task's description instead of adding one (422-423). Converting the inquiry auto-completes its open call-back tasks and links the job (283-289). The legacy fallback `comms._task_for_missed_call` (`backend/routers/comms.py:352-381`) runs only when the inquiry flow is off (90-96); it uses the `task_on_missed_call` setting and `priority="normal"`. The design is described in `docs/council-ws-c-inquiries.md:7,20,28,57`.
- **Part request from Go**: `tasks.py:146-178`, priority 1, assigned to the job's manager.
- **Overdue**: computed in `_ser` (`tasks.py:30`) and in the `overdue=true` filter (76-77). Shown in red on the web (TasksHub.tsx:113), as "Overdue" in JobTasks (72), and as an Overdue group plus badge in Go (Tasks.tsx:21-33). `backend/core/scheduled.py` has no task sweep: its jobs are status_sla, notifications, contact_lifecycle, memberships, webhooks and so on (lines 17-280). `queue_reminders` handles order `scheduled_for` only (`notification_rules.py:293-318`).
- **Notifications**: `notify_task` (`notification_rules.py:710-757`) targets `task.assigned_to_id` on assigned/changed and `created_by_id` on changed/completed, and drops the actor. It never reads `assignee_ids`, so secondary assignees are never notified. The `task` module exists (`backend/routers/notifications.py:198`), and the shared EVENTS list includes `reminder` (199-202), but the task path does not implement a reminder. Errors are swallowed (`tasks.py:381-386`).
- **Payroll**: `CommissionEngine.accrue_for_task` (`backend/services/payroll.py:95-122`) pays only `task.assigned_to_id`, with on_time/overdue terms and an idempotency key `task:{id}:{rule}`. Task docs show up in the earnings breakdown (`backend/services/payroll_views.py:134-170`). The commission event is `task_closed` (`backend/routers/payroll.py:37`).
- **Time tracking**: tasks can carry a timer whose time counts as paid labour (`timeclock.py:323-349`; CHANGELOG.md:7490-7505).
- **Mobile plan conflict**: the Tekly Go council specs list "the Tasks tab" and "**No Tasks tab.**" as non-goals for v1 (`docs/mobile/tekly-go-pwa-spec.md:48`; `docs/mobile/tekly-go-native-spec.md:43`). The PWA plan step M0.3.4 says to "remove the Tasks tab ... the routes `/tasks` ... redirect to `/today`" (`docs/mobile/tekly-go-pwa-plan.md:130-131`). The current code still ships the Tasks tab with a badge (`go/components/TabBar.tsx:1,8`; `GoApp.tsx:72`), and CHANGELOG.md:216-243 documents it.

### Inferences
- Automation-created tasks with `status="open"` will render an empty status badge on the web. TasksHub.tsx:110 looks up `STATUS_LABEL["open"]`, which is undefined. They also match no status filter pill except "All" and "Late". They still count as open everywhere, because every check is `status != "done"`. This looks like a latent bug.
- Because notifications and payroll key off only `assigned_to_id`, the multi-assignee feature is cosmetic for everything except visibility and permissions.
- Leads have an SLA (`sla_due_at`) and orders have `sweep_status_sla`, but tasks have no SLA or escalation logic of their own.

### Gaps
- I did not verify whether the frontend notification-rule drawer exposes the `reminder` event for the task module. The backend would accept it, but nothing sends it.

## 5. Known gaps, TODOs and QA notes about tasks

### Takeaway
The QA history covers small UI issues (duplicate filter labels, unlabeled icon buttons, missing tasks on the job, a priority type crash). Together with the code review above, the main structural gaps are: no editing in the drawer, no views beyond the list, no reminders, single-assignee notifications and payroll, a legacy duplicate API, an invalid status from automation, and an unresolved mobile-tab decision.

### Cited Findings
- UI/UX audit A8: "Tasks — The filter has both 'Completed' and 'Done'" (`docs/production-readiness/UI_UX_AUDIT_2026-10-15.md:28`). The current pills are All / To do / In progress / Done / Late (TasksHub.tsx:86), so this looks fixed.
- Launch QA: "Unlabeled icon-only buttons on Tasks and in the order drawer" (`docs/LAUNCH_QA_2026-10-19.md:84-85`). The current TasksHub uses `aria-label`s (108, 130). As a minor bug, the assignee-chip remove buttons are labelled "Close" (145, 196).
- J4: tasks were not shown on the job. This is now addressed by JobTasks, and the shop filter was added on the read path (`docs/audits/2026-10-30-estimate-to-invoice-flow.md:62,91`; `tasks.py:90-97, 208-219`).
- Full-codebase audit: `Task.order_id` was missing an index (`docs/audits/2026-10-29-full-codebase-audit.md:78`), fixed by `aqp01_hot_fk_and_list_indexes.py:26`.
- Production-readiness M1: cross-tenant ids on tasks were checked with `ensure_owned` (`AUDIT_2026-10-14_production_readiness.md:257`). This is covered by `backend/tests/test_cross_tenant_references.py`.
- Priority type crash on Postgres: `models/__init__.py:1770-1774`.
- AUDIT_full_app.md:56: task date parsing should go through `parse_dt`. `tasks.py` does this; the legacy `staff.py` task endpoint still uses `datetime.fromisoformat`.
- The product blueprint lists "create task/PO draft" and "add checklist" among the intended workflow automation actions (`docs/PRODUCT_BLUEPRINT.md:180`). Checklists do not exist for tasks today.
- Backend tests that touch tasks: `test_warehouses_tasks_docs.py`, `test_clock_toggles_task_timer.py`, `test_automations.py`, `test_go_create_flows.py`, `test_go_tech_tablet.py`, `test_flow_estimate_to_invoice.py`, `test_flow_extras.py`, `test_cross_tenant_references.py`, `test_144ep_fk_set_null_parents.py` and `test_postgres_compat_types.py`.
- No task mentions were found in the untracked local QA files (`QA_*.md`, `BETA_READINESS.md`, `FINAL_RETEST_PLAN.md`, `PR_launch-audit-fixes.md`) in the working tree.

### Inferences
Redesign-relevant gaps, inferred from the code above:
1. The web drawer cannot edit the title, description, due date or priority, has no delete button (the API has DELETE), and shows no subtasks.
2. There is no assignee/job/client/priority filter, search or sort on the web, and no "My tasks" view on the web.
3. There are no kanban, calendar or "Today" views on the web.
4. There are no reminders, overdue notifications or escalation, and no notification badge on the nav (Orderry has one).
5. Multi-assignee is not carried through to notifications or payroll.
6. Two task APIs exist (`/api/tasks` and the legacy `/api/staff/tasks`).
7. Automation writes the non-canonical status `"open"`, and priority is a mixed int/string type.
8. Tasks are hard-deleted, with no archive.
9. Task visibility is org-wide, with no "view all employees' tasks" permission.
10. Product direction for the mobile Tasks tab is undecided: the specs drop it, the shipped code has it.

### Gaps
- No TODO/FIXME comments were found in `backend/routers/tasks.py` or `TasksHub.tsx`. No dedicated task-redesign plan or doc exists in `docs/plans/`.
