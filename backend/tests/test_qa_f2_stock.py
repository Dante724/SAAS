"""QA fix F2 (5-shop run, 31 Oct 2026): POS returns, stocktake counts, transfer scan/receive and
serialized (IMEI) items move stock exactly once and only when they should."""
import pytest
from sqlalchemy import func, select

from backend.models import StockLevel, StockMovement
from backend.tests.test_stocktake_postings_print import _loc, _part, _stock, _wh


async def _on_hand(db, part_id):
    db.expire_all()
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
                                   .where(StockLevel.part_id == part_id))).scalar_one())


async def _moved(db, part_id):
    return float((await db.execute(select(func.coalesce(func.sum(StockMovement.qty), 0))
                                   .where(StockMovement.part_id == part_id))).scalar_one())


async def _ledger_matches(db, part_id):
    """Every unit on the shelf is explained by a movement."""
    assert await _on_hand(db, part_id) == pytest.approx(await _moved(db, part_id))


# ── 2. stocktake: the count is what's on the shelf when it posts ───────────────────────────────
@pytest.mark.asyncio
async def test_stock_moved_during_the_count_isnt_subtracted_again(client, tenant, db):
    w = await _wh(client, tenant)
    p = await _part(client, tenant, sku="PADS-1", barcode="PADS-1")
    await _stock(client, tenant, p["id"], 31)
    st = (await client.post("/api/stocktake", json={"warehouse_id": w["id"], "kind": "full"},
                            headers=tenant)).json()
    # two pads written off while the count is under way: the shelf now really holds 29
    r = await client.post(f"/api/inventory/parts/{p['id']}/adjust", headers=tenant, json={
        "location_id": await _loc(client, tenant), "qty": -2, "movement_type": "adjust"})
    assert r.status_code in (200, 201), r.text
    line = (await client.get(f"/api/stocktake/{st['id']}", headers=tenant)).json()["lines"][0]
    assert (await client.patch(f"/api/stocktake/{st['id']}/lines/{line['id']}",
                               json={"counted_qty": 29}, headers=tenant)).status_code == 200
    assert (await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)).status_code == 200
    assert await _on_hand(db, p["id"]) == 29            # was 27: the write-off was taken twice
    posted = (await client.get(f"/api/stocktake/{st['id']}", headers=tenant)).json()["lines"][0]
    assert posted["system_qty"] == 29 and posted["variance"] == 0
    await _ledger_matches(db, p["id"])


@pytest.mark.asyncio
async def test_posted_count_cant_be_edited_and_negative_is_refused(client, tenant):
    w = await _wh(client, tenant)
    p = await _part(client, tenant, sku="PADS-2", barcode="PADS-2")
    await _stock(client, tenant, p["id"], 5)
    st = (await client.post("/api/stocktake", json={"warehouse_id": w["id"], "kind": "full"},
                            headers=tenant)).json()
    line = (await client.get(f"/api/stocktake/{st['id']}", headers=tenant)).json()["lines"][0]
    url = f"/api/stocktake/{st['id']}/lines/{line['id']}"
    assert (await client.patch(url, json={"counted_qty": -3}, headers=tenant)).status_code == 422
    assert (await client.patch(url, json={"counted_qty": 5}, headers=tenant)).status_code == 200
    assert (await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)).status_code == 200
    assert (await client.patch(url, json={"counted_qty": 99}, headers=tenant)).status_code == 409


# ── 3. transfers: scan only adds, and a short delivery can be recorded from what the API returns ─
async def _draft_transfer(client, tenant, db, sku, stock=10):
    from backend.models import Location
    from backend.tests.test_stocktake_postings_print import _me
    me = await _me(client, tenant)
    dest = Location(org_id=me["org_id"], name=f"Back store {sku}")
    db.add(dest)
    await db.commit()
    p = await _part(client, tenant, sku=sku, barcode=sku)
    await _stock(client, tenant, p["id"], stock)
    t = (await client.post("/api/transfers", headers=tenant, json={
        "from_location_id": await _loc(client, tenant), "to_location_id": dest.id,
        "lines": [{"part_id": p["id"], "qty": 1}]})).json()
    return t["id"], p, dest.id


@pytest.mark.asyncio
async def test_transfer_scan_refuses_zero_and_negative_qty(client, tenant, db):
    tid, p, _ = await _draft_transfer(client, tenant, db, "BC-1700")
    ok = await client.post(f"/api/transfers/{tid}/scan", json={"code": "BC-1700", "qty": 3}, headers=tenant)
    assert ok.status_code == 200 and ok.json()["line_id"] and ok.json()["qty"] == 4
    for bad in (-5, 0):
        r = await client.post(f"/api/transfers/{tid}/scan", json={"code": "BC-1700", "qty": bad}, headers=tenant)
        assert r.status_code == 422, r.text
    lines = (await client.get(f"/api/transfers/{tid}", headers=tenant)).json()["lines"]
    assert [ln["qty"] for ln in lines] == [4]


