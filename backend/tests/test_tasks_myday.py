"""Tasks: role visibility, overdue alerts, deep links, shared commission, My day.

1. Staff without can_assign_staff see only tasks assigned to them, made by them, or unassigned;
   managers and owners see every task. A task you can't see is a 404 everywhere (get, feed,
   comments, edit, delete), and the home feed / dashboard counts follow the same rule.
2. The overdue sweep alerts every assignee once when an open task passes its due time, never
   for done/cancelled/deleted tasks, re-arms when the due time moves later, and holds alerts
   outside working hours until the next opening.
3. Every task notification links to /tasks?task=<id> (bell row and push payload).
4. task_commission_mode: split (default) | each_full | first_only, idempotent per
   task+employee+rule, nothing for cancelled tasks.
5. GET /api/tasks/my-day buckets on the shop's day; POST /api/tasks/move-to-today; due_at
   accepts a date-only value (18:00 shop time).
"""
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from backend.models import (
    CommissionRule,
    EmployeeBalanceLedger,
    EventFeedItem,
    Role,
    StaffNotification,
    Task,
)
from backend.services.auth import hash_password
from backend.services.dates import day_start_utc, today_in, utc_to_wall, wall_to_utc

TZ = "Asia/Kolkata"   # a new shop's default


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _employee(client, db, org_id, email, name="Emp", perms=None):
    """An employee whose role grants exactly `perms` (default-deny)."""
    from backend.models import Employee
    role = Role(org_id=org_id, name=f"R-{email}", permissions=perms or {}, perms_explicit=True)
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


async def _task(client, H, **body):
    r = await client.post("/api/tasks", json={"title": "t", **body}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _bell(db, emp_id, kind=None):
    q = select(StaffNotification).where(StaffNotification.employee_id == emp_id)
    if kind:
        q = q.where(StaffNotification.kind == kind)
    return (await db.execute(q)).scalars().all()


def _local(day: date, hh: int, mm: int = 0, ss: int = 0, tz: str = TZ) -> datetime:
    """Shop wall-clock time on `day` as the naive UTC instant tasks store."""
    return wall_to_utc(datetime.combine(day, time(hh, mm, ss)), tz)


def _iso(dt: datetime) -> str:
    """A naive UTC instant as the API expects an instant: with its offset (no offset = shop time)."""
    return dt.isoformat() + "+00:00"


# ── 1. visibility by role ────────────────────────────────────────────────────
async def _visibility_world(client, tenant, db):
    me = await _me(client, tenant)
    SH, staff = await _employee(client, db, me["org_id"], "staff-vis@x.dev", "Staff")
    _, other = await _employee(client, db, me["org_id"], "other-vis@x.dev", "Other")
    tasks = {
        "mine": await _task(client, tenant, title="Mine", assigned_to_id=staff.id),
        "extra": await _task(client, tenant, title="Extra", assignee_ids=[other.id, staff.id]),
        "theirs": await _task(client, tenant, title="Theirs", assigned_to_id=other.id),
        "free": await _task(client, tenant, title="Unassigned"),
        "made": await _task(client, SH, title="Made by staff"),
    }
    return me, SH, staff, other, tasks


@pytest.mark.asyncio
async def test_staff_see_only_their_own_or_unassigned_tasks(client, tenant, db):
    me, SH, staff, other, t = await _visibility_world(client, tenant, db)
    seen = {x["title"] for x in (await client.get("/api/tasks", headers=SH)).json()["items"]}
    assert seen == {"Mine", "Extra", "Unassigned", "Made by staff"}
    lst = (await client.get("/api/tasks?limit=50", headers=SH)).json()
    assert lst["total"] == 4
    # the owner sees everything
    owner = {x["title"] for x in (await client.get("/api/tasks", headers=tenant)).json()["items"]}
    assert {"Mine", "Extra", "Theirs", "Unassigned", "Made by staff"} <= owner


@pytest.mark.asyncio
async def test_a_hidden_task_is_404_everywhere(client, tenant, db):
    me, SH, staff, other, t = await _visibility_world(client, tenant, db)
    tid = t["theirs"]["id"]
    assert (await client.get(f"/api/tasks/{tid}", headers=SH)).status_code == 404
    assert (await client.get(f"/api/tasks/{tid}/feed", headers=SH)).status_code == 404
    assert (await client.post(f"/api/tasks/{tid}/comments", json={"body": "hi"}, headers=SH)).status_code == 404
    assert (await client.patch(f"/api/tasks/{tid}", json={"title": "x"}, headers=SH)).status_code == 404
    assert (await client.delete(f"/api/tasks/{tid}", headers=SH)).status_code == 404
    assert (await client.post(f"/api/tasks/{tid}/restore", headers=SH)).status_code == 404
    # the ones they can see still open
    for k in ("mine", "extra", "free", "made"):
        assert (await client.get(f"/api/tasks/{t[k]['id']}", headers=SH)).status_code == 200


@pytest.mark.asyncio
async def test_a_manager_role_sees_every_task(client, tenant, db):
    me, SH, staff, other, t = await _visibility_world(client, tenant, db)
    MH, _ = await _employee(client, db, me["org_id"], "mgr-vis@x.dev", "Mgr",
                            perms={"can_assign_staff": True})
    seen = {x["title"] for x in (await client.get("/api/tasks", headers=MH)).json()["items"]}
    assert "Theirs" in seen
    assert (await client.get(f"/api/tasks/{t['theirs']['id']}", headers=MH)).status_code == 200


@pytest.mark.asyncio
async def test_subtasks_follow_visibility(client, tenant, db):
    me, SH, staff, other, t = await _visibility_world(client, tenant, db)
    from backend.models import Task as _T
    db.add(_T(org_id=me["org_id"], title="Hidden sub", assigned_to_id=other.id,
              assignee_ids=[other.id], parent_task_id=t["mine"]["id"], status="todo"))
    db.add(_T(org_id=me["org_id"], title="My sub", assigned_to_id=staff.id,
              assignee_ids=[staff.id], parent_task_id=t["mine"]["id"], status="todo"))
    await db.commit()
    d = (await client.get(f"/api/tasks/{t['mine']['id']}", headers=SH)).json()
    assert [s["title"] for s in d["subtasks"]] == ["My sub"]


@pytest.mark.asyncio
async def test_tasks_are_isolated_between_shops(client, tenant, db):
    _, _, _, _, t = await _visibility_world(client, tenant, db)
    import uuid
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Boss", "email": f"boss-{uuid.uuid4().hex[:6]}@x.dev",
        "password": "pw-test-12345"})
    H2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get(f"/api/tasks/{t['free']['id']}", headers=H2)).status_code == 404
    assert (await client.get("/api/tasks", headers=H2)).json()["items"] == []
    day = (await client.get("/api/tasks/my-day?assignee=all", headers=H2)).json()
    assert sum(day["counts"].values()) == 0
    moved = (await client.post("/api/tasks/move-to-today", json={"ids": [t["free"]["id"]]}, headers=H2)).json()
    assert moved == {"moved": 0, "skipped": [t["free"]["id"]]}
    # a manager of the other shop: every task endpoint is a 404 (TEN-1), so is our employee's day (TEN-3)
    for method, path in (("get", ""), ("get", "/feed"), ("patch", ""), ("delete", ""), ("post", "/restore"),
                         ("post", "/comments")):
        kw = {"json": {"title": "x", "body": "x"}} if method in ("patch", "post") else {}
        r = await getattr(client, method)(f"/api/tasks/{t['mine']['id']}{path}", headers=H2, **kw)
        assert r.status_code == 404, (method, path, r.status_code)
    me = await _me(client, tenant)
    assert (await client.get(f"/api/tasks/my-day?assignee={me['id']}", headers=H2)).status_code == 404


@pytest.mark.asyncio
async def test_home_feed_and_dashboard_count_only_visible_tasks(client, tenant, db):
    me, SH, staff, other, t = await _visibility_world(client, tenant, db)
    # give the staff role the dashboard so we can read its tile
    role = (await db.execute(select(Role).where(Role.id == staff.role_id))).scalar_one()
    role.permissions = {"can_view_reports": True}
    await db.commit()
    feed = (await client.get("/api/reports/home-feed", headers=SH)).json()
    tasks = next(i for i in feed["items"] if i["key"] == "tasks")
    assert tasks["count"] == 4
    dash = (await client.get("/api/reports/dashboard", headers=SH)).json()
    assert dash["stats"]["pending_tasks"] == 4
    owner_dash = (await client.get("/api/reports/dashboard", headers=tenant)).json()
    assert owner_dash["stats"]["pending_tasks"] == 5


