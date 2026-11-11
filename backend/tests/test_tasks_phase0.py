"""Tasks redesign, phase 0 (foundations).

1. Every writer stores a canonical status (todo|in_progress|done|cancelled); automations wrote "open".
2. Priority is an integer 0-3; the API still accepts the legacy words ("normal", "high", "urgent").
3. Soft delete + restore; source/source_ref with a de-duplication guarantee (ensure_task).
4. Task alerts reach the staff bell/push, for every assignee, never the actor, once per assignment.
5. Notification failures are logged, not swallowed.
6. Time-based automations run on a schedule, without duplicates and without firing on old backlog.
7. A delayed customer message is cancelled when the job has left the status that queued it.
8. The unguarded legacy /api/staff/tasks API is gone.
9. GET /api/tasks searches and pages on the server, and stays backwards compatible.
"""
import logging
from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from backend.models import (
    AutomationRule,
    NotificationLog,
    NotificationRule,
    Role,
    StaffNotification,
    Task,
)
from backend.services.auth import hash_password
from backend.services.dates import now_utc


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _employee(client, db, org_id, email, name="Emp"):
    from backend.models import Employee
    role = Role(org_id=org_id, name=f"R-{email}", permissions={})
    db.add(role)
    await db.flush()
    e = Employee(org_id=org_id, name=name, email=email,
                 password_hash=hash_password("pw-123456789"), active=True, role_id=role.id)
    db.add(e)
    await db.commit()
    await db.refresh(e)
    tok = (await client.post("/api/auth/token", data={
        "username": email, "password": "pw-123456789"})).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}, e


async def _order(client, H, **extra):
    loc = await _loc(client, H)
    cid = (await client.post("/api/clients", json={
        "name": extra.pop("client_name", "Phase Zero Client"), "phone": "+15550100",
        "email": "pz@x.dev"}, headers=H)).json()["id"]
    body = {"client_id": cid, "location_id": loc, "complaint": "x", **extra}
    r = await client.post("/api/orders", json=body, headers=H)
    assert r.status_code == 201, r.text
    return {**r.json(), "client_id": cid}


async def _two_statuses(client, H):
    st = (await client.get("/api/statuses?is_estimate=false", headers=H)).json()["items"]
    keys = [s["key"] for s in st if s.get("status_group") not in ("closed", "dropped")]
    return keys[-1], keys[-2]


async def _bell(db, emp_id, kind=None):
    q = select(StaffNotification).where(StaffNotification.employee_id == emp_id)
    if kind:
        q = q.where(StaffNotification.kind == kind)
    return (await db.execute(q)).scalars().all()


# ── 1. canonical status ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_automation_task_is_todo_and_deduplicated(client, tenant, db):
    key, other = await _two_statuses(client, tenant)
    await client.post("/api/automations", json={
        "name": "On status → task", "event": "status_changed", "trigger_status": key,
        "action_type": "create_task", "action_config": {"title": "Prep pickup"}}, headers=tenant)
    o = await _order(client, tenant)
    for s in (key, other, key):      # enter, leave, re-enter while the task is still open
        r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": s, "reason": "t"},
                               headers=tenant)
        assert r.status_code == 200, r.text
    rows = (await db.execute(select(Task).where(Task.order_id == o["id"]))).scalars().all()
    assert len(rows) == 1
    t = rows[0]
    assert t.status == "todo"
    assert t.source == "automation"
    assert t.source_ref and t.source_ref.startswith(f"order:{o['id']}:status:{key}")
    # once done, entering the status again is a new piece of work
    await client.patch(f"/api/tasks/{t.id}", json={"status": "done"}, headers=tenant)
    for s in (other, key):
        await client.patch(f"/api/orders/{o['id']}/status", json={"status": s, "reason": "t"},
                           headers=tenant)
    rows = (await db.execute(select(Task).where(Task.order_id == o["id"]))).scalars().all()
    assert len(rows) == 2


