"""QA adversarial tests for #203 + #204 (My day). Proposed, not committed to the PR branches.

Each test states the EXPECTED behaviour (acceptance plan reports/myday-acceptance.md). A failing
test here is a bug report, not a broken test.
"""
import asyncio
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from backend.models import EmployeeBalanceLedger, StaffNotification, Task
from backend.services.dates import today_in, utc_to_wall
from backend.tests.test_tasks_myday import (
    TZ,
    _bell,
    _employee,
    _freeze,
    _iso,
    _local,
    _me,
    _paid,
    _rule,
    _task,
    _titles,
    _working_now,
)


# ── visibility side doors ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_qa_staff_can_claim_an_unassigned_pool_task(client, tenant, db):
    """VIS-6: 'when S claims it (assigns self), then it shows under S's My day'. Staff see the pool,
    so they must be able to take a pool task someone else made."""
    me = await _me(client, tenant)
    SH, staff = await _employee(client, db, me["org_id"], "qa-claim@x.dev")
    pool = await _task(client, tenant, title="pool")
    assert (await client.get(f"/api/tasks/{pool['id']}", headers=SH)).status_code == 200
    r = await client.patch(f"/api/tasks/{pool['id']}", json={"assigned_to_id": staff.id}, headers=SH)
    assert r.status_code == 200, (r.status_code, r.text)


@pytest.mark.asyncio
async def test_qa_staff_timer_on_invisible_task_is_404(client, tenant, db):
    me = await _me(client, tenant)
    SH, staff = await _employee(client, db, me["org_id"], "qa-timer@x.dev",
                                perms={"can_use_timeclock": True})
    _, other = await _employee(client, db, me["org_id"], "qa-timer-o@x.dev")
    hidden = await _task(client, tenant, title="secret", assigned_to_id=other.id)
    r = await client.post("/api/timeclock/start", json={"task_id": hidden["id"]}, headers=SH)
    assert r.status_code in (403, 404), r.text
    assert "secret" not in r.text


@pytest.mark.asyncio
async def test_qa_staff_list_filters_cannot_widen_visibility(client, tenant, db):
    me = await _me(client, tenant)
    SH, staff = await _employee(client, db, me["org_id"], "qa-list@x.dev")
    _, other = await _employee(client, db, me["org_id"], "qa-list-o@x.dev")
    await _task(client, tenant, title="theirs", assigned_to_id=other.id)
    for q in (f"assignee={other.id}", f"assigned_to_id={other.id}", "archived=true", "q=theirs", "overdue=true",
              "limit=50&offset=0"):
        items = (await client.get(f"/api/tasks?{q}", headers=SH)).json()["items"]
        assert "theirs" not in [x["title"] for x in items], q


@pytest.mark.asyncio
async def test_qa_deep_link_other_shop_task_is_plain_404(client, tenant, db):
    import uuid
    t = await _task(client, tenant, title="shop A secret")
    r = await client.post("/api/auth/register", json={
        "org_name": "B", "name": "B", "email": f"b-{uuid.uuid4().hex[:6]}@x.dev", "password": "pw-test-12345"})
    H2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    a = await client.get(f"/api/tasks/{t['id']}", headers=H2)
    b = await client.get("/api/tasks/does-not-exist", headers=H2)
    assert a.status_code == b.status_code == 404 and a.json() == b.json()


