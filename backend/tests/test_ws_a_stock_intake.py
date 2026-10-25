"""Workstream A (Orderry June–July) — stock intake and catalog.

16b  bulk IMEI/serial entry at intake: live check, posting commit and PO receive.
19   active / written-off asset filter with counts.
16a  product type on posting import; preview flags rows that still need serials.
13b  import products into a purchase order (resolve-import: match or create).
3    custom product line with "Save to catalog" (SKU, barcode, permission).
21   asset search / identify by plate, IMEI or serial.
Every new endpoint has a permission-denied and a cross-tenant check.
"""
import uuid

import pytest

from backend.services import serial_intake


def _imei(prefix: str) -> str:
    """A valid 15-digit IMEI from a 14-digit prefix (Luhn check digit appended)."""
    assert len(prefix) == 14
    total = 0
    for i, ch in enumerate(reversed(prefix)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return prefix + str((10 - total % 10) % 10)


GOOD = [_imei(f"3567890123450{i}") for i in range(10)]


async def _register(client, name="Co"):
    email = f"wsa-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": name, "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}


async def _teammate(client, H, *deny):
    r = await client.post("/api/settings/rbac/roles", json={
        "name": f"No {deny[0]} {uuid.uuid4().hex[:4]}", "permissions": {c: False for c in deny}}, headers=H)
    assert r.status_code in (200, 201), r.text
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Tech", "email": email, "password": "pw-test-12345", "role_id": r.json()["id"]}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _loc(client, H):
    return (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]


async def _product(client, H, **kw):
    body = {"name": f"Phone {uuid.uuid4().hex[:6]}", "sell_price": 100, **kw}
    r = await client.post("/api/catalog/products", json=body, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _post(client, H, loc, part_id, qty, serials=None, **kw):
    row = {"part_id": part_id, "qty": qty, "purchase_price": 50, **kw}
    if serials is not None:
        row["serials"] = serials
    return await client.post("/api/inventory/postings/commit",
                             json={"location_id": loc, "rows": [row]}, headers=H)


# ── pure helpers ─────────────────────────────────────────────────────────────
def test_split_serials_handles_newlines_commas_and_spaces():
    raw = f"{GOOD[0]}\n{GOOD[1]}, {GOOD[2]} ;{GOOD[3]}\r\n\n  "
    assert serial_intake.split_serials(raw) == GOOD[:4]


def test_normalize_imei_and_serial():
    spaced = f"{GOOD[0][:2]} {GOOD[0][2:8]} {GOOD[0][8:]}"
    assert serial_intake.normalize(spaced) == (GOOD[0], "imei")
    assert serial_intake.normalize(" c02xk1 abc ") == ("C02XK1ABC", "serial")
    assert serial_intake.normalize("") == (None, "serial")


def test_parse_product_type():
    p = serial_intake.parse_product_type
    assert p("Serialized") == "serialized" and p("IMEI") == "serialized" and p("serial no.") == "serialized"
    assert p("Expiry-tracked") == "expiry" and p("expires") == "expiry"
    assert p("") == "standard" and p("whatever") == "standard" and p(None) == "standard"


# ── 16b: live check ─────────────────────────────────────────────────────────
async def test_check_flags_luhn_duplicate_in_stock_and_sold(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, serialized=True)
    assert (await _post(client, H, loc, p["id"], 2, [GOOD[0], GOOD[1]])).status_code == 201
    bad = GOOD[2][:-1] + str((int(GOOD[2][-1]) + 1) % 10)
    r = await client.post("/api/inventory/serials/check", json={
        "part_id": p["id"], "serials": [GOOD[0], bad, GOOD[3], GOOD[3], "SN-ABC"]}, headers=H)
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert rows[0]["status"] == "block" and rows[0]["reason"] == "in_stock"
    assert rows[1]["status"] == "warn" and "check digit" in rows[1]["message"].lower()
    assert rows[2]["status"] == "ok"
    assert rows[3]["status"] == "block" and rows[3]["reason"] == "duplicate"
    assert rows[4]["status"] == "ok" and rows[4]["kind"] == "serial"
    assert r.json()["summary"] == {"ok": 2, "warn": 1, "block": 2}


async def test_check_sold_unit_is_warning_only(client, db):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, serialized=True)
    assert (await _post(client, H, loc, p["id"], 1, [GOOD[4]])).status_code == 201
    # mark the unit sold directly (the sale flow is not under test here)
    from sqlalchemy import update

    from backend.models import SerializedItem
    await db.execute(update(SerializedItem).where(SerializedItem.serial_number == GOOD[4]).values(status="sold"))
    await db.commit()
    rows = (await client.post("/api/inventory/serials/check", json={"serials": [GOOD[4]]}, headers=H)).json()["rows"]
    assert rows[0]["status"] == "warn" and rows[0]["reason"] == "sold_before"
    # and it can come back in (a trade-in or return)
    assert (await _post(client, H, loc, p["id"], 1, [GOOD[4]])).status_code == 201


async def test_check_does_not_leak_other_shops_serials(client):
    A = await _register(client, "A")
    B = await _register(client, "B")
    pa = await _product(client, A, serialized=True)
    assert (await _post(client, A, await _loc(client, A), pa["id"], 1, [GOOD[5]])).status_code == 201
    rows = (await client.post("/api/inventory/serials/check", json={"serials": [GOOD[5]]}, headers=B)).json()["rows"]
    assert rows[0]["status"] == "ok" and not rows[0].get("part_name")
    # B can post the same IMEI into its own stock
    pb = await _product(client, B, serialized=True)
    assert (await _post(client, B, await _loc(client, B), pb["id"], 1, [GOOD[5]])).status_code == 201
    # B can't point the check at A's product
    r = await client.post("/api/inventory/serials/check", json={"part_id": pa["id"], "serials": [GOOD[6]]}, headers=B)
    assert r.status_code == 404


async def test_check_needs_inventory_permission_and_caps_input(client):
    H = await _register(client)
    T = await _teammate(client, H, "can_manage_inventory")
    r = await client.post("/api/inventory/serials/check", json={"serials": [GOOD[0]]}, headers=T)
    assert r.status_code == 403
    r = await client.post("/api/inventory/serials/check", json={"serials": [str(i) for i in range(501)]}, headers=H)
    assert r.status_code == 422
    r = await client.post("/api/inventory/serials/check", json={"serials": ["X" * 121]}, headers=H)
    assert r.status_code == 422


# ── 16b: posting commit ─────────────────────────────────────────────────────
async def test_posting_creates_one_unit_per_serial_normalized(client, db):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, serialized=True)
    spaced = f"{GOOD[0][:5]} {GOOD[0][5:]}"
    r = await _post(client, H, loc, p["id"], 3, [spaced, GOOD[1], "sn-77 x"])
    assert r.status_code == 201, r.text
    from sqlalchemy import select

    from backend.models import SerializedItem
    sns = sorted((await db.execute(select(SerializedItem.serial_number).where(
        SerializedItem.part_id == p["id"], SerializedItem.status == "in_stock"))).scalars().all())
    assert sns == sorted([GOOD[0], GOOD[1], "SN-77X"])


