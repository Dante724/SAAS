"""P1 audit 2026-11-12: PERF-03, PERF-04, PERF-05, PERF-20. Lists and reports don't drag photos,
passcodes or attachments out of the database just to print names and numbers.

Each test captures the SQL an endpoint runs and checks the SELECT column lists: the heavy column
may still appear in a WHERE (``image_data IS NOT NULL``) or inside length()/substr(), never as a
selected column.
"""
import re

import pytest
from sqlalchemy import event, select

PNG = "data:image/png;base64," + ("iVBORw0KGgo" + "A" * 50_000)


class _Sql:
    def __init__(self, engine):
        self.engine, self.sql = engine, []

    def _hit(self, conn, cur, stmt, params, ctx, many):
        self.sql.append(stmt)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hit)


def _select_lists(sql: str) -> list[str]:
    """The column list of every SELECT in a statement (nested ones included)."""
    out = []
    for m in re.finditer(r"\bSELECT\b(.*?)\bFROM\b", sql, re.S | re.I):
        out.append(m.group(1))
    return out


def _selects_column(statements, column: str) -> list[str]:
    bad = []
    for s in statements:
        for cols in _select_lists(s):
            # length(parts.image_data) / substr(parts.image_data, …) / IS NOT NULL tests are fine
            stripped = re.sub(r"(length|substr|substring)\s*\([^()]*\)", "", cols, flags=re.I)
            stripped = re.sub(r"\(?[\w.]+\s+IS\s+NOT\s+NULL\)?", "", stripped, flags=re.I)
            if column in stripped:
                bad.append(s[:200])
    return bad


