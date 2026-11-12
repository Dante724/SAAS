# 05 — Workflows, notifications, jobs, clients and business settings

Source: `audit-src` snapshot of `roadmap/orderry-parity` @ d353c67b. All paths below are relative to it.
Probes: `<scratchpad>/audit/probes05/` (run from `audit-src` with
`venv/bin/python -m pytest -p backend.tests.conftest ../audit/probes05/<file> -s -q --rootdir=. -c pytest.ini`). Nothing was written into the tree.

## Summary

| Severity | Count | IDs |
|---|---|---|
| CRITICAL | 0 | |
| HIGH | 4 | WO-01, WO-02, NTF-01, CLI-01 |
| MEDIUM | 10 | WO-03, WO-04, WO-05, WO-06, WO-07, WO-08, NTF-02, NTF-03, NTF-04, CLI-02 |
| LOW | 4 | NTF-05, NTF-06, NTF-07, CLI-03 |

Prior audit items (docs/audits/2026-10-29-full-codebase-audit.md):

| Item | Status | Evidence |
|---|---|---|
| R1 order side effects in bare `except: pass` | **PARTIAL** | The create path is fixed: `_order_side_effect` (`backend/routers/orders.py:834-847`) logs, rolls back and reloads per hook. `_new_job_steps` (`:862-935`) uses it for every create hook. Test `backend/tests/test_audit_r1_order_side_effects.py` covers it. Still open: `orders.py` has 9 `except …: pass` handlers (AST count: lines 1916, 1954, 2068, 2082, 2643, 3058, 3107, 3680, 4252). Most are on the status-change, comment, finish and notify paths, and none roll back. Backend-wide count of `except: pass` is now **57** (was 46). See WO-07. |
| R2 one shop's error stops the notification sweep | **FIXED** | `backend/core/scheduled.py:76-92` opens one session per org and calls `db.rollback()` in the except. The same pattern is in notification-outbox (`:116-125`), automation-time (`:154-160`), contact-lifecycle (`:181-188`) and task-overdue (`:398-405`). membership-sweep (`:203-214`), sense-brief (`:333-338`) and sense-agents (`:362-367`) share one session but roll back per org. Test: `backend/tests/test_audit_r2_r3_sweeps.py`. |
| R3 webhook stuck in "sending", no backoff | **FIXED** | `backend/services/webhooks.py:752-772` (`retry_sweep`) reclaims rows where `status='sending'` and `claimed_at < now-2min`, and counts the interrupted try as an attempt. Backoff is `next_attempt_at` via `_fail` (`:580-584`). The claim is a CAS in `claim_delivery` (`:691-702`). `DELIVERY_DEADLINE_S=10` (`:86`) is far below the 2-minute reclaim window. |

---

## Findings

### WO-01 [HIGH] Re-sending the same status re-runs every side effect: the customer gets the message again, and closed_at, status_entered_at (SLA) and warranty are reset
- **Module:** work orders, status change
- **Tables/columns:** `orders.status`, `orders.closed_at`, `orders.status_entered_at`, `orders.warranty_until`; `notification_logs` (one new row per call); `order_status_logs`; `audit_logs` (`action='order.status_change'`); `feedback`; `webhook_deliveries` (`order.status_changed`)
- **Functions/APIs:** `PATCH /api/orders/{id}/status` → `change_status` (`backend/routers/orders.py:1524`) → `_apply_status_change` (`:1669`). Also reached by bulk set_status, API v2 status and the portal `respond` (`backend/routers/public.py:~270-290`).
- **Execution flow:**
  1. `lock_order` (`:1689-1691`) only serialises requests. Nothing returns early when `data.status == order.status`.
  2. Several blocks guard on `order.status != data.status` (timers, checklist attach, gates), but these do not:
     - `order.status_entered_at = now_utc()` (`:1873`);
     - `if is_closing: order.closed_at = now_utc()` (`:1893`), followed by recomputing `warranty_until` (`:1904-1915`);
     - `OrderStatusLog` (`:1918`) and the audit row (`:1874`);
     - `_nr.evaluate(... event=_ev, status_key=data.status)` (`:2042`), which has no dedupe for status events;
     - `emit_event("order.status_changed")` (`:2073`);
     - the `Feedback` row (`:2093`).
- **Evidence:** `order.status = data.status` (`:1846`) runs unconditionally. In `evaluate`, `db.add(log)` runs once per matched rule on every call (`backend/services/notification_rules.py:288-296`).
- **Verification level:** **test-verified** with `probes05/test_probe_status_repeat.py`.
  - Three PATCHes to `ready` produced 3 `scheduled` customer emails, 3 status-log rows and 3 audit rows.
  - Two PATCHes to `done` moved `closed_at` from 10:18:25.363 to 10:18:25.473.
- **Why it's a problem:** double-taps, retries from a phone and the tech app's offline replay are all normal. Each one texts or emails the customer again ("your device is ready" ×3) and fires duplicate webhooks and feedback requests. Re-saving a status also resets the SLA clock: `sweep_breaches` keys on `status_entered_at`, so re-saving clears a breach. It also restarts warranty from the new `closed_at`.
- **Real-world failure scenario:** a tech taps "Ready" twice on a slow connection. The customer gets two SMS, which are billed against the operator's text cap. A manager who re-saves an overdue job's status makes the SLA breach disappear from the timeline logic.
- **Recommended fix:** in `_apply_status_change`, when `data.status == order.status` and there is no comment or note, return `{"ok": True, "unchanged": True}` before any side effect. Alternatively, guard every side effect on `prev != data.status`. Add a dedupe on `(order_id, rule_id, expect_status, status_entered_at)` in `evaluate`.
- **Fix dependencies/risks:** some screens may rely on same-status saves to add a note. Keep the note path but skip the side effects. The bulk path and API v2 share this function, so one fix covers them.

### WO-02 [HIGH] `POST /api/timeclock/order/{id}/bill` bills the same time again on every call, edits closed or locked jobs, and takes the price from the request
- **Module:** labour / technician time → WO lines
- **Tables/columns:** `order_lines` (new `line_type='labour'` row), `orders.labour_total`, `orders.total`; `time_entries` (never marked billed)
- **Functions/APIs:** `bill_tracked` (`backend/routers/timeclock.py:1140-1170`)
- **Execution flow:**
  1. The endpoint needs only `get_current_employee`; no permission is checked.
  2. It sums every `TimeEntry` with `billable=True, state!='void'`.
  3. It adds an `OrderLine` with `unit_price = data.rate` (taken from the client), adds the amount to `o.total` and commits.
  4. It does not call `_guard_editable`, `lock_order`, `_recalc_order` or tax, does not update the contact ledger, and never marks the time entries as billed.
