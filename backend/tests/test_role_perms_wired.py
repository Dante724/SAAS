"""The permissions the role editor used to show as "coming soon" now do what they say.

One test per permission: a teammate whose role switches just that permission off is refused
(or, for the "see" permissions, doesn't get the data), while the owner still can.
"""
import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select


async def _register(client):
    email = f"rp-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    H = {"Authorization": f"Bearer {body['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    return H, body["org_id"], me["id"]


async def _denying(client, H, *codes):
    """A role with every permission on except `codes`."""
    r = await client.post("/api/settings/rbac/roles", json={
        "name": f"No {codes[0]} {uuid.uuid4().hex[:4]}", "permissions": {c: False for c in codes}}, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _teammate(client, H, role_id):
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Tech", "email": email, "password": "pw-test-12345", "role_id": role_id}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return r.json()["id"], {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _shop(client, *deny):
    """Owner headers, org id, and a teammate whose role denies `deny`."""
    H, org, owner_id = await _register(client)
    emp, T = await _teammate(client, H, await _denying(client, H, *deny))
    return H, T, org, owner_id, emp


async def _order(client, H, price=100):
    cid = (await client.post("/api/clients", json={"name": "Ravi"}, headers=H)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "complaint": "x", "lines": [
        {"line_type": "labour", "kind": "custom", "description": "Fix", "qty": 1, "unit_price": price}]}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _service(client, H, price=80, cost=20):
    r = await client.post("/api/catalog/services", json={"name": f"Svc {uuid.uuid4().hex[:4]}",
                                                        "price": price, "cost_price": cost}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _product(client, H, price=40):
    r = await client.post("/api/catalog/products", json={"name": f"Widget {uuid.uuid4().hex[:4]}",
                                                        "sell_price": price}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _till(client, H):
    r = await client.post("/api/cash/accounts", json={"name": "Till"}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _line(**kw):
    return {"line_type": "labour", "kind": "custom", "description": "Extra", "qty": 1, "unit_price": 10, **kw}


# ── General ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_can_set_discount_type(client):
    H, T, *_ = await _shop(client, "can_set_discount_type")
    oid = await _order(client, H)
    fixed = _line(discount=5, discount_type="fixed")
    assert (await client.post(f"/api/orders/{oid}/lines", json=fixed, headers=T)).status_code == 403
    assert (await client.post(f"/api/orders/{oid}/lines", json=_line(discount=5), headers=T)).status_code == 201
    assert (await client.post(f"/api/orders/{oid}/lines", json=fixed, headers=H)).status_code == 201


@pytest.mark.asyncio
async def test_can_set_cost_price(client):
    H, T, *_ = await _shop(client, "can_set_cost_price")
    oid = await _order(client, H)
    lid = (await client.get(f"/api/orders/{oid}", headers=H)).json()["lines"][0]["id"]
    assert (await client.patch(f"/api/orders/{oid}/lines/{lid}", json={"cost_price": 30}, headers=T)).status_code == 403
    assert (await client.patch(f"/api/orders/{oid}/lines/{lid}", json={"cost_price": 30}, headers=H)).status_code == 200
    # a new line keeps the catalog cost, whatever cost the teammate sent
    sid = await _service(client, H, price=80, cost=20)
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "service", "ref_id": sid, "description": "S", "unit_price": 80,
        "cost_price": 1}, headers=T)
    assert r.status_code == 201, r.text
    costs = [ln["cost_price"] for ln in (await client.get(f"/api/orders/{oid}", headers=H)).json()["lines"]
             if ln["ref_id"] == sid]
    assert costs == [20]


@pytest.mark.asyncio
async def test_display_in_managers(client):
    H, T, _org, owner_id, emp = await _shop(client, "display_in_managers")
    oid = await _order(client, H)
    assert (await client.patch(f"/api/orders/{oid}/assign", json={"manager_id": emp}, headers=H)).status_code == 422
    assert (await client.patch(f"/api/orders/{oid}/assign", json={"assigned_to_id": emp}, headers=H)).status_code == 200
    assert (await client.patch(f"/api/orders/{oid}/assign", json={"manager_id": owner_id}, headers=H)).status_code == 200
    staff = {e["id"]: e for e in (await client.get("/api/staff/employees", headers=H)).json()}
    assert staff[emp]["in_managers"] is False and staff[emp]["in_assignees"] is True
    assert staff[owner_id]["in_managers"] is True


@pytest.mark.asyncio
async def test_display_in_assignees(client):
    H, T, _org, owner_id, emp = await _shop(client, "display_in_assignees")
    oid = await _order(client, H)
    assert (await client.patch(f"/api/orders/{oid}/assign", json={"assigned_to_id": emp}, headers=H)).status_code == 422
    cid = (await client.post("/api/clients", json={"name": "B"}, headers=H)).json()["id"]
    assert (await client.post("/api/orders", json={"client_id": cid, "complaint": "x", "assigned_to_id": emp},
                              headers=H)).status_code == 422
    assert (await client.patch(f"/api/orders/{oid}/assign", json={"assigned_to_id": owner_id}, headers=H)).status_code == 200
    staff = {e["id"]: e for e in (await client.get("/api/staff/employees", headers=H)).json()}
    assert staff[emp]["in_assignees"] is False and staff[owner_id]["in_assignees"] is True


@pytest.mark.asyncio
async def test_can_see_asset_cost(client):
    H, T, *_ = await _shop(client, "can_see_asset_cost")
    r = await client.post("/api/assets", json={"owner_type": "company", "brand": "X", "purchase_price": 500}, headers=H)
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert "purchase_price" not in (await client.get(f"/api/assets/{aid}", headers=T)).json()
    assert all("purchase_price" not in a for a in (await client.get("/api/assets", headers=T)).json()["items"])
    # an edit by the teammate leaves the cost alone
    assert (await client.put(f"/api/assets/{aid}", json={"purchase_price": 1, "color": "red"}, headers=T)).status_code == 200
    assert (await client.get(f"/api/assets/{aid}", headers=H)).json()["purchase_price"] == 500


@pytest.mark.asyncio
async def test_can_view_own_compensation(client):
    H, _org, owner_id = await _register(client)
    sees, S = await _teammate(client, H, await _denying(client, H, "can_manage_payroll", "can_view_payroll"))
    blind, B = await _teammate(client, H, await _denying(
        client, H, "can_manage_payroll", "can_view_payroll", "can_view_own_compensation"))
    await client.put(f"/api/staff/employees/{blind}", json={"wage_type": "fixed", "wage_value": 900}, headers=H)

    assert (await client.get(f"/api/payroll/balance/{sees}", headers=S)).status_code == 200
    assert (await client.get(f"/api/payroll/balance/{blind}", headers=S)).status_code == 403   # not theirs
    assert (await client.get(f"/api/payroll/balance/{blind}", headers=B)).status_code == 403
    me = next(e for e in (await client.get("/api/staff/employees", headers=B)).json() if e["id"] == blind)
    assert me["wage_value"] is None
    assert (await client.get(f"/api/payroll/balance/{blind}", headers=H)).status_code == 200


# ── Workflows ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_can_create_estimates(client):
    H, T, *_ = await _shop(client, "can_create_estimates")
    est = {"complaint": "quote", "is_estimate": True}
    assert (await client.post("/api/orders", json=est, headers=T)).status_code == 403
    assert (await client.post("/api/orders", json={"complaint": "job"}, headers=T)).status_code == 201
    assert (await client.post("/api/orders", json=est, headers=H)).status_code == 201


@pytest.mark.asyncio
async def test_can_delete_tickets(client):
    H, T, *_ = await _shop(client, "can_delete_tickets")
    oid = await _order(client, H)
    assert (await client.delete(f"/api/orders/{oid}", headers=T)).status_code == 403
    r = await client.post("/api/orders/bulk", json={"action": "archive", "order_ids": [oid]}, headers=T)
    assert r.status_code == 403
    assert (await client.delete(f"/api/orders/{oid}", headers=H)).status_code == 200


@pytest.mark.asyncio
async def test_can_view_asset_info(client):
    H, T, *_ = await _shop(client, "can_view_asset_info")
    aid = (await client.post("/api/assets", json={"owner_type": "company", "brand": "X"}, headers=H)).json()["id"]
    assert (await client.get("/api/assets", headers=T)).status_code == 403
    assert (await client.get(f"/api/assets/{aid}", headers=T)).status_code == 403
    assert (await client.get(f"/api/assets/{aid}", headers=H)).status_code == 200


@pytest.mark.asyncio
async def test_can_view_item_prices(client):
    H, T, *_ = await _shop(client, "can_view_item_prices")
    oid = await _order(client, H)
    line = (await client.get(f"/api/orders/{oid}", headers=T)).json()["lines"][0]
    assert "unit_price" not in line and "total" not in line
    sid = await _service(client, H, price=80)
    hits = (await client.get("/api/catalog/search?type=service", headers=T)).json()["items"]
    assert hits and all("price" not in h for h in hits)
    # a pick without a visible price is billed at the catalog price
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "service", "ref_id": sid, "description": "S", "unit_price": 0}, headers=T)
    assert r.status_code == 201, r.text
    lines = (await client.get(f"/api/orders/{oid}", headers=H)).json()["lines"]
    assert [ln["unit_price"] for ln in lines if ln["ref_id"] == sid] == [80]
    assert lines[0]["unit_price"] == 100


@pytest.mark.asyncio
async def test_can_view_invoices_tab(client):
    H, T, *_ = await _shop(client, "can_view_invoices_tab")
    oid = await _order(client, H)
    assert (await client.get(f"/api/cash/orders/{oid}/invoices", headers=T)).status_code == 403
    assert (await client.get(f"/api/cash/orders/{oid}/invoices", headers=H)).status_code == 200


@pytest.mark.asyncio
async def test_can_edit_closed_ticket(client, db):
    from backend.models import Order, OrderStatus
    H, T, org, *_ = await _shop(client, "can_edit_closed_ticket")
    oid = await _order(client, H)
    db.add(OrderStatus(org_id=org, key="closed-rp", name="Closed", status_group="closed", is_terminal=True))
    order = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    order.status = "closed-rp"
    await db.commit()

    assert (await client.post(f"/api/orders/{oid}/lines", json=_line(), headers=T)).status_code == 403
    assert (await client.patch(f"/api/orders/{oid}/status", json={"status": "new"}, headers=T)).status_code == 403
    assert (await client.post(f"/api/orders/{oid}/lines", json=_line(), headers=H)).status_code == 201


@pytest.mark.asyncio
async def test_can_manage_shared_filters(client):
    H, T, *_ = await _shop(client, "can_manage_shared_filters")
    shared = {"name": "Mine", "params": {"status": "new"}, "is_shared": True}
    assert (await client.post("/api/orders/filters/saved", json=shared, headers=T)).status_code == 403
    assert (await client.post("/api/orders/filters/saved", json={**shared, "is_shared": False}, headers=T)).status_code == 201
    r = await client.post("/api/orders/filters/saved", json=shared, headers=H)
    assert r.status_code == 201
    assert (await client.delete(f"/api/orders/filters/saved/{r.json()['id']}", headers=T)).status_code == 403
    assert (await client.delete(f"/api/orders/filters/saved/{r.json()['id']}", headers=H)).status_code == 200


@pytest.mark.asyncio
async def test_can_add_custom_service(client):
    H, T, *_ = await _shop(client, "can_add_custom_service")
    oid = await _order(client, H)
    assert (await client.post(f"/api/orders/{oid}/lines", json=_line(), headers=T)).status_code == 403
    sid = await _service(client, H)
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "kind": "service", "ref_id": sid, "description": "S", "unit_price": 80}, headers=T)
    assert r.status_code == 201, r.text
    assert (await client.post(f"/api/orders/{oid}/lines", json=_line(), headers=H)).status_code == 201


