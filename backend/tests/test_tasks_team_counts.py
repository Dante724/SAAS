"""Tasks › My day: exact team and pool counts for managers, and assignee=pool (Up for grabs).

The lists in GET /api/tasks/my-day stop at 100 a bucket, so a team row counted from them undercounts
in a big shop. With assignee=all the reply now carries `team` (every active employee's overdue /
today / done today) and `pool` (open unassigned tasks per bucket), each from one grouped COUNT
query. assignee=pool lists the shop's unassigned tasks, for staff too, with true counts.
"""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import event

from backend.models import Employee, Role, Task
from backend.services.auth import hash_password
from backend.services.dates import day_start_utc, today_in, wall_to_utc

TZ = "Asia/Kolkata"   # a new shop's default


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _employee(client, db, org_id, email, name="Emp", perms=None, active=True):
    role = Role(org_id=org_id, name=f"R-{email}", permissions=perms or {}, perms_explicit=True)
    db.add(role)
    await db.flush()
    e = Employee(org_id=org_id, name=name, email=email, password_hash=hash_password("pw-123456789"),
                 active=active, role_id=role.id)
    db.add(e)
    await db.commit()
    await db.refresh(e)
    if not active:
        return None, e
    tok = (await client.post("/api/auth/token", data={
        "username": email, "password": "pw-123456789"})).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}, e


def _at(days: int, hh: int = 12):
    """A naive UTC instant: shop noon (or hh) `days` from the shop's today."""
    from datetime import datetime, time
    return wall_to_utc(datetime.combine(today_in(TZ) + timedelta(days=days), time(hh)), TZ)


def _t(org_id, title, *, who=None, ids=None, due=None, status="todo", completed_at=None, made_by=None):
    return Task(org_id=org_id, title=title, assigned_to_id=who,
                assignee_ids=ids if ids is not None else ([who] if who else []),
                due_at=due, status=status, completed_at=completed_at, created_by_id=made_by)


def _team(day):
    return {r["employee_id"]: (r["overdue"], r["today"], r["done_today"]) for r in day["team"]}


@pytest.mark.asyncio
async def test_team_and_pool_counts_are_exact_past_the_list_cap(client, tenant, db):
    me = await _me(client, tenant)
    org = me["org_id"]
    _, ann = await _employee(client, db, org, f"ann-{uuid.uuid4().hex[:6]}@x.dev", "Ann")
    _, sam = await _employee(client, db, org, f"sam-{uuid.uuid4().hex[:6]}@x.dev", "Sam")
    _, gone = await _employee(client, db, org, f"gone-{uuid.uuid4().hex[:6]}@x.dev", "Gone", active=False)
    done_at = day_start_utc(today_in(TZ), TZ) + timedelta(hours=1)
    rows = (
        # 130 due today for Ann: past the 100 cap of the today list
        [_t(org, f"ann today {i}", who=ann.id, due=_at(0)) for i in range(130)]
        # 3 overdue shared by Ann (primary) and Sam (extra): count for both
        + [_t(org, f"shared late {i}", who=ann.id, ids=[ann.id, sam.id], due=_at(-2)) for i in range(3)]
        # Sam only in assignee_ids (no primary): still Sam's
        + [_t(org, "sam extra", who=None, ids=[sam.id], due=_at(0))]
        # done today by Sam, and one done yesterday (not today)
        + [_t(org, "sam done", who=sam.id, status="done", completed_at=done_at)]
        + [_t(org, "sam done before", who=sam.id, status="done", completed_at=done_at - timedelta(days=1))]
        # closed or deleted tasks never count
        + [_t(org, "cancelled", who=ann.id, due=_at(0), status="cancelled")]
        # 120 unassigned today, 2 overdue, 1 upcoming, 4 later (2 undated + 2 far): the pool
        + [_t(org, f"pool today {i}", due=_at(0)) for i in range(120)]
        + [_t(org, f"pool late {i}", due=_at(-1)) for i in range(2)]
        + [_t(org, "pool soon", due=_at(3))]
        + [_t(org, f"pool later {i}", due=None) for i in range(2)]
        + [_t(org, f"pool far {i}", due=_at(20)) for i in range(2)]
        + [_t(org, "pool done", status="done", completed_at=done_at)]
        # someone who left: not on the team row
        + [_t(org, "gone's", who=gone.id, due=_at(0))]
    )
    deleted = _t(org, "deleted", who=ann.id, due=_at(0))
    db.add_all(rows + [deleted])
    await db.commit()
    from backend.services.dates import now_utc
    deleted.archived_at = now_utc()
    await db.commit()

    r = await client.get("/api/tasks/my-day?assignee=all", headers=tenant)
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["today"]) == 100 and d["counts"]["today"] > 100        # the lists are still capped
    team = _team(d)
    assert team[ann.id] == (3, 130, 0)
    assert team[sam.id] == (3, 1, 1)
    assert team[me["id"]] == (0, 0, 0)                                  # everyone active is listed
    assert gone.id not in team
    assert d["pool"] == {"overdue": 2, "today": 120, "upcoming": 1, "later": 4}
    # the old fields are unchanged
    assert set(d) >= {"date", "catch_up", "today", "upcoming", "later", "done_today", "counts"}


@pytest.mark.asyncio
async def test_team_and_pool_only_with_all(client, tenant, db):
    me = await _me(client, tenant)
    d = (await client.get("/api/tasks/my-day", headers=tenant)).json()
    assert "team" not in d and "pool" not in d
    d = (await client.get(f"/api/tasks/my-day?assignee={me['id']}", headers=tenant)).json()
    assert "team" not in d