@pytest.mark.parametrize("serials,qty,needle", [
    ([GOOD[0], GOOD[0]], 2, "twice"),
    ([GOOD[0]], 2, "1 of 2"),
    (None, 2, "serial"),
    ([GOOD[0], GOOD[1]], 1.5, "whole"),
])
async def test_posting_blocks_bad_serial_sets(client, serials, qty, needle):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, serialized=True)
    r = await _post(client, H, loc, p["id"], qty, serials)
    assert r.status_code in (409, 422), r.text
    assert needle in r.text.lower()
    # nothing moved
    stock = (await client.get(f"/api/inventory/parts/{p['id']}/stock", headers=H)).json()
    assert stock["total_available"] == 0


async def test_posting_blocks_serial_already_in_stock_and_across_lines(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, serialized=True)
    assert (await _post(client, H, loc, p["id"], 1, [GOOD[7]])).status_code == 201
    r = await _post(client, H, loc, p["id"], 1, [GOOD[7]])
    assert r.status_code == 409 and "already in stock" in r.text.lower()
    q = await _product(client, H, serialized=True)
    r = await client.post("/api/inventory/postings/commit", json={"location_id": loc, "rows": [
        {"part_id": p["id"], "qty": 1, "serials": [GOOD[8]]},
        {"part_id": q["id"], "qty": 1, "serials": [GOOD[8]]}]}, headers=H)
    assert r.status_code == 409 and "twice" in r.text.lower()


