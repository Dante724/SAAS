"""Launch blocker B10 (permissions): four side doors the review walked through.

* A zero-permission Viewer set a 60% standing discount on a client.
* Order lines could be deleted with no permission check.
* A Specialist could post a pay-out from the till.
* An invitee with no role was treated as the owner."""
import hashlib

import pytest
from sqlalchemy import select

from backend.models import Employee, Invite, Role
from backend.services.auth import hash_password
from backend.services.permissions import new_role_permissions


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _login(client, email, pw="pw-staff-12345"):
    r = await client.post("/api/auth/token", data={"username": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _staff(client, tenant, db, name, perms):
    me = await _me(client, tenant)
    role = Role(org_id=me["org_id"], name=name, permissions=perms, perms_explicit=True)
    db.add(role)
    await db.flush()
    email = f"{name.lower()}@b10.dev"
    db.add(Employee(org_id=me["org_id"], location_id=me["location_id"], role_id=role.id, name=name,
                    email=email, password_hash=hash_password("pw-staff-12345"), active=True))
    await db.commit()
    return await _login(client, email)


async def _viewer(client, tenant, db):
    return await _staff(client, tenant, db, "Viewer", {})


async def _specialist(client, tenant, db):
    return await _staff(client, tenant, db, "Specialist", new_role_permissions("Specialist"))


DISCOUNT = {"disc_services_mode": "percent", "disc_services_value": 60}


@pytest.mark.asyncio
async def test_viewer_cannot_set_a_standing_client_discount(client, tenant, db):
    vh = await _viewer(client, tenant, db)
    cid = (await client.post("/api/clients", json={"name": "Dee"}, headers=tenant)).json()["id"]

    r = await client.patch(f"/api/clients/{cid}", json=DISCOUNT, headers=vh)
    assert r.status_code == 403, r.text
    r = await client.put(f"/api/clients/{cid}", json={"name": "Dee", **DISCOUNT}, headers=vh)
    assert r.status_code == 403, r.text
    got = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()
    assert got["disc_services_value"] in (0, 0.0, None) and got["disc_services_mode"] in ("none", None)

    # the owner can, and re-saving the client unchanged never needs the discount right
    assert (await client.patch(f"/api/clients/{cid}", json=DISCOUNT, headers=tenant)).status_code == 200
    full = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()
    keep = {k: full[k] for k in ("name", "disc_services_mode", "disc_services_value")}
    assert (await client.patch(f"/api/clients/{cid}", json=keep, headers=vh)).status_code == 200


@pytest.mark.asyncio
async def test_order_line_delete_needs_a_permission(client, tenant, db):
    vh = await _viewer(client, tenant, db)
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    oid = (await client.post("/api/orders", json={"location_id": loc}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={"kind": "service", "description": "Fix",
                                                            "qty": 1, "unit_price": 80}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    lid = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["lines"][0]["id"]

    r = await client.delete(f"/api/orders/{oid}/lines/{lid}", headers=vh)
    assert r.status_code == 403, r.text
    assert (await client.delete(f"/api/orders/{oid}/lines/{lid}", headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_specialist_cannot_pay_out_of_the_till(client, tenant, db):
    sh = await _specialist(client, tenant, db)
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    sid = (await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 100},
                             headers=tenant)).json()["id"]
    out = {"direction": "out", "amount": 50, "note": "lunch"}
    r = await client.post(f"/api/finance/shifts/{sid}/movement", json=out, headers=sh)
    assert r.status_code == 403, r.text
    assert (await client.post(f"/api/finance/shifts/{sid}/movement", json=out, headers=tenant)).status_code == 201


@pytest.mark.asyncio
async def test_invitee_with_no_role_is_not_the_owner(client, tenant, db):
    r = await client.post("/api/staff/invites", json={"email": "newbie@b10.dev"}, headers=tenant)
    assert r.status_code == 201, r.text
    # the raw token is only in the email; mint a known one for the test
    inv = (await db.execute(select(Invite).where(Invite.email == "newbie@b10.dev"))).scalar_one()
    inv.token_hash = hashlib.sha256(b"b10-token").hexdigest()
    await db.commit()
    r = await client.post("/api/auth/invite/accept", json={"token": "b10-token", "name": "Newbie", "password": "pw-newbie-12345"})
    assert r.status_code == 200, r.text
    nh = {"Authorization": f"Bearer {r.json()['access_token']}"}

    me = await _me(client, nh)
    assert me["is_owner"] is False and me["is_shop_owner"] is False
    assert not any(me["permissions"].values())
    assert (await client.get("/api/reports/dashboard", headers=nh)).status_code == 403
    assert (await client.post("/api/staff/employees", headers=nh, json={
        "name": "X", "email": "x@b10.dev", "password": "pw-123456789"})).status_code == 403
    cid = (await client.post("/api/clients", json={"name": "Eve"}, headers=tenant)).json()["id"]
    assert (await client.patch(f"/api/clients/{cid}", json=DISCOUNT, headers=nh)).status_code == 403
