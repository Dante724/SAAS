"""Phase 2 — scan-driven stock posting: scan a part code + qty to receive/count
without looking up the part id. Delegates to the guarded adjust logic."""
from sqlalchemy import select

from backend.models import Location, Organisation


async def _loc(db):
    org = (await db.execute(select(Organisation))).scalars().first()
    return (await db.execute(select(Location).where(Location.org_id == org.id))).scalars().first()


async def test_scan_adjust_receives_by_code(client, tenant, db):
    loc = await _loc(db)
    p = (await client.post("/api/inventory/parts", headers=tenant,
                           json={"name": "Oil filter", "sku": "OF-77", "barcode": "700077"})).json()
    pid = p.get("id") or p.get("part", {}).get("id")

    r = await client.post("/api/inventory/scan-adjust", headers=tenant,
                          json={"code": "OF-77", "location_id": loc.id, "qty": 5, "movement_type": "receive"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["qty_available"] == 5 and (not pid or body["part_id"] == pid)

    # scanning the barcode to write off 2 → 3 left
    r2 = await client.post("/api/inventory/scan-adjust", headers=tenant,
                           json={"code": "700077", "location_id": loc.id, "qty": 2, "movement_type": "writeoff"})
    assert r2.status_code == 200 and r2.json()["qty_available"] == 3, r2.text


async def test_scan_adjust_unknown_code_404(client, tenant, db):
    loc = await _loc(db)
    r = await client.post("/api/inventory/scan-adjust", headers=tenant,
                          json={"code": "GHOST-999", "location_id": loc.id, "qty": 1})
    assert r.status_code == 404