# ── 2. overdue alerts ────────────────────────────────────────────────────────
def _working_now() -> datetime:
    """11:00 today on the shop's clock (inside the default 09:00-18:00 window)."""
    return _local(today_in(TZ), 11)


@pytest.mark.asyncio
async def test_overdue_alerts_every_assignee_once(client, tenant, db):
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "od1@x.dev")
    _, e2 = await _employee(client, db, me["org_id"], "od2@x.dev")
    now = _working_now()
    t = await _task(client, tenant, assignee_ids=[e1.id, e2.id], due_at=_iso(now - timedelta(minutes=30)))
    later = await _task(client, tenant, assignee_ids=[e1.id], due_at=_iso(now + timedelta(hours=2)))
    assert await sweep_overdue(db, me["org_id"], now=now) == 1
    for e in (e1, e2):
        rows = await _bell(db, e.id, "task.overdue")
        assert len(rows) == 1 and rows[0].deep_link == f"/tasks?task={t['id']}"
    assert await _bell(db, me["id"], "task.overdue") == []     # the author is not an assignee
    # a second tick says nothing new
    assert await sweep_overdue(db, me["org_id"], now=now + timedelta(minutes=15)) == 0
    assert len(await _bell(db, e1.id, "task.overdue")) == 1
    row = (await db.execute(select(Task).where(Task.id == later["id"]))).scalar_one()
    assert row.overdue_alerted_at is None


@pytest.mark.asyncio
async def test_overdue_never_alerts_closed_or_deleted_tasks(client, tenant, db):
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "od3@x.dev")
    now = _working_now()
    past = _iso(now - timedelta(hours=1))
    done = await _task(client, tenant, assigned_to_id=e1.id, due_at=past)
    cancelled = await _task(client, tenant, assigned_to_id=e1.id, due_at=past)
    deleted = await _task(client, tenant, assigned_to_id=e1.id, due_at=past)
    await client.patch(f"/api/tasks/{done['id']}", json={"status": "done"}, headers=tenant)
    await client.patch(f"/api/tasks/{cancelled['id']}", json={"status": "cancelled"}, headers=tenant)
    await client.delete(f"/api/tasks/{deleted['id']}", headers=tenant)
    assert await sweep_overdue(db, me["org_id"], now=now) == 0
    assert await _bell(db, e1.id, "task.overdue") == []


@pytest.mark.asyncio
async def test_overdue_rearms_when_the_due_time_moves_later(client, tenant, db, monkeypatch):
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "od4@x.dev")
    now = _working_now()
    _freeze(monkeypatch, now.isoformat())      # the PATCH judges "in the future" on the app clock
    t = await _task(client, tenant, assigned_to_id=e1.id, due_at=_iso(now - timedelta(hours=1)))
    assert await sweep_overdue(db, me["org_id"], now=now) == 1
    new_due = now + timedelta(hours=1)
    await client.patch(f"/api/tasks/{t['id']}", json={"due_at": _iso(new_due)}, headers=tenant)
    assert await sweep_overdue(db, me["org_id"], now=now) == 0           # not due yet
    assert await sweep_overdue(db, me["org_id"], now=new_due + timedelta(minutes=5)) == 1
    assert len(await _bell(db, e1.id, "task.overdue")) == 2


@pytest.mark.asyncio
async def test_overdue_alert_waits_for_working_hours(client, tenant, db):
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "od5@x.dev")
    today = today_in(TZ)
    night = _local(today, 22)
    t = await _task(client, tenant, assigned_to_id=e1.id, due_at=_iso(_local(today, 20)))
    assert await sweep_overdue(db, me["org_id"], now=night) == 0
    row = (await db.execute(select(Task).where(Task.id == t["id"]))).scalar_one()
    assert row.overdue_alerted_at is None                                # held, not dropped
    assert await sweep_overdue(db, me["org_id"], now=_local(today + timedelta(days=1), 9, 5)) == 1
    assert len(await _bell(db, e1.id, "task.overdue")) == 1


def test_overdue_sweep_is_scheduled_every_15_minutes():
    import backend.core.scheduled  # noqa: F401  (registers the jobs)
    from backend.core.jobs import job_runner
    jobs = {getattr(j, "name", None): j for j in job_runner._periodics}
    job = jobs.get("task-overdue-sweep")
    assert job is not None
    assert getattr(job, "interval_s", None) == 900


# ── 3. deep links ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_every_task_notification_links_to_the_task(client, tenant, db, monkeypatch):
    from backend.services import push
    sent = []

    async def capture(_db, employee_id, payload):
        sent.append(payload)
    monkeypatch.setattr(push, "_dispatch_push", capture)
    me = await _me(client, tenant)
    EH, e1 = await _employee(client, db, me["org_id"], "dl@x.dev")
    t = await _task(client, tenant, assigned_to_id=e1.id)
    await client.patch(f"/api/tasks/{t['id']}", json={"title": "renamed"}, headers=tenant)
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=EH)
    link = f"/tasks?task={t['id']}"
    kinds = {p["kind"] for p in sent}
    assert {"task.assigned", "task.changed", "task.completed"} <= kinds
    assert all(p["deep_link"] == link and p["url"] == link for p in sent)
    assert all(n.deep_link == link for n in await _bell(db, e1.id))


# ── 4. commission with several assignees ─────────────────────────────────────
async def _rule(db, org_id, amount=10):
    r = CommissionRule(org_id=org_id, name="Task", event="task_closed", calc_type="flat", amount=amount)
    db.add(r)
    await db.commit()
    return r


async def _paid(db, emp_id) -> float:
    rows = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.employee_id == emp_id, EmployeeBalanceLedger.event == "task_closed"))).scalars().all()
    return float(sum(r.amount for r in rows))


async def _two_people_task(client, tenant, db, mode):
    me = await _me(client, tenant)
    if mode:
        r = await client.put("/api/payroll/settings", json={"task_commission_mode": mode}, headers=tenant)
        assert r.status_code == 200, r.text
    _, e1 = await _employee(client, db, me["org_id"], f"c1-{mode}@x.dev")
    _, e2 = await _employee(client, db, me["org_id"], f"c2-{mode}@x.dev")
    rule = await _rule(db, me["org_id"])
    t = await _task(client, tenant, assignee_ids=[e1.id, e2.id])
    return me, e1, e2, rule, t


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,first,second", [
    (None, 5.0, 5.0), ("split", 5.0, 5.0), ("each_full", 10.0, 10.0), ("first_only", 10.0, 0.0)])
async def test_task_commission_modes(client, tenant, db, mode, first, second):
    me, e1, e2, rule, t = await _two_people_task(client, tenant, db, mode)
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
    assert await _paid(db, e1.id) == pytest.approx(first)
    assert await _paid(db, e2.id) == pytest.approx(second)
    keys = set((await db.execute(select(EmployeeBalanceLedger.idempotency_key).where(
        EmployeeBalanceLedger.ref_id == t["id"]))).scalars().all())
    assert f"task:{t['id']}:{e1.id}:{rule.id}" in keys


@pytest.mark.asyncio
async def test_split_commission_rounds_to_the_full_amount(client, tenant, db):
    me = await _me(client, tenant)
    people = [(await _employee(client, db, me["org_id"], f"r{i}@x.dev"))[1] for i in range(3)]
    await _rule(db, me["org_id"], amount=10)
    t = await _task(client, tenant, assignee_ids=[p.id for p in people])
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
    shares = [await _paid(db, p.id) for p in people]
    assert sum(shares) == pytest.approx(10.0, abs=1e-9)
    assert shares == pytest.approx([3.3334, 3.3333, 3.3333])        # the owner takes the remainder


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,first,second", [
    ("split", 5.0, 5.0), ("each_full", 10.0, 10.0), ("first_only", 10.0, 0.0)])
