"""144ep — parents that are hard-deleted must not be blocked by (or orphan) their children.

Two of the foreign keys that 144ep adds to production point at parents the app HARD-deletes:
  * tasks      — DELETE /api/tasks/{id};      time_entries.task_id references it
  * suppliers  — un-flagging a contact as supplier removes its mirror; parts.default_supplier_id
                 references it
With a plain FK the delete is rejected (it already was in dev/tests, which enforce the model's FK);
without one (old production) the child silently pointed at nothing. Time entries are labour/payroll
history and a default supplier is an optional preference, so both are ON DELETE SET NULL: the parent
goes away, the child row stays and just loses the link.
"""
from datetime import datetime

from sqlalchemy import select

from backend.models import Part, Supplier, TimeEntry


async def _me(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()


async def test_deleting_a_task_keeps_its_time_entries(client, tenant, db):
    me = await _me(client, tenant)
    r = await client.post("/api/tasks", json={"title": "Diagnose board"}, headers=tenant)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    db.add(TimeEntry(org_id=me["org_id"], employee_id=me["id"], task_id=tid,
                     started_at=datetime(2026, 10, 1, 9, 0), ended_at=datetime(2026, 10, 1, 10, 0),
                     category="task", source="manual", state="stopped"))
    await db.commit()

    d = await client.delete(f"/api/tasks/{tid}", headers=tenant)
    assert d.status_code == 200, d.text

    db.expire_all()
    entries = (await db.execute(select(TimeEntry).where(TimeEntry.org_id == me["org_id"]))).scalars().all()
    assert len(entries) == 1                    # payroll/labour history kept
    assert entries[0].task_id is None           # link cleared, nothing dangling


async def test_unflagging_a_supplier_clears_parts_default_supplier(client, tenant, db):
    me = await _me(client, tenant)
    c = await client.post("/api/clients", json={"name": "Parts Co", "is_supplier": True}, headers=tenant)
    assert c.status_code == 201, c.text
    cid = c.json()["id"]
    sup = (await db.execute(select(Supplier).where(Supplier.client_id == cid))).scalars().first()
    assert sup is not None
    db.add(Part(org_id=me["org_id"], name="Screen", default_supplier_id=sup.id))
    await db.commit()

    u = await client.patch(f"/api/clients/{cid}", json={"is_supplier": False}, headers=tenant)
    assert u.status_code == 200, u.text

    db.expire_all()
    assert (await db.execute(select(Supplier).where(Supplier.client_id == cid))).scalars().first() is None
    part = (await db.execute(select(Part).where(Part.name == "Screen"))).scalars().one()
    assert part.default_supplier_id is None     # part kept, preference cleared