def test_model_refuses_a_non_canonical_status():
    with pytest.raises(ValueError):
        Task(title="x", status="open")
    for s in ("todo", "in_progress", "done", "cancelled"):
        assert Task(title="x", status=s).status == s


@pytest.mark.asyncio
async def test_api_refuses_unknown_status(client, tenant):
    t = (await client.post("/api/tasks", json={"title": "x"}, headers=tenant)).json()
    r = await client.patch(f"/api/tasks/{t['id']}", json={"status": "open"}, headers=tenant)
    assert r.status_code == 422
    ok = await client.patch(f"/api/tasks/{t['id']}", json={"status": "cancelled"}, headers=tenant)
    assert ok.status_code == 200 and ok.json()["status"] == "cancelled"


# ── 2. integer priority ──────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("given,stored", [
    (0, 0), (1, 1), (2, 2), (3, 3), ("normal", 0), ("low", 0), ("high", 1), ("urgent", 2), ("2", 2)])
async def test_priority_accepts_ints_and_legacy_words(client, tenant, given, stored):
    r = await client.post("/api/tasks", json={"title": "p", "priority": given}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["priority"] == stored
    p = await client.patch(f"/api/tasks/{r.json()['id']}", json={"priority": given}, headers=tenant)
    assert p.status_code == 200 and p.json()["priority"] == stored


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [4, -1, "banana"])
async def test_priority_rejects_out_of_range(client, tenant, bad):
    r = await client.post("/api/tasks", json={"title": "p", "priority": bad}, headers=tenant)
    assert r.status_code == 422


def test_model_stores_priority_as_int():
    assert Task(title="x", priority="high").priority == 1
    assert Task(title="x", priority="normal").priority == 0
    assert Task(title="x", priority=2).priority == 2


# ── 3. soft delete, restore, ensure_task ─────────────────────────────────────
@pytest.mark.asyncio
async def test_delete_archives_and_restore_brings_back(client, tenant, db):
    t = (await client.post("/api/tasks", json={"title": "Keep me"}, headers=tenant)).json()
    assert (await client.delete(f"/api/tasks/{t['id']}", headers=tenant)).status_code == 200
    row = (await db.execute(select(Task).where(Task.id == t["id"]))).scalar_one()
    assert row.archived_at is not None                       # still there, just archived
    items = (await client.get("/api/tasks", headers=tenant)).json()["items"]
    assert all(x["id"] != t["id"] for x in items)
    arch = (await client.get("/api/tasks?archived=true", headers=tenant)).json()["items"]
    assert [x["id"] for x in arch] == [t["id"]]
    # an archived task can't be edited until restored
    assert (await client.patch(f"/api/tasks/{t['id']}", json={"title": "y"},
                               headers=tenant)).status_code == 409
    r = await client.post(f"/api/tasks/{t['id']}/restore", headers=tenant)
    assert r.status_code == 200 and r.json()["archived_at"] is None
    items = (await client.get("/api/tasks", headers=tenant)).json()["items"]
    assert any(x["id"] == t["id"] for x in items)


@pytest.mark.asyncio
async def test_ensure_task_is_idempotent_and_resolves(client, tenant, db):
    from backend.services.auto_tasks import ensure_task, resolve_tasks
    org = (await _me(client, tenant))["org_id"]
    a, created_a = await ensure_task(db, org_id=org, source="automation", source_ref="order:x:status:ready",
                                     title="Call", notify=False)
    b, created_b = await ensure_task(db, org_id=org, source="automation", source_ref="order:x:status:ready",
                                     title="Call", notify=False)
    await db.commit()
    assert created_a and not created_b and a.id == b.id
    assert a.status == "todo" and a.source == "automation"
    n = await resolve_tasks(db, org_id=org, source_ref_prefix="order:x:")
    await db.commit()
    assert n == 1
    await db.refresh(a)
    assert a.status == "done" and a.completed_at is not None
    c, created_c = await ensure_task(db, org_id=org, source="automation", source_ref="order:x:status:ready",
                                     title="Call", notify=False)
    await db.commit()
    assert created_c and c.id != a.id
    # cancel instead of done
    assert await resolve_tasks(db, org_id=org, source_ref="order:x:status:ready", status="cancelled") == 1
    await db.commit()
    await db.refresh(c)
    assert c.status == "cancelled"


