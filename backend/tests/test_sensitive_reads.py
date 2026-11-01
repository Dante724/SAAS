"""Security: reports, payroll data, client exports and webhook secrets respect role permissions.

Regressions for the audit findings where any logged-in employee could run/export every report
(payroll, profit, cash flow), read colleagues' wages and tax ids, export the client base and
read outbound webhook signing secrets — and where codes the API enforced (can_view_finance…)
couldn't be denied in the role editor because role validation rejected them.
"""
import uuid

import pytest
from sqlalchemy import select

from backend.models import Employee, Organisation, Role
from backend.services.auth import hash_password


async def _restricted(client, db, deny: dict):
    org = (await db.execute(select(Organisation))).scalars().first()
    owner = (await db.execute(select(Employee).where(Employee.org_id == org.id))).scalars().first()
    role = Role(org_id=org.id, name=f"R-{uuid.uuid4().hex[:4]}", permissions={k: False for k in deny})
    db.add(role)
    await db.flush()
    email = f"r-{uuid.uuid4().hex[:8]}@perm.test"
    emp = Employee(org_id=org.id, location_id=owner.location_id, role_id=role.id, name="R", email=email,
                   password_hash=hash_password("pw-r-123456"), active=True, wage_type="fixed", wage_value=1000)
    db.add(emp)
    await db.commit()
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-r-123456"})).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}, emp, owner


@pytest.mark.asyncio
async def test_reports_need_can_view_reports(client, tenant, db):
    h, _, _ = await _restricted(client, db, ["can_view_reports"])
    for path in ("/api/reports/run/wo_created", "/api/reports/export/wo_created",
                 "/api/reports/export-csv/wo_created"):
        assert (await client.get(path, headers=h)).status_code == 403, path
    r = await client.post("/api/custom-reports/run", headers=h, json={"config": {}})
    assert r.status_code == 403
    assert (await client.get("/api/reports/run/wo_created", headers=tenant)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("report, perm", [("payroll", "can_view_payroll"), ("wo_profit", "can_see_profit"),
                                          ("cash_flow", "can_view_cashflow"), ("total_money", "can_view_balance")])
async def test_sensitive_reports_need_their_own_permission(client, db, tenant, report, perm):
    h, _, _ = await _restricted(client, db, [perm])
    assert (await client.get(f"/api/reports/run/{report}", headers=h)).status_code == 403
    assert (await client.get("/api/reports/run/wo_created", headers=h)).status_code == 200


@pytest.mark.asyncio
async def test_staff_list_hides_colleagues_pay_without_payroll_permission(client, db, tenant):
    h, me, owner = await _restricted(client, db, ["can_view_payroll"])
    owner.wage_value, owner.tax_id = 5000, "TAX-123"
    await db.commit()
    rows = {e["id"]: e for e in (await client.get("/api/staff/employees", headers=h)).json()}
    assert rows[owner.id]["wage_value"] is None and rows[owner.id]["tax_id"] is None
    assert rows[me.id]["wage_value"] == 1000                     # own pay still visible
    full = {e["id"]: e for e in (await client.get("/api/staff/employees", headers=tenant)).json()}
    assert float(full[owner.id]["wage_value"]) == 5000


@pytest.mark.asyncio
async def test_saving_employee_form_cannot_wipe_hidden_pay(client, db, tenant):
    h, me, _ = await _restricted(client, db, ["can_view_payroll"])
    other = Employee(org_id=me.org_id, location_id=me.location_id, role_id=me.role_id, name="Peer",
                     email=f"p-{uuid.uuid4().hex[:6]}@perm.test", password_hash=hash_password("pw-p-123456"),
                     active=True, wage_type="fixed", wage_value=777)
    db.add(other)
    await db.commit()
    r = await client.patch(f"/api/staff/employees/{other.id}", headers=h,
                           json={"name": "Peer 2", "wage_value": None, "wage_type": None})
    assert r.status_code == 200
    await db.refresh(other)
    assert other.name == "Peer 2" and float(other.wage_value) == 777


@pytest.mark.asyncio
async def test_client_export_and_webhook_secrets_are_gated(client, db, tenant):
    h, _, _ = await _restricted(client, db, ["can_export_clients", "can_manage_settings"])
    assert (await client.get("/api/clients/export/csv", headers=h)).status_code == 403
    assert (await client.get("/api/webhooks", headers=h)).status_code == 403
    assert (await client.get("/api/clients/export/csv", headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_role_editor_accepts_previously_enforced_codes(client, tenant):
    r = await client.post("/api/settings/rbac/roles", headers=tenant, json={
        "name": "No finance", "permissions": {"can_view_finance": False, "can_delete_orders": False,
                                              "can_view_reports": False}})
    assert r.status_code in (200, 201), r.text