@pytest.mark.asyncio
async def test_can_add_custom_product(client):
    H, T, *_ = await _shop(client, "can_add_custom_product")
    oid = await _order(client, H)
    part = _line(line_type="part")
    assert (await client.post(f"/api/orders/{oid}/lines", json=part, headers=T)).status_code == 403
    assert (await client.post(f"/api/orders/{oid}/lines", json=_line(), headers=T)).status_code == 201
    assert (await client.post(f"/api/orders/{oid}/lines", json=part, headers=H)).status_code == 201


@pytest.mark.asyncio
async def test_can_create_payment_links(client):
    H, T, *_ = await _shop(client, "can_create_payment_links")
    oid = await _order(client, H)
    assert (await client.post(f"/api/orders/{oid}/payment-link", headers=T)).status_code == 403
    assert (await client.post("/api/payments/checkout", json={"order_id": oid, "amount": 10}, headers=T)).status_code == 403
    assert (await client.post(f"/api/orders/{oid}/payment-link", headers=H)).status_code == 201


# ── Catalog ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_can_view_labors(client):
    H, T, *_ = await _shop(client, "can_view_labors")
    assert (await client.get("/api/catalog/services", headers=T)).status_code == 403
    assert (await client.get("/api/catalog/services", headers=H)).status_code == 200


