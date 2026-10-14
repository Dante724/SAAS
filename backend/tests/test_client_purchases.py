"""Build 144aj — a client's purchase history unifies POS sales and work-order billing."""
import pytest


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_purchases_combine_sales_and_work_orders(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Buyer", "phone": "+15559000"},
                             headers=tenant)).json()["id"]

    # a POS sale of a product
    sale = await client.post("/api/sales", json={
        "client_id": cid, "location_id": loc,
        "lines": [{"kind": "product", "description": "Screen protector", "qty": 2,
                   "unit_price": 15, "discount": 0}]}, headers=tenant)
    assert sale.status_code == 201, sale.text

    # a work order that bills a part + labour
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "battery swap",
        "lines": [{"line_type": "part", "description": "Battery", "qty": 1, "unit_price": 40},
                  {"line_type": "labour", "description": "Fitting", "qty": 1, "unit_price": 20}]},
        headers=tenant)).json()["id"]
    assert oid

    r = await client.get(f"/api/clients/{cid}/purchases", headers=tenant)
    assert r.status_code == 200, r.text
    data = r.json()
    items = data["items"]
    labels = {i["item"] for i in items}
    assert {"Screen protector", "Battery", "Fitting"} <= labels

    # both sources are represented
    assert {"sale", "order"} <= {i["source_type"] for i in items}

    # summary totals add up (2*15 + 40 + 20 = 90)
    assert data["summary"]["total_spent"] == pytest.approx(90.0)
    assert data["summary"]["item_count"] == 3
    assert data["summary"]["transaction_count"] == 2


@pytest.mark.asyncio
async def test_purchases_scoped_to_org_and_client(client, tenant):
    # unknown client → 404, and a client with nothing bought → empty but valid
    r404 = await client.get("/api/clients/does-not-exist/purchases", headers=tenant)
    assert r404.status_code == 404

    cid = (await client.post("/api/clients", json={"name": "Quiet"}, headers=tenant)).json()["id"]
    r = await client.get(f"/api/clients/{cid}/purchases", headers=tenant)
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["summary"]["total_spent"] == 0
