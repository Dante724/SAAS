"""QA wave 3, W3-D (5-shop re-run on Postgres): stocktake, transfers, purchase orders and product
endpoints. A count is compared with the book when it is counted, so stock that moves before the
count is posted isn't put back; piece products move in whole items; a PO's stage follows what was
received; a markup never quietly lowers a price; another shop's ids and absurd prices are refused."""
import pytest

from backend.tests import kit_helpers as k
from backend.tests.test_qa_f2_stock import _ledger_matches, _on_hand
from backend.tests.test_stocktake_postings_print import _loc, _part, _stock, _wh


async def _other_shop(client):
    import uuid
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "Other", "email": f"o-{uuid.uuid4().hex[:8]}@test.dev",
        "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _count(client, H, st_id, qty, line=0):
    ln = (await client.get(f"/api/stocktake/{st_id}", headers=H)).json()["lines"][line]
    return await client.patch(f"/api/stocktake/{st_id}/lines/{ln['id']}",
                              json={"counted_qty": qty}, headers=H)


# ── 1. stocktake: stock used after the count isn't put back ────────────────────────────────────
@pytest.mark.asyncio
async def test_job_taking_stock_after_the_count_isnt_put_back(client, tenant, db):
    """Shop 5: count 25, a job takes 2 (book 23), post → the book went back to 25."""
    w = await _wh(client, tenant)
    loc = await _loc(client, tenant)
    p = await _part(client, tenant, sku="SCR-25", barcode="SCR-25")
    await _stock(client, tenant, p["id"], 25)
    st = (await client.post("/api/stocktake", json={"warehouse_id": w["id"], "kind": "full"},
                            headers=tenant)).json()
    r = await _count(client, tenant, st["id"], 25)
    assert r.status_code == 200 and r.json()["variance"] == 0
    oid, _ = await k.job(client, tenant, loc)
    await k.add_line(client, tenant, oid, kind="product", line_type="part", ref_id=p["id"],
                     description="Screen", qty=2, unit_price=10)
    assert await _on_hand(db, p["id"]) == 23
    assert (await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)).status_code == 200
    assert await _on_hand(db, p["id"]) == 23            # was 25: the job's 2 came back
    await _ledger_matches(db, p["id"])


@pytest.mark.asyncio
async def test_a_real_shortage_found_by_the_count_still_comes_off(client, tenant, db):
    """Counted 20 against 25, then a job takes 2: the 5 missing and the job's 2 both come off."""
    w = await _wh(client, tenant)
    loc = await _loc(client, tenant)
    p = await _part(client, tenant, sku="SCR-20", barcode="SCR-20")
    await _stock(client, tenant, p["id"], 25)
    st = (await client.post("/api/stocktake", json={"warehouse_id": w["id"], "kind": "full"},
                            headers=tenant)).json()
    assert (await _count(client, tenant, st["id"], 20)).json()["variance"] == -5
    oid, _ = await k.job(client, tenant, loc)
    await k.add_line(client, tenant, oid, kind="product", line_type="part", ref_id=p["id"],
                     description="Screen", qty=2, unit_price=10)
    assert (await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)).status_code == 200
    assert await _on_hand(db, p["id"]) == 18
    await _ledger_matches(db, p["id"])


@pytest.mark.asyncio
async def test_scanned_count_reads_the_book_when_scanned(client, tenant, db):
    w = await _wh(client, tenant)
    loc = await _loc(client, tenant)
    p = await _part(client, tenant, sku="SCN-4", barcode="SCN-4")
    await _stock(client, tenant, p["id"], 4)
    st = (await client.post("/api/stocktake", json={"warehouse_id": w["id"], "kind": "partial"},
                            headers=tenant)).json()
    r = await client.post(f"/api/stocktake/{st['id']}/scan", json={"code": "SCN-4", "qty": 4},
                          headers=tenant)
    assert r.status_code == 200, r.text
    await client.post(f"/api/inventory/parts/{p['id']}/adjust", headers=tenant, json={
        "location_id": loc, "qty": -1, "movement_type": "adjust"})
    assert (await client.post(f"/api/stocktake/{st['id']}/post", headers=tenant)).status_code == 200
    assert await _on_hand(db, p["id"]) == 3
    await _ledger_matches(db, p["id"])


