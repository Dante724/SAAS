"""QA fix plan A (31 Oct 2026, 5-shop run): roles and permissions.

1. A role created with a partial permission map gets only what was sent (on top of its template).
2. The technician (Specialist/Partner) template keeps money to itself; PO cost is redacted.
3. A technician can't silently "edit" a line's cost price, or close a job that still owes money.
4. Invoicing from a job uses the invoice codes; done -> delivered isn't "editing a closed job".
5. A technician doesn't read the team list's contact details, invites or the role list.
"""
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


async def _role(client, H, body):
    r = await client.post("/api/settings/rbac/roles", json=body, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


# ── 1. new roles start from their template (or nothing), plus what was sent ─────────────────

def test_new_role_without_template_starts_with_nothing():
    perms = P.new_role_permissions(None, {"can_create_tickets": True, "can_view_cashflow": False})
    assert {c for c, v in perms.items() if v} == {"can_create_tickets"}
    assert set(perms) >= P.ALL_CODES


def test_new_role_from_template_keeps_the_template():
    perms = P.new_role_permissions("Specialist", {"can_view_reports": True})
    assert perms["can_create_tickets"] is True and perms["can_manage_roles"] is False
    assert perms["can_view_reports"] is True
    assert P.new_role_permissions("Full access") == {c: c not in P.OPT_IN for c in P.ALL_CODES}


@pytest.mark.asyncio
async def test_partial_map_through_both_endpoints_grants_only_what_was_sent(client):
    H = await _owner(client)
    sent = {"can_create_tickets": True, "can_add_products": True, "display_in_assignees": True}
    r = await client.post("/api/staff/roles", json={"name": "Tech", "permissions": sent}, headers=H)
    assert r.status_code == 201, r.text
    T = await _person(client, H, r.json()["id"])
    me = (await client.get("/api/auth/me", headers=T)).json()["permissions"]
    assert {c for c, v in me.items() if v} == set(sent)
    # the role editor path behaves the same
    role = await _role(client, H, {"name": "Desk", "permissions": sent})
    assert {c for c, v in role["permissions"].items() if v} == set(sent)


@pytest.mark.asyncio
async def test_existing_roles_keep_their_stored_permissions(client, db):
    H = await _owner(client)
    role = await _role(client, H, {"name": "Old", "template": "Full access"})
    T = await _person(client, H, role["id"])
    before = (await client.get("/api/auth/me", headers=T)).json()["permissions"]
    assert before["can_manage_settings"] is True
    r = await client.put(f"/api/settings/rbac/roles/{role['id']}",
                         json={"name": "Old", "permissions": {"can_view_cashflow": False}}, headers=H)
    assert r.status_code == 200, r.text
    after = (await client.get("/api/auth/me", headers=T)).json()["permissions"]
    assert after == {**before, "can_view_cashflow": False}


# ── 2. the technician template keeps money with the desk ────────────────────────────────────

async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


@pytest.mark.parametrize("template", ["Specialist", "Partner"])
def test_technician_templates_deny_billing_and_balances(template):
    perms = P.new_role_permissions(template)
    for code in ("can_view_balance", "can_view_cashbox", "can_view_invoices", "can_create_invoices",
                 "can_view_credit_notes", "can_create_credit_notes", "can_create_returns",
                 "can_approve_discount", "can_set_discount", "can_view_purchase_price"):
        assert perms[code] is False, code
    assert perms["can_create_tickets"] is True and perms["can_add_products"] is True


@pytest.mark.asyncio
async def test_specialist_sees_no_money_and_no_cost(client):
    H = await _owner(client)
    loc = await _loc(client, H)
    T = await _person(client, H, (await _role(client, H, {"name": "Tech", "template": "Specialist"}))["id"])
    dash = await client.get(f"/api/reports/company-dashboard?location_id={loc}", headers=T)
    assert dash.status_code == 200, dash.text
    assert not dash.json().get("total_money")
    assert (await client.get("/api/finance/invoices", headers=T)).status_code == 403
    assert (await client.get("/api/finance/credit-notes", headers=T)).status_code == 403

    sup = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=H)).json()
    part = (await client.post("/api/inventory/parts", json={
        "name": "Screen", "sku": f"SKU-{uuid.uuid4().hex[:6]}"}, headers=H)).json()
    po = (await client.post("/api/inventory/po", json={
        "supplier_id": sup["id"], "location_id": loc,
        "lines": [{"part_id": part["id"], "qty_ordered": 2, "unit_cost": 289}]}, headers=H)).json()
    rec = await client.post(f"/api/inventory/po/{po['po_id']}/receive", json={"lines": []}, headers=H)
    assert rec.status_code in (200, 201), rec.text

    owner_view = (await client.get(f"/api/inventory/po/{po['po_id']}", headers=H)).json()
    assert owner_view["total"] == 578 and owner_view["match"]["expected_cost"] == 578
    tech_view = (await client.get(f"/api/inventory/po/{po['po_id']}", headers=T)).json()
    assert tech_view["po_number"] == owner_view["po_number"]
    for k in ("total", "subtotal", "charges_total", "supplier_invoice_total"):
        assert k not in tech_view, k
    assert "expected_cost" not in tech_view["match"] and "status" in tech_view["match"]
    assert all("unit_cost" not in ln for ln in tech_view["lines"])
    listed = (await client.get("/api/inventory/po", headers=T)).json()
    assert listed["total"] >= 1                                  # the count stays
    assert all("total" not in p for p in listed["items"])
    stock = (await client.get(f"/api/inventory/parts/{part['id']}/stock", headers=T)).json()
    assert stock.get("total_value") is None and stock.get("unit_cost") is None
    assert (await client.get(f"/api/inventory/parts/{part['id']}/stock", headers=H)).json()["total_value"] == 578


