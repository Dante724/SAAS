"""Full audit, 29 Oct 2026 — High findings H1–H5 and M1/M2 (docs/audits/2026-10-29-full-codebase-audit.md).

Each test replays the attack from the report and asserts it now fails without side effects.
"""
import uuid

import pytest
from sqlalchemy import func, select

from backend.models import Employee, Part, Role, SerializedItem, StockLevel, StockMovement
from backend.services.auth import hash_password


async def _register(client, name):
    r = await client.post("/api/auth/register", json={
        "org_name": name, "name": "Owner", "email": f"aud-{uuid.uuid4().hex[:8]}@t.dev",
        "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _part(client, H, **kw):
    r = await client.post("/api/catalog/products", json={"name": f"P {uuid.uuid4().hex[:6]}",
                                                         "sell_price": 50, "cost_price": 20, **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _on_hand(db, part_id):
    db.expire_all()
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
                                   .where(StockLevel.part_id == part_id))).scalar() or 0)


@pytest.fixture
async def shops(client):
    A = await _register(client, "Shop A")
    B = await _register(client, "Shop B")
    return A, B


# ── H1: client return with another shop's product ───────────────────────────
async def test_return_cannot_restock_another_shops_part(client, db, shops):
    A, B = shops
    pa = await _part(client, A)
    r = await client.post("/api/returns", json={"lines": [{"part_id": pa["id"], "qty": 10000, "restock": True}]},
                          headers=B)
    assert r.status_code in (403, 404, 422), r.text
    assert await _on_hand(db, pa["id"]) == 0
    moves = (await db.execute(select(func.count()).select_from(StockMovement)
                              .where(StockMovement.part_id == pa["id"]))).scalar()
    assert moves == 0


async def test_own_return_still_restocks(client, db, shops):
    A, _ = shops
    pa = await _part(client, A)
    r = await client.post("/api/returns", json={"lines": [{"part_id": pa["id"], "qty": 2, "restock": True}]},
                          headers=A)
    assert r.status_code == 201, r.text
    assert await _on_hand(db, pa["id"]) == 2


# ── H2: another shop's product on my job → PO → receive ─────────────────────
async def test_job_line_cannot_point_at_another_shops_part(client, db, shops):
    A, B = shops
    pa = await _part(client, A)
    o = (await client.post("/api/orders", json={"location_id": await _loc(client, B), "complaint": "x"},
                           headers=B)).json()
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "product", "ref_id": pa["id"], "description": "x", "qty": 1}, headers=B)
    assert r.status_code in (403, 404, 422), r.text
    p = (await db.execute(select(Part).where(Part.id == pa["id"]))).scalar_one()
    assert float(p.avg_cost or 0) in (0.0, 20.0)


async def test_new_job_cannot_carry_another_shops_part(client, db, shops):
    A, B = shops
    pa = await _part(client, A)
    r = await client.post("/api/orders", json={"location_id": await _loc(client, B), "complaint": "x", "lines": [
        {"line_type": "part", "kind": "product", "ref_id": pa["id"], "description": "x", "qty": 1}]}, headers=B)
    assert r.status_code in (403, 404, 422), r.text


async def test_job_line_cannot_point_at_another_shops_service(client, shops):
    A, B = shops
    svc = (await client.post("/api/catalog/services", json={"name": "Screen fit", "price": 80}, headers=A)).json()
    o = (await client.post("/api/orders", json={"location_id": await _loc(client, B), "complaint": "x"},
                           headers=B)).json()
    r = await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "ref_id": svc["id"], "description": "x", "qty": 1}, headers=B)
    assert r.status_code in (403, 404, 422), r.text


async def test_sale_cannot_sell_another_shops_part(client, shops):
    A, B = shops
    pa = await _part(client, A)
    r = await client.post("/api/sales", json={"lines": [{"kind": "product", "ref_id": pa["id"], "qty": 1,
                                                         "unit_price": 1, "description": "x"}]}, headers=B)
    assert r.status_code in (400, 403, 404, 422), r.text