async def _seed_inventory(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    sup = (await client.post("/api/suppliers", json={"name": "Sup"}, headers=tenant)).json().get("id")
    for k in range(3):
        pid = (await client.post("/api/inventory/parts", headers=tenant, json={
            "name": f"Part {k}", "sku": f"SKU-{k}", "sell_price": 50, "cost_price": 20, "min_stock": 5,
            "image_data": PNG})).json()["id"]
        await client.post(f"/api/inventory/parts/{pid}/adjust", headers=tenant,
                          json={"location_id": loc, "qty": 3, "movement_type": "receive"})
        if sup:
            await client.post("/api/inventory/po", headers=tenant, json={
                "supplier_id": sup, "location_id": loc, "lines": [{"part_id": pid, "qty_ordered": 5, "unit_cost": 20}]})
        await client.post("/api/sales", headers=tenant, json={"lines": [{
            "kind": "product", "ref_id": pid, "description": f"Part {k}", "qty": 1, "unit_price": 50}],
            "payment_method": "cash"})
    return loc


STOCK_REPORTS = ["stock_levels", "product_turnover", "inventory_writeoffs", "assortment", "stock_valuation",
                 "purchase_list", "expiration", "dead_stock", "reorder_forecast"]


@pytest.mark.parametrize("key", STOCK_REPORTS)
async def test_stock_reports_do_not_load_product_photos(client, tenant, db, key):
    await _seed_inventory(client, tenant)
    with _Sql(db.bind.sync_engine) as c:
        r = await client.get(f"/api/reports/run/{key}", headers=tenant)
    assert r.status_code == 200, r.text
    assert not _selects_column(c.sql, "image_data"), _selects_column(c.sql, "image_data")


async def test_reorder_list_does_not_load_product_photos(client, tenant, db):
    await _seed_inventory(client, tenant)
    with _Sql(db.bind.sync_engine) as c:
        r = await client.get("/api/inventory/reorder-list", headers=tenant)
    assert r.status_code == 200, r.text
    assert not _selects_column(c.sql, "image_data")


def _real_photo() -> str:
    import base64
    import io

    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (600, 600), (200, 30, 30)).save(out, "PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


async def test_parts_list_sends_thumbnails_not_full_photos(client, tenant, db):
    from backend.models import Part
    photo = _real_photo()
    for k in range(3):
        pid = (await client.post("/api/inventory/parts", headers=tenant,
                                 json={"name": f"Pic {k}", "sku": f"PIC-{k}"})).json()["id"]
        (await db.execute(select(Part).where(Part.id == pid))).scalar_one().image_data = photo
        await db.commit()
    await client.get("/api/inventory/parts", headers=tenant)          # first call builds the thumbnails
    with _Sql(db.bind.sync_engine) as c:
        r = await client.get("/api/inventory/parts", headers=tenant)
    assert r.status_code == 200
    assert not _selects_column(c.sql, "image_data")
    items = r.json()["items"]
    assert len(items) == 3
    assert all(i["image_data"] and i["image_data"] != photo for i in items)
    # the one-product view still has the full photo
    full = await client.get(f"/api/inventory/parts/{items[0]['id']}", headers=tenant)
    assert full.status_code == 200 and full.json()["image_data"] == photo


async def test_parts_list_pages_are_ordered_and_total_counts_everything(client, tenant):
    """PERF-20: no ORDER BY (pages could repeat or skip rows) and `total` was the page length."""
    for k in range(7):
        await client.post("/api/inventory/parts", headers=tenant, json={"name": f"Item {6 - k}", "sku": f"S{k}"})
    p1 = (await client.get("/api/inventory/parts?limit=3&skip=0", headers=tenant)).json()
    p2 = (await client.get("/api/inventory/parts?limit=3&skip=3", headers=tenant)).json()
    p3 = (await client.get("/api/inventory/parts?limit=3&skip=6", headers=tenant)).json()
    names = [i["name"] for p in (p1, p2, p3) for i in p["items"]]
    assert names == sorted(names) and len(set(names)) == 7
    assert p1["total"] == 7 and p3["total"] == 7


async def test_clients_list_and_detail_skip_device_photos_passcodes_and_orders(client, tenant, db):
    from backend.models import Client, Device
    cid = (await client.post("/api/clients", json={"name": "Fleet Co", "phone": "+1 415 555 0101"},
                             headers=tenant)).json()["id"]
    cl = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    for k in range(3):
        db.add(Device(org_id=cl.org_id, client_id=cid, brand="Ford", model=f"T{k}",
                      image_data=PNG, passcode_enc="secret"))
    await db.commit()
    for _ in range(2):
        await client.post("/api/orders", json={"client_id": cid}, headers=tenant)
    with _Sql(db.bind.sync_engine) as c:
        r = await client.get("/api/clients?search=Fleet", headers=tenant)
    assert r.status_code == 200
    row = next(x for x in r.json()["items"] if x["id"] == cid)
    assert len(row["devices"]) == 3 and row["devices"][0]["brand"] == "Ford"
    assert not _selects_column(c.sql, "image_data")
    assert not _selects_column(c.sql, "passcode_enc")

    with _Sql(db.bind.sync_engine) as c:
        r = await client.get(f"/api/clients/{cid}", headers=tenant)
    assert r.status_code == 200
    assert r.json()["order_count"] == 2 and len(r.json()["devices"]) == 3
    assert not _selects_column(c.sql, "image_data")
    assert not _selects_column(c.sql, "passcode_enc")
    # every order the client ever had is not loaded as full rows just to count them
    assert not _selects_column(c.sql, "orders.complaint")


async def test_activity_log_report_reads_text_not_attachments(client, tenant, db):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    oid = (await client.post("/api/orders", json={"location_id": loc}, headers=tenant)).json()["id"]
    await client.post(f"/api/feed/order/{oid}", headers=tenant, json={
        "item_type": "photo", "media_data": PNG, "body": "Before", "visibility": "private"})
    with _Sql(db.bind.sync_engine) as c:
        r = await client.get("/api/reports/run/activity_log", headers=tenant)
    assert r.status_code == 200, r.text
    assert not _selects_column(c.sql, "media_data")
    assert len(r.content) < 20_000