async def test_non_serialized_posting_unchanged_and_ignores_empty_serials(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H)
    assert (await _post(client, H, loc, p["id"], 4)).status_code == 201
    assert (await _post(client, H, loc, p["id"], 1, [])).status_code == 201
    # serials on a product that isn't serial-tracked are refused, not silently dropped
    r = await _post(client, H, loc, p["id"], 1, [GOOD[9]])
    assert r.status_code == 422 and "serial" in r.text.lower()


async def test_commit_creating_products_needs_create_right_and_caps_serials(client):
    H = await _register(client)
    loc = await _loc(client, H)
    T = await _teammate(client, H, "can_create_products")
    r = await client.post("/api/inventory/postings/commit", json={"location_id": loc, "rows": [
        {"new_name": "Sneaky new part", "qty": 1}]}, headers=T)
    assert r.status_code == 403
    p = await _product(client, H)
    assert (await _post(client, T, loc, p["id"], 1)).status_code == 201     # existing products are fine
    q = await _product(client, H, serialized=True)
    assert (await _post(client, H, loc, q["id"], 1, ["X" * 121])).status_code == 422


async def test_bin_batch_intake_uses_the_same_serial_rules(client):
    A = await _register(client, "A")
    B = await _register(client, "B")
    loc = await _loc(client, A)
    p = await _product(client, A, serialized=True)
    body = {"part_id": p["id"], "location_id": loc, "unit_cost": 5, "qty": 2}
    r = await client.post("/api/bins/batches", json={**body, "serials": [GOOD[0], GOOD[0]]}, headers=A)
    assert r.status_code == 409
    r = await client.post("/api/bins/batches", json={**body, "serials": [GOOD[0], GOOD[1]]}, headers=A)
    assert r.status_code == 201, r.text
    r = await client.post("/api/bins/batches", json={**body, "qty": 1, "serials": [GOOD[1]]}, headers=A)
    assert r.status_code == 409
    # another shop can't add a batch to A's product
    r = await client.post("/api/bins/batches", json={**body, "location_id": await _loc(client, B),
                                                       "serials": [GOOD[2], GOOD[3]]}, headers=B)
    assert r.status_code == 404


# ── 16b: PO receive ─────────────────────────────────────────────────────────
async def _po(client, H, part_id, qty):
    sup = (await client.post("/api/suppliers", json={"name": f"Sup {uuid.uuid4().hex[:4]}"}, headers=H)).json()
    sid = sup.get("id") or sup["supplier"]["id"]
    r = await client.post("/api/inventory/po", json={
        "supplier_id": sid, "location_id": await _loc(client, H),
        "lines": [{"part_id": part_id, "qty_ordered": qty, "unit_cost": 40}]}, headers=H)
    assert r.status_code == 201, r.text
    po = (await client.get(f"/api/inventory/po/{r.json()['po_id']}", headers=H)).json()
    return po


async def test_po_receive_validates_serials_and_exposes_serialized_flag(client):
    H = await _register(client)
    p = await _product(client, H, serialized=True)
    po = await _po(client, H, p["id"], 3)
    line = po["lines"][0]
    assert line["serialized"] is True
    url = f"/api/inventory/po/{po['id']}/receive"
    # no serials for a serial-tracked product → refused
    r = await client.post(url, json={"lines": [{"po_line_id": line["id"], "qty": 2}]}, headers=H)
    assert r.status_code == 422 and "serial" in r.text.lower()
    # duplicate in the same delivery → refused
    r = await client.post(url, json={"lines": [{"po_line_id": line["id"], "qty": 2,
                                                 "serials": [GOOD[0], GOOD[0]]}]}, headers=H)
    assert r.status_code == 409
    # good partial receive
    r = await client.post(url, json={"lines": [{"po_line_id": line["id"], "qty": 2,
                                                 "serials": [GOOD[0], GOOD[1]]}]}, headers=H)
    assert r.status_code == 200, r.text
    # the same IMEI again on the remainder → already in stock
    r = await client.post(url, json={"lines": [{"po_line_id": line["id"], "qty": 1,
                                                 "serials": [GOOD[1]]}]}, headers=H)
    assert r.status_code == 409 and "already in stock" in r.text.lower()
    r = await client.post(url, json={"lines": [{"po_line_id": line["id"], "qty": 1,
                                                 "serials": [GOOD[2]]}]}, headers=H)
    assert r.status_code == 200, r.text
    stock = (await client.get(f"/api/inventory/parts/{p['id']}/stock", headers=H)).json()
    assert stock["total_available"] == 3