async def test_receive_cannot_change_another_shops_part_even_with_a_stray_po_line(client, db, shops):
    """Defence in depth: even if a PO line already points at a foreign part (old data), receiving it
    must not touch that part's stock or cost."""
    from backend.models import POLine
    A, B = shops
    pa = await _part(client, A)
    pb = await _part(client, B)
    sup = (await client.post("/api/suppliers", json={"name": "Sup B"}, headers=B)).json()
    po = (await client.post("/api/inventory/po", json={
        "supplier_id": sup.get("id") or sup["supplier"]["id"], "location_id": await _loc(client, B),
        "lines": [{"part_id": pb["id"], "qty_ordered": 5, "unit_cost": 999}]}, headers=B)).json()
    line = (await db.execute(select(POLine).where(POLine.po_id == po["po_id"]))).scalar_one()
    line.part_id = pa["id"]          # simulate legacy cross-tenant data written before the guard
    await db.commit()
    r = await client.post(f"/api/inventory/po/{po['po_id']}/receive", json={}, headers=B)
    assert r.status_code in (403, 404, 409, 422), r.text
    assert await _on_hand(db, pa["id"]) == 0
    p = (await db.execute(select(Part).where(Part.id == pa["id"]))).scalar_one()
    assert float(p.avg_cost or 0) != 999


# ── H3: write-off / supplier return flips another shop's serial unit ────────
async def test_write_off_cannot_touch_another_shops_serial_unit(client, db, shops):
    A, B = shops
    pa = await _part(client, A, serialized=True)
    r = await client.post("/api/bins/batches", json={"part_id": pa["id"], "location_id": await _loc(client, A),
                                                     "unit_cost": 5, "qty": 1, "serials": ["SN-A-1"]}, headers=A)
    assert r.status_code == 201, r.text
    unit_id = (await db.execute(select(SerializedItem.id).where(SerializedItem.part_id == pa["id"]))).scalar_one()
    pb = await _part(client, B)
    await client.post("/api/returns", json={"lines": [{"part_id": pb["id"], "qty": 3, "restock": True}]}, headers=B)
    r = await client.post("/api/stock-docs/write-offs", json={
        "location_id": await _loc(client, B),
        "lines": [{"part_id": pb["id"], "qty": 1, "serialized_item_id": unit_id}]}, headers=B)
    db.expire_all()
    status = (await db.execute(select(SerializedItem.status).where(SerializedItem.id == unit_id))).scalar_one()
    assert status == "in_stock", r.text


# ── M2: transfer with another shop's product ────────────────────────────────
async def test_transfer_cannot_carry_another_shops_part(client, db, shops):
    A, B = shops
    pa = await _part(client, A)
    src = await _loc(client, B)
    dst = (await client.post("/api/staff/locations", json={"name": "Second"}, headers=B)).json()["id"]
    r = await client.post("/api/transfers", json={
        "from_location_id": src, "to_location_id": dst,
        "lines": [{"part_id": pa["id"], "qty": 1}]}, headers=B)
    assert r.status_code in (400, 403, 404, 409, 422), r.text
    assert await _on_hand(db, pa["id"]) == 0