async def test_reopen_and_close_again_nets_one_payout(client, tenant, db, mode, first, second):
    me, e1, e2, rule, t = await _two_people_task(client, tenant, db, mode)
    for status in ("done", "done", "todo", "done", "in_progress", "done"):
        await client.patch(f"/api/tasks/{t['id']}", json={"status": status}, headers=tenant)
    assert await _paid(db, e1.id) == pytest.approx(first)
    assert await _paid(db, e2.id) == pytest.approx(second)
    # reopen took it back, so while open nobody is owed anything for it
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "todo"}, headers=tenant)
    assert await _paid(db, e1.id) == 0 and await _paid(db, e2.id) == 0


@pytest.mark.asyncio
async def test_a_task_paid_under_the_old_key_is_not_paid_again(client, tenant, db):
    """Tasks closed before this change were paid to the primary under task:<task>:<rule>."""
    me, e1, e2, rule, t = await _two_people_task(client, tenant, db, "split")
    db.add(EmployeeBalanceLedger(
        org_id=me["org_id"], employee_id=e1.id, entry_type="commission", amount=10,
        event="task_closed", rule_id=rule.id, ref_type="task", ref_id=t["id"],
        idempotency_key=f"task:{t['id']}:{rule.id}", accrual_key=f"task:{t['id']}:{rule.id}"))
    await db.commit()
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
    assert await _paid(db, e1.id) == pytest.approx(10.0)      # the old payment, nothing more
    assert await _paid(db, e2.id) == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_cancelled_task_pays_nothing(client, tenant, db):
    me, e1, e2, rule, t = await _two_people_task(client, tenant, db, "each_full")
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "cancelled"}, headers=tenant)
    assert await _paid(db, e1.id) == 0 and await _paid(db, e2.id) == 0
    # done, then cancelled, takes the payout back (COM-8)
    t2 = await _task(client, tenant, assignee_ids=[e1.id, e2.id])
    await client.patch(f"/api/tasks/{t2['id']}", json={"status": "done"}, headers=tenant)
    assert await _paid(db, e1.id) == pytest.approx(10.0)
    await client.patch(f"/api/tasks/{t2['id']}", json={"status": "cancelled"}, headers=tenant)
    assert await _paid(db, e1.id) == 0 and await _paid(db, e2.id) == 0


@pytest.mark.asyncio
async def test_new_assignees_on_reclose_get_the_new_shares(client, tenant, db):
    """COM-7: close, reopen, swap a person, close: the dropped person nets 0, the new one a share."""
    me, e1, e2, rule, t = await _two_people_task(client, tenant, db, "split")
    _, e3 = await _employee(client, db, me["org_id"], "c3-swap@x.dev")
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "todo"}, headers=tenant)
    await client.patch(f"/api/tasks/{t['id']}", json={"assignee_ids": [e1.id, e3.id]}, headers=tenant)
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
    assert await _paid(db, e1.id) == pytest.approx(5.0)
    assert await _paid(db, e2.id) == 0
    assert await _paid(db, e3.id) == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_delete_and_restore_a_done_task_nets_one_payout(client, tenant, db):
    """COM-9."""
    me, e1, e2, rule, t = await _two_people_task(client, tenant, db, "each_full")
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
    await client.delete(f"/api/tasks/{t['id']}", headers=tenant)
    assert await _paid(db, e1.id) == 0
    await client.post(f"/api/tasks/{t['id']}/restore", headers=tenant)
    await client.post(f"/api/tasks/{t['id']}/restore", headers=tenant)
    assert await _paid(db, e1.id) == pytest.approx(10.0) and await _paid(db, e2.id) == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_mode_change_is_not_retroactive_and_duplicates_pay_once(client, tenant, db):
    """COM-10, COM-12, TEN-5."""
    me, e1, e2, rule, t = await _two_people_task(client, tenant, db, "each_full")
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
    await client.put("/api/payroll/settings", json={"task_commission_mode": "split"}, headers=tenant)
    assert await _paid(db, e1.id) == pytest.approx(10.0)            # already paid stays
    dup = await _task(client, tenant, assigned_to_id=e1.id, assignee_ids=[e1.id, e1.id])
    await client.patch(f"/api/tasks/{dup['id']}", json={"status": "done"}, headers=tenant)
    assert await _paid(db, e1.id) == pytest.approx(20.0)            # one person, paid once
    nobody = await _task(client, tenant)
    assert (await client.patch(f"/api/tasks/{nobody['id']}", json={"status": "done"}, headers=tenant)).status_code == 200
    # another shop's setting is its own
    import uuid
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Boss", "email": f"boss-{uuid.uuid4().hex[:6]}@x.dev",
        "password": "pw-test-12345"})
    H2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get("/api/payroll/settings", headers=H2)).json()["task_commission_mode"] == "split"
    await client.put("/api/payroll/settings", json={"task_commission_mode": "first_only"}, headers=H2)
    assert (await client.get("/api/payroll/settings", headers=tenant)).json()["task_commission_mode"] == "split"


@pytest.mark.asyncio
async def test_payroll_settings_api(client, tenant, db):
    me = await _me(client, tenant)
    got = (await client.get("/api/payroll/settings", headers=tenant)).json()
    assert got["task_commission_mode"] == "split"
    assert set(got["task_commission_modes"]) == {"split", "each_full", "first_only"}
    bad = await client.put("/api/payroll/settings", json={"task_commission_mode": "half"}, headers=tenant)
    assert bad.status_code == 422
    ok = await client.put("/api/payroll/settings", json={"task_commission_mode": "first_only"}, headers=tenant)
    assert ok.json()["task_commission_mode"] == "first_only"
    SH, _ = await _employee(client, db, me["org_id"], "nopay@x.dev")
    assert (await client.put("/api/payroll/settings", json={"task_commission_mode": "split"},
                             headers=SH)).status_code == 403
    assert (await client.get("/api/payroll/settings", headers=SH)).status_code == 403


# ── 5. My day ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_my_day_buckets_on_the_shop_day(client, tenant, db):
    me = await _me(client, tenant)
    _, other = await _employee(client, db, me["org_id"], "md-other@x.dev")
    today = today_in(TZ)
    mine = {"assigned_to_id": me["id"]}
    old = await _task(client, tenant, title="old", due_at=_iso(_local(today - timedelta(days=3), 10)), **mine)
    yday = await _task(client, tenant, title="yday", due_at=_iso(_local(today - timedelta(days=1), 23, 59)), **mine)
    early = await _task(client, tenant, title="early", due_at=_iso(_local(today, 0, 1)), **mine)
    late_hi = await _task(client, tenant, title="late-hi", priority=2, due_at=_iso(_local(today, 17)), **mine)
    late_lo = await _task(client, tenant, title="late-lo", priority=0, due_at=_iso(_local(today, 17)), **mine)
    tmrw = await _task(client, tenant, title="tmrw", due_at=_iso(_local(today + timedelta(days=1), 9)), **mine)
    wk = await _task(client, tenant, title="wk", due_at=_iso(_local(today + timedelta(days=7), 23)), **mine)
    far = await _task(client, tenant, title="far", due_at=_iso(_local(today + timedelta(days=8), 0, 30)), **mine)
    nodue = await _task(client, tenant, title="nodue", **mine)
    done = await _task(client, tenant, title="done", **mine)
    await client.patch(f"/api/tasks/{done['id']}", json={"status": "done"}, headers=tenant)
    gone = await _task(client, tenant, title="gone", due_at=_iso(_local(today, 12)), **mine)
    await client.delete(f"/api/tasks/{gone['id']}", headers=tenant)
    cancelled = await _task(client, tenant, title="cancelled", due_at=_iso(_local(today, 12)), **mine)
    await client.patch(f"/api/tasks/{cancelled['id']}", json={"status": "cancelled"}, headers=tenant)
    await _task(client, tenant, title="not mine", assigned_to_id=other.id, due_at=_iso(_local(today, 12)))
    # done yesterday is not "done today"
    dy = await _task(client, tenant, title="done-yday", **mine)
    await client.patch(f"/api/tasks/{dy['id']}", json={"status": "done"}, headers=tenant)
    await db.execute(Task.__table__.update().where(Task.id == dy["id"]).values(
        completed_at=day_start_utc(today, TZ) - timedelta(minutes=1)))
    await db.commit()

    r = await client.get("/api/tasks/my-day", headers=tenant)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["date"] == today.isoformat()
    titles = {k: [x["title"] for x in d[k]] for k in ("catch_up", "today", "upcoming", "later", "done_today")}
    assert titles["catch_up"] == ["old", "yday"]
    assert titles["today"] == ["early", "late-hi", "late-lo"]
    assert titles["upcoming"] == ["tmrw", "wk"]
    assert set(titles["later"]) == {"far", "nodue"}
    assert titles["done_today"] == ["done"]
    assert d["counts"] == {"catch_up": 2, "today": 3, "upcoming": 2, "later": 2, "done_today": 1}
    # the item is exactly the list item
    listed = {x["id"]: x for x in (await client.get("/api/tasks", headers=tenant)).json()["items"]}
    item = d["today"][0]
    assert item == listed[item["id"]]
    for k in ("client_name", "order_number", "assignee_names", "assignee_ids", "overdue", "source"):
        assert k in item
    assert {old["id"], yday["id"], early["id"], late_hi["id"], late_lo["id"], tmrw["id"], wk["id"],
            far["id"], nodue["id"]} == {x["id"] for k in ("catch_up", "today", "upcoming", "later") for x in d[k]}