# ── 3 + 4. job money: cost edits, closing unpaid, invoicing, done -> delivered ──────────────

async def _shop_job(client, H, price=100):
    loc = await _loc(client, H)
    c = (await client.post("/api/clients", json={"name": "Dana", "phone": "+15550101010"}, headers=H)).json()
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=H)
    acc = (await client.get("/api/cash/accounts", headers=H)).json()["items"][0]["id"]
    o = (await client.post("/api/orders", json={"client_id": c["id"], "location_id": loc, "complaint": "x"},
                           headers=H)).json()
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "kind": "custom", "line_type": "part", "description": "Screen", "qty": 1, "unit_price": price,
        "cost_price": 40}, headers=H)
    assert r.status_code in (200, 201), r.text
    o = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    return o, acc


@pytest.mark.asyncio
async def test_technician_cost_edit_is_refused_not_ignored(client):
    H = await _owner(client)
    o, _ = await _shop_job(client, H)
    line = o["lines"][0]
    T = await _person(client, H, (await _role(client, H, {"name": "Tech", "template": "Specialist"}))["id"])
    r = await client.patch(f"/api/orders/{o['id']}/lines/{line['id']}", json={"cost_price": 0.01}, headers=T)
    assert r.status_code == 403, r.text
    after = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["lines"][0]
    assert float(after["cost_price"]) == 40
    # a qty change that doesn't touch cost still works
    r = await client.patch(f"/api/orders/{o['id']}/lines/{line['id']}", json={"qty": 2}, headers=T)
    assert r.status_code == 200, r.text
    # sending the cost it already has is no change, so it isn't refused (the line editor never
    # sends cost on an edit; the add-line form only sends it to people who may set it)
    r = await client.patch(f"/api/orders/{o['id']}/lines/{line['id']}", json={"cost_price": 40, "qty": 1}, headers=T)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_closing_an_unpaid_job_needs_the_payments_right(client):
    H = await _owner(client)
    o, acc = await _shop_job(client, H)
    T = await _person(client, H, (await _role(client, H, {"name": "Tech", "template": "Specialist"}))["id"])
    assert (await client.patch(f"/api/orders/{o['id']}/status", json={"status": "ready"}, headers=T)).status_code == 200
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=T)
    assert r.status_code == 403, r.text
    msg = r.json()["detail"]
    assert msg.startswith(f"{o['order_number']} still has ") and "100.00 to pay. Take the payment first, " \
        "or ask someone who takes payments to close it." in msg and "can_" not in msg, msg
    assert (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["status"] == "ready"
    # the right to take client payments is what lets someone close it unpaid (no finance needed)
    C = await _person(client, H, (await _role(client, H, {"name": "Closer", "permissions": {
        "can_create_tickets": True, "can_make_client_payments": True}}))["id"], "Cleo")
    o2, _ = await _shop_job(client, H)
    r = await client.patch(f"/api/orders/{o2['id']}/status", json={"status": "done"}, headers=C)
    assert r.status_code == 200, r.text
    # paid up, the technician can close it
    r = await client.post(f"/api/cash/orders/{o['id']}/pay", json={"method": "cash", "account_id": acc}, headers=H)
    assert r.status_code == 200, r.text
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=T)
    assert r.status_code == 200, r.text


