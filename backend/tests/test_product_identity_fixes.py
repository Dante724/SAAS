"""Product data / codes fixes.

1  Generating a QR / barcode never replaces a manufacturer barcode — it is added as an extra one.
2  Product photo: type + size checked; lists carry a small thumbnail, not the full photo.
3  Name / SKU / code / barcode are trimmed, so " abc-1 " and "ABC-1" can't both be saved.
4  Archiving a product with stock, or on an open job / estimate / PO, is refused with the reason.
5  Blank name and negative prices / min stock are refused.
6  Excel import: numeric barcodes keep their leading zeros, check digits are verified, every bad
   row is reported with its row number and the good rows still import.
7  repair_price is saved (blank = same as store price).
8  Labels: {Barcode} in the product's own symbology, {QR Code} = the scannable code,
   {Product Description} = the description, {Price Without Taxes} net of an included tax.
9  /catalog/code.svg is privately cached and its data length is capped.
"""
import base64
import io
import uuid

import openpyxl
import pytest


async def _register(client):
    email = f"pif-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["org_id"]


async def _product(client, H, **kw):
    r = await client.post("/api/catalog/products", json={"name": f"P {uuid.uuid4().hex[:6]}", **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def _sheet(*rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return {"file": ("p.xlsx", buf.getvalue(), "application/octet-stream")}


def _jpeg_data_url(size=64) -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (size, size), (200, 30, 30)).save(buf, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ── 1 ──────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_generate_keeps_the_manufacturer_barcode(client):
    H, _ = await _register(client)
    p = await _product(client, H, barcode="5901234123457", barcode_type="ean13")
    g = await client.post(f"/api/catalog/products/{p['id']}/barcode/generate?type=qr", headers=H)
    assert g.status_code == 200, g.text
    body = g.json()
    assert body["is_main"] is False and body["barcode"] != "5901234123457"
    got = (await client.get(f"/api/catalog/products/{p['id']}", headers=H)).json()["product"]
    assert got["barcode"] == "5901234123457" and got["barcode_type"] == "ean13"
    s = await client.get("/api/inventory/scan", params={"code": "5901234123457"}, headers=H)
    assert s.status_code == 200 and s.json()["product"]["id"] == p["id"]
    s = await client.get("/api/inventory/scan", params={"code": body["barcode"]}, headers=H)
    assert s.status_code == 200 and s.json()["product"]["id"] == p["id"]
    codes = (await client.get(f"/api/catalog/products/{p['id']}/barcodes", headers=H)).json()["items"]
    assert {c["barcode"] for c in codes} == {"5901234123457", body["barcode"]}


@pytest.mark.asyncio
async def test_generate_on_a_product_without_barcode_sets_the_main_one(client):
    H, _ = await _register(client)
    p = await _product(client, H)
    g = (await client.post(f"/api/catalog/products/{p['id']}/barcode/generate?type=ean13", headers=H)).json()
    assert g["is_main"] is True
    got = (await client.get(f"/api/catalog/products/{p['id']}", headers=H)).json()["product"]
    assert got["barcode"] == g["barcode"]


# ── 2 ──────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_photo_type_and_size_are_checked(client):
    H, _ = await _register(client)
    r = await client.post("/api/catalog/products", json={"name": "Big", "image_data": "x" * 3_000_000}, headers=H)
    assert r.status_code in (413, 422), r.status_code
    r = await client.post("/api/catalog/products", json={"name": "Svg", "image_data":
                          "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode()}, headers=H)
    assert r.status_code == 422
    big = "data:image/jpeg;base64," + base64.b64encode(b"\xff" * 600_000).decode()
    r = await client.post("/api/catalog/products", json={"name": "Huge", "image_data": big}, headers=H)
    assert r.status_code == 413
    ok = _jpeg_data_url()
    r = await client.post("/api/catalog/products", json={"name": "Pic", "image_data": ok}, headers=H)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    # the single product carries the photo; the list carries a small thumbnail + has_image
    assert (await client.get(f"/api/catalog/products/{pid}", headers=H)).json()["product"]["image_data"] == ok
    item = next(i for i in (await client.get("/api/catalog/products", headers=H)).json()["items"] if i["id"] == pid)
    assert item["has_image"] is True
    assert item["image_data"] and item["image_data"].startswith("data:image/") and len(item["image_data"]) < 20_000
    # an edit can clear it
    r = await client.put(f"/api/catalog/products/{pid}", json={"name": "Pic", "image_data": None}, headers=H)
    assert r.status_code == 200 and r.json()["image_data"] is None


@pytest.mark.asyncio
async def test_list_drops_a_large_photo(client, db):
    """A photo saved before the limit existed still doesn't ride along on every list row."""
    H, _ = await _register(client)
    from PIL import Image
    buf = io.BytesIO()
    Image.effect_noise((900, 900), 80).convert("RGB").save(buf, "JPEG", quality=95)
    big = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    p = await _product(client, H)
    from backend.models import Part
    from sqlalchemy import update
    await db.execute(update(Part).where(Part.id == p["id"]).values(image_data=big))
    await db.commit()
    item = next(i for i in (await client.get("/api/catalog/products", headers=H)).json()["items"] if i["id"] == p["id"])
    assert item["has_image"] is True and len(item["image_data"] or "") < 20_000


# ── 3 ──────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_codes_and_name_are_trimmed(client):
    H, _ = await _register(client)
    r = await client.post("/api/catalog/products", json={"name": "  A  ", "sku": " abc-1 ", "code": " C1 ",
                                                         "barcode": " 5901234123457 ", "barcode_type": "ean13"},
                          headers=H)
    assert r.status_code == 201, r.text
    p = r.json()
    assert (p["name"], p["sku"], p["code"], p["barcode"]) == ("A", "abc-1", "C1", "5901234123457")
    r2 = await client.post("/api/catalog/products", json={"name": "B", "sku": "ABC-1"}, headers=H)
    assert r2.status_code == 409
    r3 = await client.put(f"/api/catalog/products/{p['id']}", json={"name": "A", "sku": "  abc-2  "}, headers=H)
    assert r3.status_code == 200 and r3.json()["sku"] == "abc-2"
    s = await client.get("/api/inventory/scan", params={"code": "ABC-2"}, headers=H)
    assert s.status_code == 200 and s.json()["product"]["id"] == p["id"]


# ── 4 ──────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_archive_is_blocked_by_stock_jobs_and_pos(client, db):
    H, org = await _register(client)
    stocked = await _product(client, H, name="Stocked")
    on_job = await _product(client, H, name="On job")
    on_po = await _product(client, H, name="On PO")
    free = await _product(client, H, name="Free")
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    a = await client.post(f"/api/inventory/parts/{stocked['id']}/adjust",
                          json={"location_id": loc, "qty": 4, "movement_type": "receive"}, headers=H)
    assert a.status_code in (200, 201), a.text
    from backend.models import Order, OrderLine, POLine, PurchaseOrder, Supplier
    o = Order(org_id=org, order_number="WO-T1", location_id=loc, status="new")
    db.add(o); await db.flush()
    db.add(OrderLine(order_id=o.id, line_type="part", kind="product", ref_id=on_job["id"], description="x"))
    sup = Supplier(org_id=org, name="Sup")
    db.add(sup); await db.flush()
    po = PurchaseOrder(org_id=org, supplier_id=sup.id, location_id=loc, po_number="PO-T1", status="ordered")
    db.add(po); await db.flush()
    db.add(POLine(po_id=po.id, part_id=on_po["id"], qty_ordered=2))
    await db.commit()

    ids = [stocked["id"], on_job["id"], on_po["id"], free["id"]]
    r = await client.post("/api/catalog/products/bulk", json={"ids": ids, "action": "delete"}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    reasons = {b["id"]: b["reason"] for b in body["blocked_items"]}
    assert set(reasons) == {stocked["id"], on_job["id"], on_po["id"]}
    assert "stock" in reasons[stocked["id"]]
    assert "WO-T1" in reasons[on_job["id"]]
    assert "PO-T1" in reasons[on_po["id"]]
    assert len(body["blocked"]) == 3 and all(isinstance(x, str) for x in body["blocked"])
    left = {i["id"] for i in (await client.get("/api/catalog/products", headers=H)).json()["items"]}
    assert free["id"] not in left and stocked["id"] in left


# ── 5 ──────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{"name": "   "}, {"name": "X", "sell_price": -5}, {"name": "X", "cost_price": -1},
                                 {"name": "X", "min_stock": -3}, {"name": "X", "repair_price": -1}])
async def test_blank_name_and_negatives_are_refused(client, bad):
    H, _ = await _register(client)
    r = await client.post("/api/catalog/products", json=bad, headers=H)
    assert r.status_code == 422, (bad, r.text)


# ── 6 ──────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_import_keeps_leading_zeros_and_sets_the_type(client):
    H, _ = await _register(client)
    r = await client.post("/api/catalog/products/import", headers=H, files=_sheet(
        ["Name", "SKU", "Barcode"], ["U1", "U1", 12345678905], ["E1", "E1", 5901234123457], ["C1", "C1", "ABC-9"]))
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 3 and r.json()["errors"] == []
    items = {i["name"]: i for i in (await client.get("/api/catalog/products", headers=H)).json()["items"]}
    assert (items["U1"]["barcode"], items["U1"]["barcode_type"]) == ("012345678905", "upca")
    assert (items["E1"]["barcode"], items["E1"]["barcode_type"]) == ("5901234123457", "ean13")
    assert items["C1"]["barcode_type"] == "code128"
    s = await client.get("/api/inventory/scan", params={"code": "012345678905"}, headers=H)
    assert s.status_code == 200 and s.json()["product"]["name"] == "U1"


@pytest.mark.asyncio
async def test_import_reports_bad_rows_and_keeps_good_ones(client):
    H, _ = await _register(client)
    await _product(client, H, name="Existing", sku="TAKEN")
    r = await client.post("/api/catalog/products/import", headers=H, files=_sheet(
        ["Name", "SKU", "Barcode", "Sell price"],
        ["Good", "G1", None, 5],
        ["Bad price", "G2", None, "abc"],
        ["Bad check", "G3", 5901234123458, 1],
        ["Dup sheet", "D1", None, 1],
        ["Dup sheet 2", " d1 ", None, 1],
        ["Taken", "taken", None, 1],
        ["Negative", "G4", None, -3],
        ["Good 2", " G5 ", None, 7]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 2
    errs = {e["row"]: e["message"] for e in body["errors"]}
    assert set(errs) == {3, 4, 5, 6, 7, 8}, errs
    assert "price" in errs[3].lower() and "check digit" in errs[4]
    assert "row 5" in errs[6].lower() or "row 6" in errs[5].lower()
    assert "TAKEN" in errs[7] or "taken" in errs[7]
    names = {i["name"]: i for i in (await client.get("/api/catalog/products", headers=H)).json()["items"]}
    assert "Good" in names and "Good 2" in names and names["Good 2"]["sku"] == "G5"
    assert "Bad price" not in names and "Dup sheet" not in names


@pytest.mark.asyncio
async def test_import_with_nothing_valid_is_a_409_with_rows(client):
    H, _ = await _register(client)
    r = await client.post("/api/catalog/products/import", headers=H, files=_sheet(
        ["Name", "Sell price"], ["Only", "x"]))
    assert r.status_code == 409 and "Row 2" in r.json()["detail"]


# ── 7 ──────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_repair_price_is_saved(client):
    H, _ = await _register(client)
    p = await _product(client, H, sell_price=10, repair_price=15)
    assert p["repair_price"] == 15
    r = await client.put(f"/api/catalog/products/{p['id']}", json={"name": p["name"], "repair_price": None}, headers=H)
    assert r.status_code == 200 and r.json()["repair_price"] == 0      # blank = same as store price


# ── 8 ──────────────────────────────────────────────────────────────────────────
async def _label(client, H, body, pid):
    tid = (await client.post("/api/documents/templates", json={
        "name": f"T{uuid.uuid4().hex[:4]}", "doc_type": "label", "page_size": "label",
        "page_w_mm": 40, "page_h_mm": 30, "body_html": body}, headers=H)).json()["id"]
    r = await client.post("/api/documents/render-labels", json={"template_id": tid, "part_ids": [pid]}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["html"]


@pytest.mark.asyncio
async def test_label_barcode_uses_the_products_symbology(client):
    from backend.services.codes import code128_svg, gtin_svg, qr_svg
    H, _ = await _register(client)
    p = await _product(client, H, barcode="5901234123457", barcode_type="ean13", sku="S-1",
                       description="Genuine OEM part")
    html = await _label(client, H, "<div>{Barcode}</div><div>{QR Code}</div><p>{Product Description}</p>", p["id"])
    assert gtin_svg("5901234123457", "ean13") in html
    assert code128_svg("5901234123457") not in html
    assert qr_svg("5901234123457") in html
    assert "Genuine OEM part" in html
    nob = await _product(client, H, sku="ONLY-SKU")
    html = await _label(client, H, "{QR Code}", nob["id"])
    assert qr_svg("ONLY-SKU") in html


@pytest.mark.asyncio
async def test_label_price_without_taxes_is_net_of_an_included_tax(client):
    H, _ = await _register(client)
    r = await client.post("/api/catalog/taxes", json={"name": "VAT 20", "rate": 20, "mode": "included"}, headers=H)
    assert r.status_code == 201, r.text
    r = await client.put("/api/settings/tax-rules", json={"doc_family": "tickets_sales", "tax_ids": [r.json()["id"]]},
                         headers=H)
    assert r.status_code == 200, r.text
    p = await _product(client, H, sell_price=120)
    html = await _label(client, H, "<b>{Price}</b>|<i>{Product Price Without Taxes}</i>", p["id"])
    assert "<i>100.00</i>" in html and "<b>120.00</b>" in html, html[-300:]


# ── 9 ──────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_code_svg_is_private_and_capped(client):
    H, _ = await _register(client)
    r = await client.get("/api/catalog/code.svg", params={"type": "qr", "data": "ABC"}, headers=H)
    assert r.status_code == 200 and r.headers["cache-control"].startswith("private")
    r = await client.get("/api/catalog/code.svg", params={"type": "qr", "data": "A" * 5000}, headers=H)
    assert r.status_code == 422
