"""144ju — /reservations items carry part_id so the product drawer can filter per product."""
from backend.models import StockLevel


async def _location(client, tenant):
    return (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]


async def test_reservation_serializes_part_id(client, tenant, db):
    loc = await _location(client, tenant)
    pid = (await client.post("/api/catalog/products", headers=tenant, json={
        "name": "Reservable widget", "sell_price": 20})).json()["id"]
    db.add(StockLevel(part_id=pid, location_id=loc, qty_available=10, qty_reserved=0))
    await db.commit()

    r = await client.post("/api/inventory/reserve", headers=tenant,
                          json={"part_id": pid, "location_id": loc, "qty": 3})
    assert r.status_code == 201, r.text

    items = (await client.get("/api/inventory/reservations", headers=tenant)).json()["items"]
    mine = [i for i in items if i.get("part_id") == pid]
    assert len(mine) == 1                       # the field is present and matches
    assert mine[0]["qty"] == 3
    assert mine[0]["part"] == "Reservable widget"


async def test_drawer_endpoints_share_product_id(client, tenant):
    """The product drawer's Movements + Purchasing tabs call /inventory/parts/{product_id}/*.
    Catalog products are Part rows, so the product id must be a valid part id on all three."""
    pid = (await client.post("/api/catalog/products", headers=tenant, json={
        "name": "Drawer-tab widget", "sell_price": 30})).json()["id"]

    mv = await client.get(f"/api/inventory/parts/{pid}/movements", headers=tenant)
    assert mv.status_code == 200 and isinstance(mv.json(), list)      # array, not 404

    po = await client.get(f"/api/inventory/parts/{pid}/purchase-orders", headers=tenant)
    assert po.status_code == 200 and "items" in po.json()

    rv = await client.get("/api/inventory/reservations", headers=tenant)
    assert rv.status_code == 200 and "items" in rv.json()
