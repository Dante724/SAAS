# Council seat: Senior Architect, Tasks redesign ("Next steps")

Verified read-only against `origin/roadmap/orderry-parity` @ `5518df78` (2026-11-11). Citations are `path:line` on that ref.

## Verdict

**Keep the direction, but the plan is about 40% re-invention.** The research doc never mentions **Tekly Sense** (morning brief and the quote follow-up agent), the **per-status SLA engine**, the **"parts received, job can resume" alert**, the **order board** or the **role-aware Go Today screen**. Each of these already does part of a proposed feature. The redesign should be **one new thing**: tasks that are created by a job event, de-duplicated, and closed when the job moves on, delivered through the **existing** staff inbox. Everything else should extend what is already there.

Three findings change the plan:
1. **Task alerts never reach the bell or push.** `notify_task` only writes email/SMS `NotificationLog` rows (`backend/services/notification_rules.py:710-757`). The in-app inbox and web push run through `push.notify` → `StaffNotification` (`backend/services/push.py:1-10`, `backend/routers/tech.py:138`, bell at `frontend-react/src/components/Shell.tsx:228,358`). Missed-call tasks already use `push.notify` (`backend/services/leads.py:428`), so tasks are the odd one out. This should be fixed in Phase 0, and no new inbox should be built.
2. **Time-based automations never fire.** `order_overdue` and `low_stock` only run through a manual `POST /api/automations/sweep` (`backend/routers/automations.py:189-220`). `backend/core/scheduled.py` has no job for them. Any "after N days" recipe built on this engine is dead on arrival until that changes.
3. **Delayed customer messages are not re-checked at send time.** `run_due` re-checks consent and Sense agents, but not whether the job is still in the status that queued the message (`notification_rules.py:349-420`; `evaluate` queues at `:265`). Building "Ready, day 3 / day 7 reminder" as a `delay_min` rule would text customers who have already collected their device. Pickup reminders need a "still in status" guard, either in `run_due` or in the Sense agent `send_check` pattern (`backend/services/sense_agents.py:363`).

## What already exists (duplication map)

| Proposed | Already in Tekly | Evidence |
|---|---|---|
| Status-change follow-up recipes | AutomationRule engine: `status_changed` + conditions → `create_task`, with a dry-run tester | `backend/services/automation.py:22-27,92-99`; fired at `backend/routers/orders.py:1959`; tester `automations.py:170-186` |
| "Preview last 30 days before enabling" | `/automations/{id}/test` dry run (current orders only, capped at 500) | `automations.py:170-186` |
| Quote sent, no reply | **Sense `quote_followup` agent**: drafts a customer text after 2 days, owner approval, rate caps, auto-stops on approval | `sense_agents.py:31-46,214-226,355` |
| Ready, not collected (customer side) | NotificationRule on `status_changed` with `delay_min` + working hours (but see finding 3); "Not collected" status group; 90-day disposal wording | `notification_rules.py:1-14,232-290`; `backend/routers/statuses.py:37`; `backend/routers/mobile_messages.py:37` |
| Ready, not collected (staff side) | Home feed "N ready for pickup" signal; Go Today front-desk view lists ready-for-pickup first | `backend/routers/reports.py:1607`; `frontend-react/src/go/screens/Today.tsx:1-4` |
| Part ordered → part arrived, resume | PO↔job link, feed line and in-app alert on sent/received/re-dated; "Parts received, job can resume" push to the tech | `backend/services/po_links.py:1-10,120`; `backend/services/inventory_alerts.py:10-11,170-180`; `PurchaseOrder.expected_at` `backend/models/__init__.py:1412` |
| Missed call → callback, 15-min SLA | Fully built: inquiry SLA, de-duplicated task, auto-close on convert, push | `leads.py:283-289,395-438`; `backend/routers/leads.py:132-141` |
| Pausing turnaround clock / stuck alerts | Per-status `time_limit_min` + `status_entered_at` + 10-minute `status-sla-sweep` (writes feed events only). Statuses not given a limit are effectively "paused" | `models/__init__.py:598,615,687-688`; `backend/services/sla.py:1-12`; `scheduled.py:16-29` |
| Promised / turnaround date | `Order.due_at`, `scheduled_for/to`; overdue shown on Go Today and the home feed | `models/__init__.py:687-691`; `reports.py:1605` |
| Morning digest | **Sense morning brief**: chosen hour, shop time zone, email/push, already-sent claim, covers stuck jobs, cold quotes and due today | `backend/services/sense.py:309-492`; `scheduled.py:224-247` |
| In-app inbox, few pushes | `StaffNotification` bell with unread, read/dismiss, push, per-role mute | `push.py:1-60`; `tech.py:138-200`; `Shell.tsx:228,358,610-623` |
| Counter queue / bench queue | Go Today is role-aware (tech: my jobs; front desk: ready then waiting; owner: board + counts). Web: `/my-work` | `Today.tsx:1-4`; `frontend-react/src/pages/timesheets/MyWorkHub.tsx:11-14` |
| Shop board / wall view | `GET /api/orders/board` kanban by status group (pending / ready …), filterable | `orders.py:1233-1250` |
| Sign-off checklist on completion | Order checklists v2 + `require_checklist` status gate | `backend/routers/checklists.py:1-11`; `models/__init__.py:620` |
| Cmd+K | Exists (module jump + order/client search) | `Shell.tsx:218,540-560` |
| Go "My day" tab | Tasks tab with date buckets, badge and offline tick already shipped | `go/components/TabBar.tsx:1,8`; `go/screens/Tasks.tsx` |
| Timer on a task | Timeclock `task_id` | `backend/routers/timeclock.py:323-349` |
| Task visibility permission | `Role.order_scope` all/own already scopes orders and the task count on the home feed | `models/__init__.py:304`; `reports.py:1590-1593` |
| Wake-on events | `payment_received` / `estimate_approved` / `status_changed` already fire automation; customer reply hits `public.py:1105-1119` and chats; part receipt hits `inventory_alerts` | as cited |

