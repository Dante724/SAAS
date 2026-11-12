"""Audit P1, SEC-04: a role limited to some warehouses can't move or see stock in the others, on
any path.

Before, the warehouse scope was checked by 6 endpoints (adjust, write-off and supplier-return
documents, bins, one sales path). A technician limited to branch B could still take a part from
the main branch's shelf onto a job, ship it on a transfer, and read every branch's stock.

The reach check now sits in the stock-row service (`stock_rows.add/draw/reserve`), which every
stock change goes through, using the person on the request. The stock read endpoints show only
the warehouses the person can reach."""
import pytest
from sqlalchemy import select

from backend.tests import kit_helpers as K
from backend.tests.test_scoping_crm_chat import _scoped_login

pytestmark = pytest.mark.asyncio


async def _setup(client, tenant, db):
    from backend.models import Location
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    main = await K.loc(client, tenant)
    b = Location(org_id=me["org_id"], name="Branch B")
    db.add(b)
    await db.commit()
    await db.refresh(b)
    pid = await K.part(client, tenant, main, "Screen", 99, 10, qty=5)       # stock at main only
    r = await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant, json={
        "location_id": b.id, "qty": 2, "movement_type": "receive", "unit_cost": 10})
    assert r.status_code == 200, r.text
    H, _emp, _role = await _scoped_login(client, db, me["org_id"], email=f"wh-{pid[:6]}@x.dev",
                                         warehouse_scope="selected", warehouse_ids=[b.id])
    return main, b.id, pid, H


async def _at(db, pid, loc):
    from sqlalchemy import func

    from backend.models import StockLevel
    db.expire_all()
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0)).where(
        StockLevel.part_id == pid, StockLevel.location_id == loc))).scalar_one())


async def test_job_at_another_branch_cant_take_its_stock(client, tenant, db):
    main, b, pid, H = await _setup(client, tenant, db)
    oid, _ = await K.job(client, tenant, main)
    r = await client.post(f"/api/orders/{oid}/lines", headers=H, json={
        "kind": "product", "line_type": "part", "ref_id": pid, "description": "Screen", "qty": 1, "unit_price": 99})
    assert r.status_code == 403, r.text                                     # was 201: took from main
    assert await _at(db, pid, main) == 5
    # at their own branch it works
    ob, _ = await K.job(client, tenant, b)
    r = await client.post(f"/api/orders/{ob}/lines", headers=H, json={
        "kind": "product", "line_type": "part", "ref_id": pid, "description": "Screen", "qty": 1, "unit_price": 99})
    assert r.status_code in (200, 201), r.text
    assert await _at(db, pid, b) == 1


async def test_transfer_out_of_another_branch_is_refused(client, tenant, db):
    main, b, pid, H = await _setup(client, tenant, db)
    t = (await client.post("/api/transfers", headers=tenant, json={
        "from_location_id": main, "to_location_id": b, "lines": [{"part_id": pid, "qty": 2}]})).json()
    r = await client.patch(f"/api/transfers/{t['id']}/status", json={"status": "ready"}, headers=H)
    assert r.status_code == 403, r.text                                     # was 200: boxed main's stock
    assert await _at(db, pid, main) == 5


async def test_reservation_at_another_branch_is_refused(client, tenant, db):
    main, b, pid, H = await _setup(client, tenant, db)
    r = await client.post("/api/inventory/reserve", headers=H, json={"part_id": pid, "location_id": main, "qty": 1})
    assert r.status_code == 403, r.text
    r = await client.post("/api/inventory/reserve", headers=H, json={"part_id": pid, "location_id": b, "qty": 1})
    assert r.status_code == 201, r.text


async def test_stock_reads_show_only_reachable_warehouses(client, tenant, db):
    main, b, pid, H = await _setup(client, tenant, db)
    stock = (await client.get(f"/api/inventory/parts/{pid}/stock", headers=H)).json()
    assert {r["location_id"] for r in stock["items"]} == {b} and stock["total_available"] == 2
    assert (await client.get(f"/api/inventory/parts/{pid}", headers=H)).json()["qty_available"] == 2
    listed = (await client.get("/api/inventory/parts", headers=H)).json()["items"]
    assert next(p for p in listed if p["id"] == pid)["qty_available"] == 2
    cat = (await client.get("/api/catalog/products", headers=H)).json()["items"]
    assert next(p for p in cat if p["id"] == pid)["qty_available"] == 2
    assert (await client.get(f"/api/catalog/products/{pid}", headers=H)).json()["product"]["qty_available"] == 2
    mv = (await client.get(f"/api/inventory/parts/{pid}/movements", headers=H)).json()
    assert [m["location"] for m in mv] == ["Branch B"]
    batches = (await client.get(f"/api/inventory/parts/{pid}/batches", headers=H)).json()["items"]
    assert [x["location"] for x in batches] == ["Branch B"]
    # the shop owner still sees everything
    assert (await client.get(f"/api/inventory/parts/{pid}/stock", headers=tenant)).json()["total_available"] == 7

    # transfers between two warehouses they can't reach aren't listed or opened
    from backend.models import Location
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    c = Location(org_id=me["org_id"], name="Branch C")
    db.add(c)
    await db.commit()
    await db.refresh(c)
    t = (await client.post("/api/transfers", headers=tenant, json={
        "from_location_id": main, "to_location_id": c.id, "lines": [{"part_id": pid, "qty": 1}]})).json()
    ids = {x["id"] for x in (await client.get("/api/transfers", headers=H)).json()["items"]}
    assert t["id"] not in ids
    assert (await client.get(f"/api/transfers/{t['id']}", headers=H)).status_code in (403, 404)