@pytest.mark.asyncio
async def test_my_day_caps_lists_but_counts_everything(client, tenant, db):
    me = await _me(client, tenant)
    db.add_all([Task(org_id=me["org_id"], title=f"bulk {i}", assigned_to_id=me["id"],
                     assignee_ids=[me["id"]], status="todo") for i in range(105)])
    await db.commit()
    d = (await client.get("/api/tasks/my-day", headers=tenant)).json()
    assert len(d["later"]) == 100 and d["counts"]["later"] == 105


@pytest.mark.asyncio
async def test_my_day_assignee_scopes(client, tenant, db):
    me = await _me(client, tenant)
    SH, staff = await _employee(client, db, me["org_id"], "md-staff@x.dev")
    _, other = await _employee(client, db, me["org_id"], "md-x@x.dev")
    await _task(client, tenant, title="for staff", assigned_to_id=staff.id)
    await _task(client, tenant, title="for other", assigned_to_id=other.id)
    await _task(client, SH, title="staff to-do")                          # made by staff, unassigned
    await _task(client, tenant, title="shop pool")                        # unassigned, by the owner
    staff_day = (await client.get("/api/tasks/my-day", headers=SH)).json()
    assert {x["title"] for x in staff_day["later"]} == {"for staff", "staff to-do"}
    assert (await client.get("/api/tasks/my-day?assignee=all", headers=SH)).status_code == 403
    assert (await client.get(f"/api/tasks/my-day?assignee={other.id}", headers=SH)).status_code == 403
    assert (await client.get(f"/api/tasks/my-day?assignee={staff.id}", headers=SH)).status_code == 200
    owner_all = (await client.get("/api/tasks/my-day?assignee=all", headers=tenant)).json()
    assert {"for staff", "for other", "staff to-do", "shop pool"} <= {x["title"] for x in owner_all["later"]}
    owner_other = (await client.get(f"/api/tasks/my-day?assignee={other.id}", headers=tenant)).json()
    assert [x["title"] for x in owner_other["later"]] == ["for other"]


@pytest.mark.asyncio
async def test_move_to_today(client, tenant, db, monkeypatch):
    from backend.services import push
    sent = []

    async def capture(_db, employee_id, payload):
        sent.append((employee_id, payload))
    monkeypatch.setattr(push, "_dispatch_push", capture)
    me = await _me(client, tenant)
    SH, staff = await _employee(client, db, me["org_id"], "mv@x.dev")
    _, other = await _employee(client, db, me["org_id"], "mv-o@x.dev")
    today = today_in(TZ)
    timed = await _task(client, tenant, assigned_to_id=staff.id, due_at=_iso(_local(today - timedelta(days=3), 10, 30)))
    undated = await _task(client, tenant, assigned_to_id=staff.id)
    own = await _task(client, SH, title="own")
    done = await _task(client, tenant, assigned_to_id=staff.id, due_at=_iso(_local(today - timedelta(days=2), 9)))
    await client.patch(f"/api/tasks/{done['id']}", json={"status": "done"}, headers=tenant)
    hidden = await _task(client, tenant, assigned_to_id=other.id, due_at=_iso(_local(today - timedelta(days=2), 9)))
    sent.clear()
    _freeze(monkeypatch, _local(today, 9).isoformat())        # 09:00 shop time: 10:30 is still ahead
    r = await client.post("/api/tasks/move-to-today", json={"ids": [
        timed["id"], undated["id"], own["id"], done["id"], hidden["id"], "nope"]}, headers=SH)
    assert r.status_code == 200, r.text
    assert r.json() == {"moved": 3, "skipped": [done["id"], hidden["id"], "nope"]}
    rows = {t.id: t for t in (await db.execute(select(Task).where(Task.org_id == me["org_id"]))).scalars().all()}
    assert utc_to_wall(rows[timed["id"]].due_at, TZ) == datetime.combine(today, time(10, 30))
    assert utc_to_wall(rows[undated["id"]].due_at, TZ) == datetime.combine(today, time(23, 59, 59))
    assert utc_to_wall(rows[own["id"]].due_at, TZ) == datetime.combine(today, time(23, 59, 59))
    assert rows[hidden["id"]].due_at.date() < today                           # untouched
    feed = (await db.execute(select(EventFeedItem).where(
        EventFeedItem.entity_type == "task", EventFeedItem.entity_id.in_([timed["id"], undated["id"], own["id"]]),
        EventFeedItem.body.like("%today%")))).scalars().all()
    assert len(feed) == 3
    # the actor hears nothing; the owner (author of two of them) gets one alert, not two
    assert [p for e, p in sent if e == staff.id] == []
    owner_alerts = [p for e, p in sent if e == me["id"]]
    assert len(owner_alerts) == 1
    assert owner_alerts[0]["deep_link"].startswith("/tasks?task=")
    d = (await client.get("/api/tasks/my-day", headers=SH)).json()
    assert {x["id"] for x in d["today"]} == {timed["id"], undated["id"], own["id"]}


@pytest.mark.asyncio
async def test_due_at_accepts_a_date_or_a_datetime(client, tenant):
    today = today_in(TZ)
    t = await _task(client, tenant, due_at=today.isoformat())
    assert t["due_at"] == _local(today, 23, 59, 59).isoformat()
    z = await _task(client, tenant, due_at="2026-12-01T10:00:00+05:30")
    assert z["due_at"] == "2026-12-01T04:30:00"
    naive = await _task(client, tenant, due_at="2026-12-01T10:00:00")
    assert naive["due_at"] == "2026-12-01T04:30:00"                     # no offset = shop wall time
    p = await client.patch(f"/api/tasks/{naive['id']}", json={"due_at": "2026-12-02"}, headers=tenant)
    assert p.json()["due_at"] == "2026-12-02T18:29:59"              # 23:59:59 IST
    bad = await client.post("/api/tasks", json={"title": "x", "due_at": "tomorrow"}, headers=tenant)
    assert bad.status_code == 400



# ── acceptance: shop days, boundaries, DST (DUE-*, MD-9/10) ─────────────────────────────────
def _freeze(monkeypatch, utc_iso: str):
    from datetime import timezone

    from backend.services import dates
    at = datetime.fromisoformat(utc_iso).replace(tzinfo=timezone.utc)
    monkeypatch.setattr(dates, "_clock", lambda: at)


async def _titles(client, H, **q):
    d = (await client.get("/api/tasks/my-day", params=q or None, headers=H)).json()
    return d, {k: [x["title"] for x in d[k]] for k in ("catch_up", "today", "upcoming", "later", "done_today")}


