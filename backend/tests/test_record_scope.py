"""Security: "own records only" role scopes hold on every order/client route, not just a few GETs.

Regression for the audit finding where _guard_order_access existed but 27 /{order_id} routes
skipped it (a technician limited to their own orders could assign any order to themselves,
edit notes, add lines, bill time…) and only GET /clients/{id} checked contact scope.
"""
import uuid

import pytest
from sqlalchemy import select

from backend.models import Employee, Organisation, Role
from backend.services.auth import hash_password


@pytest.fixture
async def scoped(client, tenant, db):
    org = (await db.execute(select(Organisation))).scalars().first()
    owner = (await db.execute(select(Employee).where(Employee.org_id == org.id))).scalars().first()
    role = Role(org_id=org.id, name=f"Own-{uuid.uuid4().hex[:4]}", permissions={},
                order_scope="own", contact_scope="own")
    db.add(role)
    await db.flush()
    email = f"own-{uuid.uuid4().hex[:8]}@scope.test"
    tech = Employee(org_id=org.id, location_id=owner.location_id, role_id=role.id, name="Tech", email=email,
                    password_hash=hash_password("pw-own-12345"), active=True)
    other = Employee(org_id=org.id, location_id=owner.location_id, name="Other",
                     email=f"oth-{uuid.uuid4().hex[:8]}@scope.test", password_hash=hash_password("x" * 12), active=True)
    db.add_all([tech, other])
    await db.commit()
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-own-12345"})).json()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]

    async def order(assignee=None, client_id=None):
        cid = client_id or (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
        oid = (await client.post("/api/orders", headers=tenant, json={
            "client_id": cid, "location_id": loc, "complaint": "x"})).json()["id"]
        if assignee:
            await client.patch(f"/api/orders/{oid}/assign", headers=tenant, json={"assigned_to_id": assignee})
        return oid
    return {"h": {"Authorization": f"Bearer {tok['access_token']}"}, "tech": tech, "other": other,
            "role": role, "order": order}


@pytest.mark.asyncio
async def test_scoped_tech_cannot_act_on_someone_elses_order(client, scoped):
    h = scoped["h"]
    oid = await scoped["order"](assignee=scoped["other"].id)
    attempts = [
        client.patch(f"/api/orders/{oid}/assign", headers=h, json={"assigned_to_id": scoped["tech"].id}),
        client.patch(f"/api/orders/{oid}/notes", headers=h, json={"tech_notes": "hijack"}),
        client.post(f"/api/orders/{oid}/lines", headers=h, json={"description": "x", "unit_price": 1, "qty": 1}),
        client.get(f"/api/orders/{oid}/checklist", headers=h),
        client.post(f"/api/orders/{oid}/comments", headers=h, json={"text": "x"}),
    ]
    for coro in attempts:
        r = await coro
        assert r.status_code == 403, (r.request.method, r.request.url.path, r.status_code)


@pytest.mark.asyncio
async def test_scoped_tech_keeps_working_on_own_order(client, scoped):
    oid = await scoped["order"](assignee=scoped["tech"].id)
    r = await client.patch(f"/api/orders/{oid}/notes", headers=scoped["h"], json={"tech_notes": "done"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_contact_scope_applies_to_client_writes(client, tenant, scoped):
    cid = (await client.post("/api/clients", json={"name": "Someone else's"}, headers=tenant)).json()["id"]
    h = scoped["h"]
    assert (await client.patch(f"/api/clients/{cid}", headers=h, json={"name": "x"})).status_code == 403
    assert (await client.post(f"/api/clients/{cid}/portal-link", headers=h)).status_code == 403


@pytest.mark.asyncio
async def test_hidden_status_blocks_direct_access(client, db, scoped):
    oid = await scoped["order"](assignee=scoped["tech"].id)
    o = (await client.get(f"/api/orders/{oid}", headers=scoped["h"])).json()
    scoped["role"].hidden_status_keys = [o["status"]]
    await db.commit()
    r = await client.patch(f"/api/orders/{oid}/notes", headers=scoped["h"], json={"tech_notes": "x"})
    assert r.status_code == 403