async def test_po_receive_all_without_lines_refuses_serialized(client):
    """'Receive everything' (no lines) can't land serial-tracked units without their serials."""
    H = await _register(client)
    p = await _product(client, H, serialized=True)
    po = await _po(client, H, p["id"], 1)
    r = await client.post(f"/api/inventory/po/{po['id']}/receive", json={}, headers=H)
    assert r.status_code == 422


async def test_po_partial_receive_leaves_unlisted_lines_alone(client):
    H = await _register(client)
    a = await _product(client, H)
    b = await _product(client, H)
    sup = (await client.post("/api/suppliers", json={"name": "Sup P"}, headers=H)).json()
    r = await client.post("/api/inventory/po", json={
        "supplier_id": sup.get("id") or sup["supplier"]["id"], "location_id": await _loc(client, H),
        "lines": [{"part_id": a["id"], "qty_ordered": 3, "unit_cost": 1},
                  {"part_id": b["id"], "qty_ordered": 4, "unit_cost": 1}]}, headers=H)
    po = (await client.get(f"/api/inventory/po/{r.json()['po_id']}", headers=H)).json()
    la = next(ln for ln in po["lines"] if ln["part_id"] == a["id"])
    r = await client.post(f"/api/inventory/po/{po['id']}/receive",
                          json={"lines": [{"po_line_id": la["id"], "qty": 1}]}, headers=H)
    assert r.status_code == 200, r.text
    after = (await client.get(f"/api/inventory/po/{po['id']}", headers=H)).json()
    got = {ln["part_id"]: ln["qty_received"] for ln in after["lines"]}
    assert got == {a["id"]: 1, b["id"]: 0}
    assert after["status"] == "partial"


# ── 16a: product type on import ─────────────────────────────────────────────
async def test_preview_reports_type_and_rows_needing_serials(client):
    H = await _register(client)
    p = await _product(client, H, serialized=True, sku="IP13-128")
    e = await _product(client, H, expiry_tracking=True, sku="GLUE-B7000")
    rows = (await client.post("/api/inventory/postings/preview", json={"rows": [
        {"code": "IP13-128", "qty": 3, "serials": [GOOD[0]]},
        {"code": "GLUE-B7000", "qty": 2},
        {"name": "Pixel 8 128GB", "qty": 2, "product_type": "IMEI"},
        {"name": "Thermal paste", "qty": 5, "product_type": "expiry"},
        {"name": "Screws", "qty": 100},
    ]}, headers=H)).json()["rows"]
    assert rows[0]["serialized"] is True and rows[0]["needs_serials"] == 2 and rows[0]["part_id"] == p["id"]
    assert rows[1]["expiry_tracking"] is True and rows[1]["needs_serials"] == 0 and rows[1]["part_id"] == e["id"]
    assert rows[2]["product_type"] == "serialized" and rows[2]["needs_serials"] == 2
    assert rows[3]["product_type"] == "expiry" and rows[3]["expiry_tracking"] is True
    assert rows[4]["product_type"] == "standard" and rows[4]["needs_serials"] == 0


async def test_commit_creates_typed_products_with_serials_and_expiry(client, db):
    H = await _register(client)
    loc = await _loc(client, H)
    r = await client.post("/api/inventory/postings/commit", json={"location_id": loc, "rows": [
        {"new_name": "Pixel 8 128GB", "new_sku": "PX8-128", "new_type": "serialized", "qty": 2,
         "purchase_price": 300, "serials": [GOOD[0], GOOD[1]]},
        {"new_name": "Thermal paste", "new_type": "expiry", "qty": 5, "purchase_price": 2,
         "expiry_date": "2027-03-31"},
    ]}, headers=H)
    assert r.status_code == 201, r.text
    items = (await client.get("/api/catalog/products?search=PX8-128", headers=H)).json()["items"]
    assert items[0]["serialized"] is True
    paste = (await client.get("/api/catalog/products?search=Thermal", headers=H)).json()["items"][0]
    assert paste["expiry_tracking"] is True
    from sqlalchemy import select

    from backend.models import StockItemBatch
    exp = (await db.execute(select(StockItemBatch.expiry_date).where(
        StockItemBatch.part_id == paste["id"]))).scalars().all()
    assert [str(x) for x in exp] == ["2027-03-31"]
    # a new serialized product without its serials is refused
    r = await client.post("/api/inventory/postings/commit", json={"location_id": loc, "rows": [
        {"new_name": "Pixel 9", "new_type": "serialized", "qty": 1}]}, headers=H)
    assert r.status_code == 422