There is no supplier-return / RMA entity (a grep found none). That recipe needs a new domain object.

## Per-feature calls

| # | Feature | Call | Cost | Risk | Cheapest correct path |
|---|---|---|---|---|---|
| 0a | Bug: `status="open"` | KEEP (P0) | S | Low | Write `"todo"` at `automation.py:98`; data migration `open→todo` |
| 0b | Notify all assignees **+ route through `push.notify`** | KEEP (P0) | S | Low | In `notify_task`, targets = `{assigned_to_id} ∪ assignee_ids`; call `push.notify(kind="task.assigned")` (add to `VALID_KINDS` `push.py:25`) in addition to rules. Log instead of swallowing errors (`tasks.py:381-386`) |
| 0c | Pay all assignees | KEEP, **decision first** | S | Med (money) | Owner decides split vs. each-full before code; `payroll.py:95-122` idempotency key must become `task:{id}:{emp}:{rule}` |
| 0d | Delete `/api/staff/tasks` | KEEP (P0) | S | Low | Remove `staff.py:683-740` and its `TaskIn/TaskUpdate`; no frontend callers |
| 0e | Priority enum | KEEP (P0) | S | Low | Normalise to int 0-3 (matches `Order.priority` int, `models:662`) with a migration; drop the string validator |
| 0f | Editable drawer, delete/restore, server paging + search | KEEP (P0) | M | Low | Reuse `backend/core/paging.py`; push `mine` into SQL; add `archived_at` soft delete (time entries already survive via SET NULL) |
| 0g | Visibility | KEEP, MERGE INTO `Role.order_scope` | S | Low | `own` ⇒ tasks where creator / assignee / watcher; no new permission |
| 0h | Schedule automation sweep | **ADD to P0** | S | Med (sudden backlog fire) | `@job_runner.every(3600,"automation-sweep")` per org, calling the body of `automations.py:189-220`; needs dedupe first (below) or the hourly run creates a task every hour |
| 1 | Quote-no-reply recipe | **MERGE INTO Sense `quote_followup`** | S | Low | Customer side exists. Add only a staff-side option: when a draft is waiting for approval, ensure one task "Approve follow-up for Q-123" with `source_ref=notification_log:id`, closed on approve/discard. No new engine |
| 2 | Ready-not-collected recipe | KEEP, built on AutomationRule + NotificationRule | M | **High (legal / wrong texts)** | Staff task: an `auto_close_when_status_leaves` flag on `create_task` (uses `source_ref`). Customer reminders: add a `still_in_status` check in `run_due` (store the queuing status on the log row) **before** offering day-3/7 delays. Notice log = existing `NotificationLog`. No fee logic |
| 3 | Part ordered / received recipes | MERGE INTO `po_links` + `inventory_alerts` | S | Low | Already alerts. Add only: `apply_receipt` closes auto-tasks with `source_ref=po:{id}`; optional `create_task` on PO `expected_at` passed (piggyback on the automation sweep as event `po_overdue`) |
| 4 | Supplier-return recipe | LATER | L | Med | Needs a SupplierReturn/RMA model first; separate epic |
| 5 | Missed call | KEEP as-is; extract its pattern | S | Low | Lift `leads.py:404-421,285-289` into `services/auto_tasks.ensure_task(source_ref, …)` / `resolve_tasks(source_ref)`; leads becomes the first caller |
| 6 | Morning digest | **MERGE INTO Sense morning brief** | S | Low | Add a per-person "your tasks today / overdue / handed to you" section to `build_brief` and let staff (not only owner-picked recipients) opt in. Don't build a second digest sweep |
| 7 | Due-soon / overdue alerts | KEEP, small | S | Med (noise) | One hourly sweep → `push.notify(kind="task.overdue")` once per task (a `overdue_alerted_at` column as dedupe, like `Part.low_stock_alerted`) |
| 8 | Notification inbox / "priority vs other" | MERGE INTO StaffNotification bell | S | Low | Already exists; tasks just need to land in it (0b). Auto-dismiss on resolve = set `dismissed_at` where `deep_link` matches |
| 9 | Pausable turnaround clock (B2B) | MERGE INTO per-status SLA | S-M | Low | `time_limit_min` per status is already "pause-aware" for limits per stage. A whole-job paused clock = sum of time in non-pending groups; needs a status-history read, not a new clock. LATER unless a B2B customer asks |
| 10 | My day (web + Go), start vs due, snooze | KEEP, trimmed | M | Low | Go Tasks tab already is "My day" → keep the tab (resolves bug 12), add `start_at` and a "New for you" group (`acknowledged_at`). Web: a "Mine" default + date grouping on TasksHub; reuse Go's `bucket()` logic in a shared lib |
| 11 | Wake-on-activity snooze | LATER (simplified) | M | Med | Don't build a generic `wake_on` JSON event bus. When an auto-task is tied to an order, *any* `automation.run` event on that order clears `start_at` (one line in `automation.run`). Customer reply: same call from `public.py:1105` / chats |
| 12 | Counter queue + rotating owner | MERGE INTO Go Today front-desk + web home feed | M | Med | Front-desk Today already lists ready/waiting jobs; add unassigned auto-tasks there with Take/Snooze. Rotation: CUT for now (use `Shift`/schedules "on duty" as leads do, `leads.py:_recipients`) |
| 13 | Bench queue | MERGE INTO Go Today (tech) + `/my-work` | S | Low | Pin "parts received" (already a notification) to the top. No new view |
| 14 | Shop board / TV wall | MERGE INTO `/orders/board` | M | Low | Add age chip (`status_entered_at`) and per-column money total to the board payload; a `?display=wall` read-only, auto-refresh route. No new data |
| 15 | Routines (recurring shift checklists) | LATER | L | Med | Genuinely new. When built: `routine_templates` + daily materialiser that reuses `checklist_templates` fields, not a second checklist model |
| 16 | Job sign-off checklist | CUT (exists) | — | — | `checklists.py` + `require_checklist` gate |
| 17 | Cmd+K | CUT (exists) | — | — | Add a "New task" command and task search hits to `CommandPalette` (`Shell.tsx:540`) when convenient: S |
| 18 | AI suggestions | LATER | M | Med | Follow the Sense pattern (`sense.py:1-9`: suggest only, with a "why", per-shop switch) |
| 19 | Voice drafts on Go | CUT for now | M | High (62% accuracy) | Revisit after core usage |
| 20 | Rename "Tasks" → "Next steps" | LATER / product call | S | Med (Orderry migrants search "Tasks") | Ship the behaviour first; rename only after beta feedback |
| 21 | `waiting` / `cancelled` task status | KEEP `cancelled`, CUT `waiting` | S | Low | "Waiting" belongs to the **order** status group `pending`; duplicating it on tasks creates two sources of truth. `cancelled` is needed for auto-resolve-without-doing ("customer collected anyway") |
| 22 | `task_assignees` join table + watchers | KEEP join table; watchers LATER | M | Med (migration + payroll) | Migrate `assigned_to_id`+`assignee_ids` → `task_assignees(task_id, employee_id, role, acknowledged_at)`; keep `assigned_to_id` as a denormalised owner for one release to avoid touching every reader at once |

