"""Build 133 — Settings > Inventory."""
import pytest


@pytest.mark.asyncio
async def test_inventory_settings_defaults(client, tenant):
    d = (await client.get("/api/staff/inventory-settings", headers=tenant)).json()
    assert d["enable_bins"] is False
    assert d["unique_identifier"] == "name"
    assert d["enable_reservations"] is True
    assert d["reservation_max_days"] == 14
    assert d["default_vehicle_type"] == "Car"


@pytest.mark.asyncio
async def test_inventory_settings_roundtrip(client, tenant):
    up = await client.put("/api/staff/inventory-settings", json={
        "enable_bins": True, "unique_identifier": "sku", "enable_reservations": False,
        "reservation_max_days": 30, "product_serial_template": "0-{C:19}",
        "asset_serial_template": "9-{C:4}", "default_product_type": "tire",
        "default_vehicle_type": "Truck"}, headers=tenant)
    assert up.status_code == 200, up.text

    d = (await client.get("/api/staff/inventory-settings", headers=tenant)).json()
    assert d["enable_bins"] is True and d["unique_identifier"] == "sku"
    assert d["enable_reservations"] is False and d["reservation_max_days"] == 30
    assert d["product_serial_template"] == "0-{C:19}" and d["asset_serial_template"] == "9-{C:4}"
    assert d["default_product_type"] == "tire" and d["default_vehicle_type"] == "Truck"


@pytest.mark.asyncio
async def test_inventory_settings_bad_identifier(client, tenant):
    r = await client.put("/api/staff/inventory-settings",
                         json={"unique_identifier": "hologram"}, headers=tenant)
    assert r.status_code == 422