_DESK = {"can_create_tickets": True, "can_view_client_info": True, "can_view_item_prices": True,
         "can_view_finance": True, "can_view_invoices": True, "can_create_invoices": True,
         "can_view_invoices_tab": True, "can_create_returns": True}


@pytest.mark.asyncio
async def test_desk_hands_over_a_done_job_without_edit_closed(client):
    H = await _owner(client)
    o, acc = await _shop_job(client, H)
    D = await _person(client, H, (await _role(client, H, {"name": "Desk", "permissions": _DESK}))["id"], "Desk")
    assert (await client.post(f"/api/cash/orders/{o['id']}/pay", json={"method": "cash", "account_id": acc},
                              headers=D)).status_code == 200
    assert (await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=D)).status_code == 200
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "delivered"}, headers=D)
    assert r.status_code == 200, r.text
    # reopening is still editing a closed job
    r = await client.patch(f"/api/orders/{o['id']}/status", json={"status": "in_repair"}, headers=D)
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_invoicing_a_job_uses_the_invoice_rights(client):
    H = await _owner(client)
    o, _ = await _shop_job(client, H)
    no_finance = {k: v for k, v in _DESK.items() if k != "can_view_finance"}
    D = await _person(client, H, (await _role(client, H, {"name": "Biller", "permissions": no_finance}))["id"], "Bill")
    r = await client.post(f"/api/cash/orders/{o['id']}/invoices", json={}, headers=D)
    assert r.status_code == 201, r.text
    r = await client.get(f"/api/cash/orders/{o['id']}/invoices", headers=D)
    assert r.status_code == 200 and len(r.json()["invoices"]) == 1, r.text
    # and a technician (no invoice rights) still can't
    T = await _person(client, H, (await _role(client, H, {"name": "Tech", "template": "Specialist"}))["id"])
    assert (await client.post(f"/api/cash/orders/{o['id']}/invoices", json={}, headers=T)).status_code == 403
    assert (await client.get(f"/api/cash/orders/{o['id']}/invoices", headers=T)).status_code == 403
    # another shop can't list this job's payments
    H2 = await _owner(client)
    assert (await client.get(f"/api/cash/orders/{o['id']}/invoices", headers=H2)).status_code == 404


# ── 5. a technician doesn't read the team's contact details, invites or roles ───────────────

@pytest.mark.asyncio
async def test_technician_reads_names_not_contacts_invites_or_roles(client):
    H = await _owner(client)
    T = await _person(client, H, (await _role(client, H, {"name": "Tech", "template": "Specialist"}))["id"], "Tess")
    await client.post("/api/staff/invites", json={"email": f"new-{uuid.uuid4().hex[:6]}@test.dev", "name": "New"}, headers=H)
    team = await client.get("/api/staff/employees", headers=T)
    assert team.status_code == 200
    rows = team.json()
    others = [e for e in rows if e["name"] != "Tess"]
    me = next(e for e in rows if e["name"] == "Tess")
    assert others and all(e["email"] is None and e["phone"] is None for e in others)
    assert me["email"]                                           # their own row stays whole
    assert all(e["email"] for e in (await client.get("/api/staff/employees", headers=H)).json())
    assert (await client.get("/api/staff/invites", headers=T)).status_code == 403
    assert (await client.get("/api/staff/roles", headers=T)).status_code == 403
    assert (await client.get("/api/settings/rbac/roles", headers=T)).status_code == 403
    assert (await client.get("/api/staff/invites", headers=H)).json()["items"]
    assert (await client.get("/api/settings/rbac/roles", headers=H)).status_code == 200


