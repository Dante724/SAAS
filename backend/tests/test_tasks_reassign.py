"""Reassigning a task with PATCH {"assigned_to_id": <new>} (the work order's task list, JobTasks)
replaces the owner. Since tk02 every id in assignee_ids sees the task, gets its overdue alerts and
shares its commission, so the person it was taken from must leave assignee_ids too."""
import pytest
from sqlalchemy import select

from backend.models import Task
from backend.tests.test_tasks_myday import _employee, _me, _paid, _rule, _task


@pytest.mark.asyncio
async def test_changing_the_owner_takes_the_task_off_the_old_owner(client, tenant, db):
    me = await _me(client, tenant)
    HA, a = await _employee(client, db, me["org_id"], "ra@x.dev", "A")
    _, b = await _employee(client, db, me["org_id"], "rb@x.dev", "B")
    await _rule(db, me["org_id"], amount=10)
    t = await _task(client, tenant, title="Swap", assigned_to_id=a.id)

    r = await client.patch(f"/api/tasks/{t['id']}", json={"assigned_to_id": b.id}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["assigned_to_id"] == b.id
    assert r.json()["assignee_ids"] == [b.id]

    # A no longer has it: not in their day, not visible to them
    day = (await client.get("/api/tasks/my-day", headers=HA)).json()
    assert t["id"] not in {x["id"] for k in ("catch_up", "today", "upcoming", "later") for x in day[k]}
    assert (await client.get(f"/api/tasks/{t['id']}", headers=HA)).status_code == 404

    # and closing it pays only B (split is the default mode)
    await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=tenant)
    assert await _paid(db, b.id) == pytest.approx(10.0)
    assert await _paid(db, a.id) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_changing_the_owner_keeps_the_other_assignees(client, tenant, db):
    me = await _me(client, tenant)
    _, a = await _employee(client, db, me["org_id"], "ka@x.dev", "A")
    _, b = await _employee(client, db, me["org_id"], "kb@x.dev", "B")
    _, c = await _employee(client, db, me["org_id"], "kc@x.dev", "C")
    t = await _task(client, tenant, title="Team", assignee_ids=[a.id, c.id])
    r = await client.patch(f"/api/tasks/{t['id']}", json={"assigned_to_id": b.id}, headers=tenant)
    assert r.json()["assigned_to_id"] == b.id
    assert r.json()["assignee_ids"] == [b.id, c.id]


@pytest.mark.asyncio
async def test_clearing_the_owner_promotes_the_next_assignee(client, tenant, db):
    me = await _me(client, tenant)
    _, a = await _employee(client, db, me["org_id"], "ca@x.dev", "A")
    _, c = await _employee(client, db, me["org_id"], "cc@x.dev", "C")
    solo = await _task(client, tenant, title="Solo", assigned_to_id=a.id)
    r = await client.patch(f"/api/tasks/{solo['id']}", json={"assigned_to_id": ""}, headers=tenant)
    assert (r.json()["assigned_to_id"], r.json()["assignee_ids"]) == (None, [])
    row = (await db.execute(select(Task).where(Task.id == solo["id"]))).scalar_one()
    assert row.assignee_ids == []          # back in the shop's pool
    team = await _task(client, tenant, title="Team", assignee_ids=[a.id, c.id])
    r = await client.patch(f"/api/tasks/{team['id']}", json={"assigned_to_id": ""}, headers=tenant)
    assert (r.json()["assigned_to_id"], r.json()["assignee_ids"]) == (c.id, [c.id])
