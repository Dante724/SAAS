"""144dk — a scanned barcode / serial typed into the quantity field must be rejected,
so it never lands in qty_available and shows up as 21,00,00,00,00,098 on hand."""
import pytest


async def _part_and_loc(client, tenant):
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Probe", "sku": "PRB-1"},
                             headers=tenant)).json()["id"]
    return pid, loc


@pytest.mark.asyncio
async def test_barcode_sized_qty_is_rejected(client, tenant):
    pid, loc = await _part_and_loc(client, tenant)
    # 13-digit EAN barcode mistakenly entered as a quantity.
    r = await client.post(f"/api/inventory/parts/{pid}/adjust",
                          json={"location_id": loc, "qty": 2100000000098, "movement_type": "adjust"},
                          headers=tenant)
    assert r.status_code == 422, r.text
    assert "range" in r.text.lower()


@pytest.mark.asyncio
async def test_sane_qty_still_works(client, tenant):
    pid, loc = await _part_and_loc(client, tenant)
    r = await client.post(f"/api/inventory/parts/{pid}/adjust",
                          json={"location_id": loc, "qty": 12, "movement_type": "adjust"},
                          headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["qty_available"] == 12


@pytest.mark.asyncio
async def test_bulk_adjust_rejects_barcode_set_qty(client, tenant):
    pid, loc = await _part_and_loc(client, tenant)
    r = await client.post("/api/inventory/parts/bulk-adjust",
                          json={"location_id": loc, "rows": [{"part_id": pid, "set_qty": 2100000000098}]},
                          headers=tenant)
    assert r.status_code == 422, r.text
