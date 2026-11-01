"""Old-format role permissions ({"finance": "none"} ...) are gone (security phase 4).

Enforcement only blocks a real permission code set to False, so a role saying
{"orders": "write", "finance": "none"} looked restricted but had full access. Covers:
- the lr144p4 data migration converts such roles to real-code denials, only ever restricting;
- /api/staff/roles (legacy endpoint) rejects unknown codes and non-boolean values, like rbac;
- the demo seed creates its roles with real codes.
"""
import importlib.util
import pathlib
import random
import uuid
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.models import Employee, Organisation, Role
from backend.services.auth import hash_password
from backend.services.permissions import ALL_CODES, TEMPLATES, permission_errors, role_allows

_MIG = (pathlib.Path(__file__).resolve().parents[1]
        / "migrations" / "versions" / "lr144p4_legacy_role_permissions.py")
_spec = importlib.util.spec_from_file_location("lr144p4", _MIG)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)

OLD_TECHNICIAN = {"orders": "write", "clients": "read", "inventory": "read", "finance": "none"}
FINANCE_ENDPOINTS = ["/api/cash/accounts", "/api/cash/total-money"]


# ── the mapping itself ─────────────────────────────────────────────

def test_mapping_uses_only_real_codes():
    assert mig.REAL_CODES <= ALL_CODES
    for area, (read_codes, none_codes) in mig.LEGACY_AREA_DENIALS.items():
        assert set(read_codes) | set(none_codes) <= mig.REAL_CODES, area


def test_old_technician_converts_to_finance_denials():
    new = mig.convert(OLD_TECHNICIAN)
    assert permission_errors(new) == []
    for code in ("can_view_finance", "can_view_cashflow", "can_view_balance", "can_delete_transactions",
                 "can_view_invoices", "can_view_payroll"):
        assert new[code] is False, code
    assert new["can_manage_inventory"] is False          # inventory "read"
    assert new["can_delete_clients"] is False            # clients "read"
    assert "can_view_client_info" not in new             # ... but may still view clients
    assert "can_create_tickets" not in new               # orders "write" denies nothing
    assert all(v is False for v in new.values())


def test_owner_role_and_valid_roles_are_left_alone():
    assert mig.convert({"all": "write"}) is None
    assert mig.convert({}) is None
    assert mig.convert(None) is None
    assert mig.convert({"can_view_cashflow": False, "can_create_tickets": True}) is None


def test_conversion_keeps_every_current_code():
    # A role saved through the current editor holds only catalog codes; none may be dropped.
    assert mig.REAL_CODES == ALL_CODES
    assert mig.convert({c: False for c in ALL_CODES}) is None


def test_conversion_is_idempotent():
    once = mig.convert(OLD_TECHNICIAN)
    assert mig.convert(once) is None


def test_conversion_only_ever_restricts():
    legacy_keys = list(mig.LEGACY_AREA_DENIALS) + ["all", "unknown_area"]
    values = ["write", "read", "none", True, False, None, "WRITE", "bogus", 0]
    codes = sorted(mig.REAL_CODES)
    rnd = random.Random(144)
    for _ in range(500):
        perms = {k: rnd.choice(values) for k in rnd.sample(legacy_keys, rnd.randint(0, 4))}
        perms.update({c: rnd.choice([True, False, "false", "yes", None]) for c in rnd.sample(codes, 3)})
        new = mig.convert(perms)
        before = SimpleNamespace(permissions=perms)
        after = SimpleNamespace(permissions=perms if new is None else new)
        for code in ALL_CODES:
            if not role_allows(before, code):
                assert not role_allows(after, code), (perms, code)


def test_stringly_denied_real_codes_become_false():
    assert mig.convert({"can_view_cashflow": "false", "can_see_profit": "yes"}) == {"can_view_cashflow": False}


# ── the migration against a database ───────────────────────────────

