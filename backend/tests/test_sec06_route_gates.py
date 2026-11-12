"""Audit SEC-06: authenticated writes that any active employee could make.

About 130 staff write routes had no route-level permission, and several had no inline check
either: a roleless invitee or a restricted technician could hand a company asset to their own
client, log a fake "customer" reply (which notifies the shop), or create and edit inquiries. The
route table test (test_route_permission_gate.py) now needs a reason for every open route; these
tests pin the behaviour of the ones that got a gate.
"""
import uuid

import pytest

from backend.models import Employee, Role
from backend.services.auth import hash_password
from backend.services.permissions import new_role_permissions

PW = "pw-staff-12345"


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _person(client, tenant, db, perms):
    """A signed-in employee of the tenant's shop: perms=None = an invitee accepted with no role."""
    me = await _me(client, tenant)
    role_id = None
    if perms is not None:
        role = Role(org_id=me["org_id"], name=f"R{uuid.uuid4().hex[:6]}", permissions=perms,
                    perms_explicit=True)
        db.add(role)
        await db.flush()
        role_id = role.id
    email = f"sec06-{uuid.uuid4().hex[:8]}@test.dev"
    db.add(Employee(org_id=me["org_id"], location_id=me["location_id"], role_id=role_id,
                    name="Staff", email=email, password_hash=hash_password(PW), active=True))
    await db.commit()
    r = await client.post("/api/auth/token", data={"username": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
async def outsiders(client, tenant, db):
    """A roleless invitee and a role with nothing granted: the two the audit named."""
    return [await _person(client, tenant, db, None), await _person(client, tenant, db, {})]


@pytest.mark.asyncio
async def test_asset_transfer_needs_inventory_rights(client, tenant, db, outsiders):
    asset = (await client.post("/api/assets", json={"owner_type": "company", "brand": "Loaner"},
                               headers=tenant)).json()["id"]
    cid = (await client.post("/api/clients", json={"name": "Tech's mate"}, headers=tenant)).json()["id"]
    for h in outsiders:
        r = await client.post(f"/api/assets/{asset}/transfer", json={"to_client_id": cid}, headers=h)
        assert r.status_code == 403, r.text
    got = (await client.get(f"/api/assets/{asset}", headers=tenant)).json()
    assert got["owner_type"] == "company"
    tech = await _person(client, tenant, db, new_role_permissions("Specialist"))
    r = await client.post(f"/api/assets/{asset}/transfer", json={"to_client_id": cid}, headers=tech)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_asset_transfer_respects_location_scope(client, tenant, db):
    me = await _me(client, tenant)
    other_loc = (await client.post("/api/staff/locations", json={"name": "Far away"},
                                   headers=tenant)).json()["id"]
    perms = new_role_permissions("Specialist")
    scoped = Role(org_id=me["org_id"], name="Local", permissions=perms, perms_explicit=True,
                  location_scope="selected", location_ids=f'["{me["location_id"]}"]')
    db.add(scoped)
    await db.flush()
    email = f"sec06-{uuid.uuid4().hex[:8]}@test.dev"
    db.add(Employee(org_id=me["org_id"], location_id=me["location_id"], role_id=scoped.id, name="L",
                    email=email, password_hash=hash_password(PW), active=True))
    await db.commit()
    h = {"Authorization": "Bearer " + (await client.post("/api/auth/token", data={
        "username": email, "password": PW})).json()["access_token"]}
    asset = (await client.post("/api/assets", json={"owner_type": "company", "brand": "Loaner"},
                               headers=tenant)).json()["id"]
    r = await client.post(f"/api/assets/{asset}/transfer", json={"to_location_id": other_loc}, headers=h)
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_staff_cannot_fake_a_customer_reply_without_inbox_rights(client, tenant, outsiders):
    tid = (await client.post("/api/chats/threads", json={"contact_phone": "+15550001111"},
                             headers=tenant)).json()["id"]
    for h in outsiders:
        r = await client.post(f"/api/chats/threads/{tid}/inbound", json={"body": "Pay this link"}, headers=h)
        assert r.status_code == 403, r.text
        r = await client.post(f"/api/chats/threads/{tid}/send", json={"body": "hi"}, headers=h)
        assert r.status_code == 403, r.text
        r = await client.post("/api/chats/threads", json={"contact_phone": "+15550002222"}, headers=h)
        assert r.status_code == 403, r.text
        r = await client.post(f"/api/chats/threads/{tid}/to-order", headers=h)
        assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_leads_need_job_intake_rights(client, tenant, db, outsiders):
    lid = (await client.post("/api/leads", json={"name": "Walk-in"}, headers=tenant)).json()["id"]
    for h in outsiders:
        r = await client.post("/api/leads", json={"name": "Spam"}, headers=h)
        assert r.status_code == 403, r.text
        r = await client.patch(f"/api/leads/{lid}", json={"name": "Renamed"}, headers=h)
        assert r.status_code == 403, r.text
    tech = await _person(client, tenant, db, new_role_permissions("Specialist"))
    r = await client.post("/api/leads", json={"name": "Phone-in"}, headers=tech)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_other_ungated_writes_now_need_their_permission(client, tenant, outsiders):
    cid = (await client.post("/api/clients", json={"name": "Cara"}, headers=tenant)).json()["id"]
    cid2 = (await client.post("/api/clients", json={"name": "Cara 2"}, headers=tenant)).json()["id"]
    me = await _me(client, tenant)
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": me["location_id"],
                                                  "complaint": "x"}, headers=tenant)).json()["id"]
    item = (await client.post(f"/api/orders/{oid}/checklist", json={"label": "Screws"},
                              headers=tenant)).json()
    item_id = item["id"] if isinstance(item, dict) and "id" in item else item["items"][-1]["id"]
    calls = [
        ("post", "/api/appointments", {"client_id": cid, "scheduled_for": "2030-01-01T10:00:00"}),
        ("put", f"/api/clients/{cid}", {"name": "Hijack"}),
        ("patch", f"/api/clients/{cid}", {"name": "Hijack"}),
        ("post", f"/api/clients/{cid}/devices", {"brand": "X"}),
        ("post", "/api/clients/merge/preview", {"target_id": cid, "source_ids": [cid2]}),
        ("post", f"/api/clients/{cid}/communication", {"channel": "sms", "body": "hi"}),
        ("post", f"/api/orders/{oid}/checklist", {"label": "More"}),
        ("patch", f"/api/orders/{oid}/checklist/{item_id}", {"done": True}),
        ("delete", f"/api/orders/{oid}/checklist/{item_id}", None),
        ("post", "/api/reports/insights/cards", {"subject": "sales"}),
        ("post", "/api/inventory/reorder-digest", None),
    ]
    for h in outsiders:
        for method, url, body in calls:
            kw = {"json": body} if body is not None else {}
            r = await getattr(client, method)(url, headers=h, **kw)
            assert r.status_code == 403, f"{method.upper()} {url} -> {r.status_code} {r.text}"
