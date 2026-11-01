"""Roles you can trust (phase 1): explicit allow-lists where new permissions start off,
existing roles unchanged by the snapshot, no self-widening, and the staff side of
recommendations (phone approval, approved items can't be deleted)."""
import uuid

import pytest
from sqlalchemy import select

from backend.models import Role
from backend.services import permissions as P


async def _owner(client):
    r = await client.post("/api/auth/register", json={"org_name": "Shop", "name": "Owner",
                                                      "email": f"o-{uuid.uuid4().hex[:8]}@test.dev", "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _person(client, H, role_id, name="Pat"):
    email = f"{name.lower()}-{uuid.uuid4().hex[:6]}@test.dev"
    r = await client.post("/api/staff/employees", json={"name": name, "email": email, "password": "pw-test-12345",
                                                         "role_id": role_id}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


def test_snapshot_keeps_what_a_legacy_role_grants():
    legacy = Role(name="old", permissions={"can_view_cashflow": False}, perms_explicit=False)
    before = {c: P.role_allows(legacy, c) for c in P.ALL_CODES}
    assert P.make_explicit(legacy) is True and legacy.perms_explicit is True
    assert {c: P.role_allows(legacy, c) for c in P.ALL_CODES} == before
    assert P.role_allows(legacy, "can_view_cashflow") is False
    assert P.role_allows(legacy, "can_override_reservation") is False      # opt-in stays off


def test_a_permission_added_later_is_off_for_explicit_roles():
    r = Role(name="tech", permissions=P.new_role_permissions(), perms_explicit=True)
    assert P.role_allows(r, "can_create_tickets") is True
    assert P.role_allows(r, "can_some_future_permission") is False
    legacy = Role(name="old", permissions={}, perms_explicit=False)
    assert P.role_allows(legacy, "can_some_future_permission") is True   # only until snapshotted
    assert P.role_allows(None, "anything") is True                       # the owner


def test_shop_creators_legacy_admin_role_keeps_everything():
    admin = Role(name="admin", permissions={"all": "write"}, perms_explicit=False)
    assert P.role_allows(admin, "can_override_reservation") is True
    P.make_explicit(admin)
    assert all(P.role_allows(admin, c) for c in P.ALL_CODES)


@pytest.mark.asyncio
async def test_new_role_and_me_list_every_permission(client):
    H = await _owner(client)
    r = await client.post("/api/settings/rbac/roles", json={"name": "Tech", "permissions": {"can_view_cashflow": False}}, headers=H)
    assert r.status_code == 201, r.text
    perms = r.json()["permissions"]
    assert set(perms) >= P.ALL_CODES and perms["can_view_cashflow"] is False and perms["can_create_tickets"] is True
    me = (await client.get("/api/auth/me", headers=await _person(client, H, r.json()["id"]))).json()
    assert set(me["permissions"]) == P.ALL_CODES and me["permissions"]["can_view_cashflow"] is False


@pytest.mark.asyncio
async def test_managers_cannot_widen_roles(client, db):
    H = await _owner(client)
    mgr_role = (await client.post("/api/settings/rbac/roles", json={"name": "Manager", "permissions": {"can_view_payroll": False}}, headers=H)).json()
    tech_role = (await client.post("/api/settings/rbac/roles", json={"name": "Tech", "permissions": {"can_view_payroll": False, "can_view_cashflow": False}}, headers=H)).json()
    M = await _person(client, H, mgr_role["id"], "Mgr")
    # can't edit their own role
    r = await client.put(f"/api/settings/rbac/roles/{mgr_role['id']}", json={"name": "Manager", "permissions": {"can_view_payroll": True}}, headers=M)
    assert r.status_code == 403
    # can't grant what they don't have
    r = await client.put(f"/api/settings/rbac/roles/{tech_role['id']}", json={"name": "Tech", "permissions": {"can_view_payroll": True}}, headers=M)
    assert r.status_code == 403 and "can_view_payroll" in r.text
    # can grant what they do have
    r = await client.put(f"/api/settings/rbac/roles/{tech_role['id']}", json={"name": "Tech", "permissions": {"can_view_cashflow": True}}, headers=M)
    assert r.status_code == 200 and r.json()["permissions"]["can_view_cashflow"] is True
    # a new role they create is capped at their own access
    r = await client.post("/api/staff/roles", json={"name": "Helper"}, headers=M)
    assert r.status_code == 201
    helper = (await db.execute(select(Role).where(Role.id == r.json()["id"]))).scalar_one()
    assert helper.permissions["can_view_payroll"] is False and helper.perms_explicit is True
    # the old role editor follows the same rules
    r = await client.put(f"/api/staff/roles/{tech_role['id']}", json={"name": "Tech", "permissions": {"can_view_payroll": True}}, headers=M)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_scoped_manager_cannot_open_up_scope(client):
    H = await _owner(client)
    mgr_role = (await client.post("/api/settings/rbac/roles", json={"name": "Lead", "order_scope": "own"}, headers=H)).json()
    tech_role = (await client.post("/api/settings/rbac/roles", json={"name": "Tech", "order_scope": "own"}, headers=H)).json()
    M = await _person(client, H, mgr_role["id"], "Lead")
    r = await client.put(f"/api/settings/rbac/roles/{tech_role['id']}", json={"name": "Tech", "order_scope": "all"}, headers=M)
    assert r.status_code == 403


async def _job_with_rec(client, H):
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Ravi"}, headers=H)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    rec = (await client.post(f"/api/orders/{o['id']}/recommendations", json={"title": "Battery", "unit_price": 80, "qty": 1, "kind": "service"}, headers=H)).json()
    return o, rec


@pytest.mark.asyncio
async def test_customer_said_yes_by_phone(client):
    H = await _owner(client)
    o, rec = await _job_with_rec(client, H)
    r = await client.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/approve-for-customer",
                          json={"how": "phone", "customer_name": "Ravi"}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved" and "by phone" in body["decided_by_name"] and "Ravi" in body["decided_by_name"]
    order = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert order["total"] == pytest.approx(80)
    again = await client.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/approve-for-customer", json={}, headers=H)
    assert again.status_code == 409
    assert (await client.delete(f"/api/orders/{o['id']}/recommendations/{rec['id']}", headers=H)).status_code == 409


@pytest.mark.asyncio
async def test_phone_approval_needs_the_permission(client):
    H = await _owner(client)
    role = (await client.post("/api/settings/rbac/roles", json={"name": "Desk", "permissions": {"can_approve_rec_for_customer": False}}, headers=H)).json()
    D = await _person(client, H, role["id"], "Desk")
    o, rec = await _job_with_rec(client, H)
    r = await client.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/approve-for-customer", json={}, headers=D)
    assert r.status_code == 403