@pytest.mark.asyncio
async def test_can_delete_products(client):
    H, T, *_ = await _shop(client, "can_delete_products")
    pid = await _product(client, H)
    body = {"ids": [pid], "action": "delete"}
    assert (await client.post("/api/catalog/products/bulk", json=body, headers=T)).status_code == 403
    assert (await client.post("/api/catalog/products/bulk", json={**body, "action": "price", "price_set": 5},
                              headers=T)).status_code == 200
    assert (await client.post("/api/catalog/products/bulk", json=body, headers=H)).status_code == 200


@pytest.mark.asyncio
async def test_can_track_serials(client):
    H, T, *_ = await _shop(client, "can_track_serials")
    pid = await _product(client, H)
    assert (await client.post(f"/api/catalog/products/{pid}/serialize", headers=T)).status_code == 403
    assert (await client.post(f"/api/catalog/products/{pid}/serialize", headers=H)).status_code == 200


@pytest.mark.asyncio
async def test_can_manage_bundles(client):
    H, T, *_ = await _shop(client, "can_manage_bundles")
    sid = await _service(client, H)
    kit = {"name": "Kit", "components": [{"ref_type": "service", "ref_id": sid, "qty": 1}]}
    assert (await client.post("/api/bundles", json=kit, headers=T)).status_code == 403
    r = await client.post("/api/bundles", json=kit, headers=H)
    assert r.status_code == 201, r.text
    assert (await client.delete(f"/api/bundles/{r.json()['id']}", headers=T)).status_code == 403