@pytest.mark.asyncio
async def test_partial_unique_index_blocks_a_second_open_task(client, tenant, db):
    org = (await _me(client, tenant))["org_id"]
    db.add(Task(org_id=org, title="a", source="automation", source_ref="r:1"))
    await db.commit()
    db.add(Task(org_id=org, title="b", source="automation", source_ref="r:1"))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
    # closed or archived rows don't count
    await db.execute(update(Task).where(Task.source_ref == "r:1").values(status="done"))
    db.add(Task(org_id=org, title="c", source="automation", source_ref="r:1"))
    await db.commit()


@pytest.mark.asyncio
async def test_missed_call_task_uses_source_ref(client, tenant, db):
    """Leads are the first caller of ensure_task: one open call-back task per inquiry."""
    from backend.services.leads import on_missed_call
    org = (await _me(client, tenant))["org_id"]

    class _Call:
        from_number = "+15550199"
        client_id = None
        created_at = now_utc()
    await on_missed_call(db, org, _Call())
    await on_missed_call(db, org, _Call())
    rows = (await db.execute(select(Task).where(Task.org_id == org, Task.lead_id.isnot(None)))).scalars().all()
    assert len(rows) == 1
    assert rows[0].source == "inquiry" and rows[0].source_ref == f"lead:{rows[0].lead_id}"
    assert rows[0].priority == 1      # legacy "high" → 1


# ── 4. bell + push for every assignee ────────────────────────────────────────
@pytest.mark.asyncio
async def test_bell_reaches_every_assignee_but_not_the_actor(client, tenant, db):
    me = await _me(client, tenant)
    h1, e1 = await _employee(client, db, me["org_id"], "a1@x.dev", "Ann")
    _, e2 = await _employee(client, db, me["org_id"], "a2@x.dev", "Ben")
    _, e3 = await _employee(client, db, me["org_id"], "a3@x.dev", "Cat")
    t = (await client.post("/api/tasks", json={
        "title": "Swap screen", "assignee_ids": [e1.id, e2.id, me["id"]]}, headers=tenant)).json()
    assert len(await _bell(db, e1.id, "task.assigned")) == 1
    assert len(await _bell(db, e2.id, "task.assigned")) == 1
    assert await _bell(db, me["id"]) == []                     # the actor hears nothing
    link = (await _bell(db, e1.id, "task.assigned"))[0].deep_link
    assert t["id"] in link

    # editing the title is not a new assignment
    await client.patch(f"/api/tasks/{t['id']}", json={"title": "Swap screen (OLED)"}, headers=tenant)
    assert len(await _bell(db, e1.id, "task.assigned")) == 1
    # adding someone notifies only the newcomer
    await client.patch(f"/api/tasks/{t['id']}", json={
        "assignee_ids": [e1.id, e2.id, me["id"], e3.id]}, headers=tenant)
    assert len(await _bell(db, e3.id, "task.assigned")) == 1
    assert len(await _bell(db, e1.id, "task.assigned")) == 1
    assert len(await _bell(db, e2.id, "task.assigned")) == 1

    # e1 completes: author and the other assignees hear, e1 doesn't
    r = await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=h1)
    assert r.status_code == 200, r.text
    assert len(await _bell(db, me["id"], "task.completed")) == 1
    assert len(await _bell(db, e2.id, "task.completed")) == 1
    assert await _bell(db, e1.id, "task.completed") == []