# ── overdue re-arm ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_qa_due_moved_earlier_then_later_but_still_past_does_not_realert(client, tenant, db, monkeypatch):
    """OVD-4: re-arm only when the due time moves later AND into the future. Moving 10:00 -> 08:00
    -> 09:00 (all before now=11:00) must not re-alert: the person was already told it is late."""
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "qa-ovd-el@x.dev")
    now = _working_now()
    # the app's clock and the sweep's `now` must agree: "still in the past" is judged by the app
    # clock when the PATCH lands (at 05:30 IST a real-clock 09:00 is still ahead)
    _freeze(monkeypatch, now.isoformat())
    t = await _task(client, tenant, assigned_to_id=e1.id, due_at=_iso(now - timedelta(hours=1)))
    assert await sweep_overdue(db, me["org_id"], now=now) == 1
    await client.patch(f"/api/tasks/{t['id']}", json={"due_at": _iso(now - timedelta(hours=3))}, headers=tenant)
    await client.patch(f"/api/tasks/{t['id']}", json={"due_at": _iso(now - timedelta(hours=2))}, headers=tenant)
    await sweep_overdue(db, me["org_id"], now=now + timedelta(minutes=15))
    assert len(await _bell(db, e1.id, "task.overdue")) == 1


@pytest.mark.asyncio
async def test_qa_move_to_today_keeps_a_past_time_and_realerts_at_once(client, tenant, db, monkeypatch):
    """MTT-1/OVD-4: 'Move all to today' on a task due yesterday 10:30, done at 15:00 today, puts
    it at today 10:30 - already past - and re-arms it, so the next tick sends a second overdue alert
    within 15 minutes. Expected: a moved task is not overdue again straight away."""
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "qa-mtt@x.dev")
    today = today_in(TZ)
    afternoon = _local(today, 15)
    _freeze(monkeypatch, afternoon.isoformat())
    t = await _task(client, tenant, assigned_to_id=e1.id,
                    due_at=_iso(_local(today - timedelta(days=1), 10, 30)))
    assert await sweep_overdue(db, me["org_id"], now=afternoon) == 1
    r = (await client.post("/api/tasks/move-to-today", json={"ids": [t["id"]]}, headers=tenant)).json()
    assert r["moved"] == 1
    row = (await db.execute(select(Task).where(Task.id == t["id"]).execution_options(populate_existing=True))).scalar_one()
    moved_to = utc_to_wall(row.due_at, TZ)
    # the moved task should not already be late
    assert row.due_at > afternoon, f"moved to {moved_to}, which is before now (15:00)"
    await sweep_overdue(db, me["org_id"], now=afternoon + timedelta(minutes=15))
    assert len(await _bell(db, e1.id, "task.overdue")) == 1


@pytest.mark.asyncio
async def test_qa_overdue_bell_body_shows_shop_time(client, tenant, db):
    """The bell/push body prints `due %d %b %H:%M` from the naive UTC column. In an IST shop a
    date-only task due 'today' (23:59:59 IST) reads 'due .. 18:29'."""
    from backend.services.task_alerts import sweep_overdue
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "qa-body@x.dev")
    now = _working_now()
    due_local = datetime.combine(today_in(TZ), time(10, 0))
    await _task(client, tenant, assigned_to_id=e1.id, due_at=due_local.isoformat() + "+05:30")
    await sweep_overdue(db, me["org_id"], now=now)
    rows = await _bell(db, e1.id, "task.overdue")
    assert rows and "10:00" in (rows[0].body or ""), rows[0].body