# ── Sales ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_can_sell_services(client):
    H, T, *_ = await _shop(client, "can_sell_services")
    sale = {"lines": [{"kind": "service", "description": "Diagnostic", "qty": 1, "unit_price": 50}]}
    assert (await client.post("/api/sales", json=sale, headers=T)).status_code == 403
    assert (await client.post("/api/sales", json=sale, headers=H)).status_code == 201


@pytest.mark.asyncio
async def test_can_export_sales(client):
    H, T, *_ = await _shop(client, "can_export_sales")
    for key in ("sold_products", "provided_services"):
        assert (await client.get(f"/api/reports/run/{key}", headers=T)).status_code == 200
        assert (await client.get(f"/api/reports/export-csv/{key}", headers=T)).status_code == 403
        assert (await client.get(f"/api/reports/export/{key}", headers=T)).status_code == 403
        assert (await client.get(f"/api/reports/export-csv/{key}", headers=H)).status_code == 200


# ── Invoices ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_can_delete_invoices(client):
    H, T, *_ = await _shop(client, "can_delete_invoices")
    oid = await _order(client, H)
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 100}, headers=H)).json()
    assert (await client.delete(f"/api/finance/invoices/{inv['id']}", headers=T)).status_code == 403
    r = await client.delete(f"/api/finance/invoices/{inv['id']}", headers=H)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    pay = {"amount": 10, "method": "cash", "account_id": await _till(client, H)}
    assert (await client.post(f"/api/finance/invoices/{inv['id']}/pay", json=pay, headers=H)).status_code == 409


@pytest.mark.asyncio
async def test_can_edit_issued_invoices(client, db):
    from backend.models import Invoice
    H, T, *_ = await _shop(client, "can_edit_issued_invoices")
    oid = await _order(client, H)
    iid = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 100}, headers=H)).json()["id"]
    edit = {"display_mode": "simplified"}
    assert (await client.patch(f"/api/finance/invoices/{iid}", json=edit, headers=T)).status_code == 200   # draft
    inv = (await db.execute(select(Invoice).where(Invoice.id == iid))).scalar_one()
    inv.status = "sent"
    await db.commit()
    assert (await client.patch(f"/api/finance/invoices/{iid}", json=edit, headers=T)).status_code == 403
    assert (await client.patch(f"/api/finance/invoices/{iid}", json=edit, headers=H)).status_code == 200


# ── Finance ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_can_create_incomes(client):
    H, T, *_ = await _shop(client, "can_create_incomes")
    acc = await _till(client, H)
    income = {"account_id": acc, "txn_type": "income", "amount": 20}
    assert (await client.post("/api/cash/transactions", json=income, headers=T)).status_code == 403
    assert (await client.post("/api/cash/transactions", json={**income, "txn_type": "expense"}, headers=T)).status_code == 201
    assert (await client.post("/api/cash/transactions", json=income, headers=H)).status_code == 201


