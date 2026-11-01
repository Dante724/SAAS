"""Inventory council 2026-10-25, "Later" items 9–12.

9   Bins: rename (unique per warehouse), a default bin per warehouse, bin labels in any barcode type,
    a bin barcode scans anywhere (universal scan, stocktake), per-bin quantities on the product card,
    move between bins, and a job takes from the bin you choose (and gives back to it).
10  Unit passport: purchase price, supplier, supplier warranty date, where the unit is now and its
    history (received, used on a job, returned, written off, sold); serial labels; the job document
    variables {Product Serial Number} and {Product Bin Location}.
11  Stocktake: by category, blind scan (each scan adds one, a serial counts once), matching on code,
    MPN, barcode or serial, export → fill → import (with bins), uncounted lines zeroed or kept,
    drafts that can be discarded, and a movement trail with who posted.
12  Clean-up: EAN-8 product labels render; the old /api/print/labels PDF path is gone.
Every new endpoint has a permission or cross-shop check.
"""
import csv
import io
import uuid

import pytest


def _imei(prefix: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(prefix)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return prefix + str((10 - total % 10) % 10)


SN = [_imei(f"3528410123450{i}") for i in range(10)]


async def _register(client, name="Co"):
    email = f"bps-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": name, "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


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
    body = {"name": f"Part {uuid.uuid4().hex[:6]}", "sell_price": 10, **kw}
    r = await client.post("/api/catalog/products", json=body, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _bin(client, H, loc, name, **kw):
    r = await client.post("/api/bins", json={"location_id": loc, "name": name, **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _receive(client, H, loc, pid, qty, cost=5.0, bin_id=None, serials=None, supplier_id=None):
    line = {"part_id": pid, "qty": qty, "purchase_price": cost}
    if bin_id:
        line["bin_id"] = bin_id
    if serials is not None:
        line["serials"] = serials
    body = {"location_id": loc, "lines": [line]}
    if supplier_id:
        body["supplier_id"] = supplier_id
    r = await client.post("/api/inventory/postings", json=body, headers=H)
    assert r.status_code == 201, r.text


async def _stock(client, H, pid):
    r = await client.get(f"/api/inventory/parts/{pid}/stock", headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def _by_bin(stock: dict) -> dict:
    return {i.get("bin"): i["available"] for i in stock["items"]}


async def _label_tpl(client, H, body="{Bin Name} {Bin Code} {Barcode}", doc_type="label"):
    r = await client.post("/api/documents/templates", json={
        "name": f"Tpl {uuid.uuid4().hex[:4]}", "doc_type": doc_type, "page_size": "label",
        "body_html": body}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _order(client, H, loc):
    cid = (await client.post("/api/clients", json={"name": "C", "phone": f"+1555{uuid.uuid4().int % 10**7:07d}"},
                             headers=H)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _add_product_line(client, H, oid, pid, qty=1, **kw):
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "part", "kind": "product", "ref_id": pid, "description": "Screen",
        "qty": qty, "unit_price": 20, **kw}, headers=H)
    return r


async def _units(client, H, pid):
    r = await client.get("/api/serials", params={"part_id": pid}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ── 9. Bins ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rename_a_bin_case_only_and_onto_a_free_name(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    await _bin(client, H, loc, "B1")
    r = await client.put(f"/api/bins/{a['id']}", json={"location_id": loc, "name": "a1"}, headers=H)
    assert r.status_code == 200 and r.json()["name"] == "a1"
    r = await client.put(f"/api/bins/{a['id']}", json={"location_id": loc, "name": "Shelf A"}, headers=H)
    assert r.status_code == 200 and r.json()["name"] == "Shelf A"
    r = await client.put(f"/api/bins/{a['id']}", json={"location_id": loc, "name": "b1"}, headers=H)
    assert r.status_code == 409
    # renaming keeps the label code, so labels already on the shelf still scan
    now = next(x for x in (await client.get("/api/bins", headers=H)).json()["items"] if x["id"] == a["id"])
    assert now["barcode"] == a["barcode"] and now["name"] == "Shelf A"


@pytest.mark.asyncio
async def test_default_bin_takes_receipts_without_a_bin_and_only_one_per_warehouse(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1", is_default=True)
    b = await _bin(client, H, loc, "B1")
    assert a["is_default"] is True and b["is_default"] is False
    p = await _product(client, H)
    await _receive(client, H, loc, p["id"], 3)
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 3}

    # making B the default takes it off A
    r = await client.put(f"/api/bins/{b['id']}", json={"location_id": loc, "name": "B1", "is_default": True}, headers=H)
    assert r.status_code == 200 and r.json()["is_default"] is True
    bins = {x["name"]: x for x in (await client.get("/api/bins", params={"location_id": loc}, headers=H)).json()["items"]}
    assert bins["A1"]["is_default"] is False and bins["B1"]["is_default"] is True
    await _receive(client, H, loc, p["id"], 2)
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 3, "B1": 2}

    # an explicit bin still wins over the default
    await _receive(client, H, loc, p["id"], 1, bin_id=a["id"])
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 4, "B1": 2}

    # deleting the (empty) default bin clears it: receipts go back to the unbinned row
    c = await _bin(client, H, loc, "C1", is_default=True)
    assert (await client.delete(f"/api/bins/{c['id']}", headers=H)).status_code == 200
    await _receive(client, H, loc, p["id"], 1)
    assert _by_bin(await _stock(client, H, p["id"])).get(None) == 1


@pytest.mark.asyncio
async def test_bin_labels_print_in_the_chosen_barcode_type(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1", zone="Front", shelf="2")
    b = await _bin(client, H, loc, "B1", barcode="200000000001", barcode_type="ean13")
    tpl = await _label_tpl(client, H, "{Bin Name}|{Bin Code}|{Bin Zone}|{Bin Shelf}|{Warehouse Name}|{Barcode}")
    r = await client.post("/api/documents/render-bin-labels", json={
        "template_id": tpl, "bin_ids": [a["id"], b["id"]], "copies": 2}, headers=H)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["count"] == 4 and out["html"].count("<svg") == 4
    assert "A1|" + a["barcode"] + "|Front|2|" in out["html"]
    assert "2000000000015" in out["html"]           # the EAN-13 check digit was added and printed

    # override the type for the whole sheet: a QR for every bin
    r = await client.post("/api/documents/render-bin-labels", json={
        "template_id": tpl, "bin_ids": [a["id"]], "barcode_type": "qr"}, headers=H)
    assert r.status_code == 200 and r.json()["count"] == 1
    # a code that can't be that type is refused with the bin named
    r = await client.post("/api/documents/render-bin-labels", json={
        "template_id": tpl, "bin_ids": [a["id"]], "barcode_type": "ean8"}, headers=H)
    assert r.status_code == 422 and "A1" in r.text
    # another shop's bins and templates are not found
    H2 = await _register(client, "Other")
    r = await client.post("/api/documents/render-bin-labels", json={"template_id": tpl, "bin_ids": [a["id"]]}, headers=H2)
    assert r.status_code == 404
    tpl2 = await _label_tpl(client, H2)
    r = await client.post("/api/documents/render-bin-labels", json={"template_id": tpl2, "bin_ids": [a["id"]]}, headers=H2)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_a_bin_barcode_scans_anywhere(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    r = await client.post("/api/scan/resolve", json={"code": a["barcode"]}, headers=H)
    assert r.status_code == 200, r.text
    hit = r.json()
    assert hit["type"] == "bin" and hit["id"] == a["id"] and hit["label"] == "A1"
    assert hit["location_id"] == loc and hit["warehouse_id"] == a["warehouse_id"]
    # a typed bin name scans too (lower case, as people type it)
    hit = (await client.post("/api/scan/resolve", json={"code": "a1"}, headers=H)).json()
    assert hit["type"] == "bin" and hit["id"] == a["id"]
    # another shop never sees it
    H2 = await _register(client, "Other")
    assert (await client.post("/api/scan/resolve", json={"code": a["barcode"]}, headers=H2)).json()["matched"] is False


@pytest.mark.asyncio
async def test_a_bin_code_cannot_be_a_product_barcode(client):
    H = await _register(client)
    loc = await _loc(client, H)
    await _product(client, H, barcode="BINLIKE-01")
    r = await client.post("/api/bins", json={"location_id": loc, "name": "Z", "barcode": "BINLIKE-01"}, headers=H)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_product_card_shows_per_bin_quantities_and_moves_between_bins(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    p = await _product(client, H)
    await _receive(client, H, loc, p["id"], 5, bin_id=a["id"])
    await _receive(client, H, loc, p["id"], 1)
    st = await _stock(client, H, p["id"])
    assert _by_bin(st) == {"A1": 5, None: 1}
    row = next(i for i in st["items"] if i.get("bin") == "A1")
    assert row["bin_id"] == a["id"] and row["location_id"] == loc
    r = await client.post("/api/bins/transfer", json={
        "part_id": p["id"], "source_bin_id": a["id"], "destination_bin_id": b["id"], "qty": 2}, headers=H)
    assert r.status_code == 200, r.text
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 3, "B1": 2, None: 1}
    # the unbinned stock can be put away into a bin too
    r = await client.post("/api/bins/transfer", json={
        "part_id": p["id"], "source_bin_id": None, "location_id": loc, "destination_bin_id": b["id"], "qty": 1},
        headers=H)
    assert r.status_code == 200, r.text
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 3, "B1": 3, None: 0}


@pytest.mark.asyncio
async def test_a_job_takes_from_the_chosen_bin_and_gives_back_to_it(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    p = await _product(client, H)
    await _receive(client, H, loc, p["id"], 5, bin_id=a["id"])
    await _receive(client, H, loc, p["id"], 2, bin_id=b["id"])
    oid = await _order(client, H, loc)
    r = await _add_product_line(client, H, oid, p["id"], qty=2, bin_id=b["id"])
    assert r.status_code == 201, r.text
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 5, "B1": 0}
    mv = (await client.get(f"/api/inventory/parts/{p['id']}/movements", headers=H)).json()
    use = [m for m in mv if m["type"] == "use"]
    assert use and all(m.get("bin_id") == b["id"] for m in use)

    r = await client.delete(f"/api/orders/{oid}/lines/{r.json()['line_id']}", headers=H)
    assert r.status_code == 200, r.text
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 5, "B1": 2}

    # a bin at another shop (or that doesn't exist) is refused
    H2 = await _register(client, "Other")
    loc2 = await _loc(client, H2)
    x = await _bin(client, H2, loc2, "X")
    r = await _add_product_line(client, H, oid, p["id"], qty=1, bin_id=x["id"])
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_putaway_a_bulk_posting_into_a_scanned_bin(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    p = await _product(client, H)
    q = await _product(client, H)
    r = await client.post("/api/inventory/postings/commit", json={"location_id": loc, "bin_id": a["id"], "rows": [
        {"part_id": p["id"], "qty": 2, "purchase_price": 1}, {"part_id": q["id"], "qty": 1, "purchase_price": 1, "bin_id": b["id"]}]},
        headers=H)
    assert r.status_code == 201, r.text
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 2}
    assert _by_bin(await _stock(client, H, q["id"])) == {"B1": 1}
    H2 = await _register(client, "Other")
    loc2 = await _loc(client, H2)
    r = await client.post("/api/inventory/postings/commit", json={"location_id": loc2, "bin_id": a["id"], "rows": [
        {"new_name": "X", "qty": 1}]}, headers=H2)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_bins_list_shows_what_each_bin_holds(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    p = await _product(client, H, name="Battery")
    await _receive(client, H, loc, p["id"], 4, bin_id=a["id"])
    r = await client.get(f"/api/bins/{a['id']}/contents", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [{"part_id": p["id"], "name": "Battery", "sku": None, "qty": 4.0, "reserved": 0.0}]
    H2 = await _register(client, "Other")
    assert (await client.get(f"/api/bins/{a['id']}/contents", headers=H2)).status_code == 404


# ── 10. Unit passport, serial labels, document variables ────────────────────

@pytest.mark.asyncio
async def test_unit_passport_history_from_receipt_to_job_and_back(client):
    H = await _register(client)
    loc = await _loc(client, H)
    sup = (await client.post("/api/suppliers", json={"name": "Acme Phones"}, headers=H)).json()["id"]
    a = await _bin(client, H, loc, "A1")
    p = await _product(client, H, name="iPhone 13", serialized=True, supplier_warranty_days=90)
    await _receive(client, H, loc, p["id"], 2, cost=300, bin_id=a["id"], serials=SN[:2], supplier_id=sup)

    units = await _units(client, H, p["id"])
    assert sorted(u["serial_number"] for u in units) == sorted(SN[:2])
    uid = next(u["id"] for u in units if u["serial_number"] == SN[0])
    r = await client.get(f"/api/serials/{uid}", headers=H)
    assert r.status_code == 200, r.text
    pp = r.json()
    assert pp["serial_number"] == SN[0] and pp["product"]["name"] == "iPhone 13"
    assert pp["unit_cost"] == 300 and pp["supplier"]["name"] == "Acme Phones"
    assert pp["supplier_warranty_until"] and pp["in_supplier_warranty"] is True
    assert pp["status"] == "in_stock" and pp["where"]["bin"] == "A1" and pp["where"]["location_id"] == loc
    assert [e["kind"] for e in pp["history"]] == ["received"]

    # used on a job (by serial), then the line is removed
    oid = await _order(client, H, loc)
    r = await _add_product_line(client, H, oid, p["id"], qty=1, serial_number=SN[0])
    assert r.status_code == 201, r.text
    pp = (await client.get(f"/api/serials/{uid}", headers=H)).json()
    assert pp["status"] == "allocated_to_order" and pp["where"]["order_id"] == oid
    assert pp["where"]["order_number"]
    assert pp["history"][-1]["kind"] == "used" and pp["history"][-1]["ref_id"] == oid
    assert (await client.delete(f"/api/orders/{oid}/lines/{r.json()['line_id']}", headers=H)).status_code == 200
    pp = (await client.get(f"/api/serials/{uid}", headers=H)).json()
    assert pp["status"] == "in_stock" and pp["history"][-1]["kind"] == "returned"
    assert pp["where"]["bin"] == "A1"

    # moved to another bin, then written off
    b = await _bin(client, H, loc, "B1")
    r = await client.post("/api/bins/transfer", json={
        "part_id": p["id"], "source_bin_id": a["id"], "destination_bin_id": b["id"], "qty": 1,
        "serial_numbers": [SN[0]]}, headers=H)
    assert r.status_code == 200, r.text
    pp = (await client.get(f"/api/serials/{uid}", headers=H)).json()
    assert pp["where"]["bin"] == "B1" and pp["history"][-1]["kind"] == "moved"
    r = await client.post("/api/stock-docs/write-offs", json={
        "location_id": loc, "reason": "Damaged",
        "lines": [{"part_id": p["id"], "qty": 1, "serialized_item_id": uid, "bin_id": b["id"]}]}, headers=H)
    assert r.status_code == 201, r.text
    pp = (await client.get(f"/api/serials/{uid}", headers=H)).json()
    assert pp["status"] == "written_off" and pp["history"][-1]["kind"] == "written_off"
    assert [e["kind"] for e in pp["history"]] == ["received", "used", "returned", "moved", "written_off"]

    # another shop can't read or change it
    H2 = await _register(client, "Other")
    assert (await client.get(f"/api/serials/{uid}", headers=H2)).status_code == 404
    assert (await client.patch(f"/api/serials/{uid}", json={"supplier_warranty_until": "2030-01-01"},
                               headers=H2)).status_code == 404


@pytest.mark.asyncio
async def test_counter_sale_marks_the_unit_sold_and_a_void_puts_it_back(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, name="Pixel", serialized=True)
    await _receive(client, H, loc, p["id"], 2, serials=SN[2:4])
    r = await client.post("/api/sales", json={"lines": [
        {"kind": "product", "ref_id": p["id"], "description": "Pixel", "qty": 1, "unit_price": 100,
         "serials": [SN[3]]}]}, headers=H)
    assert r.status_code == 201, r.text
    sale_id = r.json()["id"]
    units = {u["serial_number"]: u for u in await _units(client, H, p["id"])}
    assert units[SN[3]]["status"] == "sold" and units[SN[2]]["status"] == "in_stock"
    pp = (await client.get(f"/api/serials/{units[SN[3]]['id']}", headers=H)).json()
    assert pp["history"][-1]["kind"] == "sold" and pp["history"][-1]["ref_id"] == sale_id

    # without a serial, the oldest unit in stock goes
    r = await client.post("/api/sales", json={"lines": [
        {"kind": "product", "ref_id": p["id"], "description": "Pixel", "qty": 1, "unit_price": 100}]}, headers=H)
    assert r.status_code == 201, r.text
    units = {u["serial_number"]: u for u in await _units(client, H, p["id"])}
    assert units[SN[2]]["status"] == "sold"

    # a serial that isn't in stock is refused (nothing sold)
    r = await client.post("/api/sales", json={"lines": [
        {"kind": "product", "ref_id": p["id"], "description": "Pixel", "qty": 1, "unit_price": 100,
         "serials": [SN[3]]}]}, headers=H)
    assert r.status_code == 409

    assert (await client.delete(f"/api/sales/{sale_id}", headers=H)).status_code == 200
    units = {u["serial_number"]: u for u in await _units(client, H, p["id"])}
    assert units[SN[3]]["status"] == "in_stock"
    pp = (await client.get(f"/api/serials/{units[SN[3]]['id']}", headers=H)).json()
    assert pp["history"][-1]["kind"] == "returned"


@pytest.mark.asyncio
async def test_passport_hides_cost_from_staff_who_cannot_see_costs(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, serialized=True)
    await _receive(client, H, loc, p["id"], 1, cost=250, serials=SN[4:5])
    uid = (await _units(client, H, p["id"]))[0]["id"]
    T = await _teammate(client, H, "can_view_purchase_price")
    r = await client.get(f"/api/serials/{uid}", headers=T)
    assert r.status_code == 200 and r.json().get("unit_cost") is None
    assert all(u.get("unit_cost") is None for u in (await client.get(
        "/api/serials", params={"part_id": p["id"]}, headers=T)).json()["items"])


@pytest.mark.asyncio
async def test_supplier_warranty_date_can_be_corrected_by_inventory_staff_only(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, serialized=True)
    await _receive(client, H, loc, p["id"], 1, serials=SN[5:6])
    uid = (await _units(client, H, p["id"]))[0]["id"]
    assert (await client.get(f"/api/serials/{uid}", headers=H)).json()["supplier_warranty_until"] is None
    r = await client.patch(f"/api/serials/{uid}", json={"supplier_warranty_until": "2020-01-31"}, headers=H)
    assert r.status_code == 200, r.text
    pp = (await client.get(f"/api/serials/{uid}", headers=H)).json()
    assert pp["supplier_warranty_until"] == "2020-01-31" and pp["in_supplier_warranty"] is False
    T = await _teammate(client, H, "can_manage_inventory")
    assert (await client.patch(f"/api/serials/{uid}", json={"supplier_warranty_until": "2031-01-01"},
                               headers=T)).status_code == 403


@pytest.mark.asyncio
async def test_serial_search_finds_a_unit_by_its_serial(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, serialized=True)
    await _receive(client, H, loc, p["id"], 2, serials=SN[6:8])
    r = await client.get("/api/serials", params={"q": SN[7][-6:]}, headers=H)
    assert [u["serial_number"] for u in r.json()["items"]] == [SN[7]]
    H2 = await _register(client, "Other")
    assert (await client.get("/api/serials", params={"q": SN[7]}, headers=H2)).json()["items"] == []


@pytest.mark.asyncio
async def test_serial_labels_print_each_unit(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, name="Galaxy", serialized=True)
    await _receive(client, H, loc, p["id"], 2, serials=SN[8:10])
    ids = [u["id"] for u in await _units(client, H, p["id"])]
    tpl = await _label_tpl(client, H, "{Product Name}|{Serial Number}|{Product Serial Number}|{Barcode}")
    r = await client.post("/api/documents/render-serial-labels", json={"template_id": tpl, "serial_ids": ids},
                          headers=H)
    assert r.status_code == 200, r.text
    html = r.json()["html"]
    assert r.json()["count"] == 2 and html.count("<svg") == 2
    assert f"Galaxy|{SN[8]}|{SN[8]}|" in html and f"Galaxy|{SN[9]}|{SN[9]}|" in html
    H2 = await _register(client, "Other")
    assert (await client.post("/api/documents/render-serial-labels", json={
        "template_id": await _label_tpl(client, H2), "serial_ids": ids}, headers=H2)).status_code == 404


@pytest.mark.asyncio
async def test_job_documents_fill_serial_number_and_bin_location(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "Shelf A")
    p = await _product(client, H, name="Screen X", serialized=True)
    await _receive(client, H, loc, p["id"], 1, bin_id=a["id"], serials=[SN[0]])
    plain = await _product(client, H, name="Glue")
    b = await _bin(client, H, loc, "Shelf B")
    await _receive(client, H, loc, plain["id"], 3, bin_id=b["id"])
    oid = await _order(client, H, loc)
    assert (await _add_product_line(client, H, oid, p["id"])).status_code == 201
    assert (await _add_product_line(client, H, oid, plain["id"])).status_code == 201
    tpl = (await client.post("/api/documents/templates", json={
        "name": "WO", "doc_type": "work_order",
        "body_html": "<table><tbody><tr><td>{Product Name}~{Product Serial Number}~{Product Bin Location}</td>"
                     "</tr></tbody></table>"}, headers=H)).json()["id"]
    html = (await client.get(f"/api/documents/render/{oid}/{tpl}", headers=H)).json()["html"]
    assert f"Screen~{SN[0]}~Shelf A" in html
    assert "Screen~~Shelf B" in html        # the glue line: no serial, from shelf B


# ── 11. Stocktake ────────────────────────────────────────────────────────────

async def _start(client, H, loc, **kw):
    r = await client.post("/api/stocktake", json={"location_id": loc, **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _lines(client, H, st, **params):
    r = await client.get(f"/api/stocktake/{st}/lines", params=params, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_stocktake_by_category_seeds_only_that_category(client):
    H = await _register(client)
    loc = await _loc(client, H)
    cat = (await client.post("/api/catalog/categories", json={"name": "Screens", "entity_type": "product"}, headers=H))
    assert cat.status_code in (200, 201), cat.text
    cid = cat.json()["id"]
    s = await _product(client, H, name="Screen", category_id=cid)
    o = await _product(client, H, name="Cable")
    await _receive(client, H, loc, s["id"], 2)
    await _receive(client, H, loc, o["id"], 2)
    st = await _start(client, H, loc, category_id=cid)
    assert [i["part_id"] for i in (await _lines(client, H, st))["items"]] == [s["id"]]
    # a product outside the category can't be scanned into it
    r = await client.post(f"/api/stocktake/{st}/scan", json={"code": o["sku"] or o["barcode"] or o["id"]}, headers=H)
    assert r.status_code in (404, 409)
    lst = (await client.get("/api/stocktake", headers=H)).json()["items"][0]
    assert lst["category_name"] == "Screens"
    H2 = await _register(client, "Other")
    r = await client.post("/api/stocktake", json={"location_id": await _loc(client, H2), "category_id": cid}, headers=H2)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_blind_scan_adds_one_per_scan_and_a_serial_only_once(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, barcode="4006381333931")
    await _receive(client, H, loc, p["id"], 5)
    ph = await _product(client, H, serialized=True)
    await _receive(client, H, loc, ph["id"], 2, serials=SN[:2])
    st = await _start(client, H, loc, blind=True)
    for _ in range(3):
        r = await client.post(f"/api/stocktake/{st}/scan", json={"code": "4006381333931", "qty": 10}, headers=H)
        assert r.status_code == 200, r.text
    assert r.json()["counted_qty"] == 3                 # a blind scan always adds one
    assert "variance" not in r.json() or r.json()["variance"] is None
    r1 = await client.post(f"/api/stocktake/{st}/scan", json={"code": SN[0]}, headers=H)
    r2 = await client.post(f"/api/stocktake/{st}/scan", json={"code": SN[0].lower() + " "}, headers=H)
    assert r1.json()["counted_qty"] == 1 and r2.json()["counted_qty"] == 1 and r2.json()["duplicate"] is True
    # the counter doesn't see book quantities until the review step
    items = (await _lines(client, H, st))["items"]
    assert all(i["system_qty"] is None and i["variance"] is None for i in items)
    items = (await _lines(client, H, st, reveal="true"))["items"]
    got = {i["part_id"]: (i["system_qty"], i["counted_qty"]) for i in items}
    assert got[p["id"]] == (5, 3) and got[ph["id"]] == (2, 1)


@pytest.mark.asyncio
async def test_a_serial_scanned_twice_counts_once_in_a_normal_count_too(client):
    H = await _register(client)
    loc = await _loc(client, H)
    ph = await _product(client, H, serialized=True)
    await _receive(client, H, loc, ph["id"], 2, serials=SN[2:4])
    st = await _start(client, H, loc)
    for code in (SN[2], SN[2], SN[3]):
        r = await client.post(f"/api/stocktake/{st}/scan", json={"code": code}, headers=H)
        assert r.status_code == 200, r.text
    assert r.json()["counted_qty"] == 2 and r.json()["variance"] == 0


@pytest.mark.asyncio
async def test_scanning_a_bin_label_sets_the_bin_for_the_next_scans(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    p = await _product(client, H, barcode="4006381333948")
    await _receive(client, H, loc, p["id"], 2, bin_id=a["id"])
    await _receive(client, H, loc, p["id"], 1, bin_id=b["id"])
    st = await _start(client, H, loc, kind="partial")
    r = await client.post(f"/api/stocktake/{st}/scan", json={"code": b["barcode"]}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["bin"] == {"id": b["id"], "name": "B1"} and r.json().get("part_id") is None
    r = await client.post(f"/api/stocktake/{st}/scan", json={"code": "4006381333948", "bin_id": b["id"]}, headers=H)
    assert r.status_code == 200 and r.json()["variance"] == 0     # B1 held 1, counted 1
    r = await client.post(f"/api/stocktake/{st}/post", json={}, headers=H)
    assert r.status_code == 200, r.text
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 2, "B1": 1}


@pytest.mark.asyncio
async def test_scan_and_import_match_code_mpn_barcode_or_serial(client):
    H = await _register(client)
    loc = await _loc(client, H)
    by_sku = await _product(client, H, sku="SKU-1")
    by_mpn = await _product(client, H, mpn="MPN-77")
    by_bc = await _product(client, H, barcode="4006381333955")
    by_code = await _product(client, H, code="INT-9")
    ph = await _product(client, H, serialized=True)
    for x in (by_sku, by_mpn, by_bc, by_code):
        await _receive(client, H, loc, x["id"], 1)
    await _receive(client, H, loc, ph["id"], 2, serials=SN[4:6])
    st = await _start(client, H, loc, kind="partial")
    for code in ("mpn-77", "INT-9"):
        r = await client.post(f"/api/stocktake/{st}/scan", json={"code": code}, headers=H)
        assert r.status_code == 200, (code, r.text)
    r = await client.post(f"/api/stocktake/{st}/import", json={"rows": [
        {"code": "sku-1", "counted_qty": 4}, {"code": "MPN-77", "counted_qty": 3},
        {"code": "4006381333955", "counted_qty": 2}, {"code": SN[4], "counted_qty": 1},
        {"code": SN[4], "counted_qty": 1}, {"code": SN[5], "counted_qty": 1},
        {"code": "nope", "counted_qty": 1}]}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == 6 and [e["row"] for e in r.json()["errors"]] == [7]
    got = {i["part_id"]: i["counted_qty"] for i in (await _lines(client, H, st))["items"]}
    assert got == {by_sku["id"]: 4, by_mpn["id"]: 3, by_bc["id"]: 2, by_code["id"]: 1, ph["id"]: 2}


@pytest.mark.asyncio
async def test_export_fill_import_round_trip_by_bin(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    p = await _product(client, H, sku="RT-1", name="Round trip")
    await _receive(client, H, loc, p["id"], 4, bin_id=a["id"])
    await _receive(client, H, loc, p["id"], 2, bin_id=b["id"])
    st = await _start(client, H, loc, by_bin=True)
    r = await client.get(f"/api/stocktake/{st}/export/csv", headers=H)
    assert r.status_code == 200
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert {(x["code"], x["bin"], x["system_qty"]) for x in rows} == {("RT-1", "A1", "4.0"), ("RT-1", "B1", "2.0")}
    for x in rows:                           # the counter writes the real numbers in
        x["counted_qty"] = "3" if x["bin"] == "A1" else "2"
    r = await client.post(f"/api/stocktake/{st}/import", json={"rows": rows, "bin_field": "bin"}, headers=H)
    assert r.status_code == 200 and r.json()["applied"] == 2 and not r.json()["errors"], r.text
    again = list(csv.DictReader(io.StringIO((await client.get(f"/api/stocktake/{st}/export/csv", headers=H)).text)))
    assert {(x["bin"], x["counted_qty"]) for x in again} == {("A1", "3.0"), ("B1", "2.0")}
    assert (await client.post(f"/api/stocktake/{st}/post", json={}, headers=H)).status_code == 200
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 3, "B1": 2}


@pytest.mark.asyncio
async def test_blind_export_leaves_the_book_quantity_out(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, sku="BL-1")
    await _receive(client, H, loc, p["id"], 7)
    st = await _start(client, H, loc, blind=True)
    rows = list(csv.DictReader(io.StringIO((await client.get(f"/api/stocktake/{st}/export/csv", headers=H)).text)))
    assert rows[0]["code"] == "BL-1" and rows[0]["system_qty"] == ""


@pytest.mark.asyncio
async def test_uncounted_lines_are_kept_or_set_to_zero_on_post(client):
    H = await _register(client)
    loc = await _loc(client, H)
    counted = await _product(client, H, sku="C-1")
    left = await _product(client, H, sku="L-1")
    for x in (counted, left):
        await _receive(client, H, loc, x["id"], 3)
    st = await _start(client, H, loc)
    await client.post(f"/api/stocktake/{st}/scan", json={"code": "C-1"}, headers=H)
    r = await client.post(f"/api/stocktake/{st}/post", json={"uncounted": "keep"}, headers=H)
    assert r.status_code == 200 and r.json()["adjustments"] == 1
    assert (await _stock(client, H, left["id"]))["total_available"] == 3

    st = await _start(client, H, loc)
    await client.post(f"/api/stocktake/{st}/scan", json={"code": "C-1"}, headers=H)
    r = await client.post(f"/api/stocktake/{st}/post", json={"uncounted": "zero"}, headers=H)
    assert r.status_code == 200 and r.json()["adjustments"] == 1     # the counted line matched (1 = 1)
    assert (await _stock(client, H, left["id"]))["total_available"] == 0
    assert (await _stock(client, H, counted["id"]))["total_available"] == 1
    assert (await client.post(f"/api/stocktake/{await _start(client, H, loc)}/post",
                              json={"uncounted": "maybe"}, headers=H)).status_code == 422


@pytest.mark.asyncio
async def test_draft_counts_list_resume_and_discard(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, sku="D-1")
    await _receive(client, H, loc, p["id"], 3)
    st = await _start(client, H, loc, kind="partial", note="Back room")
    await client.post(f"/api/stocktake/{st}/scan", json={"code": "D-1"}, headers=H)
    row = next(x for x in (await client.get("/api/stocktake", headers=H)).json()["items"] if x["id"] == st)
    assert row["status"] == "in_progress" and row["counted"] == 1 and row["kind"] == "partial"
    # resuming carries on adding
    await client.post(f"/api/stocktake/{st}/scan", json={"code": "D-1"}, headers=H)
    assert (await _lines(client, H, st))["items"][0]["counted_qty"] == 2
    H2 = await _register(client, "Other")
    assert (await client.delete(f"/api/stocktake/{st}", headers=H2)).status_code == 404
    T = await _teammate(client, H, "can_manage_inventory")
    assert (await client.delete(f"/api/stocktake/{st}", headers=T)).status_code == 403
    assert (await client.delete(f"/api/stocktake/{st}", headers=H)).status_code == 200
    assert all(x["id"] != st for x in (await client.get("/api/stocktake", headers=H)).json()["items"])
    assert (await _stock(client, H, p["id"]))["total_available"] == 3     # a discarded draft changes nothing
    # a posted count can't be discarded
    st = await _start(client, H, loc)
    await client.post(f"/api/stocktake/{st}/post", json={}, headers=H)
    assert (await client.delete(f"/api/stocktake/{st}", headers=H)).status_code == 409


@pytest.mark.asyncio
async def test_posting_writes_adjustments_with_who_and_where(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    p = await _product(client, H, sku="M-1")
    await _receive(client, H, loc, p["id"], 5, bin_id=a["id"])
    st = await _start(client, H, loc, bin_id=a["id"])
    await client.post(f"/api/stocktake/{st}/import", json={"rows": [{"code": "M-1", "counted_qty": 3}]}, headers=H)
    assert (await client.post(f"/api/stocktake/{st}/post", json={}, headers=H)).status_code == 200
    mv = (await client.get(f"/api/inventory/parts/{p['id']}/movements", headers=H)).json()
    adj = [m for m in mv if m.get("ref_type") == "stocktake"]
    assert len(adj) == 1 and adj[0]["qty"] == -2 and adj[0]["bin_id"] == a["id"]
    assert adj[0].get("created_by")


# ── 12. Clean-up ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ean8_product_label_renders(client):
    H = await _register(client)
    p = await _product(client, H, name="Mini", barcode="96385074", barcode_type="ean8")
    tpl = (await client.post("/api/documents/templates", json={
        "name": "Tag", "doc_type": "price_tag", "page_size": "label", "barcode_field": "barcode",
        "barcode_types": {"barcode": "ean8"}, "body_html": "{Product Title}{Barcode}"}, headers=H)).json()["id"]
    r = await client.post("/api/documents/render-labels", json={"template_id": tpl, "part_ids": [p["id"]]}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["html"].count("<svg") == 1 and "96385074" in r.json()["html"]


@pytest.mark.asyncio
async def test_old_pdf_label_path_is_retired(client):
    H = await _register(client)
    p = await _product(client, H)
    r = await client.post("/api/print/labels", json={"part_ids": [p["id"]]}, headers=H)
    assert r.status_code in (404, 405)


# ── security review fixes ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_job_cannot_use_a_serial_held_at_another_location(client):
    H = await _register(client)
    loc = await _loc(client, H)
    loc2 = (await client.post("/api/staff/locations", json={"name": "Annex"}, headers=H)).json()["id"]
    p = await _product(client, H, serialized=True)
    await _receive(client, H, loc2, p["id"], 1, serials=[SN[1]])
    await _receive(client, H, loc, p["id"], 1, serials=[SN[2]])
    oid = await _order(client, H, loc)
    r = await _add_product_line(client, H, oid, p["id"], serial_number=SN[1])
    assert r.status_code == 409
    units = {u["serial_number"]: u["status"] for u in await _units(client, H, p["id"])}
    assert units == {SN[1]: "in_stock", SN[2]: "in_stock"}


@pytest.mark.asyncio
async def test_named_serials_must_match_the_quantity_moved(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    p = await _product(client, H, serialized=True)
    await _receive(client, H, loc, p["id"], 2, bin_id=a["id"], serials=SN[3:5])
    r = await client.post("/api/bins/transfer", json={"part_id": p["id"], "source_bin_id": a["id"],
                          "destination_bin_id": b["id"], "qty": 2, "serial_numbers": [SN[3]]}, headers=H)
    assert r.status_code == 422
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 2}


@pytest.mark.asyncio
async def test_label_sheets_are_capped_before_rendering(client):
    H = await _register(client)
    loc = await _loc(client, H)
    bins = [(await _bin(client, H, loc, f"C{i}"))["id"] for i in range(5)]
    tpl = await _label_tpl(client, H)
    r = await client.post("/api/documents/render-bin-labels", json={"template_id": tpl, "bin_ids": bins, "copies": 401},
                          headers=H)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_only_inventory_staff_can_reveal_a_blind_count(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, sku="RV-1")
    await _receive(client, H, loc, p["id"], 4)
    st = await _start(client, H, loc, blind=True)
    T = await _teammate(client, H, "can_manage_inventory")
    r = await client.get(f"/api/stocktake/{st}/lines", params={"reveal": "true"}, headers=T)
    assert r.status_code == 200 and r.json()["items"][0]["system_qty"] is None
    assert (await _lines(client, H, st, reveal="true"))["items"][0]["system_qty"] == 4


@pytest.mark.asyncio
async def test_a_new_shop_has_bin_and_serial_label_templates_ready(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    items = (await client.get("/api/documents/templates", headers=H)).json()["items"]
    tpl = {t["name"]: t["id"] for t in items}
    assert "Bin label" in tpl and "Serial label" in tpl
    r = await client.post("/api/documents/render-bin-labels", json={"template_id": tpl["Bin label"], "bin_ids": [a["id"]]},
                          headers=H)
    assert r.status_code == 200 and "A1" in r.json()["html"] and r.json()["html"].count("<svg") == 1
    again = [t["name"] for t in (await client.get("/api/documents/templates", headers=H)).json()["items"]]
    assert again.count("Bin label") == 1


@pytest.mark.asyncio
async def test_serials_not_scanned_in_a_count_are_written_off_on_post(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    p = await _product(client, H, serialized=True)
    await _receive(client, H, loc, p["id"], 3, bin_id=a["id"], serials=SN[5:8])
    st = await _start(client, H, loc, by_bin=True)
    r = await client.post(f"/api/stocktake/{st}/scan", json={"code": SN[6], "bin_id": a["id"]}, headers=H)
    assert r.status_code == 200, r.text
    assert (await client.post(f"/api/stocktake/{st}/post", json={}, headers=H)).status_code == 200
    assert (await _stock(client, H, p["id"]))["total_available"] == 1
    units = {u["serial_number"]: u["status"] for u in await _units(client, H, p["id"])}
    assert units == {SN[5]: "written_off", SN[6]: "in_stock", SN[7]: "written_off"}
    uid = next(u["id"] for u in await _units(client, H, p["id"]) if u["serial_number"] == SN[5])
    assert (await client.get(f"/api/serials/{uid}", headers=H)).json()["history"][-1]["kind"] == "written_off"


# ── tester review: stock rows and units stay in step ────────────────────────

async def _units_by_bin(client, H, pid):
    out: dict = {}
    for u in await _units(client, H, pid):
        if u["status"] == "in_stock":
            out[u["bin"]] = out.get(u["bin"], 0) + 1
    return out


@pytest.mark.asyncio
async def test_sale_and_void_of_a_unit_use_its_own_bin(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    p = await _product(client, H, serialized=True)
    await _receive(client, H, loc, p["id"], 1, bin_id=a["id"], serials=[SN[0]])
    await _receive(client, H, loc, p["id"], 1, bin_id=b["id"], serials=[SN[1]])
    r = await client.post("/api/sales", json={"lines": [{"kind": "product", "ref_id": p["id"], "description": "x",
                                                         "qty": 1, "unit_price": 1, "serials": [SN[1]]}]}, headers=H)
    assert r.status_code == 201, r.text
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 1, "B1": 0} and await _units_by_bin(client, H, p["id"]) == {"A1": 1}
    assert (await client.delete(f"/api/sales/{r.json()['id']}", headers=H)).status_code == 200
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 1, "B1": 1}
    assert await _units_by_bin(client, H, p["id"]) == {"A1": 1, "B1": 1}


@pytest.mark.asyncio
async def test_deleting_a_job_line_gives_each_bin_back_what_it_gave(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    p = await _product(client, H)
    await _receive(client, H, loc, p["id"], 2, bin_id=a["id"])
    await _receive(client, H, loc, p["id"], 1, bin_id=b["id"])
    oid = await _order(client, H, loc)
    r = await _add_product_line(client, H, oid, p["id"], qty=3, bin_id=a["id"])
    assert r.status_code == 201, r.text
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 0, "B1": 0}
    assert (await client.delete(f"/api/orders/{oid}/lines/{r.json()['line_id']}", headers=H)).status_code == 200
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 2, "B1": 1}


@pytest.mark.asyncio
async def test_a_named_serial_comes_out_of_its_own_bin(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    p = await _product(client, H, serialized=True)
    await _receive(client, H, loc, p["id"], 1, bin_id=a["id"], serials=[SN[2]])
    await _receive(client, H, loc, p["id"], 1, bin_id=b["id"], serials=[SN[3]])
    oid = await _order(client, H, loc)
    assert (await _add_product_line(client, H, oid, p["id"], bin_id=a["id"], serial_number=SN[3])).status_code == 409
    assert (await _add_product_line(client, H, oid, p["id"], serial_number=SN[3])).status_code == 201
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 1, "B1": 0}
    assert await _units_by_bin(client, H, p["id"]) == {"A1": 1}


@pytest.mark.asyncio
async def test_a_unit_found_in_another_bin_moves_with_its_stock(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    p = await _product(client, H, serialized=True)
    await _receive(client, H, loc, p["id"], 1, bin_id=b["id"], serials=[SN[4]])
    st = await _start(client, H, loc, bin_id=a["id"], kind="partial")
    assert (await client.post(f"/api/stocktake/{st}/scan", json={"code": SN[4]}, headers=H)).status_code == 200
    assert (await client.post(f"/api/stocktake/{st}/post", json={}, headers=H)).status_code == 200
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 1, "B1": 0}
    assert await _units_by_bin(client, H, p["id"]) == {"A1": 1}


@pytest.mark.asyncio
async def test_zeroing_uncounted_serialized_lines_writes_their_units_off(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, serialized=True)
    await _receive(client, H, loc, p["id"], 2, serials=SN[8:10])
    st = await _start(client, H, loc)
    assert (await client.post(f"/api/stocktake/{st}/post", json={"uncounted": "zero"}, headers=H)).status_code == 200
    assert (await _stock(client, H, p["id"]))["total_available"] == 0
    assert await _units_by_bin(client, H, p["id"]) == {}


@pytest.mark.asyncio
async def test_a_by_bin_count_puts_a_serial_scanned_without_a_bin_in_its_own_bin(client):
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    p = await _product(client, H, serialized=True)
    await _receive(client, H, loc, p["id"], 1, bin_id=a["id"], serials=[SN[0]])
    st = await _start(client, H, loc, by_bin=True)
    r = await client.post(f"/api/stocktake/{st}/scan", json={"code": SN[0]}, headers=H)
    assert r.status_code == 200 and r.json()["variance"] == 0
    assert (await client.post(f"/api/stocktake/{st}/post", json={"uncounted": "zero"}, headers=H)).status_code == 200
    assert _by_bin(await _stock(client, H, p["id"])) == {"A1": 1}
