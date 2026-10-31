"""ii144b — create and scan every common barcode type.

- render + round-trip decode for each type (encoded here, decoded by zxing-cpp's reader);
- check-digit validation; UPC-E expansion; UPC-A ↔ EAN-13 (and GTIN-14) are one product;
- GS1 Application Identifiers from GS1-128 / GS1 DataMatrix / GS1 QR, raw and bracketed;
- AIM symbology prefixes stripped;
- uniqueness across types (the same GTIN as UPC-A and EAN-13 is a duplicate), per shop;
- one GS1 scan fills product, batch, expiry and serial at intake.
"""
import uuid
from datetime import date

import pytest
import zxingcpp

from backend.services import barcode_types as bt
from backend.services import scan_codes as sc

SAMPLES = {
    "ean13": "200000000001", "ean8": "2000001", "upca": "03600029145", "upce": "0425261",
    "itf14": "1540014128876", "itf": "12345678", "code39": "abc-123", "code128": "Hello 12",
    "gs1_128": "(01)02000000000015(17)261231(10)L1", "qr": "(01)02000000000015(21)SN1",
    "datamatrix": "(01)02000000000015(10)B7", "pdf417": "Tekly label 42",
}


@pytest.mark.parametrize("kind", list(bt.TYPES))
def test_every_type_renders_and_decodes_back_to_the_same_product(kind):
    value = bt.normalize(kind, SAMPLES[kind])
    svg = bt.svg(kind, value)
    assert svg.startswith("<svg") and "<rect" in svg
    img = zxingcpp.write_barcode_to_image(bt.encode(kind, value), scale=3)
    got = zxingcpp.read_barcode(img)
    assert got is not None, kind
    # whatever form the reader reports (UPC-A as EAN-13, UPC-E expanded, GS1 bracketed), the scan
    # finds the stored code: its match key is among the scan's keys
    assert sc.match_key(value, kind) in sc.parse(got.text).keys


@pytest.mark.asyncio
async def test_code_svg_endpoint_accepts_all_types(client, tenant):
    for kind in bt.TYPES:
        r = await client.get("/api/catalog/code.svg", params={"type": kind, "data": SAMPLES[kind]}, headers=tenant)
        assert r.status_code == 200, (kind, r.text)
        assert r.text.startswith("<svg")
    r = await client.get("/api/catalog/code.svg", params={"type": "ean13", "data": "2000000000016"}, headers=tenant)
    assert r.status_code == 400 and "check digit" in r.json()["detail"]


def test_check_digits_are_validated():
    assert bt.normalize("ean13", "2000000000015") == "2000000000015"
    assert bt.normalize("ean8", "2000001") == "20000011"
    assert bt.normalize("itf14", "1540014128876") == "15400141288763"
    for kind, bad in (("ean13", "2000000000016"), ("ean8", "20000012"), ("upca", "036000291453"),
                      ("itf14", "15400141288760"), ("upce", "04252615")):
        with pytest.raises(ValueError, match="check digit"):
            bt.normalize(kind, bad)
    with pytest.raises(ValueError):
        bt.normalize("itf", "123")                       # odd length
    with pytest.raises(ValueError):
        bt.normalize("code39", "lower_case!")
    with pytest.raises(ValueError):
        bt.normalize("gs1_128", "not gs1")
    assert bt.normalize("code39", "abc-1") == "ABC-1"


def test_upce_expands_to_upca():
    assert sc.upce_to_upca("04252614") == "042100005264"
    assert sc.upce_to_upca("0425261") == "042100005264"       # check digit computed
    assert sc.upce_to_upca("01234565") == "012345000065"
    assert sc.upce_to_upca("04252615") is None                # wrong check digit
    assert sc.upce_to_upca("24252614") is None                # number system must be 0 or 1


def test_upca_ean13_and_gtin14_are_one_code():
    keys = {sc.match_key("036000291452"), sc.match_key("0036000291452"), sc.match_key("00036000291452"),
            sc.match_key("(01)00036000291452"), sc.match_key("04210000526", "upca")}
    assert len(keys) == 2                                     # the last one is a different UPC-A
    assert sc.match_key("04252614", "upce") == sc.match_key("042100005264")
    assert sc.parse("036000291452").keys[0] == sc.parse("0036000291452").keys[0]


