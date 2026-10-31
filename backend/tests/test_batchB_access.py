"""Batch B: access rules match what the role editor promises.

- Invoice, report and analytics reads needed only a login.
- Refunds needed only "view finance"; bulk assign/invoice skipped the single-order rights.
- Line discounts and price changes needed nothing, so a technician could zero a job.
- Order/client feeds ignored "own orders only"; reports ignored a role's locations.
- The role editor showed codes an allow-list role doesn't hold as granted.
"""
import uuid

import pytest


async def _register(client):
    email = f"pb-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _as_role(client, H, **role):
    rid = (await client.post("/api/settings/rbac/roles", json={"name": f"R-{uuid.uuid4().hex[:4]}", **role}, headers=H))
    assert rid.status_code == 201, rid.text
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Tm", "email": email, "password": "pw-test-12345", "role_id": rid.json()["id"]}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _order_with_line(client, H):
    cid = (await client.post("/api/clients", json={"name": "Ravi"}, headers=H)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "complaint": "screen"}, headers=H)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Fix", "qty": 1, "unit_price": 100}, headers=H)
    assert r.status_code in (200, 201), r.text
    lid = (await client.get(f"/api/orders/{oid}", headers=H)).json()["lines"][-1]["id"]
    return oid, lid


@pytest.mark.asyncio
async def test_invoice_report_and_analytics_reads_need_permission(client):
    H = await _register(client)
    T = await _as_role(client, H, permissions={"can_view_invoices": False, "can_view_reports": False})
    assert (await client.get("/api/finance/invoices", headers=T)).status_code == 403
    assert (await client.get("/api/reports/insights/data", headers=T)).status_code == 403
    assert (await client.get("/api/analytics/business-insights", headers=T)).status_code == 403
    assert (await client.get("/api/finance/invoices", headers=H)).status_code == 200


@pytest.mark.asyncio
async def test_refunds_and_bulk_actions_need_their_own_rights(client):
    H = await _register(client)
    T = await _as_role(client, H, permissions={"can_create_returns": False, "can_assign_staff": False,
                                               "can_create_invoices": False})
    assert (await client.post("/api/returns/refunds", json={"amount": 10, "account_id": "x"}, headers=T)).status_code == 403
    oid, _ = await _order_with_line(client, H)
    for action in ("assign", "create_invoice"):
        r = await client.post("/api/orders/bulk", json={"order_ids": [oid], "action": action, "value": None}, headers=T)
        assert r.status_code == 403, (action, r.text)


@pytest.mark.asyncio
async def test_line_discount_and_price_changes_need_rights(client):
    H = await _register(client)
    T = await _as_role(client, H, permissions={"can_set_discount": False, "can_edit_sale_price": False})
    oid, lid = await _order_with_line(client, H)
    assert (await client.patch(f"/api/orders/{oid}/lines/{lid}", json={"discount": 100}, headers=T)).status_code == 403
    assert (await client.patch(f"/api/orders/{oid}/lines/{lid}", json={"unit_price": 0}, headers=T)).status_code == 403
    assert (await client.patch(f"/api/orders/{oid}/lines/{lid}", json={"qty": 2}, headers=T)).status_code == 200


@pytest.mark.asyncio
async def test_feed_honours_own_orders_scope(client):
    H = await _register(client)
    T = await _as_role(client, H, order_scope="own")
    oid, _ = await _order_with_line(client, H)
    me = (await client.get("/api/auth/me", headers=H)).json()["id"]
    # "own" still shows unassigned jobs (the pick-up pool); assign it to someone else
    r = await client.post("/api/orders/bulk", json={"order_ids": [oid], "action": "assign", "value": me}, headers=H)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/feed/order/{oid}", headers=T)).status_code == 403
    assert (await client.get(f"/api/feed/order/{oid}", headers=H)).status_code == 200


@pytest.mark.asyncio
async def test_reports_stay_inside_a_roles_locations(client):
    H = await _register(client)
    a = (await client.post("/api/staff/locations", json={"name": "North"}, headers=H)).json()["id"]
    b = (await client.post("/api/staff/locations", json={"name": "South"}, headers=H)).json()["id"]
    c = (await client.post("/api/staff/locations", json={"name": "East"}, headers=H)).json()["id"]
    T = await _as_role(client, H, location_scope="selected", location_ids=[a, b])
    assert (await client.get("/api/reports/company-dashboard", headers=T)).status_code == 403   # must pick one
    assert (await client.get(f"/api/reports/company-dashboard?location_id={c}", headers=T)).status_code == 403
    assert (await client.get(f"/api/reports/company-dashboard?location_id={a}", headers=T)).status_code == 200


@pytest.mark.asyncio
async def test_role_api_says_when_a_role_is_an_allow_list(client):
    H = await _register(client)
    roles = (await client.get("/api/settings/rbac/roles", headers=H)).json()
    items = roles["items"] if isinstance(roles, dict) else roles
    assert all("perms_explicit" in r for r in items)
