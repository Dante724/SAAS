"""144ds — the Home feed is role-shaped: owner picks which signals a role sees
(home_feed_sources), and order_scope='own' limits order/task signals to what's
assigned to that person."""
import pytest
from datetime import datetime, timedelta
from sqlalchemy import select


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _attach_role(db, org_id, emp_id, **role_kw):
    from backend.models import Role, Employee
    r = Role(org_id=org_id, name="FeedRole", **role_kw)
    db.add(r)
    await db.flush()
    emp = (await db.execute(select(Employee).where(Employee.id == emp_id))).scalar_one()
    emp.role_id = r.id
    await db.commit()
    return r


@pytest.mark.asyncio
async def test_home_feed_sources_filters_items(client, tenant, db):
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    # a low-stock part + an open task would normally both appear
    from backend.models import Task
    db.add(Task(org_id=me["org_id"], title="T", status="todo"))
    await db.commit()
    # role that only shows 'tasks' on the feed
    await _attach_role(db, me["org_id"], me["id"], home_feed_sources=["tasks"])

    body = (await client.get("/api/reports/home-feed", headers=tenant)).json()
    keys = {i["key"] for i in body["items"]}
    assert keys <= {"tasks"}, keys          # nothing outside the allowed source
    assert "tasks" in keys


@pytest.mark.asyncio
async def test_own_scope_hides_unassigned_overdue(client, tenant, db):
    from backend.models import Order
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=tenant)).json()
    row = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    row.due_at = datetime.utcnow() - timedelta(days=1)   # overdue, but assigned to nobody
    await db.commit()

    # role scoped to own assigned work
    await _attach_role(db, me["org_id"], me["id"], order_scope="own")
    body = (await client.get("/api/reports/home-feed", headers=tenant)).json()
    assert not [i for i in body["items"] if i["key"] == "overdue"], "unassigned overdue must not show for an own-scoped role"