# ── 2. whole items for piece products ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_stocktake_counts_of_pieces_are_whole(client, tenant):
    w = await _wh(client, tenant)
    p = await _part(client, tenant, sku="PC-1", barcode="PC-1")
    await _stock(client, tenant, p["id"], 5)
    st = (await client.post("/api/stocktake", json={"warehouse_id": w["id"], "kind": "full"},
                            headers=tenant)).json()
    r = await _count(client, tenant, st["id"], 2.5)
    assert r.status_code == 422 and "whole" in r.json()["detail"]
    r = await client.post(f"/api/stocktake/{st['id']}/scan", json={"code": "PC-1", "qty": 0.5},
                          headers=tenant)
    assert r.status_code == 422 and "whole" in r.json()["detail"]
    r = await client.post(f"/api/stocktake/{st['id']}/import", headers=tenant, json={
        "rows": [{"code": "PC-1", "counted_qty": "1.5"}]})
    if r.status_code == 200:                      # an import reports each bad row
        assert r.json()["applied"] == 0 and "whole" in r.json()["errors"][0]["error"]
    else:
        assert r.status_code == 422


async def _two_shops(client, H):
    a = await _loc(client, H)
    b = (await client.post("/api/staff/locations", json={"name": "Branch W3D"}, headers=H)).json()["id"]
    return a, b


@pytest.mark.asyncio
async def test_transfers_move_whole_pieces(client, tenant):
    a, b = await _two_shops(client, tenant)
    p = await _part(client, tenant, sku="TR-1", barcode="TR-1")
    await _stock(client, tenant, p["id"], 10)
    r = await client.post("/api/transfers", headers=tenant, json={
        "from_location_id": a, "to_location_id": b, "lines": [{"part_id": p["id"], "qty": 1.5}]})
    assert r.status_code == 422 and "whole" in r.json()["detail"]

    t = (await client.post("/api/transfers", headers=tenant, json={
        "from_location_id": a, "to_location_id": b, "lines": [{"part_id": p["id"], "qty": 2}]})).json()
    r = await client.post(f"/api/transfers/{t['id']}/scan", json={"code": "TR-1", "qty": 0.5},
                          headers=tenant)
    assert r.status_code == 422 and "whole" in r.json()["detail"]

    for s in ("ready", "in_transit"):
        assert (await client.patch(f"/api/transfers/{t['id']}/status", json={"status": s},
                                   headers=tenant)).status_code == 200
    line = (await client.get(f"/api/transfers/{t['id']}", headers=tenant)).json()["lines"][0]
    r = await client.post(f"/api/transfers/{t['id']}/receive", headers=tenant, json={
        "lines": [{"line_id": line["id"], "qty_received": 1.5}]})
    assert r.status_code == 422 and "whole" in r.json()["detail"]


# ── 3. purchase orders and markup ──────────────────────────────────────────────────────────────
async def _po(client, H, qty=4, draft=False):
    sid = (await client.post("/api/suppliers", json={"name": "W3D Supply"}, headers=H)).json()["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Fan", "sku": "W3D-FAN"},
                             headers=H)).json()["id"]
    po = (await client.post("/api/inventory/po", headers=H, json={
        "supplier_id": sid, "location_id": await _loc(client, H), "draft": draft,
        "lines": [{"part_id": pid, "qty_ordered": qty, "unit_cost": 3}]})).json()["po_id"]
    return po, pid


@pytest.mark.asyncio
async def test_po_receive_negative_qty_is_refused(client, tenant, db):
    po, pid = await _po(client, tenant)
    line = (await client.get(f"/api/inventory/po/{po}", headers=tenant)).json()["lines"][0]["id"]
    r = await client.post(f"/api/inventory/po/{po}/receive", headers=tenant,
                          json={"lines": [{"po_line_id": line, "qty": -2}]})
    assert r.status_code == 422, r.text
    assert await _on_hand(db, pid) == 0


@pytest.mark.asyncio
async def test_po_stage_follows_ordered_part_received_received(client, tenant):
    po, _ = await _po(client, tenant, draft=True)
    get = lambda: client.get(f"/api/inventory/po/{po}", headers=tenant)  # noqa: E731
    assert (await get()).json()["workflow_status"] == "po_draft"
    r = await client.post(f"/api/inventory/po/{po}/status", json={"status": "ordered"}, headers=tenant)
    assert r.status_code == 200, r.text
    d = (await get()).json()
    assert d["workflow_status"] == "po_ordered"
    line = d["lines"][0]["id"]
    await client.post(f"/api/inventory/po/{po}/receive", headers=tenant,
                      json={"lines": [{"po_line_id": line, "qty": 1}]})
    assert (await get()).json()["workflow_status"] == "po_partial"
    await client.post(f"/api/inventory/po/{po}/receive", headers=tenant,
                      json={"lines": [{"po_line_id": line, "qty": 3}]})
    assert (await get()).json()["workflow_status"] == "po_received"


