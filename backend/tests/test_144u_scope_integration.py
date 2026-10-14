"""144u — end-to-end proof that order_scope="own" actually restricts a real
logged-in technician: they see only their assigned orders on the list, get 403
opening someone else's, and get 403 trying to mutate (comment on) it — while the
admin sees everything. This is the belt-and-suspenders integration test that the
144t/144u unit + transparency checks were standing in for.
"""
from sqlalchemy import select

from backend.models import Employee, Organisation, Role
from backend.services.auth import hash_password


async def _login(client, email, password):
    r = await client.post("/api/auth/token", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_scoped_tech_sees_and_touches_only_own_orders(client, tenant, db):
    # tenant = admin of a fresh org. Find that org + its admin employee.
    org = (await db.execute(select(Organisation))).scalars().first()
    admin = (await db.execute(select(Employee).where(Employee.org_id == org.id))).scalars().first()

    # a restricted technician in the SAME org (role scoped to their own orders)
    tech_role = Role(org_id=org.id, name="Bench tech", order_scope="own", permissions={})
    db.add(tech_role)
    await db.flush()
    tech = Employee(org_id=org.id, location_id=admin.location_id, role_id=tech_role.id,
                    name="Bench Tech", email="benchtech@scopetest.dev",
                    password_hash=hash_password("pw-tech-12345"), active=True)
    db.add(tech)
    await db.commit()

    # admin creates a client + two work orders
    cid = (await client.post("/api/clients", json={"name": "Scoped Co", "phone": "+15550333"}, headers=tenant)).json()["id"]
    mine = (await client.post("/api/orders", headers=tenant, json={"client_id": cid, "complaint": "mine"})).json()["id"]
    theirs = (await client.post("/api/orders", headers=tenant, json={"client_id": cid, "complaint": "someone else"})).json()["id"]

    # assign one to the tech, the other to the admin (i.e. NOT the tech, NOT unowned)
    for oid, emp in ((mine, tech), (theirs, admin)):
        from backend.models import Order
        o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
        o.assigned_to_id = emp.id
    await db.commit()

    tech_h = await _login(client, "benchtech@scopetest.dev", "pw-tech-12345")

    # 1) list: tech sees only their own order
    ids = [o["id"] for o in (await client.get("/api/orders", headers=tech_h)).json()["items"]]
    assert mine in ids and theirs not in ids, ids

    # 2) detail: 403 on the order that isn't theirs, 200 on their own
    assert (await client.get(f"/api/orders/{theirs}", headers=tech_h)).status_code == 403
    assert (await client.get(f"/api/orders/{mine}", headers=tech_h)).status_code == 200

    # 3) write guard: 403 trying to comment on someone else's order
    r = await client.post(f"/api/orders/{theirs}/comments", headers=tech_h, json={"body": "peeking", "visible_to_client": False})
    assert r.status_code == 403, r.text

    # 4) admin still sees both (scoping is opt-in per role)
    admin_ids = [o["id"] for o in (await client.get("/api/orders", headers=tenant)).json()["items"]]
    assert mine in admin_ids and theirs in admin_ids