# ── 13b: resolve-import for purchase orders ─────────────────────────────────
async def test_resolve_import_matches_and_creates(client):
    H = await _register(client)
    p = await _product(client, H, barcode="0123456789012")
    r = await client.post("/api/inventory/import/resolve-products", json={"rows": [
        {"part_id": p["id"]},
        {"new_name": "Battery S22", "new_sku": "BAT-S22", "new_barcode": "8800000000001", "product_type": "serialized"},
    ]}, headers=H)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["created"] == 1
    assert out["rows"][0] == {"part_id": p["id"], "created": False, "name": p["name"]}
    new_id = out["rows"][1]["part_id"]
    assert out["rows"][1]["created"] is True
    prod = (await client.get(f"/api/catalog/products/{new_id}", headers=H)).json()["product"]
    assert prod["sku"] == "BAT-S22" and prod["barcode"] == "8800000000001" and prod["serialized"] is True
    # preview then finds it by barcode
    prev = (await client.post("/api/inventory/postings/preview", json={"rows": [
        {"code": "8800000000001", "qty": 1}]}, headers=H)).json()["rows"]
    assert prev[0]["part_id"] == new_id
    # and a PO can be raised on it
    po = await _po(client, H, new_id, 4)
    assert po["lines"][0]["part_id"] == new_id


async def test_resolve_import_scoped_and_permissioned(client):
    A = await _register(client, "A")
    B = await _register(client, "B")
    pa = await _product(client, A)
    r = await client.post("/api/inventory/import/resolve-products", json={"rows": [{"part_id": pa["id"]}]}, headers=B)
    assert r.status_code == 422
    T = await _teammate(client, A, "can_create_products")
    r = await client.post("/api/inventory/import/resolve-products", json={"rows": [{"new_name": "X"}]}, headers=T)
    assert r.status_code == 403
    # matching only (nothing to create) is fine without the create right
    r = await client.post("/api/inventory/import/resolve-products", json={"rows": [{"part_id": pa["id"]}]}, headers=T)
    assert r.status_code == 200
    U = await _teammate(client, A, "can_manage_inventory")
    r = await client.post("/api/inventory/import/resolve-products", json={"rows": [{"part_id": pa["id"]}]}, headers=U)
    assert r.status_code == 403
    r = await client.post("/api/inventory/import/resolve-products", json={"rows": [{"sku": "nothing"}]}, headers=A)
    assert r.status_code == 422


# ── 3: custom product line saved to the catalog ─────────────────────────────
async def _order(client, H):
    c = (await client.post("/api/clients", json={"name": "Cust", "phone": "+15550000001"}, headers=H)).json()
    r = await client.post("/api/orders", json={"location_id": await _loc(client, H), "complaint": "x",
                                               "client_id": c["id"]}, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_custom_product_saved_to_catalog_with_sku_and_barcode(client):
    H = await _register(client)
    oid = await _order(client, H)
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "kind": "custom", "line_type": "part", "description": "Ribbon cable 30cm", "qty": 1,
        "unit_price": 12, "cost_price": 3, "save_to_catalog": True, "catalog_kind": "product",
        "catalog_sku": "RIB-30", "catalog_barcode": "4006381333931"}, headers=H)
    assert r.status_code in (200, 201), r.text
    items = (await client.get("/api/catalog/products?search=RIB-30", headers=H)).json()["items"]
    assert len(items) == 1 and items[0]["barcode"] == "4006381333931" and items[0]["name"] == "Ribbon cable 30cm"
    # the same SKU again points the user at the existing product
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "kind": "custom", "line_type": "part", "description": "Ribbon again", "qty": 1, "unit_price": 12,
        "save_to_catalog": True, "catalog_kind": "product", "catalog_sku": "rib-30"}, headers=H)
    assert r.status_code == 409 and "RIB-30" in r.text


async def test_custom_one_off_product_line_stays_out_of_catalog(client):
    H = await _register(client)
    oid = await _order(client, H)
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "kind": "custom", "line_type": "part", "description": "OneOffBracket", "qty": 2, "unit_price": 5}, headers=H)
    assert r.status_code in (200, 201), r.text
    items = (await client.get("/api/catalog/products?search=OneOffBracket", headers=H)).json()["items"]
    assert items == []


