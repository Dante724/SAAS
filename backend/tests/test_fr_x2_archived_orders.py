"""Final review QA-8: GET /orders/archived follows the same rules as the live work-order list.

It had no permission gate and no record scope, so a role limited to its own jobs or to some
locations saw every archived job in the shop (number, client, total, reason). Its `total` was the
page size and it had no `skip`, so archived jobs past the first page could not be reached.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def _login(client, db, org_id, perms, **role_kw):
    from backend.models import Employee, Role
    from backend.services.auth import hash_password
    role = Role(org_id=org_id, name=f"R {uuid.uuid4().hex[:4]}", permissions=perms, perms_explicit=True, **role_kw)
    db.add(role)
    await db.flush()
    email = f"ar-{uuid.uuid4().hex[:6]}@x.dev"
    emp = Employee(org_id=org_id, name="Arch", email=email, password_hash=hash_password("pw-123456789"),
                   active=True, role_id=role.id)
    db.add(emp)
    await db.commit()
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-123456789"})).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}, emp


async def _archived(db, org_id, n, *, location_id=None, assigned_to_id=None):
    from backend.models import Location, Order
    loc = location_id or (await db.execute(select(Location.id).where(Location.org_id == org_id))).scalars().first()
    now = datetime.utcnow()
    ids = []
    for i in range(n):
        o = Order(org_id=org_id, order_number=f"A-{uuid.uuid4().hex[:6]}", location_id=loc, complaint="x",
                  status="new", archived=True, archived_at=now - timedelta(minutes=i), archive_reason="dupe",
                  assigned_to_id=assigned_to_id, total=10)
        db.add(o)
        ids.append(o)
    await db.commit()
    return [o.id for o in ids]


async def _branch(db, org_id):
    from backend.models import Location
    b = Location(org_id=org_id, name="Branch B")
    db.add(b)
    await db.commit()
    return b.id


VIEW = {"can_view_orders": True, "can_delete_orders": True}


async def test_archived_list_needs_the_delete_orders_right(client, tenant, db):
    org = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    await _archived(db, org, 1)
    H, _ = await _login(client, db, org, {**VIEW, "can_delete_orders": False})
    assert (await client.get("/api/orders/archived", headers=H)).status_code == 403


async def test_archived_list_follows_own_jobs_scope(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    H, emp = await _login(client, db, me["org_id"], VIEW, order_scope="own")
    mine = await _archived(db, me["org_id"], 1, assigned_to_id=emp.id)
    await _archived(db, me["org_id"], 2, assigned_to_id=me["id"])       # someone else's
    r = await client.get("/api/orders/archived", headers=H)
    assert r.status_code == 200, r.text
    assert [x["id"] for x in r.json()["items"]] == mine
    assert r.json()["total"] == 1


async def test_archived_list_follows_location_scope(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    main = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    b = await _branch(db, me["org_id"])
    at_b = await _archived(db, me["org_id"], 1, location_id=b)
    await _archived(db, me["org_id"], 2, location_id=main)                # main branch
    H, _ = await _login(client, db, me["org_id"], VIEW, location_scope="selected", location_ids=f'["{b}"]')
    r = (await client.get("/api/orders/archived", headers=H)).json()
    assert [x["id"] for x in r["items"]] == at_b and r["total"] == 1


async def test_archived_list_has_a_real_total_and_pages(client, tenant, db):
    org = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    ids = await _archived(db, org, 5)
    p1 = (await client.get("/api/orders/archived?limit=2", headers=tenant)).json()
    assert p1["total"] == 5 and [x["id"] for x in p1["items"]] == ids[:2]
    p3 = (await client.get("/api/orders/archived?limit=2&skip=4", headers=tenant)).json()
    assert p3["total"] == 5 and [x["id"] for x in p3["items"]] == ids[4:]
    assert p3["skip"] == 4 and p3["limit"] == 2


@pytest.mark.parametrize("url", ["/api/orders?priority=99999999999999999999", "/api/orders?priority_min=-1",
                                 "/api/orders/board?priority_min=10"])
async def test_priority_filters_have_a_range(client, tenant, url):
    """X4 follow-up: an out-of-range priority is a 422, not a database overflow."""
    assert (await client.get(url, headers=tenant)).status_code == 422
    assert (await client.get("/api/orders?priority=1&priority_min=1", headers=tenant)).status_code == 200