@pytest.mark.asyncio
async def test_apply_markup_never_lowers_a_price_unless_asked(client, tenant):
    pid = (await client.post("/api/inventory/parts", headers=tenant, json={
        "name": "Charger", "sku": "W3D-CHG", "cost_price": 14.30, "sell_price": 39.99})).json()["id"]
    cheap = (await client.post("/api/inventory/parts", headers=tenant, json={
        "name": "Cable", "sku": "W3D-CBL", "cost_price": 10, "sell_price": 5})).json()["id"]
    r = await client.post("/api/inventory/parts/apply-markup", headers=tenant, json={
        "markup_pct": 40, "basis": "cost_price", "part_ids": [pid, cheap]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == 1 and body["items"][0]["id"] == cheap
    assert body["items"][0]["old_sell_price"] == 5 and body["items"][0]["sell_price"] == 14
    assert body["kept"] == 1 and body["kept_items"][0]["id"] == pid
    assert (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()["sell_price"] == 39.99

    r = await client.post("/api/inventory/parts/apply-markup", headers=tenant, json={
        "markup_pct": 40, "basis": "cost_price", "part_ids": [pid], "allow_lower": True})
    assert r.json()["updated"] == 1 and r.json()["items"][0]["old_sell_price"] == 39.99
    assert (await client.get(f"/api/inventory/parts/{pid}", headers=tenant)).json()["sell_price"] == 20.02


# ── 4. product endpoints ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_batches_of_an_unknown_product_is_404(client, tenant):
    tenant_b = await _other_shop(client)
    assert (await client.get("/api/inventory/parts/nope/batches", headers=tenant)).status_code == 404
    foreign = (await client.post("/api/inventory/parts", json={"name": "Theirs", "sku": "TB-1"},
                                 headers=tenant_b)).json()["id"]
    assert (await client.get(f"/api/inventory/parts/{foreign}/batches",
                             headers=tenant)).status_code == 404


@pytest.mark.asyncio
async def test_a_price_past_the_column_is_422_not_500(client, tenant):
    r = await client.post("/api/inventory/parts", headers=tenant, json={
        "name": "Huge", "sku": "HUGE-1", "sell_price": 99999999999})
    assert r.status_code == 422, r.text
    pid = (await client.post("/api/inventory/parts", headers=tenant, json={
        "name": "Fine", "sku": "FINE-1", "sell_price": 10})).json()["id"]
    r = await client.put(f"/api/inventory/parts/{pid}", headers=tenant, json={
        "name": "Fine", "sell_price": 99999999999})
    assert r.status_code == 422, r.text
    r = await client.post("/api/catalog/products", headers=tenant, json={
        "name": "Huge 2", "sku": "HUGE-2", "sell_price": 99999999999})
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_another_shops_warehouse_access_is_404(client, tenant):
    tenant_b = await _other_shop(client)
    theirs = next(w for w in (await client.get("/api/settings/warehouses?kind=product",
                                               headers=tenant_b)).json()["items"] if w["is_default"])
    assert (await client.get(f"/api/settings/warehouses/{theirs['id']}/access",
                             headers=tenant)).status_code == 404
    mine = await _wh(client, tenant)
    assert (await client.get(f"/api/settings/warehouses/{mine['id']}/access",
                             headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_reorder_forecast_suggests_whole_pieces(client, tenant):
    loc = await _loc(client, tenant)
    pid = await k.part(client, tenant, loc, "Glue", 5, 1, qty=1, sku="W3D-GLUE")
    oid, _ = await k.job(client, tenant, loc)
    await k.add_line(client, tenant, oid, kind="product", line_type="part", ref_id=pid,
                     description="Glue", qty=1, unit_price=5)
    # QA N15: 1 used in 28 days → 0.5 over the 14-day cover
    item = next(i for i in (await client.get("/api/inventory/reorder-forecast", headers=tenant)).json()["items"]
                if i["part_id"] == pid)
    assert item["suggest_qty"] == 1