def test_gs1_application_identifiers():
    s = sc.parse("]C1" + "0102000000000015" + "17261231" + "10LOT9" + sc.GS + "21SN1")
    assert (s.gtin, s.lot, s.expiry, s.serial) == ("02000000000015", "LOT9", date(2026, 12, 31), "SN1")
    assert s.text == "(01)02000000000015(17)261231(10)LOT9(21)SN1"
    b = sc.parse("(01)02000000000015(17)270200(10)A1")
    assert b.expiry == date(2027, 2, 28)                      # day 00 = last day of the month
    q = sc.parse("]Q3" + "0102000000000015" + "21X7")
    assert q.serial == "X7" and q.symbology == "]Q3"
    d = sc.parse("]d2" + "0102000000000015" + "10B" + "<GS>" + "17261231")   # GS typed out by a wedge
    assert d.lot == "B" and d.expiry == date(2026, 12, 31)
    assert sc.parse("0102000000000015").gtin == "02000000000015"   # unflagged, starts with (01)
    assert sc.parse("ABC-123").ais == []


def test_aim_prefixes_are_stripped():
    assert sc.parse("]E00036000291452").text == "0036000291452"
    assert sc.parse("]C0Hello").text == "Hello"
    assert sc.parse("]A0ABC-123").text == "ABC-123"
    assert sc.parse(" ]Q1hello\r\n").text == "hello"


