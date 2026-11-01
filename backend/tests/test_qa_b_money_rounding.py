"""QA 2026-10-31 (B, low): float noise in stored prices (19.999 kept despite price_decimals).
Prices are rounded once, at write time, by services.money.round_price."""
import pytest

from backend.services.money import round_price


def test_round_price_helper():
    assert round_price(19.999) == 20.0
    assert round_price(162.32999999999998) == 162.33
    assert round_price(2.675) == 2.68                 # half-up, not banker's
    assert round_price(19.6, 0) == 20.0
    assert round_price(19.25, 1) == 19.3
    assert round_price(1.23456, 4) == 1.23            # columns hold 2 decimals at most
    assert round_price(5, None) == 5.0


@pytest.mark.asyncio
async def test_service_price_is_stored_rounded(client, tenant):
    r = await client.post("/api/catalog/services", headers=tenant, json={"name": "S", "price": 19.999, "cost_price": 3.333})
    assert r.status_code == 201, r.text
    assert r.json()["price"] == 20.0 and r.json()["cost_price"] == 3.33
    sid = r.json()["id"]
    await client.put("/api/pricing/defaults", headers=tenant, json={"price_decimals": 0})
    r = await client.put(f"/api/catalog/services/{sid}", headers=tenant, json={"name": "S", "price": 49.5})
    assert r.status_code == 200, r.text
    assert r.json()["price"] == 50.0


@pytest.mark.asyncio
async def test_fixed_coupon_value_is_stored_rounded(client, tenant):
    await client.post("/api/coupons", headers=tenant, json={"code": "ODD", "kind": "fixed", "value": 4.999})
    items = (await client.get("/api/coupons", headers=tenant)).json()["items"]
    assert items[0]["value"] == 5.0