async def test_save_to_catalog_needs_create_right(client):
    H = await _register(client)
    oid = await _order(client, H)
    T = await _teammate(client, H, "can_create_products")
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "kind": "custom", "line_type": "part", "description": "Sneaky part", "qty": 1, "unit_price": 5,
        "save_to_catalog": True, "catalog_kind": "product"}, headers=T)
    assert r.status_code == 403
    S = await _teammate(client, H, "can_edit_labors")
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "kind": "custom", "line_type": "fee", "description": "Sneaky service", "qty": 1, "unit_price": 5,
        "save_to_catalog": True, "catalog_kind": "service"}, headers=S)
    assert r.status_code == 403


# ── 19: active / written-off filter ─────────────────────────────────────────
async def _asset(client, H, **kw):
    r = await client.post("/api/assets", json={"owner_type": "company", "brand": "Apple", "model": "iPhone", **kw},
                          headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def test_asset_state_filter_with_counts(client):
    H = await _register(client)
    a1 = await _asset(client, H, serial="S-1")
    await _asset(client, H, serial="S-2")
    a3 = await _asset(client, H, serial="S-3")
    for a in (a1, a3):
        assert (await client.post(f"/api/assets/{a['id']}/write-off", json={}, headers=H)).status_code == 201
    r = (await client.get("/api/assets?state=active", headers=H)).json()
    assert r["total"] == 1 and r["counts"] == {"active": 1, "written_off": 2, "all": 3}
    r = (await client.get("/api/assets?state=written_off", headers=H)).json()
    assert {x["id"] for x in r["items"]} == {a1["id"], a3["id"]}
    r = (await client.get("/api/assets", headers=H)).json()
    assert r["total"] == 3          # no state → everything, as before
    # counts follow the search but not the state
    r = (await client.get("/api/assets?state=active&search=S-3", headers=H)).json()
    assert r["total"] == 0 and r["counts"] == {"active": 0, "written_off": 1, "all": 1}
    assert (await client.get("/api/assets?state=bogus", headers=H)).status_code == 422


async def test_asset_counts_are_per_shop(client):
    A = await _register(client, "A")
    B = await _register(client, "B")
    await _asset(client, A, serial="A-1")
    r = (await client.get("/api/assets?state=active", headers=B)).json()
    assert r["counts"] == {"active": 0, "written_off": 0, "all": 0}


# ── 21: search by plate, IMEI, serial ───────────────────────────────────────
async def test_asset_search_by_plate_imei_vin_and_serial(client):
    H = await _register(client)
    car = await _asset(client, H, asset_type="Vehicle", brand="Ford", model="Focus",
                       vin="1HGCM82633A004352", custom_data={"plate": "AB12 CDE"})
    van = await _asset(client, H, asset_type="Vehicle", brand="Ford", model="Transit",
                       custom_data={"number_plate": "XY99 ZZZ"})
    phone = await _asset(client, H, imei=GOOD[0])
    sn = await _asset(client, H, serial="C02XK1ABC")

    async def ids(q):
        from urllib.parse import quote
        return {x["id"] for x in (await client.get(f"/api/assets?search={quote(q)}", headers=H)).json()["items"]}

    assert await ids("ab12cde") == {car["id"]}
    assert await ids("AB12 CDE") == {car["id"]}
    assert await ids("xy99") == {van["id"]}
    assert await ids(f"{GOOD[0][:5]} {GOOD[0][5:]}") == {phone["id"]}
    assert await ids("1HGCM826") == {car["id"]}
    assert await ids("c02xk1") == {sn["id"]}
    # LIKE wildcards are literal
    assert await ids("%") == set()
    assert await ids("_") == set()


async def test_identify_finds_vehicle_by_plate_only_in_own_shop(client):
    A = await _register(client, "A")
    B = await _register(client, "B")
    car = await _asset(client, A, asset_type="Vehicle", custom_data={"plate_registration": "LM05 PQR"})
    r = (await client.post("/api/assets/identify", json={"value": "lm05pqr"}, headers=A)).json()
    assert [m["id"] for m in r["matches"]] == [car["id"]]
    assert r["matches"][0]["matched_on"] == "plate"
    r = (await client.post("/api/assets/identify", json={"value": "lm05pqr"}, headers=B)).json()
    assert r["matches"] == []
