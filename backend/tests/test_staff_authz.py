"""Security regression: staff employee/role endpoints require can_manage_employees /
can_manage_roles. Before the fix any authenticated employee could escalate (PATCH own
role_id to null → owner) or reset a teammate's password. Owner (no role) stays allowed."""
import pytest
from sqlalchemy import select

from backend.models import Employee, Organisation, Role
from backend.services.auth import hash_password


async def _login(client, email, pw):
    r = await client.post("/api/auth/token", data={"username": email, "password": pw})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_staff_writes_need_manage_employees(client, tenant, db):
    org = (await db.execute(select(Organisation))).scalars().first()
    owner = (await db.execute(select(Employee).where(Employee.org_id == org.id))).scalars().first()

    # a restricted employee: management of employees + roles explicitly denied
    role = Role(org_id=org.id, name="Tech", permissions={"can_manage_employees": False, "can_manage_roles": False})
    db.add(role)
    await db.flush()
    tech = Employee(org_id=org.id, location_id=owner.location_id, role_id=role.id,
                    name="Tech", email="tech@authz.test", password_hash=hash_password("pw-tech-123"), active=True)
    db.add(tech)
    await db.commit()
    th = await _login(client, "tech@authz.test", "pw-tech-123")

    # owner (no role) can still manage — not 403
    assert (await client.post("/api/staff/employees", headers=tenant, json={
        "name": "New", "email": "new@authz.test", "password": "pw-123456"})).status_code in (200, 201)

    # restricted tech is blocked on every sensitive staff write …
    assert (await client.post("/api/staff/employees", headers=th, json={
        "name": "X", "email": "x@authz.test", "password": "pw-123456"})).status_code == 403
    # … including the privilege-escalation path (PATCH own record to clear role_id)
    assert (await client.patch(f"/api/staff/employees/{tech.id}", headers=th,
                               json={"role_id": None})).status_code == 403
    assert (await client.post(f"/api/staff/employees/{owner.id}/password", headers=th,
                              json={"password": "hijacked1"})).status_code == 403
    assert (await client.post("/api/staff/roles", headers=th,
                              json={"name": "Admin2", "permissions": {}})).status_code == 403

    # the tech's role_id is unchanged (escalation did not take)
    await db.refresh(tech)
    assert tech.role_id == role.id