- **Evidence:** `line = OrderLine(order_id=order_id, line_type="labour", ... unit_price=rate, total=amount)`, then `o.total = (o.total or 0) + amount`, then `await db.commit()`. There is no `_guard_editable` call in the file for this route.
- **Verification level:** **test-verified** with `probes05/test_probe_timeclock_bill.py`. On a job in `done`, two calls returned 200 and 200. The job ended with two "Tracked labor (1.0h)" lines and `total` 0 → 100.
- **Why it's a problem:**
  - The same hours are billed twice.
  - A closed or invoiced job changes total without "Edit the closed ticket".
  - A technician can set any rate.
  - The client ledger charge posted at close (`orders.py:1994`) no longer matches `orders.total`, which the integrity sweep reports as a mismatch.
  - Tax is not recomputed.
- **Real-world failure scenario:** a tech presses "Bill time" again after a page reload. The customer is overcharged, or the invoice disagrees with the job.
- **Recommended fix:**
  - Require `can_edit_order_lines` (or the line-add permission).
  - Call `_lock_for_lines` and `_guard_editable`.
  - Mark the billed entries (`time_entries.order_line_id`, which already exists at models line 2509) and exclude already-billed entries.
  - Use the catalog or employee rate unless the caller has the price-edit right.
  - Go through `_recalc_order` and reuse the `add_line` path, including `run_once`.
- **Fix dependencies/risks:** the rule for J6 auto-billing (`_stop_job_timers`) must use the same "billed" marker.

### WO-03 [MEDIUM] No optimistic concurrency on work-order edits: the last write wins on header fields
- **Module:** work orders, concurrent edits
- **Tables/columns:** `orders.*`. There is no version column anywhere: grep for `version_id_col|If-Match|lock_version` found nothing in `backend` outside tests.
- **Functions/APIs:**
  - Not locked: `save_notes` (`orders.py:4160`), `reschedule` (`:4178`), `assign_people` (`:4217`), `save_custom_data` (`:4121`), `set_priority` (`:2454`), `set_deposit` (`:2380`).
  - Locked: status changes (`lock_order`, `:1689`) and line edits (`_lock_for_lines`, `:5072`).
- **Execution flow:** each handler reads the order, sets attributes from the request and commits. Two editors overwrite each other silently. `save_custom_data` merges into `dict(order.custom_data)` read without a lock, so concurrent saves drop each other's keys.
- **Evidence:** `cur = dict(order.custom_data or {}) ... order.custom_data = cur; await db.commit()` (`:4141-4155`).
- **Verification level:** schema-level / code-read. The race was NOT probed: SQLite in tests serialises writers.
- **Why it's a problem:** a counter and a technician editing the same job lose fields such as passcode, accessories or the tech conclusion, with no error.
- **Recommended fix:** add `orders.row_version` (`version_id_col`) and accept `If-Match` from the job drawer, or at least take `lock_order` and re-read in the header PATCHes. Merge `custom_data` server-side under the lock.
- **Fix dependencies/risks:** the frontend must send and refresh the version. The offline tech app needs conflict UX.

### WO-04 [MEDIUM] `PATCH /orders/{id}/schedule` changes the assignee without `can_assign_staff`; `/notes` edits closed jobs with no audit
- **Module:** work orders, assignment
- **Tables/columns:** `orders.assigned_to_id`, `orders.resource_id`, `orders.scheduled_for/_to`, `orders.tech_notes`, `orders.conclusion`
- **Functions/APIs:** `reschedule` (`orders.py:4178-4210`), `save_notes` (`:4160-4175`). Compare `assign_people` (`:4217`), which requires `can_assign_staff`, `_assert_listed` and `_guard_editable`.
- **Execution flow:** `reschedule` depends only on `get_current_employee` (plus the router-level `record_scope`). It then runs `if data.assigned_to_id is not None: order.assigned_to_id = data.assigned_to_id or None`. It skips the permission, the "listed as assignee" check and the closed-ticket check, writes no audit row and sends no push to the new assignee. `save_notes` uses `setattr` with no `_guard_editable`.
- **Evidence:** `orders.py:4205-4208`, quoted above.
- **Verification level:** code-read. Cross-tenant ids are still blocked by the flush guard (`backend/db/tenancy.py`). This finding is about in-shop RBAC only.
- **Why it's a problem:** any staff member can reassign jobs or edit a closed job's conclusion through a side door. Reassignments leave no audit trail.
- **Recommended fix:** in `reschedule`, route `assigned_to_id` through `assign_people`'s checks. Add `_guard_editable` and an audit row to both handlers.
- **Fix dependencies/risks:** the scheduler drag-and-drop UI uses `/schedule` for assignment. Make sure roles that use the board hold `can_assign_staff`.

### WO-05 [MEDIUM] Seven other ways of creating a WO skip the create pipeline: no "created" notifications, commissions, automations, checklists, `order.create` audit or asset rule
- **Module:** work orders, create paths
- **Tables/columns:** `orders`, `audit_logs`, `commission_*`, `notification_logs`, `tasks`
- **Functions/APIs:** only `_create_order` (`orders.py:1181`, `:1227`) and `_convert_estimate` (`:2167`) call `_new_job_steps` or write `order.create`. These paths do not:
  - `services/leads.convert_lead` (`backend/services/leads.py:214-300`), used by inquiries, self check-in (`services/checkin.convert`, `:301-394`) and API v2;
  - `routers/v1.py:598`;
  - `routers/appointments.py:327`;
  - `routers/chats.py:487`;
  - `routers/comms_ai.py:242`;
  - `duplicate_order` (`orders.py:3247`);
  - `order_items.py:287` and `:342` (split / continue).