@pytest.mark.asyncio
async def test_changes_reach_the_bell_once_until_read(client, tenant, db):
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "c1@x.dev")
    t = (await client.post("/api/tasks", json={"title": "x", "assignee_ids": [e1.id]},
                           headers=tenant)).json()
    for body in ("one", "two", "three"):
        await client.post(f"/api/tasks/{t['id']}/comments", json={"body": body}, headers=tenant)
    assert len(await _bell(db, e1.id, "task.changed")) == 1     # one unread "changed" per task


@pytest.mark.asyncio
async def test_automation_task_alerts_its_assignee(client, tenant, db):
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "auto@x.dev")
    key, _ = await _two_statuses(client, tenant)
    await client.post("/api/automations", json={
        "name": "Ready → task", "event": "status_changed", "trigger_status": key,
        "action_type": "create_task", "action_config": {"title": "Call", "employee_id": e1.id}},
        headers=tenant)
    o = await _order(client, tenant)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": key, "reason": "t"}, headers=tenant)
    assert len(await _bell(db, e1.id, "task.assigned")) == 1


# ── 5. failures are logged ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_notification_failure_is_logged_not_swallowed(client, tenant, monkeypatch, caplog):
    from backend.services import notification_rules as nr

    async def boom(*a, **k):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(nr, "notify_task", boom)
    with caplog.at_level(logging.WARNING):
        r = await client.post("/api/tasks", json={"title": "x"}, headers=tenant)
    assert r.status_code == 201
    assert any("task notification" in rec.getMessage() for rec in caplog.records)


# ── 6. scheduled automation sweep ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_scheduled_sweep_fires_once_and_skips_old_backlog(client, tenant, db):
    from backend.core import scheduled
    org = (await _me(client, tenant))["org_id"]
    rid = (await client.post("/api/automations", json={
        "name": "Overdue → task", "event": "order_overdue",
        "action_type": "create_task", "action_config": {"title": "Chase overdue"}},
        headers=tenant)).json()["id"]
    now = now_utc()
    # the rule has been on for two hours
    await db.execute(update(AutomationRule).where(AutomationRule.id == rid).values(
        created_at=now - timedelta(hours=2), activated_at=now - timedelta(hours=2)))
    await db.commit()
    fresh = await _order(client, tenant, due_at=(now - timedelta(hours=1)).isoformat())
    old = await _order(client, tenant, due_at=(now - timedelta(days=30)).isoformat())

    await scheduled.sweep_time_automations()
    await scheduled.sweep_time_automations()          # repeated runs add nothing

    def count(oid):
        return select(Task).where(Task.org_id == org, Task.order_id == oid)
    assert len((await db.execute(count(fresh["id"]))).scalars().all()) == 1
    assert (await db.execute(count(old["id"]))).scalars().all() == []   # backlog untouched
    rule = (await db.execute(select(AutomationRule).where(AutomationRule.id == rid))).scalar_one()
    await db.refresh(rule)
    assert rule.fire_count == 1


@pytest.mark.asyncio
async def test_low_stock_fires_once_per_episode(client, tenant, db):
    from backend.core import scheduled
    from backend.models import Part
    org = (await _me(client, tenant))["org_id"]
    rid = (await client.post("/api/automations", json={
        "name": "Low → tag", "event": "low_stock", "action_type": "notify",
        "action_config": {"message": "low"}}, headers=tenant)).json()["id"]
    db.add(Part(org_id=org, name="Glue", sku="G-1", min_stock=5, active=True))
    await db.commit()
    await scheduled.sweep_time_automations()
    await scheduled.sweep_time_automations()
    rule = (await db.execute(select(AutomationRule).where(AutomationRule.id == rid))).scalar_one()
    await db.refresh(rule)
    assert rule.fire_count == 1