async def _employee_headers(client, db, perms):
    org = (await db.execute(select(Organisation))).scalars().first()
    owner = (await db.execute(select(Employee).where(Employee.org_id == org.id))).scalars().first()
    role = Role(org_id=org.id, name="technician", permissions=perms)
    db.add(role)
    await db.flush()
    email = f"tech-{uuid.uuid4().hex[:6]}@legacy.test"
    db.add(Employee(org_id=org.id, location_id=owner.location_id, role_id=role.id, name="Tech",
                    email=email, password_hash=hash_password("pw-tech-123456"), active=True))
    await db.commit()
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-tech-123456"})).json()
    return role.id, {"Authorization": f"Bearer {tok['access_token']}"}


async def _run_upgrade(db):
    conn = await db.connection()

    def _go(sync_conn):
        with Operations.context(MigrationContext.configure(sync_conn)):
            mig.upgrade()
    await conn.run_sync(_go)
    await db.commit()


@pytest.mark.asyncio
async def test_old_format_technician_is_denied_finance_after_migration(client, tenant, db):
    role_id, h = await _employee_headers(client, db, dict(OLD_TECHNICIAN))
    # The bug: before the migration the "finance: none" technician could use finance.
    for url in FINANCE_ENDPOINTS:
        assert (await client.get(url, headers=h)).status_code == 200, url

    await _run_upgrade(db)
    await _run_upgrade(db)   # idempotent

    db.expire_all()
    perms = (await db.execute(select(Role.permissions).where(Role.id == role_id))).scalar_one()
    assert permission_errors(perms) == []
    for url in FINANCE_ENDPOINTS:
        assert (await client.get(url, headers=h)).status_code == 403, url
    # Work-order access ("orders": "write") is untouched.
    assert (await client.get("/api/orders", headers=h)).status_code == 200


@pytest.mark.asyncio
async def test_migration_keeps_owner_role_full_access(client, tenant, db):
    # Registration now stores the owner role with every code explicit; the migration leaves it be.
    before = (await db.execute(select(Role.permissions))).scalars().all()
    await _run_upgrade(db)
    db.expire_all()
    assert (await db.execute(select(Role.permissions))).scalars().all() == before
    assert mig.convert({"all": "write"}) is None
    for url in FINANCE_ENDPOINTS:
        assert (await client.get(url, headers=tenant)).status_code == 200, url


# ── the legacy /api/staff/roles endpoint ───────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("perms", [
    {"finance": "none"},
    {"nonsense_perm": False},
    {"can_view_cashflow": "none"},
    {"can_view_cashflow": 0},
])
async def test_staff_roles_rejects_invalid_permissions(client, tenant, perms):
    r = await client.post("/api/staff/roles", json={"name": "Bad", "permissions": perms}, headers=tenant)
    assert r.status_code == 422, r.text
    ok = await client.post("/api/staff/roles", json={"name": "Ok", "permissions": {}}, headers=tenant)
    assert ok.status_code == 201
    r = await client.put(f"/api/staff/roles/{ok.json()['id']}", json={"name": "Ok", "permissions": perms},
                         headers=tenant)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_staff_roles_accepts_real_codes(client, tenant):
    r = await client.post("/api/staff/roles", json={
        "name": "Floor", "permissions": {"can_view_finance": False, "can_create_tickets": True}}, headers=tenant)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_rbac_roles_rejects_non_boolean_values(client, tenant):
    r = await client.post("/api/settings/rbac/roles", json={
        "name": "Bad", "permissions": {"can_view_cashflow": "none"}}, headers=tenant)
    assert r.status_code == 422


# ── demo seed ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_roles_use_real_codes(db, monkeypatch):
    import scripts.seed as seed
    monkeypatch.setattr(seed, "AsyncSessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    await seed.seed_demo_data()

    roles = {r.name: r.permissions for r in (await db.execute(select(Role))).scalars().all()}
    assert set(roles) >= {"admin", "technician"}
    for name, perms in roles.items():
        assert permission_errors(perms) == [], name
    assert roles["admin"] == TEMPLATES["Full access"]
    assert roles["technician"]["can_view_finance"] is False
    assert roles["technician"]["can_view_cashflow"] is False
    assert roles["technician"]["can_manage_roles"] is False