@pytest.mark.asyncio
async def test_ist_late_evening_and_just_after_midnight(client, tenant, monkeypatch):
    """DUE-4 / DUE-5: the shop's day, not UTC's."""
    me = await _me(client, tenant)
    mine = {"assigned_to_id": me["id"]}
    await _task(client, tenant, title="due 12", due_at="2026-11-12", **mine)          # 18:29:59 UTC
    await _task(client, tenant, title="due 13 00:30", due_at="2026-11-13T00:30:00+05:30", **mine)
    await _task(client, tenant, title="due 13", due_at="2026-11-13", **mine)
    _freeze(monkeypatch, "2026-11-12T18:00:00")                                        # 23:30 IST, 12th
    d, t = await _titles(client, tenant)
    assert d["date"] == "2026-11-12"
    assert t["today"] == ["due 12"] and t["upcoming"] == ["due 13 00:30", "due 13"]
    _freeze(monkeypatch, "2026-11-12T19:00:00")                                        # 00:30 IST, 13th
    d, t = await _titles(client, tenant)
    assert d["date"] == "2026-11-13"
    assert t["catch_up"] == ["due 12"] and t["today"] == ["due 13 00:30", "due 13"]


@pytest.mark.asyncio
async def test_midnight_and_seven_day_edges(client, tenant):
    """MD-9 / MD-10: 00:00 tomorrow is upcoming, the last instant of today is today, day +7 is
    upcoming, day +8 is later."""
    me = await _me(client, tenant)
    mine = {"assigned_to_id": me["id"]}
    today = today_in(TZ)
    tomorrow = _local(today + timedelta(days=1), 0)
    await _task(client, tenant, title="last instant", due_at=_iso(tomorrow - timedelta(microseconds=1)), **mine)
    await _task(client, tenant, title="midnight", due_at=_iso(tomorrow), **mine)
    await _task(client, tenant, title="day 7", due_at=(today + timedelta(days=7)).isoformat(), **mine)
    await _task(client, tenant, title="day 8", due_at=_iso(_local(today + timedelta(days=8), 0)), **mine)
    _, t = await _titles(client, tenant)
    assert t["today"] == ["last instant"]
    assert t["upcoming"] == ["midnight", "day 7"]
    assert t["later"] == ["day 8"]


@pytest.mark.asyncio
@pytest.mark.parametrize("zone,day,expect", [
    ("UTC", "2026-11-12", "2026-11-12T23:59:59"),                 # DUE-2
    ("Europe/London", "2026-03-29", "2026-03-29T22:59:59"),       # DUE-6: 23-hour day, BST after
    ("Europe/London", "2026-10-25", "2026-10-25T23:59:59"),       # DUE-6: 25-hour day, GMT after
    ("America/New_York", "2026-03-08", "2026-03-09T03:59:59"),    # DUE-7: that day's offset (EDT)
    ("America/New_York", "2026-11-01", "2026-11-02T04:59:59"),    # DUE-7: EST
])
async def test_date_only_due_uses_that_days_offset(client, tenant, zone, day, expect):
    r = await client.put("/api/staff/company", json={"timezone": zone}, headers=tenant)
    assert r.status_code == 200, r.text
    assert (await _task(client, tenant, due_at=day))["due_at"] == expect


@pytest.mark.asyncio
async def test_dst_day_buckets_follow_local_midnight(client, tenant, monkeypatch):
    """DUE-6: on London's 23-hour day, every task lands in exactly one group."""
    await client.put("/api/staff/company", json={"timezone": "Europe/London"}, headers=tenant)
    me = await _me(client, tenant)
    mine = {"assigned_to_id": me["id"]}
    lon = "Europe/London"
    d0 = date(2026, 3, 29)
    await _task(client, tenant, title="sat 23:30", due_at=_iso(_local(d0 - timedelta(days=1), 23, 30, tz=lon)), **mine)
    await _task(client, tenant, title="sun 00:30", due_at=_iso(_local(d0, 0, 30, tz=lon)), **mine)
    await _task(client, tenant, title="sun 23:30", due_at=_iso(_local(d0, 23, 30, tz=lon)), **mine)
    await _task(client, tenant, title="mon 00:30", due_at=_iso(_local(d0 + timedelta(days=1), 0, 30, tz=lon)), **mine)
    _freeze(monkeypatch, "2026-03-29T12:00:00")
    d, t = await _titles(client, tenant)
    assert d["date"] == "2026-03-29"
    assert t["catch_up"] == ["sat 23:30"]
    assert t["today"] == ["sun 00:30", "sun 23:30"]
    assert t["upcoming"] == ["mon 00:30"]
    assert sum(d["counts"].values()) == 4


@pytest.mark.asyncio
async def test_bad_shop_timezone_falls_back_to_utc(client, tenant, db):
    """DUE-8: an unknown zone in the database never breaks My day."""
    from backend.models import Organisation
    me = await _me(client, tenant)
    org = (await db.execute(select(Organisation).where(Organisation.id == me["org_id"]))).scalar_one()
    org.timezone = "Mars/Olympus"
    await db.commit()
    r = await client.get("/api/tasks/my-day", headers=tenant)
    assert r.status_code == 200
    assert (await _task(client, tenant, due_at="2026-11-12"))["due_at"] == "2026-11-12T23:59:59"


@pytest.mark.asyncio
async def test_my_day_unknown_people_and_bad_bodies(client, tenant, db):
    """MD-11, MTT-3."""
    me = await _me(client, tenant)
    _, gone = await _employee(client, db, me["org_id"], "gone@x.dev")
    await _task(client, tenant, title="for gone", assigned_to_id=gone.id)
    gone.active = False
    await db.commit()
    assert (await client.get("/api/tasks/my-day?assignee=nobody", headers=tenant)).status_code == 404
    d = (await client.get(f"/api/tasks/my-day?assignee={gone.id}", headers=tenant)).json()
    assert [x["title"] for x in d["later"]] == ["for gone"]           # inactive still useful
    assert (await client.post("/api/tasks/move-to-today", json={"ids": []}, headers=tenant)).status_code == 422
    too_many = {"ids": [f"id{i}" for i in range(501)]}
    assert (await client.post("/api/tasks/move-to-today", json=too_many, headers=tenant)).status_code == 422
    t = await _task(client, tenant, assigned_to_id=me["id"])
    r = (await client.post("/api/tasks/move-to-today", json={"ids": [t["id"], t["id"]]}, headers=tenant)).json()
    assert r == {"moved": 1, "skipped": []}


@pytest.mark.asyncio
async def test_unassigning_and_claiming_change_who_sees_it(client, tenant, db):
    """VIS-4, VIS-6, MD-8."""
    me = await _me(client, tenant)
    SH, staff = await _employee(client, db, me["org_id"], "vis46@x.dev")
    OH, other = await _employee(client, db, me["org_id"], "vis46-o@x.dev")
    shared = await _task(client, tenant, title="shared", assignee_ids=[other.id, staff.id])
    assert (await client.get(f"/api/tasks/{shared['id']}", headers=SH)).status_code == 200
    await client.patch(f"/api/tasks/{shared['id']}", json={"assignee_ids": [other.id]}, headers=tenant)
    assert (await client.get(f"/api/tasks/{shared['id']}", headers=SH)).status_code == 404
    pool = await _task(client, tenant, title="pool")
    assert (await client.get(f"/api/tasks/{pool['id']}", headers=OH)).status_code == 200
    r = await client.patch(f"/api/tasks/{pool['id']}", json={"assigned_to_id": staff.id}, headers=tenant)
    assert r.status_code == 200
    _, t = await _titles(client, SH)
    assert t["later"].count("pool") == 1
    assert (await client.get(f"/api/tasks/{pool['id']}", headers=OH)).status_code == 404
    both = await _task(client, tenant, title="both", assignee_ids=[staff.id, other.id])
    d = (await client.get("/api/tasks/my-day?assignee=all", headers=tenant)).json()
    assert [x["id"] for x in d["later"]].count(both["id"]) == 1


# ── acceptance: overdue guards (OVD-2/3/4/5/6) ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_overdue_newcomer_gets_their_own_alert(client, tenant, db):
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "ovd5a@x.dev")
    _, e2 = await _employee(client, db, me["org_id"], "ovd5b@x.dev")
    now = _working_now()
    t = await _task(client, tenant, assigned_to_id=e1.id, due_at=_iso(now - timedelta(hours=1)))
    assert await sweep_overdue(db, me["org_id"], now=now) == 1
    await client.patch(f"/api/tasks/{t['id']}", json={"assignee_ids": [e1.id, e2.id]}, headers=tenant)
    assert await sweep_overdue(db, me["org_id"], now=now + timedelta(minutes=15)) == 1
    assert len(await _bell(db, e1.id, "task.overdue")) == 1
    assert len(await _bell(db, e2.id, "task.overdue")) == 1
    # moving it earlier or renaming it doesn't re-arm (OVD-4)
    await client.patch(f"/api/tasks/{t['id']}", json={"title": "renamed", "due_at": _iso(now - timedelta(hours=3))},
                       headers=tenant)
    assert await sweep_overdue(db, me["org_id"], now=now + timedelta(minutes=30)) == 0


