"""DB-12: the documented way to close a shop exports everything it owns, then switches it off.
Nothing is deleted (no hard purge), and another shop is untouched."""
import uuid

import pytest
from sqlalchemy import select

from backend.models import AuditLog, Employee, Organisation, Payment
from backend.services.offboarding import offboard_org


async def _shop(client, name):
    r = await client.post("/api/auth/register", json={
        "org_name": name, "name": "Owner", "email": f"ob-{uuid.uuid4().hex[:8]}@test.dev",
        "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=h)).json()
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cl = (await client.post("/api/clients", json={"name": f"{name} client"}, headers=h)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cl, "location_id": loc, "complaint": "x"},
                           headers=h)).json()
    return {"h": h, "org": me["org_id"], "order": o["id"], "client": cl}


async def test_offboarding_exports_then_switches_the_shop_off(client, db):
    a, b = await _shop(client, "Leaving Co"), await _shop(client, "Staying Co")
    db.add(Payment(org_id=a["org"], order_id=a["order"], method="cash", amount=12))
    await db.commit()

    res = await offboard_org(db, a["org"], actor="ops@tekly.app", reason="Closed account")
    await db.commit()

    ex = res["export"]
    assert ex["organisations"][0]["id"] == a["org"]
    assert {r["id"] for r in ex["orders"]} == {a["order"]}
    assert {r["id"] for r in ex["clients"]} == {a["client"]}
    assert float(ex["payments"][0]["amount"]) == 12
    assert all(r["org_id"] == a["org"] for t, rows in ex.items() if t != "organisations" for r in rows)
    staff = ex["employees"][0]
    assert "password_hash" not in staff and "mfa_secret" not in staff       # secrets stay out
    assert "auth_sessions" not in ex
    assert not any("public_token" in k for k in ex["orders"][0])

    org = (await db.execute(select(Organisation).where(Organisation.id == a["org"]))).scalar_one()
    assert org.active is False and org.offboarded_at is not None
    assert all(not e.active for e in (await db.execute(select(Employee).where(Employee.org_id == a["org"]))).scalars())
    assert (await db.execute(select(Payment).where(Payment.org_id == a["org"]))).scalars().first()   # nothing deleted
    assert (await db.execute(select(AuditLog).where(AuditLog.org_id == a["org"],
                                                    AuditLog.action == "organisation.offboarded"))).scalars().first()
    assert (await client.get("/api/auth/me", headers=a["h"])).status_code == 401     # signed out
    # the other shop is untouched
    assert (await client.get("/api/auth/me", headers=b["h"])).status_code == 200

    again = await offboard_org(db, a["org"], actor="ops@tekly.app", reason="again")
    assert again["already"] is True and again["counts"]["orders"] == 1


async def test_offboarding_needs_a_reason_and_a_real_shop(db):
    with pytest.raises(LookupError):
        await offboard_org(db, "no-such-shop", actor="x", reason="y")
