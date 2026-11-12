"""Probe CLI: a deactivated employee still gets job pushes on their registered devices."""
import uuid
from sqlalchemy import select


async def test_deactivated_employee_still_pushed(client, tenant, db, monkeypatch):
    from backend.models import Client, PushSubscription, StaffNotification
    from backend.services import push
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    r = await client.post("/api/staff/employees", headers=tenant,
                          json={"name": "Ex Tech", "email": f"ex-{uuid.uuid4().hex[:6]}@test.dev"})
    assert r.status_code == 201, r.text
    ex = r.json()["id"]
    db.add(PushSubscription(org_id=me["org_id"], employee_id=ex, endpoint="https://push.invalid/ex", p256dh="k", auth="a"))
    await db.commit()
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    c.manager_id = ex       # the client's account manager
    await db.commit()
    r = await client.delete(f"/api/staff/employees/{ex}", headers=tenant)
    assert r.status_code == 200, r.text
    sent = []

    async def capture(db_, emp_id, payload):
        sent.append((emp_id, payload["title"]))
    monkeypatch.setattr(push, "_dispatch_push", capture)
    o = (await client.post("/api/orders", json={"complaint": "secret issue", "client_id": cid}, headers=tenant)).json()
    print("PROBE new order auto-assigned to deactivated:", o.get("assigned_to_id") == ex or (o.get("assigned_to") or {}).get("id") == ex)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "ready"}, headers=tenant)
    bells = (await db.execute(select(StaffNotification).where(StaffNotification.employee_id == ex))).scalars().all()
    print("PROBE pushes to deactivated:", [s for s in sent if s[0] == ex], "bell rows:", len(bells))
    assert any(s[0] == ex for s in sent)
