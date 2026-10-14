"""Build Phase 4 (parity pack) + Phase 5 (RBAC v2)."""
import pytest


# ── P4: contact duplicate guards ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_dup_phone_guard(client, tenant):
    await client.put("/api/staff/company", json={"prohibit_dup_phone": True}, headers=tenant)
    a = await client.post("/api/clients", json={"name": "A", "phone": "555-1"}, headers=tenant)
    assert a.status_code in (200, 201)
    b = await client.post("/api/clients", json={"name": "B", "phone": "555-1"}, headers=tenant)
    assert b.status_code == 409
    # different phone is fine
    c = await client.post("/api/clients", json={"name": "C", "phone": "555-2"}, headers=tenant)
    assert c.status_code in (200, 201)


@pytest.mark.asyncio
async def test_dup_email_guard_off_by_default(client, tenant):
    # default off -> duplicates allowed
    await client.post("/api/clients", json={"name": "D", "email": "x@x.dev"}, headers=tenant)
    dup = await client.post("/api/clients", json={"name": "E", "email": "x@x.dev"}, headers=tenant)
    assert dup.status_code in (200, 201)


# ── P4: invoice backdating guard ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_backdating_setting_roundtrip(client, tenant):
    r = await client.put("/api/staff/company",
                         json={"prevent_backdating_invoices": True, "default_due_days": 30,
                               "warranty_count_from": "created", "asset_one_open_ticket": True,
                               "rounding_enabled": True, "currency_position": "after"}, headers=tenant)
    assert r.status_code == 200
    c = (await client.get("/api/staff/company", headers=tenant)).json()
    assert c["prevent_backdating_invoices"] is True and c["default_due_days"] == 30
    assert c["warranty_count_from"] == "created" and c["asset_one_open_ticket"] is True
    assert c["rounding_enabled"] is True and c["currency_position"] == "after"


# ── P4: commission-event extension ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_assignee_variant_commission_fires(client, tenant, db):
    """A line performed by tech B on an order assigned to A: A earns an
    assignee_labor commission, B earns labor_performed."""
    from sqlalchemy import select

    from backend.models import CommissionRule, Employee, EmployeeBalanceLedger
    from backend.services.auth import hash_password

    me = (await client.get("/api/auth/me", headers=tenant)).json()
    techB = Employee(org_id=me["org_id"], name="Tech B", email="tb@x.dev",
                     password_hash=hash_password("pw-123456789"), active=True)
    db.add(techB)
    db.add(CommissionRule(org_id=me["org_id"], employee_id=None, name="Perform 10%",
                          event="labor_performed", calc_type="percent",
                          percent_basis="line_total", amount=10.0))
    db.add(CommissionRule(org_id=me["org_id"], employee_id=None, name="Assignee 5%",
                          event="assignee_labor", calc_type="percent",
                          percent_basis="line_total", amount=5.0))
    await db.commit()
    await db.refresh(techB)

    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    term = await client.post("/api/statuses", json={
        "name": "Done AV", "key": "done-av", "status_group": "closed", "is_terminal": True}, headers=tenant)
    assert term.status_code == 201
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x",
         "assigned_to_id": me["id"]}, headers=tenant)).json()
    # line performed by tech B
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "custom", "description": "job",
        "qty": 1, "unit_price": 100.0, "technician_id": techB.id}, headers=tenant)
    closed = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done-av"}, headers=tenant)
    assert closed.status_code == 200, closed.text

    rows = (await db.execute(select(EmployeeBalanceLedger))).scalars().all()
    by = {(r.employee_id, r.event): r.amount for r in rows}
    assert by.get((techB.id, "labor_performed")) == pytest.approx(10.0)
    assert by.get((me["id"], "assignee_labor")) == pytest.approx(5.0)


# ── P5: RBAC v2 ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_permission_catalog(client, tenant):
    cat = (await client.get("/api/settings/rbac/catalog", headers=tenant)).json()
    assert "General" in cat["catalog"] and "Finance" in cat["catalog"]
    codes = {p["code"] for g in cat["catalog"].values() for p in g}
    assert "can_manage_settings" in codes and "can_view_purchase_price" in codes
    assert "Specialist" in cat["templates"]


@pytest.mark.asyncio
async def test_role_from_template_and_validation(client, tenant):
    r = await client.post("/api/settings/rbac/roles",
                         json={"name": "Front desk", "template": "Specialist"}, headers=tenant)
    assert r.status_code == 201
    perms = r.json()["permissions"]
    assert perms.get("can_manage_settings") is False       # denied by template
    assert perms.get("can_view_purchase_price") is False

    bad = await client.post("/api/settings/rbac/roles",
                           json={"name": "Bad", "permissions": {"nonsense_perm": False}}, headers=tenant)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_role_location_scope_roundtrip(client, tenant):
    r = await client.post("/api/settings/rbac/roles",
                         json={"name": "Loc A only", "location_scope": "selected",
                               "location_ids": ["locA"]}, headers=tenant)
    assert r.status_code == 201
    got = next(x for x in (await client.get("/api/settings/rbac/roles", headers=tenant)).json()["items"]
               if x["id"] == r.json()["id"])
    assert got["location_scope"] == "selected" and got["location_ids"] == ["locA"]


@pytest.mark.asyncio
async def test_legacy_flat_perms_still_enforced(client, tenant, db):
    """Deny-wins compatibility: a role with a flat False still blocks require_perm."""
    from backend.models import Role
    from backend.services.permissions import role_allows
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    role = Role(org_id=me["org_id"], name="Legacy", permissions={"can_manage_settings": False})
    db.add(role)
    await db.commit()
    await db.refresh(role)
    assert role_allows(role, "can_manage_settings") is False
    assert role_allows(role, "can_create_tickets") is True   # omitted => allowed