@pytest.mark.asyncio
async def test_short_delivery_recorded_with_ids_the_api_returns(client, tenant, db):
    tid, p, dest = await _draft_transfer(client, tenant, db, "TUBE-1")
    await client.post(f"/api/transfers/{tid}/scan", json={"code": "TUBE-1", "qty": 3}, headers=tenant)
    for s in ("ready", "in_transit"):
        assert (await client.patch(f"/api/transfers/{tid}/status", json={"status": s}, headers=tenant)).status_code == 200
    detail = (await client.get(f"/api/transfers/{tid}", headers=tenant)).json()["lines"][0]
    listed = next(t for t in (await client.get("/api/transfers", headers=tenant)).json()["items"] if t["id"] == tid)
    assert detail["id"] == detail["line_id"] == listed["lines"][0]["line_id"]
    # by line id (from the detail) …
    r = await client.post(f"/api/transfers/{tid}/receive", headers=tenant,
                          json={"lines": [{"line_id": detail["line_id"], "qty_received": 3}]})
    assert r.status_code == 200 and r.json()["discrepancies"] == 1, r.text
    # … or by the product, as the person holding the box knows it
    r = await client.post(f"/api/transfers/{tid}/receive", headers=tenant,
                          json={"lines": [{"part_id": p["id"], "qty_received": 3}]})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/transfers/{tid}/receive", headers=tenant,
                          json={"lines": [{"code": "TUBE-1", "qty_received": 3}]})
    assert r.status_code == 200, r.text
    assert (await client.post(f"/api/transfers/{tid}/receive", headers=tenant,
                              json={"lines": [{"qty_received": 3}]})).status_code == 422
    assert (await client.patch(f"/api/transfers/{tid}/status", json={"status": "completed"},
                               headers=tenant)).status_code == 200
    db.expire_all()
    at_dest = (await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0)).where(
        StockLevel.part_id == p["id"], StockLevel.location_id == dest))).scalar_one()
    assert float(at_dest) == 3 and await _on_hand(db, p["id"]) == 9      # 1 booked as lost
    await _ledger_matches(db, p["id"])


# ── 4. serialized (IMEI) items: the serial is asked for wherever stock moves ──────────────────
async def _phone(client, tenant, n=2):
    from backend.tests.test_inventory_bins_passport_stocktake import SN, _product, _receive
    loc = await _loc(client, tenant)
    p = await _product(client, tenant, name="iPhone 13", serialized=True)
    await _receive(client, tenant, loc, p["id"], n, serials=SN[5:5 + n])
    return p, loc, SN[5:5 + n]


@pytest.mark.asyncio
async def test_till_needs_the_imei_of_a_serialized_phone_and_shows_it(client, tenant, db):
    from backend.models import SerializedItem
    p, _, sns = await _phone(client, tenant)
    line = {"kind": "product", "ref_id": p["id"], "description": "iPhone 13", "qty": 1, "unit_price": 400}
    r = await client.post("/api/sales", json={"lines": [line]}, headers=tenant)
    assert r.status_code == 422 and "IMEI" in r.json()["detail"], r.text          # was: oldest unit, silently
    assert await _on_hand(db, p["id"]) == 2
    # scanning the phone's IMEI at the till says which product and which unit
    hit = (await client.get(f"/api/sales/lookup?code={sns[1]}", headers=tenant)).json()["items"][0]
    assert hit["id"] == p["id"] and hit["serialized"] and hit["serial"] == sns[1]
    r = await client.post("/api/sales", json={"lines": [{**line, "serials": [sns[1]]}]}, headers=tenant)
    assert r.status_code == 201, r.text
    db.expire_all()
    status = {u.serial_number: u.status for u in (await db.execute(select(SerializedItem).where(
        SerializedItem.part_id == p["id"]))).scalars()}
    assert status == {sns[0]: "in_stock", sns[1]: "sold"}
    rc = (await client.get(f"/api/sales/{r.json()['id']}/receipt", headers=tenant)).json()
    assert rc["lines"][0]["serials"] == [sns[1]] and sns[1] in rc["text"]
    assert await _on_hand(db, p["id"]) == 1
    await _ledger_matches(db, p["id"])


@pytest.mark.asyncio
async def test_serialized_stock_only_rises_with_a_serial_per_unit(client, tenant, db):
    from backend.models import SerializedItem
    from backend.tests.test_inventory_bins_passport_stocktake import SN
    p, loc, _ = await _phone(client, tenant, n=1)
    url = f"/api/inventory/parts/{p['id']}/adjust"
    r = await client.post(url, json={"location_id": loc, "qty": 2, "movement_type": "receive"}, headers=tenant)
    assert r.status_code == 422 and "serial" in r.json()["detail"], r.text      # was 200: +2, no units
    r = await client.post(url, json={"location_id": loc, "qty": 2, "movement_type": "adjust",
                                     "serials": [SN[8]]}, headers=tenant)
    assert r.status_code == 422, r.text                                         # one short
    r = await client.post(url, json={"location_id": loc, "qty": 2, "movement_type": "receive",
                                     "serials": SN[8:10]}, headers=tenant)
    assert r.status_code == 200, r.text
    db.expire_all()
    units = (await db.execute(select(SerializedItem).where(SerializedItem.part_id == p["id"],
                                                           SerializedItem.status == "in_stock"))).scalars().all()
    assert len(units) == 3 and await _on_hand(db, p["id"]) == 3                # every unit has its serial
    await _ledger_matches(db, p["id"])

