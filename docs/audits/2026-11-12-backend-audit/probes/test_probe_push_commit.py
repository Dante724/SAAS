"""Probe NTF: push.notify(commit=False) commits the caller's open transaction when a real push goes out."""
import sys, types
from sqlalchemy import select


async def test_notify_commit_false_still_commits_caller_work(client, tenant, db, monkeypatch):
    from backend.models import Client, Employee, PushSubscription, StaffNotification
    from backend.services import push
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org_id, emp_id = me["org_id"], me["id"]
    db.add(PushSubscription(org_id=org_id, employee_id=emp_id, endpoint="https://push.invalid/x",
                            p256dh="k", auth="a"))
    await db.commit()
    fake = types.ModuleType("pywebpush")
    fake.webpush = lambda **kw: None
    monkeypatch.setitem(sys.modules, "pywebpush", fake)
    monkeypatch.setattr(push, "push_enabled", lambda: True)

    # caller's unit of work: a row that must be rolled back if the caller fails later
    db.add(Client(org_id=org_id, name="SHOULD-ROLL-BACK"))
    await push.notify(db, org_id=org_id, employee_id=emp_id, kind="inventory.reorder",
                      title="Low stock", body="x", commit=False)
    await db.rollback()           # the caller fails after the alert (e.g. a later 409)
    left = (await db.execute(select(Client).where(Client.name == "SHOULD-ROLL-BACK"))).scalars().all()
    bell = (await db.execute(select(StaffNotification).where(StaffNotification.employee_id == emp_id,
                                                             StaffNotification.title == "Low stock"))).scalars().all()
    print("PROBE rows surviving rollback: client", len(left), "bell", len(bell))
    assert len(left) == 1 and len(bell) == 1


async def test_notify_commit_false_simulated_rolls_back(client, tenant, db):
    from backend.models import Client
    from backend.services import push
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org_id, emp_id = me["org_id"], me["id"]
    db.add(Client(org_id=org_id, name="SIM-ROLL-BACK"))
    await push.notify(db, org_id=org_id, employee_id=emp_id, kind="inventory.reorder",
                      title="Low stock", body="x", commit=False)
    await db.rollback()
    left = (await db.execute(select(Client).where(Client.name == "SIM-ROLL-BACK"))).scalars().all()
    print("PROBE simulated push: surviving", len(left))
    assert len(left) == 0