# ── H4: a scoped manager can't give themselves "no role" (= unlimited) ──────
@pytest.fixture
async def scoped_manager(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    owner = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    # Every permission, but only their own orders and contacts, and only their own pay.
    from backend.services.permissions import ALL_CODES
    role = Role(org_id=me["org_id"], name="Scoped full", permissions={c: True for c in ALL_CODES},
                order_scope="own", contact_scope="own", payroll_scope="own")
    db.add(role)
    await db.flush()
    sfx = uuid.uuid4().hex[:8]
    mgr = Employee(org_id=me["org_id"], location_id=owner.location_id, role_id=role.id, name="Scoped",
                   email=f"scoped-{sfx}@esc.test", password_hash=hash_password("pw-mgr-12345"), active=True)
    db.add(mgr)
    await db.commit()
    tok = (await client.post("/api/auth/token", data={"username": mgr.email, "password": "pw-mgr-12345"})).json()
    return mgr, {"Authorization": f"Bearer {tok['access_token']}"}


async def test_scoped_manager_cannot_set_own_role_to_none(client, db, scoped_manager):
    mgr, H = scoped_manager
    mid = mgr.id
    r = await client.patch(f"/api/staff/employees/{mid}", json={"role_id": None}, headers=H)
    assert r.status_code == 403, r.text
    db.expire_all()
    assert (await db.execute(select(Employee.role_id).where(Employee.id == mid))).scalar_one() is not None


async def test_scoped_manager_cannot_invite_with_no_role(client, scoped_manager):
    _, H = scoped_manager
    r = await client.post("/api/staff/invites", json={"email": f"x-{uuid.uuid4().hex[:6]}@esc.test",
                                                       "name": "Alt", "role_id": None}, headers=H)
    assert r.status_code in (403, 422), r.text


async def test_scoped_manager_cannot_add_staff_with_no_role(client, scoped_manager):
    _, H = scoped_manager
    r = await client.post("/api/staff/employees", json={"name": "Alt", "email": f"alt-{uuid.uuid4().hex[:6]}@esc.test",
                                                         "password": "pw-test-12345", "role_id": None}, headers=H)
    assert r.status_code == 403, r.text


async def test_owner_can_still_give_no_role(client, tenant, db, scoped_manager):
    mgr, _ = scoped_manager
    r = await client.patch(f"/api/staff/employees/{mgr.id}", json={"role_id": None}, headers=tenant)
    assert r.status_code == 200, r.text


# ── H5: order total never goes below zero → no cash refund for a gift card ──
async def test_deleting_lines_after_a_gift_card_cannot_make_money(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "GC"}, headers=tenant)).json()["id"]
    code = (await client.post("/api/giftcards", json={"value": 100, "client_id": cid}, headers=tenant)).json()["code"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                           headers=tenant)).json()
    line = (await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "part", "kind": "custom", "description": "p", "qty": 1, "unit_price": 100},
        headers=tenant)).json()
    r = await client.post(f"/api/orders/{o['id']}/redeem-giftcard", json={"code": code, "amount": 100}, headers=tenant)
    assert r.status_code == 200, r.text
    r = await client.delete(f"/api/orders/{o['id']}/lines/{line['line_id']}", headers=tenant)
    assert r.status_code == 200, r.text
    order = (await client.get(f"/api/orders/{o['id']}", headers=tenant)).json()
    assert float(order["total"]) == 0
    # the parts of the total still add up to the total
    assert float(order.get("labour_total") or 0) + float(order.get("parts_total") or 0) == pytest.approx(0)
    ctx = (await client.get(f"/api/cash/orders/{o['id']}/payment-context", headers=tenant)).json()
    assert float(ctx["overpaid"]) == 0
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=tenant)
    acc = (await client.get("/api/cash/accounts", headers=tenant)).json()["items"][0]["id"]
    r = await client.post(f"/api/cash/orders/{o['id']}/refund", json={"amount": 100, "account_id": acc,
                                                                      "method": "cash"}, headers=tenant)
    assert r.status_code == 400, r.text


# ── M1: a file item can't carry a javascript: link onto the public page ─────
@pytest.mark.parametrize("bad", [
    "javascript:alert(document.domain)",
    "JaVaScRiPt:alert(1)",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
    "https://evil.example/x.exe",
])
async def test_feed_file_rejects_unsafe_links(client, tenant, bad):
    loc = await _loc(client, tenant)
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)).json()
    r = await client.post(f"/api/feed/order/{o['id']}", json={
        "item_type": "file", "media_data": bad, "media_name": "x", "visibility": "client"}, headers=tenant)
    assert r.status_code == 422, r.text


async def test_feed_file_accepts_a_real_file(client, tenant):
    loc = await _loc(client, tenant)
    o = (await client.post("/api/orders", json={"location_id": loc, "complaint": "x"}, headers=tenant)).json()
    r = await client.post(f"/api/feed/order/{o['id']}", json={
        "item_type": "file", "media_data": "data:application/pdf;base64,JVBERi0xLjQK", "media_name": "quote.pdf",
        "visibility": "client"}, headers=tenant)
    assert r.status_code == 201, r.text


# ── follow-ups from the security review ─────────────────────────────────────
@pytest.mark.parametrize("url,code", [("javascript:alert(1)", 422), ("https://g.page/r/abc", 200)])
async def test_review_link_must_be_a_web_address(client, tenant, url, code):
    r = await client.put("/api/feedback/settings", json={"google_review_url": url}, headers=tenant)
    assert r.status_code == code, r.text


async def test_cannot_delete_another_shops_row_even_if_loaded(client, db, shops):
    """The guard also covers deletes: a row of shop A deleted inside a shop-B request is refused."""
    from backend.db.tenancy import CrossTenantReference
    A, B = shops
    pa = await _part(client, A)
    me_b = (await client.get("/api/auth/me", headers=B)).json()
    part = (await db.execute(select(Part).where(Part.id == pa["id"]))).scalar_one()
    db.sync_session.info["tenant_org"] = me_b["org_id"]
    try:
        await db.delete(part)
        with pytest.raises(CrossTenantReference):
            await db.flush()
    finally:
        await db.rollback()
        db.sync_session.info.pop("tenant_org", None)