@pytest.mark.asyncio
async def test_overdue_skips_undated_and_unassigned(client, tenant, db):
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    now = _working_now()
    await _task(client, tenant, title="nobody", due_at=_iso(now - timedelta(hours=1)))
    await _task(client, tenant, title="undated", assigned_to_id=me["id"])
    assert await sweep_overdue(db, me["org_id"], now=now) == 0


@pytest.mark.asyncio
async def test_task_closed_mid_sweep_is_not_alerted(client, tenant, db, monkeypatch):
    """OVD-3: the claim is a conditional UPDATE, so a task closed after the sweep read it is left."""
    from sqlalchemy import update

    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "ovd3@x.dev")
    now = _working_now()
    t = await _task(client, tenant, assigned_to_id=e1.id, due_at=_iso(now - timedelta(hours=1)))
    from backend.services import task_alerts
    real = task_alerts._held

    async def close_it_first(_db, *a, **k):
        await _db.execute(update(Task).where(Task.id == t["id"]).values(status="done"))
        return await real(_db, *a, **k)
    monkeypatch.setattr(task_alerts, "_held", close_it_first)
    assert await sweep_overdue(db, me["org_id"], now=now) == 0
    assert await _bell(db, e1.id, "task.overdue") == []


@pytest.mark.asyncio
async def test_two_sweeps_at_once_alert_once(client, tenant, db):
    """OVD-2: two workers racing on the same tick still send one alert per person."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.services.task_alerts import sweep_overdue
    TestSession = async_sessionmaker(db.bind, expire_on_commit=False)
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "ovd2@x.dev")
    now = _working_now()
    for i in range(3):
        await _task(client, tenant, title=f"race {i}", assigned_to_id=e1.id, due_at=_iso(now - timedelta(hours=1)))

    async def one():
        async with TestSession() as s:
            return await sweep_overdue(s, me["org_id"], now=now)
    results = await asyncio.gather(one(), one(), return_exceptions=True)
    assert sum(r for r in results if isinstance(r, int)) == 3
    assert len(await _bell(db, e1.id, "task.overdue")) == 3


# ── acceptance: no N+1 (NF-1) ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_my_day_query_count_is_flat(client, tenant, db):
    from sqlalchemy import event
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "nf1@x.dev")
    order = (await client.post("/api/orders", json={"complaint": "x"}, headers=tenant)).json()

    async def add(n):
        db.add_all([Task(org_id=me["org_id"], title=f"n{i}", assigned_to_id=me["id"], status="todo",
                         assignee_ids=[me["id"], e1.id], order_id=order["id"], created_by_id=e1.id)
                    for i in range(n)])
        await db.commit()

    seen = []

    def count(*_a, **_k):
        seen.append(1)
    engine = db.bind.sync_engine
    await add(5)
    event.listen(engine, "before_cursor_execute", count)
    try:
        await client.get("/api/tasks/my-day", headers=tenant)
        small = len(seen)
        await add(95)
        seen.clear()
        await client.get("/api/tasks/my-day", headers=tenant)
        big = len(seen)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert small == big, (small, big)


# ── review round 2 ────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_staff_can_claim_a_pool_task_themselves(client, tenant, db):
    """H1 / VIS-6: a staff member picks up an unassigned task someone else made, with their own
    headers. Giving it to someone else, or editing anything else, is still refused."""
    me = await _me(client, tenant)
    SH, staff = await _employee(client, db, me["org_id"], "claim@x.dev")
    OH, other = await _employee(client, db, me["org_id"], "claim-o@x.dev")
    pool = await _task(client, tenant, title="pool")
    assert (await client.patch(f"/api/tasks/{pool['id']}", json={"title": "mine now"}, headers=SH)).status_code == 403
    assert (await client.patch(f"/api/tasks/{pool['id']}", json={"assigned_to_id": other.id},
                               headers=SH)).status_code == 403
    r = await client.patch(f"/api/tasks/{pool['id']}", json={"assigned_to_id": staff.id, "status": "in_progress"},
                           headers=SH)
    assert r.status_code == 200, r.text
    assert r.json()["assigned_to_id"] == staff.id and r.json()["assignee_ids"] == [staff.id]
    assert r.json()["status"] == "in_progress"
    # it is theirs now: gone from the pool for others
    assert (await client.get(f"/api/tasks/{pool['id']}", headers=OH)).status_code == 404
    assert (await client.patch(f"/api/tasks/{pool['id']}", json={"assignee_ids": [other.id]},
                               headers=OH)).status_code == 404
    # assignee_ids form of the claim works too
    pool2 = await _task(client, tenant, title="pool 2")
    assert (await client.patch(f"/api/tasks/{pool2['id']}", json={"assignee_ids": [other.id]},
                               headers=OH)).status_code == 200


@pytest.mark.asyncio
async def test_my_day_include_pool(client, tenant, db):
    """include_pool=true adds the shop's unassigned tasks to the buckets; default unchanged."""
    me = await _me(client, tenant)
    SH, staff = await _employee(client, db, me["org_id"], "pool@x.dev")
    await _task(client, tenant, title="pool today", due_at=today_in(TZ).isoformat())
    await _task(client, tenant, title="mine", assigned_to_id=staff.id)
    _, t = await _titles(client, SH)
    assert t["today"] == [] and t["later"] == ["mine"]
    d, t = await _titles(client, SH, include_pool="true")
    assert t["today"] == ["pool today"] and t["later"] == ["mine"]
    assert d["today"][0]["assigned_to_id"] is None and d["today"][0]["assignee_ids"] == []


@pytest.mark.asyncio
async def test_old_go_clients_send_shop_wall_time(client, tenant):
    """H2: a Go build from before the shop-clock fix sends datetime-local values ("…T18:00", no
    offset, no seconds) meaning 18:00 in the shop. They are stored as that instant in UTC."""
    t = await _task(client, tenant, due_at="2026-11-12T18:00")
    assert t["due_at"] == "2026-11-12T12:30:00"                       # 18:00 IST
    p = await client.patch(f"/api/tasks/{t['id']}", json={"due_at": "2026-11-13T09:15"}, headers=tenant)
    assert p.json()["due_at"] == "2026-11-13T03:45:00"
    await client.put("/api/staff/company", json={"timezone": "America/New_York"}, headers=tenant)
    summer = await _task(client, tenant, due_at="2026-07-01T09:00")
    assert summer["due_at"] == "2026-07-01T13:00:00"                  # EDT that day, not today's EST


@pytest.mark.asyncio
async def test_move_to_today_past_time_goes_to_end_of_day_without_alerting(client, tenant, db, monkeypatch):
    """H3 / MTT-1: a kept time that has already passed today becomes the end of today, so the
    moved task isn't overdue and the sweep has nothing to say about it."""
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "h3@x.dev")
    today = today_in(TZ)
    morning = await _task(client, tenant, assigned_to_id=e1.id, due_at=_iso(_local(today - timedelta(days=1), 9)))
    evening = await _task(client, tenant, assigned_to_id=e1.id, due_at=_iso(_local(today - timedelta(days=1), 17)))
    three_pm = _local(today, 15)
    assert await sweep_overdue(db, me["org_id"], now=three_pm) == 2
    _freeze(monkeypatch, three_pm.isoformat())
    r = await client.post("/api/tasks/move-to-today", json={"ids": [morning["id"], evening["id"]]}, headers=tenant)
    assert r.json()["moved"] == 2
    rows = {t.id: t for t in (await db.execute(select(Task).where(Task.id.in_([morning["id"], evening["id"]])))).scalars()}
    assert utc_to_wall(rows[morning["id"]].due_at, TZ) == datetime.combine(today, time(23, 59, 59))
    assert utc_to_wall(rows[evening["id"]].due_at, TZ) == datetime.combine(today, time(17, 0))
    for t in rows.values():
        assert t.overdue_alerted_at is None                          # both now in the future: re-armed
    assert await sweep_overdue(db, me["org_id"], now=three_pm + timedelta(minutes=15)) == 0
    assert len(await _bell(db, e1.id, "task.overdue")) == 2           # only the original two