## Minimal data-model change set

**`tasks` table (one migration):**
- `status`: data-fix `open→todo`; allowed `todo|in_progress|done|cancelled` (CHECK constraint, not a PG enum: cheaper to change)
- `priority`: convert to `Integer` 0-3 with a migration of `"normal"/"high"/"urgent"` strings
- add `start_at` DateTime null, `updated_at`, `archived_at` (soft delete), `overdue_alerted_at`
- add `source` String(20) default `manual` (`manual|automation|sense|missed_call|part_request|po`), `source_ref` String(80) null (`order:<id>:<status>`, `lead:<id>`, `po:<id>`, `nlog:<id>`)
- **partial unique index** `(org_id, source_ref) WHERE status NOT IN ('done','cancelled') AND archived_at IS NULL`. This is the de-dupe guarantee that makes the hourly sweeps and repeated status entries safe. Insert with `ON CONFLICT DO NOTHING`
- index `(org_id, assigned_to_id, status, due_at)` for "mine" paging

**New table:** `task_assignees(task_id FK CASCADE, employee_id FK, role 'owner'|'helper', acknowledged_at)`, PK `(task_id, employee_id)`. Backfill from `assignee_ids`.

**Elsewhere (tiny):**
- `automation_rules.action_config` gains `auto_close: true` (JSON, no migration)
- `notification_logs` gains `expect_status` String null so `run_due` can skip a delayed status message whose job has moved on
- `push.VALID_KINDS` += `task.assigned`, `task.overdue`