@pytest.mark.asyncio
async def test_pool_lists_unassigned_tasks_with_true_counts(client, tenant, db):
    me = await _me(client, tenant)
    org = me["org_id"]
    SH, staff = await _employee(client, db, org, f"st-{uuid.uuid4().hex[:6]}@x.dev", "Staff")
    db.add_all([_t(org, f"grab {i}", due=_at(0), made_by=me["id"]) for i in range(140)]
               + [_t(org, "staff to-do", made_by=staff.id),                 # unassigned, made by staff
                  _t(org, "taken", who=me["id"], due=_at(0)),
                  _t(org, "late grab", due=_at(-3)),
                  _t(org, "done grab", status="done", completed_at=day_start_utc(today_in(TZ), TZ) + timedelta(hours=2))])
    await db.commit()
    for H in (tenant, SH):                                                  # managers and staff alike
        r = await client.get("/api/tasks/my-day?assignee=pool", headers=H)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["counts"] == {"catch_up": 1, "today": 140, "upcoming": 0, "later": 1, "done_today": 1}
        assert len(d["today"]) == 100
        listed = [x for k in ("catch_up", "today", "upcoming", "later", "done_today") for x in d[k]]
        assert all(x["assigned_to_id"] is None and x["assignee_ids"] == [] for x in listed)
        assert "taken" not in {x["title"] for x in listed}
        assert "team" not in d


@pytest.mark.asyncio
async def test_pool_respects_visibility(client, tenant, db, monkeypatch):
    """Staff get the pool through the visibility rules: whatever those rules hide stays hidden."""
    from backend.routers import tasks as tasks_router
    from backend.services import task_visibility as tv
    me = await _me(client, tenant)
    org = me["org_id"]
    SH, staff = await _employee(client, db, org, f"vis-{uuid.uuid4().hex[:6]}@x.dev", "Staff")
    db.add_all([_t(org, "open to all", made_by=me["id"]), _t(org, "secret pool", made_by=me["id"])])
    await db.commit()
    assert {x["title"] for x in (await client.get("/api/tasks/my-day?assignee=pool", headers=SH)).json()["later"]} \
        == {"open to all", "secret pool"}

    real = tv.visibility_conds

    async def stricter(db_, employee):
        conds = await real(db_, employee)
        return conds + ([Task.title != "secret pool"] if conds else [])
    monkeypatch.setattr(tasks_router, "visibility_conds", stricter)
    staff_titles = {x["title"] for x in (await client.get("/api/tasks/my-day?assignee=pool", headers=SH)).json()["later"]}
    assert staff_titles == {"open to all"}
    owner_titles = {x["title"] for x in (await client.get("/api/tasks/my-day?assignee=pool", headers=tenant)).json()["later"]}
    assert owner_titles == {"open to all", "secret pool"}                 # managers see every task


@pytest.mark.asyncio
async def test_staff_still_cannot_see_everyone(client, tenant, db):
    me = await _me(client, tenant)
    SH, _ = await _employee(client, db, me["org_id"], f"no-{uuid.uuid4().hex[:6]}@x.dev")
    assert (await client.get("/api/tasks/my-day?assignee=all", headers=SH)).status_code == 403


@pytest.mark.asyncio
async def test_team_and_pool_are_isolated_between_shops(client, tenant, db):
    me = await _me(client, tenant)
    _, ann = await _employee(client, db, me["org_id"], f"iso-{uuid.uuid4().hex[:6]}@x.dev", "Ann")
    db.add_all([_t(me["org_id"], "ours", who=ann.id, due=_at(0)), _t(me["org_id"], "our pool", due=_at(0))])
    await db.commit()
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Boss", "email": f"boss-{uuid.uuid4().hex[:6]}@x.dev",
        "password": "pw-test-12345"})
    H2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    other = await _me(client, H2)
    d = (await client.get("/api/tasks/my-day?assignee=all", headers=H2)).json()
    assert [x["employee_id"] for x in d["team"]] == [other["id"]]
    assert d["team"][0] == {"employee_id": other["id"], "overdue": 0, "today": 0, "done_today": 0}
    assert d["pool"] == {"overdue": 0, "today": 0, "upcoming": 0, "later": 0}
    p = (await client.get("/api/tasks/my-day?assignee=pool", headers=H2)).json()
    assert sum(p["counts"].values()) == 0
    ours = (await client.get("/api/tasks/my-day?assignee=all", headers=tenant)).json()
    assert _team(ours)[ann.id] == (0, 1, 0) and ours["pool"]["today"] == 1


@pytest.mark.asyncio
async def test_statement_count_does_not_grow_with_the_team(client, tenant, db):
    me = await _me(client, tenant)
    org = me["org_id"]
    seen = []

    def count(*_a, **_k):
        seen.append(1)

    async def people(n):
        for _ in range(n):
            _, e = await _employee(client, db, org, f"p-{uuid.uuid4().hex[:8]}@x.dev")
            db.add_all([_t(org, "x", who=e.id, ids=[e.id, me["id"]], due=_at(0)), _t(org, "p", due=_at(1))])
        await db.commit()

    async def measure():
        seen.clear()
        event.listen(engine, "before_cursor_execute", count)
        try:
            r = await client.get("/api/tasks/my-day?assignee=all", headers=tenant)
        finally:
            event.remove(engine, "before_cursor_execute", count)
        assert r.status_code == 200
        return len(seen), r.json()

    engine = db.bind.sync_engine
    await people(2)
    small, d1 = await measure()
    await people(12)
    big, d2 = await measure()
    assert small == big, (small, big)
    assert big <= 12, big                                                  # a small constant
    assert len(d2["team"]) == len(d1["team"]) + 12