@pytest.mark.asyncio
async def test_manual_sweep_still_fires_backlog_but_dedupes(client, tenant, db):
    org = (await _me(client, tenant))["org_id"]
    await client.post("/api/automations", json={
        "name": "Overdue → task", "event": "order_overdue",
        "action_type": "create_task", "action_config": {"title": "Chase"}}, headers=tenant)
    old = await _order(client, tenant, due_at=(now_utc() - timedelta(days=30)).isoformat())
    assert (await client.post("/api/automations/sweep", headers=tenant)).json()["fired"] == 1
    assert (await client.post("/api/automations/sweep", headers=tenant)).json()["fired"] == 0
    rows = (await db.execute(select(Task).where(Task.org_id == org, Task.order_id == old["id"]))).scalars().all()
    assert len(rows) == 1


# ── 7. send-time status guard ────────────────────────────────────────────────
async def _delayed_rule(client, db, H, key):
    org = (await _me(client, H))["org_id"]
    from sqlalchemy import delete
    await db.execute(delete(NotificationRule).where(NotificationRule.org_id == org))
    await db.commit()
    r = await client.post("/api/notifications/rules", json={
        "module": "order", "event": "status_changed", "trigger_status_key": key,
        "channel": "email", "delay_min": 60}, headers=H)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_delayed_message_cancelled_when_job_moved_on(client, tenant, db):
    key, other = await _two_statuses(client, tenant)
    await _delayed_rule(client, db, tenant, key)
    o = await _order(client, tenant)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": key, "reason": "t"}, headers=tenant)
    log = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == o["id"], NotificationLog.status == "scheduled"))).scalar_one()
    assert log.expect_status == key
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": other, "reason": "t"}, headers=tenant)
    log.send_at = now_utc() - timedelta(minutes=1)
    await db.commit()
    res = (await client.post("/api/notifications/rules/run-due", headers=tenant)).json()
    assert res["sent"] == 0
    await db.refresh(log)
    assert log.status == "skipped" and key in (log.error or "")


@pytest.mark.asyncio
async def test_delayed_message_sent_when_job_still_in_status(client, tenant, db):
    key, _ = await _two_statuses(client, tenant)
    await _delayed_rule(client, db, tenant, key)
    o = await _order(client, tenant)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": key, "reason": "t"}, headers=tenant)
    log = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == o["id"], NotificationLog.status == "scheduled"))).scalar_one()
    log.send_at = now_utc() - timedelta(minutes=1)
    await db.commit()
    res = (await client.post("/api/notifications/rules/run-due", headers=tenant)).json()
    assert res["sent"] == 1


# ── 8. legacy API removed ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_legacy_staff_tasks_api_is_gone(client, tenant):
    assert (await client.get("/api/staff/tasks", headers=tenant)).status_code in (404, 405)
    assert (await client.post("/api/staff/tasks", json={"title": "x"}, headers=tenant)).status_code in (404, 405)
    assert (await client.patch("/api/staff/tasks/abc", json={"status": "done"},
                               headers=tenant)).status_code in (404, 405)


# ── 9. server-side search and paging ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_search_assignee_and_paging(client, tenant, db):
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "l1@x.dev")
    o = await _order(client, tenant, client_name="Zelda Quartz")
    await client.post("/api/tasks", json={"title": "Order battery", "order_id": o["id"],
                                          "client_id": o["client_id"]}, headers=tenant)
    await client.post("/api/tasks", json={"title": "Clean bench", "description": "with alcohol",
                                          "assignee_ids": [e1.id]}, headers=tenant)
    for i in range(5):
        await client.post("/api/tasks", json={"title": f"Filler {i}"}, headers=tenant)

    def titles(r):
        return {x["title"] for x in r.json()["items"]}
    assert titles(await client.get("/api/tasks?q=battery", headers=tenant)) == {"Order battery"}
    assert titles(await client.get("/api/tasks?q=alcohol", headers=tenant)) == {"Clean bench"}
    assert titles(await client.get(f"/api/tasks?q={o['order_number']}", headers=tenant)) == {"Order battery"}
    assert titles(await client.get("/api/tasks?q=zelda", headers=tenant)) == {"Order battery"}
    assert titles(await client.get(f"/api/tasks?assignee={e1.id}", headers=tenant)) == {"Clean bench"}
    mine = await client.get("/api/tasks?assignee=me&q=bench", headers=tenant)
    assert titles(mine) == {"Clean bench"}            # I created it, so it involves me

    page = (await client.get("/api/tasks?limit=3&offset=0", headers=tenant)).json()
    assert len(page["items"]) == 3 and page["total"] == 7
    page2 = (await client.get("/api/tasks?limit=3&offset=6", headers=tenant)).json()
    assert len(page2["items"]) == 1 and page2["total"] == 7

    # no params: the old shape, everything
    full = (await client.get("/api/tasks", headers=tenant)).json()
    assert len(full["items"]) == 7 and "statuses" in full
    assert "todo" in full["statuses"]