# ── follow-up: every technician GET checked for cost and admin data ─────────────────────────
# A probe ran every GET route as a Specialist and searched the responses for the unit cost
# (289) and its multiples. These were the last ones that still gave the cost away.

@pytest.mark.asyncio
async def test_no_technician_response_carries_stock_cost(client):
    H = await _owner(client)
    loc = await _loc(client, H)
    loc2 = (await client.post("/api/staff/locations", json={"name": "B"}, headers=H)).json()["id"]
    T = await _person(client, H, (await _role(client, H, {"name": "Tech", "template": "Specialist"}))["id"])
    sup = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=H)).json()
    part = (await client.post("/api/inventory/parts", json={"name": "Crown", "sku": "CR-1", "cost_price": 289,
                                                             "sell_price": 499}, headers=H)).json()
    po = (await client.post("/api/inventory/po", json={"supplier_id": sup["id"], "location_id": loc,
          "lines": [{"part_id": part["id"], "qty_ordered": 2, "unit_cost": 289}]}, headers=H)).json()
    await client.post(f"/api/inventory/po/{po['po_id']}/receive", json={"lines": []}, headers=H)
    tr = (await client.post("/api/transfers", json={"from_location_id": loc, "to_location_id": loc2,
          "lines": [{"part_id": part["id"], "qty": 1}]}, headers=H)).json()
    await client.post("/api/pricing/types", json={"name": "Trade", "markup_pct": 50}, headers=H)
    urls = [f"/api/transfers/{tr['id']}", f"/api/inventory/parts/{part['id']}/batches",
            f"/api/pricing/parts/{part['id']}/prices", f"/api/suppliers/{sup['id']}/performance"]
    for url in urls:
        owner = await client.get(url, headers=H)
        assert owner.status_code == 200, (url, owner.text)
        assert any(n in owner.text for n in ("289", "578")), url           # the owner sees the cost
        tech = await client.get(url, headers=T)
        assert tech.status_code == 200, (url, tech.text)
        assert "289" not in tech.text and "578" not in tech.text, (url, tech.text)
    prices = (await client.get(f"/api/pricing/parts/{part['id']}/prices", headers=T)).json()
    assert "basis" not in prices and all("markup_pct" not in p for p in prices["prices"])


@pytest.mark.asyncio
async def test_technician_cannot_read_admin_settings_or_a_contacts_payments(client):
    H = await _owner(client)
    T = await _person(client, H, (await _role(client, H, {"name": "Tech", "template": "Specialist"}))["id"])
    cid = (await client.post("/api/clients", json={"name": "Dana"}, headers=H)).json()["id"]
    for url in ("/api/integrations/credentials", "/api/integrations/qbo/mappings", "/api/integrations/qbo/logs",
                "/api/webhooks/deliveries", "/api/settings/numbering", f"/api/clients/{cid}/payments"):
        assert (await client.get(url, headers=T)).status_code == 403, url
        assert (await client.get(url, headers=H)).status_code == 200, url


@pytest.mark.asyncio
async def test_a_full_discount_needs_approval_even_without_a_threshold(client):
    H = await _owner(client)
    o, _ = await _shop_job(client, H)
    line = o["lines"][0]
    perms = {**_DESK, "can_set_discount": True}               # may discount, may not approve
    D = await _person(client, H, (await _role(client, H, {"name": "Desk", "permissions": perms}))["id"], "Desk")
    r = await client.patch(f"/api/orders/{o['id']}/lines/{line['id']}", json={"discount": 100}, headers=D)
    assert r.status_code == 403, r.text
    r = await client.patch(f"/api/orders/{o['id']}/lines/{line['id']}", json={"discount": 20}, headers=D)
    assert r.status_code == 200, r.text
    r = await client.patch(f"/api/orders/{o['id']}/lines/{line['id']}", json={"discount": 100}, headers=H)
    assert r.status_code == 200, r.text                        # the owner can