@pytest.mark.asyncio
async def test_qa_overdue_concurrent_sweeps_many_tasks(client, tenant, db):
    """OVD-2 on a bigger batch, three workers at once."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.services.task_alerts import sweep_overdue
    S = async_sessionmaker(db.bind, expire_on_commit=False)
    me = await _me(client, tenant)
    _, e1 = await _employee(client, db, me["org_id"], "qa-race1@x.dev")
    _, e2 = await _employee(client, db, me["org_id"], "qa-race2@x.dev")
    now = _working_now()
    for i in range(20):
        await _task(client, tenant, title=f"r{i}", assignee_ids=[e1.id, e2.id], due_at=_iso(now - timedelta(hours=1)))

    async def one():
        async with S() as s:
            return await sweep_overdue(s, me["org_id"], now=now)
    res = await asyncio.gather(one(), one(), one(), return_exceptions=True)
    errs = [r for r in res if not isinstance(r, int)]
    assert sum(r for r in res if isinstance(r, int)) == 20, (res, errs)
    assert len(await _bell(db, e1.id, "task.overdue")) == 20
    assert len(await _bell(db, e2.id, "task.overdue")) == 20


# ── commission ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["split", "each_full", "first_only"])
async def test_qa_close_reopen_change_assignees_close(client, tenant, db, mode):
    """COM-6/7 in each mode: A,B,C close -> reopen -> drop C add D -> mode flip -> close -> reopen -> close."""
    me = await _me(client, tenant)
    await client.put("/api/payroll/settings", json={"task_commission_mode": mode}, headers=tenant)
    emps = {}
    for k in "ABCD":
        _, emps[k] = await _employee(client, db, me["org_id"], f"qa-com-{mode}-{k}@x.dev")
    await _rule(db, me["org_id"], amount=10)
    t = await _task(client, tenant, assignee_ids=[emps["A"].id, emps["B"].id, emps["C"].id])
    p = lambda body: client.patch(f"/api/tasks/{t['id']}", json=body, headers=tenant)  # noqa: E731
    await p({"status": "done"})
    await p({"status": "done"})                 # double close
    await p({"status": "todo"})
    await p({"assignee_ids": [emps["A"].id, emps["B"].id, emps["D"].id]})
    await p({"status": "done"})
    await p({"status": "in_progress"})
    await p({"status": "done"})
    got = {k: round(await _paid(db, emps[k].id), 4) for k in "ABCD"}
    expect = {"split": {"A": 3.3334, "B": 3.3333, "C": 0, "D": 3.3333},
              "each_full": {"A": 10, "B": 10, "C": 0, "D": 10},
              "first_only": {"A": 10, "B": 0, "C": 0, "D": 0}}[mode]
    assert got == pytest.approx(expect), got


@pytest.mark.asyncio
async def test_qa_mode_change_between_close_and_reclose(client, tenant, db):
    me = await _me(client, tenant)
    _, a = await _employee(client, db, me["org_id"], "qa-mc-a@x.dev")
    _, b = await _employee(client, db, me["org_id"], "qa-mc-b@x.dev")
    await _rule(db, me["org_id"], amount=10)
    await client.put("/api/payroll/settings", json={"task_commission_mode": "each_full"}, headers=tenant)
    t = await _task(client, tenant, assignee_ids=[a.id, b.id])
    p = lambda body: client.patch(f"/api/tasks/{t['id']}", json=body, headers=tenant)  # noqa: E731
    await p({"status": "done"})
    await client.put("/api/payroll/settings", json={"task_commission_mode": "split"}, headers=tenant)
    await p({"status": "todo"})
    await p({"status": "done"})
    assert await _paid(db, a.id) == pytest.approx(5) and await _paid(db, b.id) == pytest.approx(5)


@pytest.mark.asyncio
async def test_qa_done_cancel_done_and_delete_restore_cycles(client, tenant, db):
    me = await _me(client, tenant)
    _, a = await _employee(client, db, me["org_id"], "qa-cyc-a@x.dev")
    _, b = await _employee(client, db, me["org_id"], "qa-cyc-b@x.dev")
    await _rule(db, me["org_id"], amount=10)
    t = await _task(client, tenant, assignee_ids=[a.id, b.id])
    p = lambda body: client.patch(f"/api/tasks/{t['id']}", json=body, headers=tenant)  # noqa: E731
    await p({"status": "done"})
    await p({"status": "cancelled"})
    assert await _paid(db, a.id) == pytest.approx(0)
    await p({"status": "done"})
    await client.delete(f"/api/tasks/{t['id']}", headers=tenant)
    await client.post(f"/api/tasks/{t['id']}/restore", headers=tenant)
    await client.delete(f"/api/tasks/{t['id']}", headers=tenant)
    await client.post(f"/api/tasks/{t['id']}/restore", headers=tenant)
    assert await _paid(db, a.id) == pytest.approx(5) and await _paid(db, b.id) == pytest.approx(5)


@pytest.mark.asyncio
async def test_qa_concurrent_double_close_pays_once(client, tenant, db):
    me = await _me(client, tenant)
    _, a = await _employee(client, db, me["org_id"], "qa-dc-a@x.dev")
    _, b = await _employee(client, db, me["org_id"], "qa-dc-b@x.dev")
    await _rule(db, me["org_id"], amount=10)
    t = await _task(client, tenant, assignee_ids=[a.id, b.id])
    res = await asyncio.gather(*[client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
                                 for _ in range(3)], return_exceptions=True)
    codes = [getattr(r, "status_code", repr(r)) for r in res]
    assert await _paid(db, a.id) == pytest.approx(5), codes
    assert await _paid(db, b.id) == pytest.approx(5), codes


# ── move to today ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_qa_move_to_today_mixed_ids(client, tenant, db):
    import uuid
    me = await _me(client, tenant)
    SH, staff = await _employee(client, db, me["org_id"], "qa-mix@x.dev")
    _, other = await _employee(client, db, me["org_id"], "qa-mix-o@x.dev")
    today = today_in(TZ)
    past = _iso(_local(today - timedelta(days=2), 9))
    mine = await _task(client, tenant, assigned_to_id=staff.id, due_at=past)
    pool_by_owner = await _task(client, tenant, title="pool", due_at=past)            # visible, not changeable
    hidden = await _task(client, tenant, assigned_to_id=other.id, due_at=past)
    done = await _task(client, tenant, assigned_to_id=staff.id, due_at=past)
    await client.patch(f"/api/tasks/{done['id']}", json={"status": "done"}, headers=tenant)
    cancelled = await _task(client, tenant, assigned_to_id=staff.id, due_at=past)
    await client.patch(f"/api/tasks/{cancelled['id']}", json={"status": "cancelled"}, headers=tenant)
    deleted = await _task(client, tenant, assigned_to_id=staff.id, due_at=past)
    await client.delete(f"/api/tasks/{deleted['id']}", headers=tenant)
    r = await client.post("/api/auth/register", json={
        "org_name": "B", "name": "B", "email": f"mx-{uuid.uuid4().hex[:6]}@x.dev", "password": "pw-test-12345"})
    H2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    foreign = await _task(client, H2, title="foreign", due_at=past)
    ids = [mine["id"], pool_by_owner["id"], hidden["id"], done["id"], cancelled["id"], deleted["id"],
           foreign["id"], "", mine["id"]]
    r = await client.post("/api/tasks/move-to-today", json={"ids": ids}, headers=SH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["moved"] == 1
    assert set(body["skipped"]) == {pool_by_owner["id"], hidden["id"], done["id"], cancelled["id"],
                                    deleted["id"], foreign["id"]}
    f = (await db.execute(select(Task).where(Task.id == foreign["id"]))).scalar_one()
    assert f.due_at.date() < today


# ── my day: clocks and caps ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_qa_done_today_follows_the_ist_day(client, tenant, db, monkeypatch):
    me = await _me(client, tenant)
    t = await _task(client, tenant, title="late finish", assigned_to_id=me["id"])
    _freeze(monkeypatch, "2026-11-12T18:20:00")            # 23:50 IST on the 12th
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
    _freeze(monkeypatch, "2026-11-12T18:25:00")            # 23:55 IST
    _, ti = await _titles(client, tenant)
    assert ti["done_today"] == ["late finish"]
    _freeze(monkeypatch, "2026-11-12T18:35:00")            # 00:05 IST on the 13th (UTC still the 12th)
    _, ti = await _titles(client, tenant)
    assert ti["done_today"] == []


@pytest.mark.asyncio
async def test_qa_new_york_fall_back_day(client, tenant, monkeypatch):
    await client.put("/api/staff/company", json={"timezone": "America/New_York"}, headers=tenant)
    me = await _me(client, tenant)
    mine = {"assigned_to_id": me["id"]}
    ny = "America/New_York"
    d0 = date(2026, 11, 1)    # 25-hour day
    await _task(client, tenant, title="sat 23:59", due_at=_iso(_local(d0 - timedelta(days=1), 23, 59, tz=ny)), **mine)
    await _task(client, tenant, title="sun 01:30", due_at="2026-11-01T01:30:00-05:00", **mine)   # 2nd 01:30
    await _task(client, tenant, title="sun date", due_at="2026-11-01", **mine)
    await _task(client, tenant, title="mon 00:00", due_at=_iso(_local(d0 + timedelta(days=1), 0, tz=ny)), **mine)
    _freeze(monkeypatch, "2026-11-01T16:00:00")
    d, t = await _titles(client, tenant)
    assert d["date"] == "2026-11-01"
    assert t["catch_up"] == ["sat 23:59"]
    assert sorted(t["today"]) == ["sun 01:30", "sun date"]
    assert t["upcoming"] == ["mon 00:00"]


@pytest.mark.asyncio
async def test_qa_101_tasks_cap_and_counts(client, tenant, db, monkeypatch):
    me = await _me(client, tenant)
    today = today_in(TZ)
    base = _local(today, 23, 0)
    db.add_all([Task(org_id=me["org_id"], title=f"t{i:03d}", assigned_to_id=me["id"], assignee_ids=[me["id"]],
                     status="todo", due_at=base + timedelta(seconds=i), created_by_id=me["id"]) for i in range(101)])
    db.add_all([Task(org_id=me["org_id"], title=f"late{i:03d}", assigned_to_id=me["id"], assignee_ids=[me["id"]],
                     status="todo", due_at=base - timedelta(days=3, seconds=i), created_by_id=me["id"]) for i in range(101)])
    await db.commit()
    _freeze(monkeypatch, _local(today, 12).isoformat())
    d, t = await _titles(client, tenant)
    assert d["counts"]["today"] == 101 and len(t["today"]) == 100
    assert t["today"][0] == "t000" and t["today"][-1] == "t099"
    assert d["counts"]["catch_up"] == 101 and len(t["catch_up"]) == 100
    assert t["catch_up"][0] == "late100"                 # oldest due first
    # move-all from the screen only sends the 100 shown ids
    ids = [x["id"] for x in d["catch_up"]]
    r = (await client.post("/api/tasks/move-to-today", json={"ids": ids}, headers=tenant)).json()
    d2, _ = await _titles(client, tenant)
    assert r["moved"] == 100 and d2["counts"]["catch_up"] == 1   # documents: "Move all" leaves the 101st behind


@pytest.mark.asyncio
async def test_qa_quick_add_date_only_today_is_in_today_bucket_late_evening(client, tenant, monkeypatch):
    _freeze(monkeypatch, "2026-11-12T18:00:00")      # 23:30 IST
    me = await _me(client, tenant)
    t = await _task(client, tenant, title="qa today", due_at="2026-11-12", assigned_to_id=me["id"])
    assert t["due_at"] == "2026-11-12T18:29:59"
    assert t["overdue"] is False
    _, ti = await _titles(client, tenant)
    assert ti["today"] == ["qa today"]


@pytest.mark.asyncio
async def test_qa_staff_my_day_assignee_case_and_own_id(client, tenant, db):
    me = await _me(client, tenant)
    SH, staff = await _employee(client, db, me["org_id"], "qa-own@x.dev")
    assert (await client.get(f"/api/tasks/my-day?assignee={staff.id}", headers=SH)).status_code == 200
    assert (await client.get("/api/tasks/my-day?assignee=all", headers=SH)).status_code == 403
    assert (await client.get(f"/api/tasks/my-day?assignee={me['id']}", headers=SH)).status_code == 403