**Not needed now:** `wake_on` JSON, `recipe_key` (use `source`+`source_ref`), `task_watchers`, `routine_templates`, `task_checklist_items` (orders already have checklists), `completion_note` (use the feed).

## Recommended build order

1. **P0 Foundations (≈1 week).** 0a, 0b (push routing is the biggest win), 0d, 0e, 0f, 0g; the tasks migration above incl. `source_ref` unique index; extract `auto_tasks.ensure_task/resolve_tasks` from leads. Tests: Postgres test for the partial index + concurrent ensure; notify-all-assignees; legacy route 404.
2. **P1 Auto-close engine (≈1 week).** `create_task` → `ensure_task(source_ref=order:id:status)`; on every `status_changed`, `resolve_tasks` for refs whose status no longer matches; schedule the automation sweep (0h); `expect_status` guard in `run_due`. Ship two **default-off** starter rules: "Ready → follow up" and "Waiting for parts → check ETA", previewed with the existing dry-run.
3. **P2 Delivery through what exists (≈3-4 days).** Tasks section in the Sense brief; hourly overdue push; PO receipt resolves `po:` tasks; Sense quote-followup draft → approval task.
4. **P3 Surfaces (≈2 weeks, before/after review required).** Web TasksHub "Mine" + date groups + editable drawer; Go Tasks tab keeps its name for now, adds `start_at` snooze + "New for you"; board age chips + money total + wall mode; auto-tasks on Go Today front desk.
5. **P4 (after beta interviews).** Payroll split decision → `task_assignees` payroll; routines; supplier returns; AI suggestions; rename.

## Risks to watch
- **Backlog storm:** turning on the scheduled sweep or a new rule fires against every existing order. Rules must apply only to entries *after* `rule.created_at` (compare `status_entered_at`), and the dry-run should say so.
- **Plan gating:** `automation.run` and `evaluate` are gated on the `notification_rules` feature (`automation.py:140-142`). Recipes are invisible on lower plans. That is a product call to make explicitly.
- **Two automation engines** (AutomationRule vs NotificationRule vs Sense agents) is already confusing. Don't add a fourth "recipes" engine. Present "recipes" in the UI as **pre-filled AutomationRule + NotificationRule pairs**.
- **Payroll idempotency** changes when multiple assignees are paid; this needs a test against double accrual on reopen/close.