async def _register(client, name="Co"):
    r = await client.post("/api/auth/register", json={
        "org_name": name, "name": "Owner", "email": f"bt-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _product(client, H, **kw):
    r = await client.post("/api/catalog/products", json={"name": f"P {uuid.uuid4().hex[:6]}", **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_the_same_gtin_in_another_format_is_a_duplicate_but_not_across_shops(client):
    A = await _register(client, "A")
    B = await _register(client, "B")
    p = await _product(client, A, barcode="036000291452", barcode_type="upca")
    r = await client.post("/api/catalog/products", json={"name": "Twin", "barcode": "0036000291452",
                                                         "barcode_type": "ean13"}, headers=A)
    assert r.status_code == 409 and "another barcode format" in r.json()["detail"]
    q = await _product(client, A)
    r = await client.post(f"/api/catalog/products/{q['id']}/barcodes",
                          json={"barcode": "(01)00036000291452", "barcode_type": "gs1_128"}, headers=A)
    assert r.status_code == 409
    await _product(client, B, barcode="0036000291452", barcode_type="ean13")   # another shop: fine
    # every form finds the product
    for code in ("036000291452", "0036000291452", "]E00036000291452", "00036000291452"):
        r = await client.get("/api/inventory/scan", params={"code": code}, headers=A)
        assert r.status_code == 200 and r.json()["product"]["id"] == p["id"], code


@pytest.mark.asyncio
async def test_products_can_carry_several_types_and_every_scan_path_finds_them(client):
    H = await _register(client)
    p = await _product(client, H, barcode="2000000000015", barcode_type="ean13")
    for code, kind in (("04252614", "upce"), ("ABC-77", "code39"), ("PART-DM-1", "datamatrix"), ("12345678", "itf")):
        r = await client.post(f"/api/catalog/products/{p['id']}/barcodes", json={"barcode": code, "barcode_type": kind},
                              headers=H)
        assert r.status_code == 201, (kind, r.text)
    lst = (await client.get(f"/api/catalog/products/{p['id']}/barcodes", headers=H)).json()["items"]
    assert {x["barcode_type"] for x in lst} == {"ean13", "upce", "code39", "datamatrix", "itf"}
    # UPC-E scanned as its expanded UPC-A / EAN-13 form
    for code in ("042100005264", "0042100005264", "]E00042100005264"):
        r = await client.post("/api/scan/resolve", json={"code": code}, headers=H)
        assert r.json().get("id") == p["id"], code
    r = await client.get("/api/sales/lookup", params={"code": "]A0ABC-77"}, headers=H)
    assert [x["id"] for x in r.json()["items"]] == [p["id"]], r.text
    # a bad check digit for the chosen type is refused with the reason
    r = await client.post(f"/api/catalog/products/{p['id']}/barcodes", json={"barcode": "2000000000016",
                                                                            "barcode_type": "ean13"}, headers=H)
    assert r.status_code == 422 and "check digit" in r.json()["detail"]


@pytest.mark.asyncio
async def test_generating_each_type_gives_scannable_shop_codes(client):
    H = await _register(client)
    for kind in ("ean13", "ean8", "upca", "itf14", "code128", "code39", "gs1_128", "qr", "datamatrix", "pdf417"):
        p = await _product(client, H)
        r = await client.post(f"/api/catalog/products/{p['id']}/barcode/generate?type={kind}", headers=H)
        assert r.status_code == 200, (kind, r.text)
        code = r.json()["barcode"]
        bt.normalize(kind, code)                            # valid for its type
        r = await client.get("/api/catalog/code.svg", params={"type": kind, "data": code}, headers=H)
        assert r.status_code == 200, (kind, r.text)
    p = await _product(client, H)
    r = await client.post(f"/api/catalog/products/{p['id']}/barcode/generate?type=upce", headers=H)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_one_gs1_scan_fills_product_batch_expiry_and_serial_at_intake(client, db):
    from sqlalchemy import select

    from backend.models import SerializedItem, StockItemBatch
    H = await _register(client)
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    p = await _product(client, H, barcode="2000000000015", barcode_type="ean13", serialized=True)
    label = "]d2" + "0102000000000015" + "17261231" + "10LOT9" + sc.GS + "21SN-777"
    r = await client.post("/api/scan/parse", json={"code": label}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product"]["id"] == p["id"]
    assert (body["lot"], body["expiry"], body["serial"]) == ("LOT9", "2026-12-31", "SN-777")

    r = await client.post("/api/inventory/postings/preview", json={"rows": [{"code": label, "qty": 1}]}, headers=H)
    row = r.json()["rows"][0]
    assert row["part_id"] == p["id"] and row["serials"] == ["SN-777"] and row["needs_serials"] == 0
    assert row["lot"] == "LOT9" and row["expiry_date"] == "2026-12-31"
    r = await client.post("/api/inventory/postings/commit", json={"location_id": loc, "rows": [
        {"part_id": p["id"], "qty": 1, "purchase_price": 5, "serials": row["serials"], "lot": row["lot"]}]},
        headers=H)
    assert r.status_code == 201, r.text
    batch = (await db.execute(select(StockItemBatch).where(StockItemBatch.part_id == p["id"]))).scalar_one()
    assert batch.lot_number == "LOT9"
    unit = (await db.execute(select(SerializedItem).where(SerializedItem.part_id == p["id"]))).scalar_one()
    assert unit.serial_number == "SN-777"
    # scanning the same label in a stocktake finds the unit by its (21) serial
    wid = (await client.get("/api/settings/warehouses?kind=product", headers=H)).json()["items"][0]["id"]
    st = (await client.post("/api/stocktake", json={"warehouse_id": wid}, headers=H)).json()
    r = await client.post(f"/api/stocktake/{st['id']}/scan", json={"code": label}, headers=H)
    assert r.status_code == 200 and r.json()["part_id"] == p["id"], r.text


@pytest.mark.asyncio
async def test_bin_labels_take_a_type_that_fits_their_code(client):
    H = await _register(client)
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    r = await client.post("/api/bins", json={"location_id": loc, "name": "A1", "barcode_type": "datamatrix"}, headers=H)
    assert r.status_code == 201 and r.json()["barcode_type"] == "datamatrix"
    r = await client.post("/api/bins", json={"location_id": loc, "name": "A2", "barcode_type": "ean13"}, headers=H)
    assert r.status_code == 422 and "EAN-13" in r.json()["detail"]


# ── review findings ──────────────────────────────────────────────────────────

def test_unicode_digits_are_not_digits():
    for v in ("²" * 12, "٠٣٦٠٠٠٢٩١٤٥٢", "０３６０００２９１４５２"):
        s = sc.parse(v)                                   # no crash
        assert s.gtin is None
        assert sc.match_key(v) == v                       # never a GTIN key
        with pytest.raises(ValueError):
            bt.normalize("upca", v)
    assert sc.parse("​036000291452​").gtin == "00036000291452"   # zero-width spaces dropped


def test_a_code_that_merely_looks_like_gs1_still_finds_itself():
    raw = "0100036000291452211234"                     # a Code 128 / serial that happens to parse as GS1
    s = sc.parse(raw)
    assert raw in s.keys and raw in s.all_texts()


def test_unknown_ai_after_a_gtin_keeps_the_gtin_and_duplicate_ais_are_refused():
    s = sc.parse("]C1" + "0112345678901231" + "10ABC" + sc.GS + "7240XYZ")
    assert s.gtin == "12345678901231" and s.lot == "ABC"
    s = sc.parse("(01)12345678901231(9999)X")
    assert s.gtin is None or s.gtin == "12345678901231"
    assert sc.parse_gs1("(10)A(10)B") is None


def test_eight_digit_codes_prefer_ean8_unless_the_scanner_says_upce():
    s = sc.parse("12345670")
    upce_key = sc.upce_to_upca("12345670").zfill(14)
    assert s.keys[0] == "00000012345670" and s.fallback_keys == [upce_key]
    assert sc.parse("]E012345670").keys[0] == upce_key
    assert sc.parse("]E412345670").fallback_keys == []


@pytest.mark.asyncio
async def test_eight_digit_scan_picks_the_ean8_product_over_a_upce_reading(client):
    H = await _register(client)
    a = await _product(client, H, barcode="12345670", barcode_type="ean8")
    b = await _product(client, H, barcode=sc.upce_to_upca("12345670"), barcode_type="upca")
    r = await client.get("/api/inventory/scan", params={"code": "12345670"}, headers=H)
    assert r.json()["product"]["id"] == a["id"]
    r = await client.get("/api/inventory/scan", params={"code": "]E012345670"}, headers=H)
    assert r.json()["product"]["id"] == b["id"]


@pytest.mark.asyncio
async def test_generated_itf_prints_and_unicode_barcode_saves_without_a_500(client):
    H = await _register(client)
    p = await _product(client, H)
    r = await client.post(f"/api/catalog/products/{p['id']}/barcode/generate?type=itf", headers=H)
    code = r.json()["barcode"]
    assert len(code) % 2 == 0
    r = await client.get("/api/catalog/code.svg", params={"type": "itf", "data": code}, headers=H)
    assert r.status_code == 200
    r = await client.post("/api/catalog/products", json={"name": "U", "barcode": "²" * 13}, headers=H)
    assert r.status_code == 422                           # refused with a reason, not a 500
    r = await client.post("/api/scan/resolve", json={"code": "²" * 12}, headers=H)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_bin_code_change_rechecks_the_label_type_and_code39_is_stored_as_printed(client):
    H = await _register(client)
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    b = (await client.post("/api/bins", json={"location_id": loc, "name": "A1", "barcode": "shelf-a1",
                                             "barcode_type": "code39"}, headers=H)).json()
    assert b["barcode"] == "SHELF-A1"
    b2 = (await client.post("/api/bins", json={"location_id": loc, "name": "A2", "barcode": "2000000000015",
                                              "barcode_type": "ean13"}, headers=H)).json()
    r = await client.put(f"/api/bins/{b2['id']}", json={"location_id": loc, "name": "A2", "barcode": "NOT-EAN"},
                         headers=H)
    assert r.status_code == 422


def test_migration_dedupes_one_product_holding_both_forms_and_refuses_two(tmp_path):
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false")
    env.pop("TEST_DATABASE_URL", None)

    def alembic(path, target):
        return subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", target],
                              cwd=repo, env={**env, "DATABASE_URL": f"sqlite+aiosqlite:///{path}"},
                              capture_output=True, text=True, timeout=300)

    def seed(path, rows):
        con = sqlite3.connect(path)
        for pid in {r[1] for r in rows}:
            con.execute("INSERT INTO parts (id, org_id, name, cost_price, avg_cost, sell_price, uom, warranty_days, "
                        "supplier_warranty_days, min_stock, barcode_type, serialized, expiry_tracking, active, "
                        "created_at) VALUES (?, 'shop-1', ?, 0, 0, 0, 'pcs', 0, 0, 0, 'code128', 0, 0, 1, "
                        "'2026-01-01')", (pid, f"Part {pid}"))
        for rid, pid, code, main in rows:
            con.execute("INSERT INTO product_barcodes (id, org_id, part_id, barcode, is_main, active, created_at) "
                        "VALUES (?, 'shop-1', ?, ?, ?, 1, '2026-01-01')", (rid, pid, code, main))
        con.commit()
        con.close()

    ok = tmp_path / "ok.db"
    assert alembic(ok, "ii144a01").returncode == 0
    seed(ok, [("b1", "p1", "036000291452", 1), ("b2", "p1", "0036000291452", 0)])
    r = alembic(ok, "head")
    assert r.returncode == 0, r.stderr[-2000:]
    con = sqlite3.connect(ok)
    assert con.execute("SELECT id, match_key FROM product_barcodes").fetchall() == [("b1", "00036000291452")]
    con.close()

    bad = tmp_path / "bad.db"
    assert alembic(bad, "ii144a01").returncode == 0
    seed(bad, [("b1", "p1", "036000291452", 1), ("b2", "p2", "0036000291452", 1)])
    r = alembic(bad, "head")
    assert r.returncode != 0 and "same product number" in r.stderr