@pytest.mark.asyncio
async def test_list_client_name_priority_overdue_filters(client, tenant):
    """What the web Tasks page sends: client_name on every row (task's client, else the job's),
    priority and overdue filters, and unknown parameters ignored."""
    o = await _order(client, tenant, client_name="Yara Okafor")
    past = (now_utc() - timedelta(days=1)).isoformat()
    await client.post("/api/tasks", json={"title": "Via job", "order_id": o["id"], "priority": "urgent",
                                          "due_at": past}, headers=tenant)
    await client.post("/api/tasks", json={"title": "Plain", "priority": 0}, headers=tenant)
    done = (await client.post("/api/tasks", json={"title": "Late but done", "due_at": past},
                              headers=tenant)).json()
    await client.patch(f"/api/tasks/{done['id']}", json={"status": "done"}, headers=tenant)

    items = {x["title"]: x for x in (await client.get("/api/tasks", headers=tenant)).json()["items"]}
    assert items["Via job"]["client_name"] == "Yara Okafor"
    assert items["Plain"]["client_name"] is None

    def titles(r):
        return {x["title"] for x in r.json()["items"]}
    assert titles(await client.get("/api/tasks?q=okafor", headers=tenant)) == {"Via job"}
    assert titles(await client.get("/api/tasks?priority=2", headers=tenant)) == {"Via job"}
    assert titles(await client.get("/api/tasks?priority=urgent,high", headers=tenant)) == {"Via job"}
    assert titles(await client.get("/api/tasks?overdue=true", headers=tenant)) == {"Via job"}
    assert titles(await client.get("/api/tasks?overdue=true&assignee=me&nonsense=1",
                                   headers=tenant)) == {"Via job"}
    assert (await client.get("/api/tasks?priority=banana", headers=tenant)).status_code == 422


@pytest.mark.asyncio
async def test_list_never_leaks_another_shops_job_or_client(client, tenant, db):
    """A task row pointing at another shop's job and client (written straight to the database;
    the API refuses such links): client_name, order_number and q search must not follow it."""
    import uuid

    from sqlalchemy import insert
    r = await client.post("/api/auth/register", json={
        "org_name": "Other shop", "name": "O", "email": f"o-{uuid.uuid4().hex[:8]}@x.dev",
        "password": "pw-test-12345"})
    H2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    theirs = await _order(client, H2, client_name="Quentin Secretname")
    org = (await _me(client, tenant))["org_id"]
    tid = str(uuid.uuid4())
    await db.execute(insert(Task.__table__).values(
        id=tid, org_id=org, title="Bad link", status="todo", priority=0,
        order_id=theirs["id"], client_id=theirs["client_id"]))
    await db.commit()

    rows = (await client.get("/api/tasks", headers=tenant)).json()["items"]
    row = next(x for x in rows if x["id"] == tid)
    assert row["client_name"] is None and row["order_number"] is None
    for term in ("Secretname", theirs["order_number"]):
        hits = (await client.get("/api/tasks", params={"q": term}, headers=tenant)).json()["items"]
        assert all(x["id"] != tid for x in hits), term
    # and the other shop never sees this shop's task
    assert all(x["id"] != tid for x in (await client.get("/api/tasks", headers=H2)).json()["items"])