@pytest.mark.asyncio
async def test_can_retroactive_cash(client):
    H, T, *_ = await _shop(client, "can_retroactive_cash")
    acc = await _till(client, H)
    back = {"account_id": acc, "txn_type": "income", "amount": 20,
            "actual_date": (datetime.utcnow() - timedelta(days=3)).isoformat()}
    assert (await client.post("/api/cash/transactions", json=back, headers=T)).status_code == 403
    assert (await client.post("/api/cash/transactions", json={**back, "actual_date": None}, headers=T)).status_code == 201
    assert (await client.post("/api/cash/transactions", json=back, headers=H)).status_code == 201


@pytest.mark.asyncio
async def test_can_view_cashflow_any_period(client):
    H, T, *_ = await _shop(client, "can_view_cashflow_any_period")
    acc = await _till(client, H)
    old = (datetime.utcnow() - timedelta(days=3)).isoformat()
    await client.post("/api/cash/transactions", json={"account_id": acc, "txn_type": "income", "amount": 7,
                                                      "actual_date": old}, headers=H)
    await client.post("/api/cash/transactions", json={"account_id": acc, "txn_type": "income", "amount": 9}, headers=H)
    seen = [t["amount"] for t in (await client.get("/api/cash/transactions", headers=T)).json()["items"]]
    assert seen == [9]
    owner = sorted(t["amount"] for t in (await client.get("/api/cash/transactions", headers=H)).json()["items"])
    assert owner == [7, 9]


# ── Payroll ───────────────────────────────────────────────────────

def _last_month():
    first = date.today().replace(day=1)
    end = first - timedelta(days=1)
    return end.replace(day=1).isoformat(), end.isoformat()


@pytest.mark.asyncio
async def test_can_accrue_payroll(client):
    H, T, _org, _owner, _emp = await _shop(client, "can_accrue_payroll")
    other, _ = await _teammate(client, H, await _denying(client, H, "can_send_sms"))
    ps, pe = _last_month()
    run = {"employee_id": other, "period_start": ps, "period_end": pe, "base_salary": 100}
    assert (await client.post("/api/payroll/run", json=run, headers=T)).status_code == 403
    assert (await client.post("/api/payroll/run-all", json={"period_start": ps, "period_end": pe}, headers=T)).status_code == 403
    assert (await client.post("/api/payroll/run", json=run, headers=H)).status_code == 201


@pytest.mark.asyncio
async def test_can_create_penalties(client):
    H, T, *_ = await _shop(client, "can_create_penalties")
    other, _ = await _teammate(client, H, await _denying(client, H, "can_send_sms"))
    adj = {"employee_id": other, "kind": "penalty", "amount": 15}
    assert (await client.post("/api/payroll/adjustment", json=adj, headers=T)).status_code == 403
    assert (await client.post("/api/payroll/adjustment", json={**adj, "kind": "bonus"}, headers=T)).status_code == 201
    ps, pe = _last_month()
    run = {"employee_id": other, "period_start": ps, "period_end": pe, "penalty": 5}
    assert (await client.post("/api/payroll/run", json=run, headers=T)).status_code == 403
    assert (await client.post("/api/payroll/adjustment", json=adj, headers=H)).status_code == 201


@pytest.mark.asyncio
async def test_can_create_bonuses(client):
    H, T, *_ = await _shop(client, "can_create_bonuses")
    other, _ = await _teammate(client, H, await _denying(client, H, "can_send_sms"))
    adj = {"employee_id": other, "kind": "bonus", "amount": 15}
    assert (await client.post("/api/payroll/adjustment", json=adj, headers=T)).status_code == 403
    assert (await client.post("/api/payroll/adjustment", json={**adj, "kind": "penalty"}, headers=T)).status_code == 201
    ps, pe = _last_month()
    run = {"employee_id": other, "period_start": ps, "period_end": pe, "bonus": 5}
    assert (await client.post("/api/payroll/run", json=run, headers=T)).status_code == 403
    assert (await client.post("/api/payroll/adjustment", json=adj, headers=H)).status_code == 201


@pytest.mark.asyncio
async def test_can_export_payroll(client):
    H, T, *_ = await _shop(client, "can_export_payroll")
    for key in ("payroll", "labor_cost"):
        assert (await client.get(f"/api/reports/run/{key}", headers=T)).status_code == 200
        assert (await client.get(f"/api/reports/export-csv/{key}", headers=T)).status_code == 403
        assert (await client.get(f"/api/reports/export-csv/{key}", headers=H)).status_code == 200
