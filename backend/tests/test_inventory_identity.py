"""Inventory identity foundation (council 2026-10-25, "Now" items 1–4).

1  Stock rows per bin: a bin transfer used to create a second stock row for (product, location)
   and the next receipt crashed (MultipleResultsFound). Bins now carry their warehouse
   renaming
   a bin onto another bin's name is refused.
2  Uniqueness in the database: barcode (several per product), SKU, bin name / bin code, in-stock
   serial
   the "each product is unique by" setting
   database clashes come back as a plain 409.
3  One code generator: GS1 in-store EAN-13 barcodes, SKU / serial / bin / asset templates.
4  Bulk "generate missing barcodes"; extra barcodes found by every scan path.
Every new endpoint has a cross-shop check.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


async def _register(client, name="Co"):
    email = f"ii-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": name, "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


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


async def _receive(client, H, loc, pid, qty, cost=5.0, bin_id=None):
    line = {"part_id": pid, "qty": qty, "purchase_price": cost}
    if bin_id:
        line["bin_id"] = bin_id
    r = await client.post("/api/inventory/postings", json={"location_id": loc, "lines": [line]}, headers=H)
    assert r.status_code == 201, r.text


async def _on_hand(client, H, pid):
    r = await client.get(f"/api/inventory/parts/{pid}", headers=H)
    return r.json()["qty_available"]


async def _batch_into_bin(client, H, loc, pid, bin_id, qty):
    await _receive(client, H, loc, pid, qty, bin_id=bin_id)


# ── 1. Bins and per-bin stock rows ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_bin_transfer_then_receipt_does_not_crash_and_keeps_totals(client, db):
    from backend.models import StockLevel
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    await _receive(client, H, loc, p["id"], 5, bin_id=a["id"])

    r = await client.post("/api/bins/transfer", json={
        "part_id": p["id"], "source_bin_id": a["id"], "destination_bin_id": b["id"], "qty": 2}, headers=H)
    assert r.status_code == 200, r.text

    # The receipt that used to raise MultipleResultsFound.
    await _receive(client, H, loc, p["id"], 3)
    assert await _on_hand(client, H, p["id"]) == 8

    rows = (await db.execute(select(StockLevel).where(StockLevel.part_id == p["id"]))).scalars().all()
    by_bin = {r.bin_id: r.qty_available for r in rows}
    assert by_bin == {a["id"]: 3, b["id"]: 2, None: 3}

    bins = {x["name"]: x["qty_on_hand"] for x in
            (await client.get(f"/api/bins?location_id={loc}", headers=H)).json()["items"]}
    assert bins == {"A1": 3, "B1": 2}


@pytest.mark.asyncio
async def test_bin_transfer_cannot_move_more_than_the_bin_holds(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    await _batch_into_bin(client, H, loc, p["id"], a["id"], 2)
    r = await client.post("/api/bins/transfer", json={
        "part_id": p["id"], "source_bin_id": a["id"], "destination_bin_id": b["id"], "qty": 3}, headers=H)
    assert r.status_code == 409
    assert "only 2" in r.json()["detail"]
    # a bin from another shop can't be posted into
    B = await _register(client, "B")
    r = await client.post("/api/inventory/postings", json={"location_id": await _loc(client, B), "lines": [
        {"part_id": (await _product(client, B))["id"], "qty": 1, "bin_id": a["id"]}]}, headers=B)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_using_stock_after_a_transfer_draws_across_bins(client, db):
    """A write-off for more than one bin holds takes from several rows and never leaves a bin negative."""
    from backend.models import StockLevel
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    await _batch_into_bin(client, H, loc, p["id"], a["id"], 4)
    await client.post("/api/bins/transfer", json={
        "part_id": p["id"], "source_bin_id": a["id"], "destination_bin_id": b["id"], "qty": 3}, headers=H)
    r = await client.post(f"/api/catalog/products/{p['id']}/write-off",
                          json={"location_id": loc, "qty": 2}, headers=H)
    assert r.status_code == 200, r.text
    rows = (await db.execute(select(StockLevel).where(StockLevel.part_id == p["id"]))).scalars().all()
    assert sum(r.qty_available for r in rows) == 2
    assert all(r.qty_available >= 0 for r in rows)
    r = await client.post(f"/api/catalog/products/{p['id']}/write-off",
                          json={"location_id": loc, "qty": 5}, headers=H)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_stock_row_service_draws_named_bin_first(client, db):
    from backend.models import Location, Organisation, Part, StorageBin
    from backend.services import stock_rows
    org = Organisation(name="S")
    db.add(org)
    await db.flush()
    loc = Location(org_id=org.id, name="Main")
    db.add(loc)
    await db.flush()
    part = Part(org_id=org.id, name="Cable")
    db.add(part)
    b1 = StorageBin(org_id=org.id, location_id=loc.id, name="X1")
    b2 = StorageBin(org_id=org.id, location_id=loc.id, name="X2")
    db.add_all([b1, b2])
    await db.flush()
    await stock_rows.add(db, part.id, loc.id, 2)
    await stock_rows.add(db, part.id, loc.id, 3, bin_id=b1.id)
    await stock_rows.add(db, part.id, loc.id, 4, bin_id=b2.id)
    await stock_rows.add(db, part.id, loc.id, 1, bin_id=b2.id)    # same row, not a new one
    assert (await stock_rows.totals(db, part.id, loc.id)) == (10, 0)

    taken = await stock_rows.draw(db, part.id, loc.id, 6, bin_id=b2.id)
    assert taken == [(b2.id, 5), (None, 1)]
    assert (await stock_rows.totals(db, part.id, loc.id))[0] == 4
    with pytest.raises(stock_rows.NotEnoughStock):
        await stock_rows.draw(db, part.id, loc.id, 5)
    assert (await stock_rows.totals(db, part.id, loc.id))[0] == 4   # nothing moved on a refusal

    await stock_rows.reserve(db, part.id, loc.id, 2)
    assert (await stock_rows.totals(db, part.id, loc.id)) == (4, 2)
    await stock_rows.reserve(db, part.id, loc.id, -5)
    assert (await stock_rows.totals(db, part.id, loc.id)) == (4, 0)


@pytest.mark.asyncio
async def test_draw_after_another_writer_plans_on_fresh_numbers(db):
    """A lost race used to retry on the numbers read before it and fail every time (tester review)."""
    from sqlalchemy import update

    from backend.models import Location, Organisation, Part, StockLevel, StorageBin
    from backend.services import stock_rows
    org = Organisation(name="S")
    db.add(org)
    await db.flush()
    loc = Location(org_id=org.id, name="Main")
    db.add(loc)
    await db.flush()
    part = Part(org_id=org.id, name="Cable")
    db.add(part)
    b1 = StorageBin(org_id=org.id, location_id=loc.id, name="X1")
    b2 = StorageBin(org_id=org.id, location_id=loc.id, name="X2")
    db.add_all([b1, b2])
    await db.flush()
    await stock_rows.add(db, part.id, loc.id, 10, bin_id=b1.id)
    await stock_rows.add(db, part.id, loc.id, 5, bin_id=b2.id)
    await stock_rows.rows(db, part.id, loc.id)                # rows now held by the session
    await db.execute(update(StockLevel).where(StockLevel.bin_id == b1.id)     # another request takes 6
                     .values(qty_available=StockLevel.qty_available - 6).execution_options(synchronize_session=False))
    taken = await stock_rows.draw(db, part.id, loc.id, 8)
    assert sum(t for _, t in taken) == 8
    assert (await stock_rows.totals(db, part.id, loc.id))[0] == 1
    taken = await stock_rows.draw(db, part.id, loc.id, 3, allow_short=True)   # 1 on hand: short 2, no error
    assert sum(t for _, t in taken) == 3


@pytest.mark.asyncio
async def test_stock_document_write_off_from_a_bin_next_to_a_warehouse_row(client):
    H = await _register(client)
    w = (await client.get("/api/settings/warehouses?kind=product", headers=H)).json()["items"][0]
    p = await _product(client, H)
    r = await client.post("/api/inventory/postings", json={"location_id": w["location_id"],
        "lines": [{"part_id": p["id"], "qty": 1, "purchase_price": 1}]}, headers=H)
    assert r.status_code == 201, r.text
    b = await _bin(client, H, w["location_id"], "Z1")
    await _receive(client, H, w["location_id"], p["id"], 4, bin_id=b["id"])
    r = await client.post("/api/stock-docs/write-offs", json={"warehouse_id": w["id"],
        "lines": [{"part_id": p["id"], "qty": 3, "bin_id": b["id"]}]}, headers=H)
    assert r.status_code == 201, r.text
    bins = {x["name"]: x["qty_on_hand"] for x in (await client.get("/api/bins", headers=H)).json()["items"]}
    assert bins == {"Z1": 1}


@pytest.mark.asyncio
async def test_a_second_row_for_the_same_bin_is_refused_by_the_database(db):
    from backend.models import Location, Organisation, Part, StockLevel, StorageBin
    org = Organisation(name="S")
    db.add(org)
    await db.flush()
    loc = Location(org_id=org.id, name="Main")
    db.add(loc)
    await db.flush()
    part = Part(org_id=org.id, name="Cable")
    db.add(part)
    b = StorageBin(org_id=org.id, location_id=loc.id, name="X1")
    db.add(b)
    await db.flush()
    db.add(StockLevel(part_id=part.id, location_id=loc.id, bin_id=b.id, qty_available=1))
    db.add(StockLevel(part_id=part.id, location_id=loc.id, bin_id=b.id, qty_available=1))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_bins_get_their_warehouse_and_rename_onto_another_name_is_refused(client, db):
    from backend.models import StorageBin, Warehouse
    H = await _register(client)
    loc = await _loc(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    row = (await db.execute(select(StorageBin).where(StorageBin.id == a["id"]))).scalar_one()
    wh = (await db.execute(select(Warehouse).where(Warehouse.id == row.warehouse_id))).scalar_one()
    assert wh.location_id == loc and wh.kind == "product"
    assert a["warehouse_id"] == wh.id

    r = await client.put(f"/api/bins/{b['id']}", json={"location_id": loc, "name": "a1"}, headers=H)
    assert r.status_code == 409
    assert "already" in r.json()["detail"]
    r = await client.post("/api/bins", json={"location_id": loc, "name": "A1"}, headers=H)
    assert r.status_code == 409
    # renaming a bin to its own name (or a new one) still works
    r = await client.put(f"/api/bins/{b['id']}", json={"location_id": loc, "name": "B2"}, headers=H)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_bin_codes_come_from_the_template_and_are_unique(client):
    H = await _register(client)
    loc = await _loc(client, H)
    r = await client.put("/api/staff/inventory-settings", json={"bin_template": "SH-{C:3}"}, headers=H)
    assert r.status_code == 200, r.text
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "A2")
    assert a["barcode"] == "SH-001" and b["barcode"] == "SH-002"
    # a typed bin code that another bin already has is refused
    r = await client.post("/api/bins", json={"location_id": loc, "name": "A3", "barcode": "SH-001"}, headers=H)
    assert r.status_code == 409
    # leaving the name empty names the bin after its code ("add one more")
    r = await client.post("/api/bins", json={"location_id": loc}, headers=H)
    assert r.status_code == 201 and r.json()["name"] == r.json()["barcode"] == "SH-003"


@pytest.mark.asyncio
async def test_location_transfer_takes_stock_split_across_bins(client, db):
    """A transfer used to read one row for (product, location) — with stock in two bins it crashed."""
    from backend.models import StockLevel
    H = await _register(client)
    loc = await _loc(client, H)
    r = await client.post("/api/staff/locations", json={"name": "Branch 2"}, headers=H)
    assert r.status_code == 201, r.text
    loc2 = r.json()["id"]
    p = await _product(client, H)
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    await _receive(client, H, loc, p["id"], 2, bin_id=a["id"])
    await _receive(client, H, loc, p["id"], 2, bin_id=b["id"])
    t = (await client.post("/api/transfers", json={"from_location_id": loc, "to_location_id": loc2,
                                                   "lines": [{"part_id": p["id"], "qty": 3}]}, headers=H)).json()
    for st in ("ready", "in_transit", "received", "completed"):
        r = await client.patch(f"/api/transfers/{t['id']}/status", json={"status": st}, headers=H)
        assert r.status_code == 200, (st, r.text)
    rows = (await db.execute(select(StockLevel).where(StockLevel.part_id == p["id"]))).scalars().all()
    at = {}
    for row in rows:
        at[row.location_id] = at.get(row.location_id, 0) + row.qty_available
        assert row.qty_available >= 0 and (row.qty_reserved or 0) == 0
    assert at == {loc: 1, loc2: 3}


@pytest.mark.asyncio
async def test_bin_stocktake_posts_to_that_bin_only(client, db):
    from backend.models import StockLevel
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, barcode="CNT-1")
    a = await _bin(client, H, loc, "A1")
    b = await _bin(client, H, loc, "B1")
    await _receive(client, H, loc, p["id"], 2, bin_id=a["id"])
    await _receive(client, H, loc, p["id"], 5, bin_id=b["id"])
    wid = (await client.get("/api/settings/warehouses?kind=product", headers=H)).json()["items"][0]["id"]
    st = (await client.post("/api/stocktake", json={"warehouse_id": wid, "bin_id": a["id"], "kind": "partial"},
                            headers=H)).json()
    r = await client.post(f"/api/stocktake/{st['id']}/scan", json={"code": "CNT-1"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["variance"] == -1            # one counted against the 2 in bin A1, not the 7 overall
    r = await client.post(f"/api/stocktake/{st['id']}/post", headers=H)
    assert r.status_code == 200, r.text
    rows = {row.bin_id: row.qty_available for row in
            (await db.execute(select(StockLevel).where(StockLevel.part_id == p["id"]))).scalars().all()}
    assert rows == {a["id"]: 1, b["id"]: 5}
    # another shop can't count into (and so create stock in) this shop's bin
    B = await _register(client, "B")
    await _product(client, B, barcode="CNT-1")
    wb = (await client.get("/api/settings/warehouses?kind=product", headers=B)).json()["items"][0]["id"]
    r = await client.post("/api/stocktake", json={"warehouse_id": wb, "bin_id": a["id"], "kind": "partial"}, headers=B)
    assert r.status_code == 404
    stb = (await client.post("/api/stocktake", json={"warehouse_id": wb, "kind": "partial"}, headers=B)).json()
    r = await client.post(f"/api/stocktake/{stb['id']}/scan", json={"code": "CNT-1", "bin_id": a["id"]}, headers=B)
    assert r.status_code == 404


# ── 2. Uniqueness ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_barcode_is_unique_per_shop_but_not_across_shops(client):
    A = await _register(client, "A")
    B = await _register(client, "B")
    await _product(client, A, barcode="2000000000015")
    r = await client.post("/api/catalog/products", json={"name": "Other", "barcode": "2000000000015"}, headers=A)
    assert r.status_code == 409
    assert "barcode" in r.json()["detail"].lower()
    await _product(client, B, barcode="2000000000015")      # another shop: neither blocked nor revealed


@pytest.mark.asyncio
async def test_sku_is_unique_case_insensitively_among_active_products(client):
    H = await _register(client)
    p = await _product(client, H, sku="SCR-1")
    r = await client.post("/api/catalog/products", json={"name": "Two", "sku": "scr-1"}, headers=H)
    assert r.status_code == 409 and "SKU" in r.json()["detail"]
    r = await client.post("/api/inventory/parts", json={"name": "Three", "sku": "SCR-1"}, headers=H)
    assert r.status_code == 409
    r = await client.post("/api/catalog/products/bulk", json={"ids": [p["id"]], "action": "delete"}, headers=H)
    assert r.status_code == 200, r.text
    await _product(client, H, sku="SCR-1")                    # an archived product doesn't hold its SKU


@pytest.mark.asyncio
async def test_a_race_past_the_app_check_comes_back_as_a_plain_409(client, monkeypatch):
    from backend.services import product_codes
    H = await _register(client)
    await _product(client, H, barcode="ABC-1", sku="RACE-1")

    async def _skip(*a, **k):
        return None
    monkeypatch.setattr(product_codes, "ensure_barcode_free", _skip)
    monkeypatch.setattr(product_codes, "ensure_sku_free", _skip)
    r = await client.post("/api/catalog/products", json={"name": "Racer", "barcode": "ABC-1"}, headers=H)
    assert r.status_code == 409
    assert r.json()["detail"] == "That barcode is already used by another product."
    r = await client.post("/api/catalog/products", json={"name": "Racer 2", "sku": "race-1"}, headers=H)
    assert r.status_code == 409
    assert r.json()["detail"] == "That SKU is already used by another product."


@pytest.mark.asyncio
async def test_serial_is_unique_while_in_stock(db):
    from backend.models import Organisation, Part, SerializedItem
    org = Organisation(name="S")
    db.add(org)
    await db.flush()
    p = Part(org_id=org.id, name="Phone", serialized=True)
    db.add(p)
    await db.flush()
    db.add(SerializedItem(org_id=org.id, part_id=p.id, serial_number="SN1", status="sold"))
    db.add(SerializedItem(org_id=org.id, part_id=p.id, serial_number="SN1", status="in_stock"))
    await db.flush()                                          # sold before + in stock now: fine
    db.add(SerializedItem(org_id=org.id, part_id=p.id, serial_number="sn 1", status="allocated_to_order"))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_unique_identifier_setting_decides_which_field_must_be_unique(client):
    H = await _register(client)
    await _product(client, H, name="Screen", code="C-1")
    # default: the name is the identifier
    r = await client.post("/api/catalog/products", json={"name": "Screen"}, headers=H)
    assert r.status_code == 409 and "name" in r.json()["detail"]

    r = await client.put("/api/staff/inventory-settings", json={"unique_identifier": "code"}, headers=H)
    assert r.status_code == 200
    await _product(client, H, name="Screen", code="C-2")      # same name is fine now
    r = await client.post("/api/catalog/products", json={"name": "Glass", "code": "C-1"}, headers=H)
    assert r.status_code == 409 and "code" in r.json()["detail"]
    r = await client.post("/api/inventory/parts", json={"name": "Glass", "code": "C-1"}, headers=H)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_import_matches_on_the_chosen_identifier(client):
    import io

    import openpyxl
    H = await _register(client)
    await client.put("/api/staff/inventory-settings", json={"unique_identifier": "sku"}, headers=H)
    p = await _product(client, H, name="Old name", sku="IMP-1")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["SKU", "Name", "Sell price"])
    ws.append(["IMP-1", "New name", 25])
    buf = io.BytesIO()
    wb.save(buf)
    r = await client.post("/api/catalog/products/import", headers=H, files={
        "file": ("p.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1 and r.json()["created"] == 0
    got = (await client.get(f"/api/catalog/products/{p['id']}", headers=H)).json()["product"]
    assert got["name"] == "New name" and got["sell_price"] == 25


# ── 3. Code generator ────────────────────────────────────────────────────────

def test_in_store_ean13_codes_are_valid_unique_and_in_range():
    from backend.services.codes import gtin_check_digit
    from backend.services.numbering import in_store_ean13
    seen = set()
    for n in range(1, 10_001):
        code = in_store_ean13("20", n)
        assert len(code) == 13 and code.isdigit()
        assert 20 <= int(code[:2]) <= 29
        assert gtin_check_digit(code[:12]) == code[12]
        seen.add(code)
    assert len(seen) == 10_000
    assert in_store_ean13("27", 1).startswith("27")
    with pytest.raises(ValueError):
        in_store_ean13("50", 1)          # a real manufacturer range


@pytest.mark.asyncio
async def test_generated_barcode_skips_a_code_already_taken(client):
    from backend.services.numbering import in_store_ean13
    H = await _register(client)
    await _product(client, H, barcode=in_store_ean13("20", 1))     # typed in by hand earlier
    p = await _product(client, H)
    r = await client.post(f"/api/catalog/products/{p['id']}/barcode/generate?type=ean13", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["barcode"] == in_store_ean13("20", 2)


@pytest.mark.asyncio
async def test_templates_fill_sku_serials_and_asset_serials(client):
    H = await _register(client)
    r = await client.put("/api/staff/inventory-settings", json={
        "sku_template": "{CAT}-{C:4}", "product_serial_template": "TK{C:5}",
        "asset_serial_template": "{T}-{C:3}", "barcode_prefix": "25"}, headers=H)
    assert r.status_code == 200, r.text
    s = (await client.get("/api/staff/inventory-settings", headers=H)).json()
    assert s["sku_template"] == "{CAT}-{C:4}" and s["barcode_prefix"] == "25"

    cat = (await client.post("/api/catalog/categories", json={"name": "Screens", "entity_type": "product"},
                             headers=H)).json()
    p = await _product(client, H, category_id=cat["id"])
    assert p["sku"] == "SCR-0001"
    typed = await _product(client, H, sku="MINE")
    assert typed["sku"] == "MINE"                             # a typed SKU is never replaced

    sp = await _product(client, H, serialized=True)
    r = await client.post(f"/api/catalog/products/{sp['id']}/serials/generate", json={"count": 3}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["serials"] == ["TK00001", "TK00002", "TK00003"]

    r = await client.post(f"/api/catalog/products/{p['id']}/barcode/generate?type=ean13", headers=H)
    assert r.json()["barcode"].startswith("25")

    r = await client.post("/api/assets", json={"owner_type": "company", "asset_type": "Laptop",
                                               "brand": "Acme", "model": "X"}, headers=H)
    assert r.status_code in (200, 201), r.text
    assert r.json()["serial"] == "LAP-001"


@pytest.mark.asyncio
async def test_bad_templates_and_prefixes_are_refused(client):
    H = await _register(client)
    for body in ({"sku_template": "NOCOUNTER"}, {"bin_template": "{C:40}"}, {"barcode_prefix": "50"},
                 {"product_serial_template": "x" * 41}, {"sku_template": "{C:4}<script>"}):
        r = await client.put("/api/staff/inventory-settings", json=body, headers=H)
        assert r.status_code == 422, (body, r.text)


# ── 4. Bulk barcodes and several barcodes per product ────────────────────────

@pytest.mark.asyncio
async def test_bulk_generate_fills_only_products_without_a_barcode(client):
    from backend.services.codes import gtin_check_digit
    A = await _register(client, "A")
    B = await _register(client, "B")
    keep = await _product(client, A, barcode="5012345678900")
    empty1 = await _product(client, A)
    empty2 = await _product(client, A)
    other = await _product(client, B)
    r = await client.post("/api/catalog/products/barcodes/generate-missing", json={}, headers=A)
    assert r.status_code == 200, r.text
    assert r.json()["generated"] == 2
    got = {x["id"]: x for x in (await client.get("/api/catalog/products?limit=200", headers=A)).json()["items"]}
    assert got[keep["id"]]["barcode"] == "5012345678900"       # never rewritten
    codes = {got[empty1["id"]]["barcode"], got[empty2["id"]]["barcode"]}
    assert len(codes) == 2
    for c in codes:
        assert c.startswith("20") and gtin_check_digit(c[:12]) == c[12]
    assert got[empty1["id"]]["barcode_type"] == "ean13"
    # another shop's ids are ignored, and its products untouched
    r = await client.post("/api/catalog/products/barcodes/generate-missing", json={"ids": [other["id"]]}, headers=A)
    assert r.json()["generated"] == 0
    o = (await client.get(f"/api/catalog/products/{other['id']}", headers=B)).json()["product"]
    assert not o["barcode"]


@pytest.mark.asyncio
async def test_bulk_generate_needs_manage_inventory(client):
    H = await _register(client)
    r = await client.post("/api/settings/rbac/roles", json={
        "name": "No stock", "permissions": {"can_manage_inventory": False}}, headers=H)
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    await client.post("/api/staff/employees", json={
        "name": "Tech", "email": email, "password": "pw-test-12345", "role_id": r.json()["id"]}, headers=H)
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})).json()
    T = {"Authorization": f"Bearer {tok['access_token']}"}
    r = await client.post("/api/catalog/products/barcodes/generate-missing", json={}, headers=T)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_extra_barcodes_are_found_by_every_scan_path(client):
    H = await _register(client)
    loc = await _loc(client, H)
    p = await _product(client, H, barcode="MAIN-1")
    r = await client.post(f"/api/catalog/products/{p['id']}/barcodes", json={"barcode": "EXTRA-1"}, headers=H)
    assert r.status_code == 201, r.text
    lst = (await client.get(f"/api/catalog/products/{p['id']}/barcodes", headers=H)).json()["items"]
    assert [(x["barcode"], x["is_main"]) for x in lst] == [("MAIN-1", True), ("EXTRA-1", False)]

    r = await client.get("/api/inventory/scan?code=EXTRA-1", headers=H)
    assert r.status_code == 200 and r.json()["product"]["id"] == p["id"], r.text
    r = await client.post("/api/scan/resolve", json={"code": "EXTRA-1"}, headers=H)
    assert r.json()["type"] == "product" and r.json()["id"] == p["id"]
    r = await client.post("/api/inventory/scan-adjust", json={
        "code": "EXTRA-1", "location_id": loc, "qty": 2, "movement_type": "receive"}, headers=H)
    assert r.status_code == 200 and r.json()["part_id"] == p["id"], r.text
    r = await client.get("/api/catalog/products?search=EXTRA-1", headers=H)
    assert [x["id"] for x in r.json()["items"]] == [p["id"]]

    wid = (await client.get("/api/settings/warehouses?kind=product", headers=H)).json()["items"][0]["id"]
    st = (await client.post("/api/stocktake", json={"warehouse_id": wid}, headers=H)).json()
    r = await client.post(f"/api/stocktake/{st['id']}/scan", json={"code": "EXTRA-1"}, headers=H)
    assert r.status_code == 200, r.text

    # an extra barcode is as unique as a main one; another shop may use it
    q = await _product(client, H)
    r = await client.post(f"/api/catalog/products/{q['id']}/barcodes", json={"barcode": "EXTRA-1"}, headers=H)
    assert r.status_code == 409
    r = await client.post("/api/catalog/products", json={"name": "Z", "barcode": "EXTRA-1"}, headers=H)
    assert r.status_code == 409
    B = await _register(client, "B")
    await _product(client, B, barcode="EXTRA-1")
    r = await client.post(f"/api/catalog/products/{p['id']}/barcodes", json={"barcode": "X"}, headers=B)
    assert r.status_code == 404

    extra = next(x for x in lst if not x["is_main"])
    r = await client.delete(f"/api/catalog/products/{p['id']}/barcodes/{extra['id']}", headers=H)
    assert r.status_code == 200
    r = await client.get("/api/inventory/scan?code=EXTRA-1", headers=H)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_changing_the_main_barcode_frees_the_old_one(client):
    H = await _register(client)
    p = await _product(client, H, barcode="OLD-1")
    r = await client.put(f"/api/catalog/products/{p['id']}", json={"name": p["name"], "barcode": "NEW-1"}, headers=H)
    assert r.status_code == 200, r.text
    await _product(client, H, barcode="OLD-1")
    lst = (await client.get(f"/api/catalog/products/{p['id']}/barcodes", headers=H)).json()["items"]
    assert [x["barcode"] for x in lst] == ["NEW-1"]


# ── Migration pre-flight ─────────────────────────────────────────────────────

def test_migration_refuses_with_a_list_when_codes_are_duplicated(tmp_path):
    """Existing duplicates must stop the upgrade with a readable list, not fail half-way."""
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    path = tmp_path / "dupes.db"
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=f"sqlite+aiosqlite:///{path}")
    env.pop("TEST_DATABASE_URL", None)

    def alembic(target):
        return subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", target],
                              cwd=repo, env=env, capture_output=True, text=True, timeout=300)
    assert alembic("opd1").returncode == 0
    con = sqlite3.connect(path)          # foreign keys off: rows for a shop id without the shop row
    for i, (sku, bc) in enumerate([("DUP-1", "111"), ("dup-1", "111"), ("OK-1", None)]):
        con.execute("INSERT INTO parts (id, org_id, name, sku, barcode, cost_price, avg_cost, sell_price, uom, "
                    "warranty_days, supplier_warranty_days, min_stock, barcode_type, serialized, expiry_tracking, "
                    "active, created_at) VALUES (?, 'shop-1', ?, ?, ?, 0, 0, 0, 'pcs', 0, 0, 0, 'code128', 0, 0, 1, "
                    "'2026-01-01')", (f"p{i}", f"Part {i}", sku, bc))
    con.commit()
    con.close()
    r = alembic("head")
    assert r.returncode != 0
    assert "SKU “dup-1” is on 2 records" in r.stderr
    assert "barcode “111” is on 2 records" in r.stderr
    con = sqlite3.connect(path)          # nothing half-done: the new table was never created
    assert not con.execute("SELECT name FROM sqlite_master WHERE name = 'product_barcodes'").fetchall()
    con.close()