@pytest.mark.asyncio
async def test_moving_a_due_time_to_another_past_time_does_not_realert(client, tenant, db):
    """H3 / OVD-4: re-arm only when the new due time is in the future."""
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "h3b@x.dev")
    now = _working_now()
    t = await _task(client, tenant, assigned_to_id=e1.id, due_at=_iso(now - timedelta(days=2)))
    assert await sweep_overdue(db, me["org_id"], now=now) == 1
    await client.patch(f"/api/tasks/{t['id']}", json={"due_at": _iso(now - timedelta(days=1))}, headers=tenant)
    assert await sweep_overdue(db, me["org_id"], now=now) == 0
    assert len(await _bell(db, e1.id, "task.overdue")) == 1


@pytest.mark.asyncio
async def test_pushes_go_out_only_after_the_commit(client, tenant, db, monkeypatch):
    """H4 / MTT-5: with real push on, nothing commits halfway through move-to-today, a PATCH or
    the sweep; every push is sent after the request's (or the shop's) one commit."""
    import sys
    import types

    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.models import PushSubscription
    from backend.services import push
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "h4@x.dev")
    db.add(PushSubscription(employee_id=e1.id, org_id=me["org_id"], endpoint="https://push.example/x",
                            p256dh="k", auth="a"))
    await db.commit()
    events = []
    fake = types.ModuleType("pywebpush")
    fake.webpush = lambda **kw: events.append("push")
    monkeypatch.setitem(sys.modules, "pywebpush", fake)
    monkeypatch.setattr(push, "push_enabled", lambda: True)
    real_commit = AsyncSession.commit

    async def commit(self):
        events.append("commit")
        return await real_commit(self)
    monkeypatch.setattr(AsyncSession, "commit", commit)

    t1 = await _task(client, tenant, assigned_to_id=e1.id)
    assert "push" in events and events[0] == "commit"
    t2 = await _task(client, tenant, assigned_to_id=e1.id)
    events.clear()
    r = await client.post("/api/tasks/move-to-today", json={"ids": [t1["id"], t2["id"]]}, headers=tenant)
    assert r.status_code == 200
    assert events and events[0] == "commit" and "push" in events
    first_push = events.index("push")
    assert events[:first_push].count("commit") == 1                  # one transaction, then the push
    events.clear()
    await client.patch(f"/api/tasks/{t1['id']}", json={"title": "renamed"}, headers=tenant)
    assert events[0] == "commit"
    now = _working_now()
    t3 = await _task(client, tenant, assigned_to_id=e1.id, due_at=_iso(now - timedelta(hours=1)))
    events.clear()
    assert await sweep_overdue(db, me["org_id"], now=now) >= 1
    assert events[0] == "commit" and "push" in events
    assert t3


@pytest.mark.asyncio
async def test_racing_reopen_and_cancel_claw_back_once(client, tenant, db):
    """M1: two reversals that both read the paid state post one claw-back, not two."""
    from backend.services.payroll import CommissionEngine
    me, e1, e2, rule, t = await _two_people_task(client, tenant, db, "each_full")
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
    task = (await db.execute(select(Task).where(Task.id == t["id"]))).scalar_one()
    eng = CommissionEngine(db, me["org_id"])
    stale = {}
    real_state = eng._group_state

    async def remembered(key):
        if key not in stale:
            stale[key] = await real_state(key)
        return stale[key]                       # the second reversal sees what the first one saw
    eng._group_state = remembered
    await eng.reverse_for_task(task, "reopened")
    await eng.reverse_for_task(task, "cancelled")
    assert await _paid(db, e1.id) == 0 and await _paid(db, e2.id) == 0
    keys = (await db.execute(select(EmployeeBalanceLedger.idempotency_key).where(
        EmployeeBalanceLedger.ref_id == t["id"], EmployeeBalanceLedger.entry_type == "clawback"))).scalars().all()
    assert keys and all(k.startswith("task_reversal:") for k in keys)


@pytest.mark.asyncio
async def test_alert_texts_show_the_shop_clock(client, tenant, db):
    """M3: bell and push texts show the due time on the shop's clock; a date-only due shows no time."""
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "m3@x.dev")
    await _task(client, tenant, title="dated", assigned_to_id=e1.id, due_at="2026-12-24")
    await _task(client, tenant, title="timed", assigned_to_id=e1.id, due_at="2026-12-24T14:30:00+05:30")
    today = await _task(client, tenant, title="today", assigned_to_id=e1.id, due_at=today_in(TZ).isoformat())
    bodies = {n.body for n in await _bell(db, e1.id, "task.assigned")}
    assert "dated · due 24 Dec" in bodies
    assert "timed · due 24 Dec 14:30" in bodies
    assert "today · due today" in bodies
    assert today


@pytest.mark.asyncio
async def test_unassigned_overdue_task_is_marked_handled_then_alerts_its_new_owner(client, tenant, db):
    """L5: an overdue task with nobody on it is stamped (it leaves the pending index), and giving
    it to someone alerts that person on the next tick."""
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "l5@x.dev")
    now = _working_now()
    t = await _task(client, tenant, title="nobody", due_at=_iso(now - timedelta(hours=1)))
    assert await sweep_overdue(db, me["org_id"], now=now) == 0
    row = (await db.execute(select(Task).where(Task.id == t["id"]))).scalar_one()
    assert row.overdue_alerted_at is not None
    await client.patch(f"/api/tasks/{t['id']}", json={"assigned_to_id": e1.id}, headers=tenant)
    assert await sweep_overdue(db, me["org_id"], now=now + timedelta(minutes=15)) == 1
    assert len(await _bell(db, e1.id, "task.overdue")) == 1


@pytest.mark.asyncio
async def test_new_endpoints_are_rate_limited_like_the_rest(client, tenant):
    """NF-4: the shared /api limiter counts my-day and move-to-today (its headers are on them)."""
    r = await client.get("/api/tasks/my-day", headers=tenant)
    assert "X-RateLimit-Limit" in r.headers
    m = await client.post("/api/tasks/move-to-today", json={"ids": ["x"]}, headers=tenant)
    assert "X-RateLimit-Limit" in m.headers


@pytest.mark.asyncio
async def test_job_task_list_respects_visibility(client, tenant, db):
    """R2: the work order's task list (GET /api/tasks?order_id=…, the JobTasks drawer) only shows
    a staff member the tasks they can see; search goes through the same list."""
    me = await _me(client, tenant)
    SH, staff = await _employee(client, db, me["org_id"], "jt@x.dev")
    _, other = await _employee(client, db, me["org_id"], "jt-o@x.dev")
    order = (await client.post("/api/orders", json={"complaint": "x"}, headers=tenant)).json()
    await _task(client, tenant, title="Swap screen", order_id=order["id"], assigned_to_id=staff.id)
    await _task(client, tenant, title="Swap battery", order_id=order["id"], assigned_to_id=other.id)
    got = (await client.get(f"/api/tasks?order_id={order['id']}", headers=SH)).json()
    assert [x["title"] for x in got["items"]] == ["Swap screen"] and got["total"] == 1
    found = (await client.get("/api/tasks?q=Swap", headers=SH)).json()
    assert [x["title"] for x in found["items"]] == ["Swap screen"]


# ── QA round: untested criteria and sweep batching ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_role_change_takes_effect_on_the_next_request(client, tenant, db):
    """VIS-7: visibility follows the role as it is now; nothing is cached per session."""
    me = await _me(client, tenant)
    SH, staff = await _employee(client, db, me["org_id"], "vis7@x.dev")
    _, other = await _employee(client, db, me["org_id"], "vis7-o@x.dev")
    t = await _task(client, tenant, title="theirs", assigned_to_id=other.id)
    assert (await client.get(f"/api/tasks/{t['id']}", headers=SH)).status_code == 404
    role = (await db.execute(select(Role).where(Role.id == staff.role_id))).scalar_one()
    role.permissions = {"can_assign_staff": True}
    await db.commit()
    assert (await client.get(f"/api/tasks/{t['id']}", headers=SH)).status_code == 200
    assert (await client.get("/api/tasks/my-day?assignee=all", headers=SH)).status_code == 200
    role.permissions = {}
    await db.commit()
    assert (await client.get(f"/api/tasks/{t['id']}", headers=SH)).status_code == 404