- **Execution flow:** each path builds `Order(...)`, writes a status log and maybe a webhook, then commits. None of them runs the creation commissions, the `created` notification rules, automations, checklists, or the asset-one-open-ticket check (`orders.py:1114-1127`).
- **Evidence:** grep for `_new_job_steps|action="order.create"` returns only orders.py lines 1181, 1227 and 2167.
- **Verification level:** code-read.
- **Why it's a problem:** a customer who checks in at the kiosk (converted by staff) gets no "we've received your device" message, the creator earns no commission, and order-created automations never fire. Behaviour depends on which button made the job.
- **Recommended fix:** after commit, make every creator call one `after_job_created(db, actor, order)` (today's `_new_job_steps` plus the audit row and asset rule).
- **Fix dependencies/risks:** the creation commissions rule is idempotent per job (comment at `:886`), so it is safe to add.

### WO-06 [MEDIUM] `orders.status` is not validated on create: a job type's free-text `initial_status` becomes the job's status, and `duplicate` hard-codes `new`/`draft`
- **Module:** status model
- **Tables/columns:** `orders.status` (string, no FK), `work_order_types.initial_status`
- **Functions/APIs:**
  - `create_type` / `update_type` (`backend/routers/forms.py:373-420`): `initial_status=data.initial_status`, unvalidated, default `"new"` (`:76`).
  - `_create_order` (`orders.py:1058-1062`): `initial_status = wot.initial_status or "new"`.
  - `switch_type` (`:2436-2443`).
  - `duplicate_order` (`:3246`): `initial_status = "draft" if src.is_estimate else "new"`.
- **Execution flow:** if a type's `initial_status` names a key that is missing, inactive or from another pipeline, every new job of that type is saved with that key. Contrast `_convert_estimate` (`:2134-2139`), which does check the key exists and is active.
- **Evidence:** `if wot: initial_status = wot.initial_status or "new"` with no lookup.
- **Verification level:** code-read.
- **Why it's a problem:** a job lands in a status that no board column shows, with no SLA and no group, so it is invisible. `_is_closed` falls back to `closed_at`. Estimates duplicated as `draft` are not an estimate pipeline key (`est_draft`).
- **Recommended fix:** validate `initial_status` against `order_statuses` (`record_type='work_order'`, active) in forms.py, and again in `_create_order`, `switch_type` and `duplicate_order`, falling back to the default status.
- **Fix dependencies/risks:** existing bad `work_order_types` rows need a data check.

### WO-07 [MEDIUM] R1 is still open on the status-change, comment, finish and notify paths: swallowed exceptions with no rollback, and an ambiguous 500 after a saved change
- **Module:** work orders, side effects
- **Tables/columns:** `webhook_deliveries`, `staff_notifications`, `notification_logs`, `feedback`
- **Functions/APIs:**
  - `orders.py:2066-2069`: tech push, `except Exception: pass`.
  - `:2071-2083`: `emit_event` plus `db.commit()`, `except Exception: pass`.
  - `:2085-2097`: the Feedback block, which runs on the possibly-broken session.
  - `notify_comment_added` (`:2633-2647`).
  - `_finish_order` (`:3049-3058`).
  - `notify_order_customer` (`:3107`).
  - `orders.py:1954`: QBO autosync.
- **Execution flow:** if the `db.commit()` at `:2081` fails, the session is left needing a rollback. The Feedback query at `:2089` then raises `PendingRollbackError`, and the client gets a 500 for a status change that is already committed. The client retries, which hits WO-01 and sends duplicates.

  In `notify_comment_added`, a failure inside `evaluate` is swallowed with `c_matched = 0`. That triggers the legacy template fallback (`:2645-2647`) on a session that may be broken. The result is either a 500 after the comment is saved (so the retry posts a duplicate comment) or a legacy send in addition to rule rows.
- **Evidence:** `except Exception:\n        pass` at `:2082-2083` and `:2643-2644`.
- **Verification level:** code-read. AST count of `except: pass` handlers is 9 in orders.py and 57 across the backend.
- **Why it's a problem:** the same failure class R1 named, still on the most-used mutation.
- **Recommended fix:** wrap each post-commit hook in `_order_side_effect` (it already exists).
- **Fix dependencies/risks:** none; the helper is proven on the create path.

### WO-08 [MEDIUM] Customer and staff messages are queued after the business commit, in a separate transaction: the change can be committed while the message is never queued
- **Module:** status change, create, payment → notification
- **Tables/columns:** `notification_logs`
- **Functions/APIs:**
  - Status change: `orders.py:2034-2058` commits the status (`:2036`), then `evaluate(commit=False)` and `db.commit()` (`:2042-2051`). The except rolls back and logs only.
  - Create: `_new_job_steps`, run after `:1215`.
  - Payment: `notify_payment_received` (`backend/services/invoice_money.py:591-602`).
- **Execution flow:** the outbox row is not written in the same transaction as the event. If the process dies, a template render fails or the DB blips between the two commits, the message is silently never queued. There is no reconciler. The comment at `:2023-2030` explains this was chosen to avoid SQLite read-snapshot locking.
- **Evidence:** `except Exception: await db.rollback() ... logging ... "notification rules failed ... status change kept"`.
- **Verification level:** code-read.
- **Why it's a problem:** this is "committed but never sent". The outbox gives at-least-once delivery only for rows that were written.
- **Recommended fix:** on Postgres, queue in the same transaction (a savepoint around `evaluate`, which is safe there). Or write a cheap "pending event" row in the business transaction and expand it in the sweep.
- **Fix dependencies/risks:** the SQLite constraint the comment describes. This can be gated on dialect.

### NTF-01 [HIGH] `push.notify(commit=False)` still commits the caller's half-done transaction whenever a real Web Push goes out
- **Module:** notifications, push
- **Tables/columns:** every table the caller has pending; `staff_notifications`; `push_subscriptions`
- **Functions/APIs:** `notify` (`backend/services/push.py:39-71`) → `_dispatch_push` (`:92-126`), which ends with an unconditional `await db.commit()` (`:126`). Callers that pass `commit=False` without `deferred=`:
  - `services/inventory_alerts.maybe_low_stock` (`:120`), called from `_maybe_deduct` (`orders.py:5069`) during `_create_order` stock write-offs (before the main commit at `:1215`) and during `add_line`;
  - `services/payments.py:487`;
  - `services/customer_messages.py:208`;
  - `services/checkin.py:434`;
  - `services/po_links.py:121`;
  - `services/memberships.py:760`;
  - `routers/inventory.py:2526`;
  - `services/webhooks.py:661`.
- **Execution flow:** with VAPID keys configured, `pywebpush` installed and the employee holding a subscription, `_dispatch_push` sends and then commits the session. In tests (no VAPID) it returns before the commit, so the suite never sees this.
- **Evidence:** `push.py:126`: `await db.commit()`. The docstring of `maybe_low_stock_by_id` says "Never raises", but it can commit.
- **Verification level:** **test-verified** (mechanism) with `probes05/test_probe_push_commit.py`. With `push_enabled` patched on and a stub `pywebpush`, a `Client` row added before `notify(commit=False)` survived the caller's `rollback()` (client 1, bell 1). The control run with simulated push rolled back cleanly (0). The order-create consequence is code-read.
- **Why it's a problem:** a low-stock alert part-way through order creation commits the order, its lines and the first deductions. If a later line then fails (for example a 409 because the serial is not in stock, `orders.py:4967`), the client gets an error while a half-built job exists with no side effects. Inside `create_order`, the idempotency claim is then marked committed and answers 409 "already carried out", which hides the half-built job. On the payment-webhook path, it commits the payment mid-processing. Each send also runs blocking HTTP while row locks are held.
- **Real-world failure scenario:** in production with push enabled, a counter creates a job with two parts. The first part drops below its minimum stock and the manager's phone is subscribed. The second part fails its serial check. The user sees an error, but job WO-00123 exists with one part deducted.
- **Recommended fix:** make `_dispatch_push` never commit (move the subscription prune/touch to its own short session), or force `deferred` whenever `commit=False`. Fix each listed caller to send its pushes after its own commit.
- **Fix dependencies/risks:** small change. Re-run push tests with a fake VAPID.

### NTF-02 [MEDIUM] The legacy template path sends inside the request, before commit, with no retry; it is the only path for Solo-plan shops
- **Module:** notifications
- **Tables/columns:** `notification_logs` (status `queued`, then the final status, in the same transaction)
- **Functions/APIs:** `dispatch_order_notifications` (`backend/services/notifications.py:375-461`). Called from `_new_job_steps.legacy_notifications` (`orders.py:910-914`) when `created_matched == 0` and from `notify_comment_added` (`:2646`). `evaluate` returns `matched: 0` when the shop lacks the `notification_rules` feature (`notification_rules.py:255-257`), so Solo shops always use this path. It also fires when rule evaluation failed (WO-07).
- **Execution flow:** the adapter `.send()` (Twilio/SMTP/OAuth) is called in the loop (`:443-449`) and `db.commit()` runs at the end (`:461`). A failure is recorded as `failed` and never retried. If the commit fails, the SMS was sent with no log ("sent but rolled back").
- **Evidence:** `result = await _adapter_for(t.channel, _creds).send(...)` before `await db.commit()`.
- **Verification level:** code-read.
- **Why it's a problem:** the outbox guarantees (retry, backoff, send-time consent and status re-check, reclaim) do not apply to this path. Provider latency is also added to the request.
- **Recommended fix:** make the legacy path write `status='scheduled'` rows and let `run_due` deliver them, as `evaluate` does.
- **Fix dependencies/risks:** the plan gating of rules versus outbox must stay separate.

### NTF-03 [MEDIUM] The manual "Message customer" endpoint ignores consent and STOP, keeps no delivery record, and reports success on failure
- **Module:** notifications, manual send
- **Tables/columns:** none written (no `notification_logs` row)
- **Functions/APIs:** `notify_order_customer` (`orders.py:3070-3113`)
- **Execution flow:** it resolves the recipient and sends through `try_oauth_email` or the adapter. It does not call `channel_consented` or `with_opt_out`. Any exception, and any `result['status']=='failed'`, is ignored, and the endpoint returns `{"ok": True}`.
- **Evidence:** `except Exception:\n        pass   # seam: simulated/absent gateway` at `:3106-3107`, then `return {"ok": True, ...}`.
- **Verification level:** code-read.
- **Why it's a problem:** a customer who replied STOP can still be texted. Staff are told the message went when it did not, and the message is missing from the order's communication history.
- **Recommended fix:** queue through the outbox (a NotificationLog with `trigger_event='manual'`), check consent at send time (already done by `_hold_or_skip`), and return the real status.
- **Fix dependencies/risks:** none.

### NTF-04 [MEDIUM] Rescheduling a job does not re-arm its reminder: the old reminder still goes out with the old time, and no new one is queued
- **Module:** notifications, reminders
- **Tables/columns:** `notification_logs (rule_id, order_id)`, `orders.scheduled_for`
- **Functions/APIs:** `queue_reminders` (`backend/services/notification_rules.py:314-367`), `reschedule` (`orders.py:4178`)
- **Execution flow:**
  1. The dedupe treats "any earlier row for `(rule_id, order_id)`, whatever its status" as a duplicate (`:343-348`).
  2. The body is rendered at queue time.
  3. `reschedule` does not cancel or replace queued reminder rows.
  4. `_hold_or_skip` re-checks only status and consent, not `scheduled_for`.
- **Evidence:** `dupe = select(NotificationLog.id).where(NotificationLog.rule_id == rule.id, NotificationLog.order_id == order.id)`.
- **Verification level:** code-read.
- **Why it's a problem:** the customer is reminded of the wrong time, and never of the new one. Appointment reminders include `send_at` in their dedupe (`:784-787`) and are not affected.
- **Recommended fix:** store `expect_scheduled_for` on reminder rows and skip on mismatch at send time. Include `scheduled_for` in the dedupe key.
- **Fix dependencies/risks:** none.

### NTF-05 [LOW] The outbox can send twice after a provider timeout or a crash after send (no provider idempotency key)
- **Functions/APIs:** `run_due` (`notification_rules.py:550-603`). `asyncio.wait_for(_send(...), SEND_TIMEOUT_S)` treats a timeout as a failed try and retries. A worker killed between the provider call and `_record` plus commit leaves the row in `sending`, and `_reclaim_stuck` resends it after 10 minutes.
- **Evidence:** the comment at `:376-377`: "a crash can at worst repeat one message". This is documented and accepted.
- **Verification level:** code-read.
- **Recommended fix:** pass the NotificationLog id as an idempotency or reference key where the provider supports it (SMTP Message-ID, Twilio does not dedupe). Accept the rest.

### NTF-06 [LOW] Web Push runs inline in requests with no explicit timeout
- `push._dispatch_push` (`push.py:108-118`) calls `pywebpush.webpush` in a thread without passing `timeout=`. Whether the library's default is "no timeout" is **NOT VERIFIED**. It is called inline (not deferred) from `create_order` (`orders.py:891-894`) and `assign_people` (`:4244-4253`). Recommended fix: pass `timeout=10` and defer all pushes until after commit.

### NTF-07 [LOW] `audit.record` swallows a failed flush without a rollback
- `backend/services/audit.py:9-19`: `db.add(AuditLog(...)); await db.flush()` inside `except Exception: pass`. The flush includes all of the caller's pending rows, so a constraint error (or an `HTTPException` raised by the flush guard) is hidden here and resurfaces later as a confusing `PendingRollbackError`. Recommended fix: use `begin_nested()` around the audit add, or let the error propagate. Code-read.

### CLI-01 [HIGH] A suspended shop keeps sending customer SMS/email, billing memberships and running automations; only staff sign-in is blocked
- **Module:** tenant management, suspension
- **Tables/columns:** `organisations.sub_status='suspended'`, `organisations.active` (left `true`), `notification_logs`
- **Functions/APIs:**
  - `admin.suspend` (`backend/routers/admin.py:584-594`) sets only `sub_status` and `suspend_message`, and boots staff.
  - `assert_org_active` (`backend/services/auth.py:96-109`) is checked only on staff tokens and API keys.
  - Every sweep filters on `Organisation.active == True`: `scheduled.py:71`, `:178`, `:206`, `:331`, `:360`; `notification_rules.orgs_with_due` (`:439-447`); `task_alerts` (`:70-71`).
  - `retry_sweep` (webhooks) and `automation-time-sweep` filter on neither.
  - Public pages (`routers/public.py`, `routers/checkin_public.py`) never check `sub_status`.
- **Execution flow:** after suspension, queued and newly scheduled messages, reminders, birthday texts, Sense agent messages, membership invoices and webhooks are all still processed.
- **Evidence:** `org.sub_status, org.suspend_message = "suspended", ...`. Nothing writes `org.active = False`.
- **Verification level:** **test-verified** with `probes05/test_probe_suspended_outbox.py`. With `sub_status='suspended'`, `/api/auth/me` returned 403, but the org was in `orgs_with_due` and `run_due` returned `sent: 1`.
- **Why it's a problem:** suspension is the platform's kill switch, used for unpaid accounts and for toll-fraud or SMS-pumping abuse on the operator's Twilio. In practice it does not stop outbound messaging, spend, or public booking intake.
- **Recommended fix:** define one predicate, `org_is_live = active AND sub_status NOT IN ('suspended') AND (sub_end IS NULL OR sub_end >= today)`. Use it in every sweep, in `orgs_with_due`, in `retry_sweep`, at send time in `_hold_or_skip`, and on public/check-in/booking pages.
- **Fix dependencies/risks:** decide what lapsed (`sub_end` passed, not suspended) shops should still send. Today they are blocked for staff but not for jobs either.

### CLI-02 [MEDIUM] A deactivated employee keeps getting job pushes on their phone and is still auto-assigned new jobs
- **Module:** employees, deactivation
- **Tables/columns:** `employees.active`, `push_subscriptions` (not removed), `clients.manager_id`, `orders.assigned_to_id`, `staff_notifications`
- **Functions/APIs:**
  - `deactivate_employee` (`backend/routers/staff.py:658-675`) only sets `emp.active = False`.
  - `push.notify` (`push.py:39`) does not check `active`.
  - `_create_order` (`orders.py:1131-1139`) defaults `assigned_to_id` and `manager_id` from `client.manager_id` without checking active.
  - `_assert_listed` (`:4428-4437`) ignores `active`.
  - `notify_staff_order` (`notification_rules.py:841-857`).
- **Execution flow:** staff deactivates a tech. Clients still have `manager_id` set to that tech, so a new job for such a client is assigned to them. `notify_technician` and the status-change push then reach the ex-employee's registered device, carrying the order number, the status and up to 140 characters of the complaint.
- **Evidence:** deactivation consists only of `emp.active = False` plus an audit row. No `PushSubscription` delete exists outside `routers/push.py`.
- **Verification level:** **test-verified** with `probes05/test_probe_deactivated_push.py`. A new order was auto-assigned to the deactivated employee, two pushes were dispatched to them ("New job WO-00001", "WO-00001 → ready") and two bell rows were created.
- **Why it's a problem:** customer job details leak to a former employee's personal device, and jobs land on someone who no longer works there.
- **Recommended fix:**
  - On deactivation, delete their `push_subscriptions`, revoke their sessions (if not already done) and clear or reassign `clients.manager_id`.
  - Flag their open `orders.assigned_to_id`.
  - Make `notify` and `_assert_listed` refuse inactive employees.
  - Skip inactive managers in the `_create_order` default.
- **Fix dependencies/risks:** decide the reassignment UX (a "hand over to…" choice on deactivation).

### CLI-03 [LOW] Some double-submit-prone POSTs have no Idempotency-Key handling (see the Idempotency section)
- Money endpoints without a key are mostly bounded by locks or guards. Example: `refund_overpayment` (`backend/routers/cash.py:863-905`) locks the order and caps the refund at the overpayment or held deposit. A partial refund sent twice still pays out twice while there is room. Code-read.

---

## Orderry rule checks (competitor checklist §4, Q19, Q20, Q25, Q27)

**Q19: does closing with a balance due force payment or an explicit dropped path? — PARTIAL (no for the default "Done").**
- Moving into a `closed`-group status with money owed requires the actor to hold one of `can_make_client_payments`, `can_take_payments` or `can_view_finance` (`orders.py:1714-1724`). Anyone who holds one can close the job unpaid with no prompt. The default `done` status is created with `require_payment=False`: `require_payment=key == "delivered"` in `backend/routers/statuses.py:148`, and the comment there says "Done stays closable unpaid".
- `delivered` (`require_payment`) blocks unless paid, or unless `allow_unpaid` is sent by a `can_view_finance` user (`orders.py:1771-1789`).
- The dropped path does exist and is explicit: a reason is required (`:1737-1738`), the debt is written off into `orders.written_off`, and a write-off needs `can_view_finance` (`:1852-1860`).
- `/finish` charges first, then closes in one transaction (`orders.py:3000-3048`).
- Code-read.

**Q20: do closed or `locks_edits` orders block every line, price, discount and payment-affecting endpoint unless the caller has `can_edit_closed_ticket`? — NO.**
- `_guard_editable` (`orders.py:4407-4415`) is applied to line add, edit and delete (`:4576`, `:5284`, `:5402`), kits (`:5180`, `:5451`), deposit, type, priority, currency, assign, custom-data, receive-stock, `order_items.py:271`, `recommendations.py:302` and `memberships.py:425/469`.
- It is missing from:
  - `POST /api/timeclock/order/{id}/bill` (WO-02, test-verified: added a line to a `done` job);
  - `POST /api/engage/orders/{id}/apply-coupon`, `/redeem-giftcard` and `/redeem-loyalty`, which add discount lines via `_add_discount_line` (`backend/routers/engage.py:60`, `:183`, `:305`, `:434`; grep finds no `_guard_editable` in engage.py);
  - `PATCH /orders/{id}/schedule` and `/notes` (WO-04);
  - `POST /orders/{id}/reserve-stock` (`:3433-3516`, which has only `_guard_order_access`).
- There is no separate `technician_id` PATCH; it goes through `update_line`, which is guarded.
- Imports do not write order lines (no `OrderLine(` in imports or importer).
- Payments on closed jobs are intentionally allowed (paying later).
- Also, `_guard_editable`'s `locks_edits` lookup filters only `org_id` and `key`, not `record_type`, so a shared key can resolve to another pipeline's status.
- Code-read, plus the WO-02 probe.

**Q25: is `orders.status` validated against that org's `order_statuses` on every write? — NO.**
- The status-change path validates: `_pipeline_status`, plus the 422 for unknown keys except the legacy `closed` key (`orders.py:1692-1698`).
- Create uses an unvalidated `work_order_types.initial_status` (`:1058-1062`; forms.py accepts any string). So do `switch_type` (`:2436-2443`) and `duplicate_order`, which hard-codes `new`/`draft` (`:3246`).
- The portal fallback (`public.py:280-288`) sets the status directly after a refused gate, though with a looked-up target.
- `statuses.py:406` (moving jobs off a deleted status) uses a validated target.
- See WO-06. Code-read.

**Q27: is "asset in one open ticket" enforced, including bulk create and estimate conversion? — NO.**
- It is checked only in `_create_order` (`orders.py:1114-1127`); grep for `asset_one_open_ticket` finds no other call site.
- It is not checked in `_convert_estimate` (`:2111-2168`) or `duplicate_order` (copies `device_id`, `:3247-3250`).
- It is not checked in the other WO creators: `leads.convert_lead` (inquiry and self check-in), `v1.py:598`, `appointments.py:327`, `chats.py:487`, `comms_ai.py:242`, `order_items.py:287/342`.
- It is not checked when a job's device is changed.
- There is no bulk-create endpoint (`/orders/bulk` only acts on existing jobs).
- Code-read.

---

## End-to-end trace

```mermaid
sequenceDiagram
  autonumber
  participant UI as Staff app
  participant API as orders.py / cash.py
  participant DB as Postgres
  participant OB as Outbox sweeps (scheduled.py)
  participant P as Twilio/SMTP/Push/Webhook
  UI->>API: POST /api/clients (idem "clients.create")
  API->>DB: INSERT clients · COMMIT
  UI->>API: POST /api/orders (idem "orders.create")
  API->>DB: idem reserve · COMMIT (tx0)
  API->>DB: [tx1] devices(auto asset) · next_number · orders+order_lines · audit order.create · order_status_logs · stock deduct (_maybe_deduct) · COMMIT orders.py:1215
  Note over API,DB: NTF-01: a low-stock push inside tx1 may COMMIT early
  API->>DB: [tx2..n] each side effect commits alone (_order_side_effect): upsells, checklists, webhook row, commissions, tech bell, rules→notification_logs(scheduled), automations
  API-->>P: tech web push inline (after its own commit)
  API->>DB: idem finish · COMMIT
  UI->>API: POST /orders/{id}/lines (run_once "orders.add_line") / timeclock bill (WO-02, unguarded)
  API->>DB: lock_order · line · stock · recalc · COMMIT
  UI->>API: PATCH /orders/{id}/status (no idem; WO-01)
  API->>DB: [tx] lock_order · gates · status · write_off · audit · invoice(auto-draft/trigger) · warranty · status log · feed · automations · ledger charge · commissions → COMMIT (inside CommissionEngine) 
  API->>DB: [tx'] rules→notification_logs(scheduled, expect_status) · COMMIT (WO-08 gap)
  API->>DB: [tx''] webhook rows · COMMIT (except: pass, WO-07) · feedback · COMMIT
  DB-->>OB: post-commit: job_runner.enqueue(deliver_pending)
  OB->>P: webhook POST (claim CAS → send → record)
  OB->>DB: run_due: claim scheduled→sending COMMIT
  OB->>P: SMS/email
  OB->>DB: sent/retry/dead COMMIT
  UI->>API: POST /cash/orders/{id}/pay (idem "cash.pay_order")
  API->>DB: [tx] FOR NO KEY UPDATE order · balance check · transactions(income+fee) · payments (+invoice) · contact_ledger income · cash shift link · cancel pay links · COMMIT cash.py:~772
  API->>DB: payment_received rules → notification_logs COMMIT (best-effort)
  Note over DB: Reports read orders/payments/transactions/ledger directly (no extra writes)
```

| Step | Function (file:line) | Transaction / commits | Left behind if it fails midway | Audit log | Client history |
|---|---|---|---|---|---|
| Client select / create | `clients.create` (`routers/clients.py:211`), with idem | one commit | nothing | yes (`clients.py`, 2 audit calls) | n/a |
| WO create | `create_order` / `_create_order` (`orders.py:937`, `:967`) | tx0 idem reserve; tx1 = everything up to `:1215` | Crash in tx1: nothing (key goes stale and is taken over after 2 minutes). Crash after `:1215`: the job exists without side effects, and the key is `committed`, so a retry gets 409 DONE_NO_ANSWER. NTF-01 can commit tx1 early. | `order.create` (`:1181`) | job visible on client (FK); feed event `:1189` |
| Asset/device | inline Device insert (`:1069-1094`), passcode `:1097-1104` | in tx1 | rolled back with the order | no separate row | device linked to client |
| Service lines | `_create_order` loop (`:1009-1043`); later `add_line` (`:4549`) via `run_once` + `_lock_for_lines` | tx1 / own tx | consistent | not for lines (feed events only) | — |
| Labour/technician | `OrderLine.technician_id`, `work_share`; `_stop_job_timers` on close; `bill_tracked` (`timeclock.py:1140`) | own tx | WO-02: repeatable, unguarded | no | — |
| Part consumption | `_maybe_deduct` (`orders.py:4942`), stock movements, serials, `inventory_alerts` | tx1 / line tx | NTF-01 early commit | no AuditLog; `stock_movements` are the ledger | — |
| Status change | `_apply_status_change` (`:1669`) | main tx committed by `CommissionEngine._accrue` (`payroll.py:383/448`) or `:2036`; notifications `:2051`; webhooks `:2081`; feedback `:2097` | business state atomic; messages can be lost (WO-08); a 500 is possible after commit (WO-07) | `order.status_change` (`:1874`), `order.write_off` | `contact_ledger` charge delta (`:1983-1998`); status log; feed |
| Notification | `notification_rules.evaluate` (`:248`) → `run_due` (`:550`) | outbox: claim commit, then result commit | crash after send: at most one resend (NTF-05). Not queued (WO-08). Legacy path sends before commit (NTF-02). | n/a (`notification_logs` is the record) | message history per client (`notification_logs.client_id`) |
| Invoice | `auto_draft_on_close` (`invoicing.py:323`, savepoint `orders.py:1898`) or `triggers_invoice` → `new_invoice` (`invoice_money.py:148`) | inside the status tx | savepoint isolates auto-draft failures; the `triggers_invoice` path's QBO sync is `except: pass` | no AuditLog | invoice linked to client |
| Payment → cash box | `pay_order` / `_pay_order` (`cash.py:678`, `:705`) → `post_order_payment` (`:595`) → `post_transaction` (`cash.py:52`), `apply_order_payment` (`invoice_money.py:270`), `post_contact_ledger` | one tx under `FOR NO KEY UPDATE`; idem key; 10-second same-amount double-click guard when unkeyed | atomic | **no AuditLog row** for payments (`transactions.employee_id` / `payments.received_by_id` are the trail) | `contact_ledger` income row |
| Reports | read-only (`reports.py`, `finance_pnl.py`) | — | — | — | — |

**Other traces**
- **Client self check-in → WO.** `POST /api/checkin/{token}` (`checkin_public.py:272`) is rate-limited and creates the Checkin, Lead and queue number. Staff convert it with `checkin.convert` (`services/checkin.py:301`), which uses a row lock via a no-op UPDATE and is idempotent on `converted_order_id`, then calls `leads.convert_lead` (`services/leads.py:214-300`). That is one commit. It emits `order.created`, but **runs no `_new_job_steps`, writes no `order.create` audit row, applies no asset rule** (WO-05), and enforces no plan job limit on that path. `text_customer` (`checkin.py:404-422`) sends SMS **inline**, outside the outbox, with no retry. The public endpoint does not check suspension (CLI-01).
- **Estimate approval via portal → WO lines.**
  - `POST /api/public/portal/{token}/order/{id}/respond` (`public.py:863`) and `track/{token}/respond` (`:498`) call the move helper (`public.py:~270-290`), which runs `_apply_status_change` with `SystemActor`.
  - If a gate refuses an **estimate**, the code sets `order.status = target.key` directly and logs "without the usual check".
  - Conversion goes through `_convert_estimate`. Its conditional UPDATE (`orders.py:2120-2125`) makes it single-winner. It commits at `:2161`, then runs `_new_job_steps`.
  - A double-tap of "Approve" still triggers WO-01's duplicate messages.
  - Recommendation decisions turn into lines through `recommendations.py` (guarded by `_guard_editable` at `:302`).
- **Warranty/return job.**
  - There is no first-class warranty job: no `parent_order_id` or warranty-claim model was found. Warranty is just `orders.warranty_until`, set on close (`orders.py:1904-1915`) and reset by WO-01.
  - A warranty repair is a normal new WO, so it has no link to the original and no automatic zero price.
  - Goods returns: `POST /api/returns` (`returns.py:35`) is idempotent with an idem key. `job_returns.still_held` caps restocking.
  - Refunds: `/returns/refunds` and `cash/orders/{id}/refund` have no idem key (CLI-03).

## Idempotency (`backend/services/idempotency.py`)

The design is sound:
- the key is reserved and committed before the work;
- `committed_at` is stamped in the same transaction as the work's first commit (`before_commit` listener, `:80-101`);
- a stale takeover after 2 minutes uses the DB clock and a token CAS;
- a 409 is returned when work committed but the answer was lost.

Note that `idem_begin` without `body=` (`orders.create`, `cash.pay_order`, `returns.create`, `sales.create`, `clients.create`, `tasks.*`, `finance.invoice_pay`, `cash.prepay_order`) does not bind the key to the request: the same key with a different body replays the first answer.

**Endpoints that use it**
- `idem_begin`: `orders.create`, `orders.finish`, `cash.pay_order`, `cash.prepay_order`, `finance.invoice_pay`, `returns.create`, `sales.create`, `sales.return`, `clients.create`, `tasks.create`, `tasks.part_request`.
- `run_once` (body-bound): `cash.transaction`, `cash.transfer`, `terminal.charge`, `finance.payout`, `credit_notes.create`, `sales.pay`, `payroll.run_pay`, `orders.add_line`.
- Public API `_idempotent` (v1/v2): contacts, orders, appointments, time entries, inquiries (+convert), tags, services, bundles, products, webhooks, v2 order status and comments, appointment cancel.

**Double-submit-prone POSTs without it** (code-read):
- `PATCH /orders/{id}/status`: **WO-01**, the worst one.
- `POST /orders/{id}/comments`: duplicate comment and duplicate customer message.
- `POST /orders/{id}/notify`: duplicate SMS.
- `POST /orders/{id}/duplicate`, `/convert`. Convert is CAS-guarded; duplicate is not.
- `POST /timeclock/order/{id}/bill`: **WO-02**.
- `POST /cash/orders/{id}/refund` and `/returns/refunds`: bounded by caps (CLI-03).
- `POST /cash/orders/{id}/invoices`: guarded by `lock_order` + `billed_on`.
- `POST /finance/invoices`, `/finance/expenses`, `/finance/shifts/{id}/movement`: expense and drawer movement would double-post.
- `POST /payments/checkout`.
- `POST /memberships` (create), `/memberships/{id}/billing`.
- `POST /engage/orders/{id}/apply-coupon`, `/redeem-giftcard`, `/redeem-loyalty`: guards not inspected.
- Public: `POST /public/track/{token}/deposit`, `/appointment/{slug}`, `/checkin/{token}` (rate-limited only), `/track/{token}/new-request`.

## Background jobs (`backend/core/jobs.py`, `core/scheduled.py`)

- **Retry/backoff:** `enqueue` retries in-process with exponential backoff (`jobs.py:46-69`), retries=3. It is not durable: a restart loses queued one-shots. The only user found is the webhook post-commit outbox, and the 60-second `retry_sweep` picks up anything lost. Periodics never retry; they run again next tick.
- **Idempotency on retry:** the outbox claim CAS (`notification_rules._claim`, `:476-485`) and webhook `claim_delivery` (`webhooks.py:691`) make a retried job skip completed rows. Other dedupes:
  - reminders: by `(rule, order)` (NTF-04);
  - appointment reminders: by `send_at`;
  - automations: by `automation_fires` / `source_ref` (docstring, `scheduled.py:137-142`; not re-verified);
  - Sense briefs: claim the day (docstring);
  - memberships: "idempotent per day" (docstring, **NOT VERIFIED**);
  - SLA: `EventFeedItem` since `status_entered_at` (`sla.py:113-120`).
- **Multi-worker / multi-instance:**
  - Every periodic runs under `run_exclusive` (`jobs.py:115-146`), a Postgres session advisory lock held on a dedicated AUTOCOMMIT connection, so one runner per tick. It requires session pooling, not PgBouncer transaction pooling, as noted in the docstring.
  - Notification delivery is additionally serialised across the two jobs by `_deliver_exclusively` (`scheduled.py:35-47`).
  - Even with two instances sending concurrently, the per-row CAS prevents double sends.
  - The advisory lock is per tick, not per job instance, so a sweep finishing on instance A can be followed immediately by instance B's tick. That is safe only because the sweeps are state-idempotent (see above).
  - On SQLite, nothing is locked (single process assumed).
- **R2: FIXED. R3: FIXED** (see the table at the top).
- **SLA sweep:** `sweep_breaches` (`sla.py:96-142`) does one commit for all orgs, so one bad org loses the whole tick's events. LOW, not filed separately. Its status lookup is by key without `record_type`.

## Business & tenant management (scope A)

- **Signup** (`routers/auth.py:217-379`):
  - org, location, warehouse, admin role, tax, employee and default rules are written in one transaction (commit at `:362`);
  - status pipelines are seeded afterwards (`:366`), insert-or-ignore;
  - email uniqueness rests on a global unique index (`uq_employees_email_lower`), so a concurrent duplicate gets an IntegrityError and a 500 instead of a 400 (minor);
  - rate-limited at 10 per hour per IP.
- **Employees:** create checks the seat limit and that the role is assignable. Update audits role, email, active and wage changes. **Deactivation:** CLI-02. Deleting a role with members is refused. Employees are never hard-deleted (`DELETE /employees/{id}` only deactivates).
- **Locations/settings:** not deeply reviewed (see "Not inspected").
- **Org suspension:** CLI-01.

## Clients (scope B)

- **Delete** (`clients.py:509-536`) refuses when any FK-discovered table references the client (`_client_fk_targets`, `:782`), and revokes portal links. Nothing is orphaned.
- **Merge** (`:863-923`) re-points every mapped FK to `clients.id` (Invoice `payer_client_id` and the `Client.payer_id` self-reference included) in one transaction. It snapshots the sources into `merge_logs` and sums `clients.balance`. Undo (`:926-990`) moves back only rows still on the target.
  - An introspection check (metadata scan) found no client-referencing `*_id` column without an FK, apart from the unrelated `sso_connections.client_id`.
  - Orders, invoices and payments are not orphaned. **Verified by code-read + metadata scan.**
  - `contact_ledger` rows move with the FK, but `clients.balance` is summed separately and is not recomputed from the ledger. Drift between the two is possible if they already disagreed; the integrity sweep would flag it. NOT VERIFIED further.
- **Memberships / discount cards / coupons:**
  - Memberships: sweep and usage were covered only on the close path (`record_usage` is idempotent per line, `memberships.py:503-512`).
  - Coupons and loyalty (`engage.py`) bypass the closed-job guard (Q20).
  - The rest was not inspected in depth.

## 1. Inspected
- `backend/routers/orders.py`: create, status change, finish, notify, comments, schedule, notes, assign, custom-data, convert, duplicate, `_guard_editable`. About 1,800 of 5,715 lines were read closely.
- `backend/services/notification_rules.py` (evaluate, reminders, outbox `run_due`/claim/reclaim/`_hold_or_skip`, `notify_staff_order`).
- `backend/services/notifications.py` (legacy dispatch, operator text cap).
- `backend/services/push.py` (whole file); `backend/services/webhooks.py` (emit, ORM hooks, delivery, `retry_sweep`); `backend/services/idempotency.py` (whole file); `backend/services/sla.py` (sweep).
- `backend/core/jobs.py` (whole file) and `backend/core/scheduled.py` (whole file).
- `backend/services/numbering.next_number`; `invoice_money.lock_order` and `notify_payment_received`; `invoicing.auto_draft_on_close`; `memberships.record_usage`; `audit.record`; `db/actors.py`; `db/tenancy.py` (header).
- `routers/cash.py` (pay, refund, sub-invoice, `post_order_payment`); `routers/timeclock.bill_tracked`; `routers/auth.register`; `routers/staff.py` (employee create, update, deactivate, delete role); `routers/clients.py` (delete, merge, undo); `routers/admin.suspend`; `services/auth.assert_org_active`.
- `services/checkin.convert`, `text_customer` and `alert_staff`; `services/leads.convert_lead`; `routers/forms.py` types; `routers/statuses.py` (seed, transitions); `routers/engage.py` (route list); `services/job_returns.py`.

## 2. Not inspected
- `services/automation.py` (only greps), `services/customer_messages.py` (only the notify call site), `services/legacy_status_notifs.py`, and `services/messaging.py` beyond its outline.
- `services/memberships.sweep` idempotency.
- `services/sense*.py`.
- Location CRUD and company settings (`staff.py:712-1240`).
- Coupon and discount-card business rules (`services/coupons.py`).
- `routers/public.py` respond and deposit, read only around `:270-290`.
- `bulk_action` (`orders.py:687-831`) beyond its comment.
- `_archive_order` and `delete_order`.
- Reports.
- v1/v2 order routes beyond creation.

## 3. Verified (by test/probe)
- WO-01: repeated status PATCH queues duplicate messages and resets `closed_at` (`probes05/test_probe_status_repeat.py`, 2 passed).
- WO-02: timeclock bill double-bills and edits a closed job (`probes05/test_probe_timeclock_bill.py`).
- NTF-01: `notify(commit=False)` with real push commits the caller's work; the simulated control does not (`probes05/test_probe_push_commit.py`, 2 passed).
- CLI-01: a suspended org's outbox is still delivered (`probes05/test_probe_suspended_outbox.py`).
- CLI-02: a deactivated employee is auto-assigned and pushed (`probes05/test_probe_deactivated_push.py`).

## 4. Uncertain
- `pywebpush` default timeout (NTF-06).
- Whether every caller of `maybe_low_stock` can actually fail after the alert in the same request. Serial 409 is a confirmed raise site; others were not enumerated.
- Membership sweep and Sense per-day idempotency (docstrings only).
- Whether the frontend sends Idempotency-Key on status PATCH (none server-side).

## 5. Needs staging to test properly
- NTF-01 end to end with real VAPID keys and a subscribed device during order create.
- WO-03 concurrent header edits and WO-01 double-tap timing on Postgres (row locks).
- The `run_exclusive` advisory lock behind the production pooler (PgBouncer mode), with two app instances.
- NTF-05 duplicate SMS on a Twilio timeout.
- CLI-01 against a suspended shop with real provider credentials (spend impact).
