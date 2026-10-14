"""144bp — soft-dismiss staff notifications: hidden from the bell + unread count, row kept."""
import pytest
from sqlalchemy import select

from backend.models import Employee, StaffNotification


async def _owner_id(client, tenant, db):
    # the tenant fixture authenticates as the org owner; grab that employee
    me = (await client.get("/api/tech/me", headers=tenant)).json()
    if isinstance(me, dict) and me.get("employee_id"):
        return me["employee_id"]
    # fallback: first employee in the org
    emp = (await db.execute(select(Employee))).scalars().first()
    return emp.id


async def _seed(db, emp_id, org_id, n=3):
    for i in range(n):
        db.add(StaffNotification(org_id=org_id, employee_id=emp_id, kind="inventory.low_stock",
                                 title=f"N{i}", body="b"))
    await db.commit()


@pytest.mark.asyncio
async def test_dismiss_one_hides_it(client, tenant, db):
    emp = (await db.execute(select(Employee))).scalars().first()
    await _seed(db, emp.id, emp.org_id, 3)
    rows = (await client.get("/api/tech/notifications", headers=tenant)).json()["items"]
    assert len(rows) >= 3
    target = rows[0]["id"]
    r = await client.post(f"/api/tech/notifications/{target}/dismiss", headers=tenant)
    assert r.status_code == 200, r.text
    after = (await client.get("/api/tech/notifications", headers=tenant)).json()["items"]
    assert all(x["id"] != target for x in after)          # gone from the bell
    # row still exists in the table (soft dismiss), with dismissed_at + read_at set
    n = (await db.execute(select(StaffNotification).where(StaffNotification.id == target))).scalar_one()
    assert n.dismissed_at is not None and n.read_at is not None


@pytest.mark.asyncio
async def test_dismiss_all_clears_bell_and_unread(client, tenant, db):
    emp = (await db.execute(select(Employee))).scalars().first()
    await _seed(db, emp.id, emp.org_id, 4)
    r = await client.post("/api/tech/notifications/dismiss-all", headers=tenant)
    assert r.status_code == 200 and r.json()["dismissed"] >= 4
    items = (await client.get("/api/tech/notifications", headers=tenant)).json()["items"]
    assert items == []                                     # bell is empty
    me = (await client.get("/api/tech/me", headers=tenant)).json()
    assert me.get("unread_notifications", 0) == 0          # unread count cleared


@pytest.mark.asyncio
async def test_dismiss_foreign_notification_404(client, tenant, db):
    # a notification owned by someone else must not be dismissable. Use a REAL second org so the
    # fixture is referentially valid under enforced FKs (a fabricated org_id would now fail the
    # organisations FK on insert, which is correct — orphan rows shouldn't be possible).
    import uuid
    from sqlalchemy import select as _sel
    from backend.models import Organisation
    oid = f"org-{uuid.uuid4().hex[:8]}"
    db.add(Organisation(id=oid, name="Other Co"))
    await db.flush()
    other = Employee(org_id=oid, email=f"x-{uuid.uuid4().hex[:6]}@y.z", name="Other", password_hash="x")
    db.add(other); await db.flush()
    sn = StaffNotification(org_id=oid, employee_id=other.id, kind="x", title="foreign")
    db.add(sn); await db.commit()
    r = await client.post(f"/api/tech/notifications/{sn.id}/dismiss", headers=tenant)
    assert r.status_code == 404