@pytest.mark.asyncio
async def test_overdue_and_commission_stay_inside_the_shop(client, tenant, db):
    """TEN-4: an id from another shop smuggled into assignee_ids gets no alert and no pay, and
    another shop's commission rule never applies."""
    import uuid

    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "ten4@x.dev")
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Boss", "email": f"boss-{uuid.uuid4().hex[:6]}@x.dev",
        "password": "pw-test-12345"})
    H2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    other = await _me(client, H2)
    db.add(CommissionRule(org_id=other["org_id"], name="Other shop", event="task_closed", calc_type="flat", amount=99))
    await db.commit()
    now = _working_now()
    t = await _task(client, tenant, assigned_to_id=e1.id, due_at=_iso(now - timedelta(hours=1)))
    await db.execute(Task.__table__.update().where(Task.id == t["id"]).values(assignee_ids=[e1.id, other["id"]]))
    await db.commit()
    assert await sweep_overdue(db, me["org_id"], now=now) == 1
    assert len(await _bell(db, e1.id, "task.overdue")) == 1
    assert await _bell(db, other["id"], "task.overdue") == []
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
    assert await _paid(db, e1.id) == 0 and await _paid(db, other["id"]) == 0


@pytest.mark.asyncio
async def test_move_to_today_rolls_back_entirely_on_a_failure(client, tenant, db, monkeypatch):
    """MTT-5: a failure part-way through the batch changes nothing."""
    from backend.routers import tasks as tasks_router
    me = await _me(client, tenant)
    old = _iso(_local(today_in(TZ) - timedelta(days=2), 10))
    ids = [(await _task(client, tenant, assigned_to_id=me["id"], due_at=old))["id"] for _ in range(3)]
    real, calls = tasks_router._feed, []

    async def fail_on_second(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("disk full")
        return await real(*a, **k)
    monkeypatch.setattr(tasks_router, "_feed", fail_on_second)
    r = await client.post("/api/tasks/move-to-today", json={"ids": ids}, headers=tenant)
    assert r.status_code == 500
    rows = (await db.execute(select(Task).where(Task.id.in_(ids)).execution_options(populate_existing=True))).scalars().all()
    assert all(utc_to_wall(r.due_at, TZ).date() == today_in(TZ) - timedelta(days=2) for r in rows)
    moved = (await db.execute(select(EventFeedItem).where(
        EventFeedItem.entity_id.in_(ids), EventFeedItem.body.like("%moved to today%")))).scalars().all()
    assert moved == []


@pytest.mark.asyncio
async def test_overdue_without_a_device_or_with_a_failing_push(client, tenant, db, monkeypatch, caplog):
    """OVD-7: no subscription still gives a bell row; a push that blows up is logged and the
    bell row stays."""
    import logging

    from backend.models import PushSubscription
    from backend.services import push
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, quiet = await _employee(client, db, me["org_id"], "ovd7a@x.dev")
    _, broken = await _employee(client, db, me["org_id"], "ovd7b@x.dev")
    db.add(PushSubscription(employee_id=broken.id, org_id=me["org_id"], endpoint="https://push.example/b",
                            p256dh="k", auth="a"))
    await db.commit()

    async def explode(*a, **k):
        raise RuntimeError("push service down")
    monkeypatch.setattr(push, "_dispatch_push", explode)
    now = _working_now()
    await _task(client, tenant, assignee_ids=[quiet.id, broken.id], due_at=_iso(now - timedelta(hours=1)))
    with caplog.at_level(logging.ERROR):
        assert await sweep_overdue(db, me["org_id"], now=now) == 1
    assert len(await _bell(db, quiet.id, "task.overdue")) == 1
    assert len(await _bell(db, broken.id, "task.overdue")) == 1
    assert any("deferred push failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_rules_for_one_person_and_percentage_rules(client, tenant, db):
    """COM-4: a rule tied to one employee pays only them (the mode applies among the people it
    covers, i.e. them alone, in full); a percentage rule has no basis on a task and pays nothing."""
    me = await _me(client, tenant)
    await client.put("/api/payroll/settings", json={"task_commission_mode": "split"}, headers=tenant)
    _, e1 = await _employee(client, db, me["org_id"], "com4a@x.dev")
    _, e2 = await _employee(client, db, me["org_id"], "com4b@x.dev")
    _, e3 = await _employee(client, db, me["org_id"], "com4c@x.dev")
    db.add(CommissionRule(org_id=me["org_id"], name="e2 bonus", event="task_closed", calc_type="flat",
                          amount=6, employee_id=e2.id))
    db.add(CommissionRule(org_id=me["org_id"], name="e3 bonus", event="task_closed", calc_type="flat",
                          amount=7, employee_id=e3.id))
    db.add(CommissionRule(org_id=me["org_id"], name="Percent", event="task_closed", calc_type="percent", amount=10))
    await db.commit()
    t = await _task(client, tenant, assignee_ids=[e1.id, e2.id])
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
    assert await _paid(db, e1.id) == 0
    assert await _paid(db, e2.id) == pytest.approx(6.0)
    assert await _paid(db, e3.id) == 0                    # not on the task


@pytest.mark.asyncio
async def test_on_time_term_uses_the_shop_day_for_date_only_dues(client, tenant, db, monkeypatch):
    """COM-11: a task due "today" (date only) and closed at 23:30 shop time is on time; one due
    yesterday is late."""
    me = await _me(client, tenant)
    db.add(CommissionRule(org_id=me["org_id"], name="On time", event="task_closed", calc_type="flat",
                          amount=5, term_condition="on_time"))
    await db.commit()
    today = today_in(TZ)
    t_ok = await _task(client, tenant, assigned_to_id=me["id"], due_at=today.isoformat())
    t_late = await _task(client, tenant, assigned_to_id=me["id"], due_at=(today - timedelta(days=1)).isoformat())
    _freeze(monkeypatch, _local(today, 23, 30).isoformat())
    ok = (await client.patch(f"/api/tasks/{t_ok['id']}", json={"status": "done"}, headers=tenant)).json()
    late = (await client.patch(f"/api/tasks/{t_late['id']}", json={"status": "done"}, headers=tenant)).json()
    assert ok["completed_on_time"] is True and late["completed_on_time"] is False
    assert await _paid(db, me["id"]) == pytest.approx(5.0)


def test_unknown_zone_is_logged_once(caplog):
    """DUE-8: an unknown zone falls back to UTC and says so once, not on every request."""
    import logging

    from backend.services import dates
    dates._BAD_ZONES.discard("Mars/Olympus-2")
    with caplog.at_level(logging.WARNING, logger="tekly.dates"):
        for _ in range(3):
            assert dates.zone("Mars/Olympus-2") is not None
            dates.utc_to_wall(datetime(2026, 1, 1), "Mars/Olympus-2")
    assert sum("Mars/Olympus-2" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.asyncio
async def test_a_closed_branch_does_not_starve_open_ones(client, tenant, db, monkeypatch):
    """Batching: held tasks at a branch outside its hours don't use up the batch."""
    from backend.models import Location
    from backend.services import task_alerts
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "starve@x.dev")
    shut = Location(org_id=me["org_id"], name="Night branch", working_hours={
        d: {"open": "20:00", "close": "21:00"} for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")})
    db.add(shut)
    await db.commit()
    now = _working_now()
    for i in range(3):     # the oldest overdue tasks are at the closed branch
        db.add(Task(org_id=me["org_id"], title=f"night {i}", assigned_to_id=e1.id, assignee_ids=[e1.id],
                    status="todo", location_id=shut.id, due_at=now - timedelta(days=3, minutes=i)))
    await db.commit()
    await _task(client, tenant, title="open branch", assigned_to_id=e1.id, due_at=_iso(now - timedelta(hours=1)))
    monkeypatch.setattr(task_alerts, "OVERDUE_BATCH", 2)
    assert await task_alerts.sweep_overdue(db, me["org_id"], now=now) == 1
    assert [n.body.split(" · ")[0] for n in await _bell(db, e1.id, "task.overdue")] == ["open branch"]
