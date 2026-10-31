"""Security: staff managers must not escalate to owner or take over higher-privileged accounts.

Regression for the audit finding where anyone with can_manage_employees (incl. the default
"Manager" template) could PATCH their own role_id to null (= every permission), and reset the
owner's password / 2FA / email.
"""
import uuid

import pytest
from sqlalchemy import select

from backend.models import Employee, Role
from backend.services.auth import hash_password
from backend.services.permissions import TEMPLATES


async def _login(client, email, pw):
    r = await client.post("/api/auth/token", data={"username": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
async def team(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org_id = me["org_id"]
    owner = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    full = Role(org_id=org_id, name="Full access", permissions=dict(TEMPLATES["Full access"]))
    manager_role = Role(org_id=org_id, name="Manager", permissions=dict(TEMPLATES["Manager"]))
    # A role the manager may manage: denies everything the Manager template denies, and more.
    tech_role = Role(org_id=org_id, name="Specialist",
                     permissions={**TEMPLATES["Specialist"], "can_delete_transactions": False})
    db.add_all([full, manager_role, tech_role])
    await db.flush()
    sfx = uuid.uuid4().hex[:8]   # fresh emails per test: the login rate limiter is per process
    mgr = Employee(org_id=org_id, location_id=owner.location_id, role_id=manager_role.id, name="Mgr",
                   email=f"mgr-{sfx}@esc.test", password_hash=hash_password("pw-mgr-12345"), active=True)
    tech = Employee(org_id=org_id, location_id=owner.location_id, role_id=tech_role.id, name="Tech",
                    email=f"tech-{sfx}@esc.test", password_hash=hash_password("pw-tech-12345"), active=True)
    db.add_all([mgr, tech])
    await db.commit()
    return {"owner": owner, "mgr": mgr, "tech": tech, "full": full, "manager_role": manager_role,
            "tech_role": tech_role, "mh": await _login(client, mgr.email, "pw-mgr-12345"),
            "owner_h": tenant}


@pytest.mark.asyncio
@pytest.mark.parametrize("target_role", ["none", "full"])
async def test_manager_cannot_raise_own_role(client, db, team, target_role):
    role_id = None if target_role == "none" else team["full"].id
    r = await client.patch(f"/api/staff/employees/{team['mgr'].id}", headers=team["mh"], json={"role_id": role_id})
    assert r.status_code == 403
    await db.refresh(team["mgr"])
    assert team["mgr"].role_id == team["manager_role"].id


@pytest.mark.asyncio
async def test_manager_cannot_create_or_invite_more_privileged_staff(client, team):
    h = team["mh"]
    assert (await client.post("/api/staff/employees", headers=h,
                              json={"name": "A", "email": "a@esc.test", "role_id": None})).status_code == 403
    assert (await client.post("/api/staff/employees", headers=h,
                              json={"name": "B", "email": "b@esc.test", "role_id": team["full"].id})).status_code == 403
    assert (await client.post("/api/staff/invites", headers=h,
                              json={"email": "c@esc.test", "role_id": None})).status_code == 403
    # …but can add people with equal or less access.
    assert (await client.post("/api/staff/employees", headers=h,
                              json={"name": "D", "email": "d@esc.test",
                                    "role_id": team["tech_role"].id})).status_code == 201
    assert (await client.post("/api/staff/invites", headers=h,
                              json={"email": "e@esc.test", "role_id": team["manager_role"].id})).status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize("method, path, body", [
    ("patch", "/api/staff/employees/{id}", {"email": "attacker@esc.test"}),
    ("post", "/api/staff/employees/{id}/password", {"password": "hijacked-123"}),
    ("post", "/api/staff/employees/{id}/send-reset", None),
    ("post", "/api/staff/employees/{id}/reset-mfa", None),
    ("delete", "/api/staff/employees/{id}", None),
])
async def test_manager_cannot_take_over_owner(client, db, team, method, path, body):
    url = path.format(id=team["owner"].id)
    kw = {"headers": team["mh"]}
    if body is not None:
        kw["json"] = body
    r = await getattr(client, method)(url, **kw)
    assert r.status_code == 403
    await db.refresh(team["owner"])
    assert team["owner"].email != "attacker@esc.test" and team["owner"].active


@pytest.mark.asyncio
async def test_manager_can_still_manage_less_privileged_staff(client, team):
    h = team["mh"]
    tech_h = await _login(client, team["tech"].email, "pw-tech-12345")
    r = await client.post(f"/api/staff/employees/{team['tech'].id}/password", headers=h,
                          json={"password": "pw-new-12345"})
    assert r.status_code == 200
    # admin reset signs the old session out and the new password works
    assert (await client.get("/api/auth/me", headers=tech_h)).status_code == 401
    await _login(client, team["tech"].email, "pw-new-12345")
    assert (await client.patch(f"/api/staff/employees/{team['tech'].id}", headers=h,
                               json={"name": "Tech 2"})).status_code == 200


@pytest.mark.asyncio
async def test_admin_password_reset_needs_eight_chars(client, team):
    r = await client.post(f"/api/staff/employees/{team['tech'].id}/password", headers=team["owner_h"],
                          json={"password": "1234567"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_unknown_or_foreign_role_rejected(client, team):
    r = await client.post("/api/staff/employees", headers=team["owner_h"],
                          json={"name": "F", "email": "f@esc.test", "role_id": "not-a-role"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_owner_keeps_full_control(client, db, team):
    h = team["owner_h"]
    assert (await client.patch(f"/api/staff/employees/{team['mgr'].id}", headers=h,
                               json={"role_id": team["full"].id})).status_code == 200
    assert (await client.post("/api/staff/employees", headers=h,
                              json={"name": "G", "email": "g@esc.test", "role_id": None})).status_code == 201
    assert (await client.post(f"/api/staff/employees/{team['mgr'].id}/reset-mfa", headers=h)).status_code == 200


@pytest.mark.asyncio
async def test_manager_cannot_manage_role_that_can_do_more_in_any_area(client, db, team):
    """A Full access account may delete transactions while Manager may not. Managing that
    account (e.g. resetting its password) would let the manager act with that permission,
    so it is refused — owners can deny can_delete_transactions on the role to allow it."""
    org_id = team["owner"].org_id
    spec = Role(org_id=org_id, name="Full access (default)", permissions=dict(TEMPLATES["Full access"]))
    db.add(spec)
    await db.flush()
    other = Employee(org_id=org_id, location_id=team["owner"].location_id, role_id=spec.id, name="S",
                     email=f"spec-{uuid.uuid4().hex[:8]}@esc.test", password_hash=hash_password("pw-s-12345"),
                     active=True)
    db.add(other)
    await db.commit()
    r = await client.post(f"/api/staff/employees/{other.id}/password", headers=team["mh"],
                          json={"password": "pw-taken-12345"})
    assert r.status_code == 403
