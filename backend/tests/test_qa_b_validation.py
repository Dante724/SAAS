"""QA 2026-10-31 (B, med): tax / pricing-default / service / commission input validation.

Each of these used to be stored as sent (or silently changed); they are now refused with a 422
that says what's wrong."""
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"name": "Bad", "rate": 150},
    {"name": "Bad", "rate": -7},
    {"name": "Bad", "rate": 5, "mode": "sideways"},
])
async def test_tax_rate_and_mode_are_checked(client, tenant, body):
    r = await client.post("/api/catalog/taxes", headers=tenant, json=body)
    assert r.status_code == 422, r.text
    ok = (await client.post("/api/catalog/taxes", headers=tenant, json={"name": "VAT", "rate": 20, "mode": "included"})).json()
    r = await client.put(f"/api/catalog/taxes/{ok['id']}", headers=tenant, json=body)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"price_decimals": -3}, {"price_decimals": 9}, {"margin_guard": "banana"},
    {"min_margin_pct": 150}, {"discount_approval_pct": -1},
])
async def test_price_defaults_are_checked(client, tenant, body):
    r = await client.put("/api/pricing/defaults", headers=tenant, json=body)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_price_defaults_accept_sane_values(client, tenant):
    r = await client.put("/api/pricing/defaults", headers=tenant, json={"price_decimals": 0, "margin_guard": "warn"})
    assert r.status_code == 200, r.text
    d = (await client.get("/api/pricing/defaults", headers=tenant)).json()
    assert d["price_decimals"] == 0 and d["margin_guard"] == "warn"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"name": "S", "price": -5}, {"name": "S", "price": 1e12}, {"name": "S", "cost_price": -1},
    {"name": "S", "duration_min": -30}, {"name": "S", "warranty_days": -1},
])
async def test_service_numbers_are_checked(client, tenant, body):
    r = await client.post("/api/catalog/services", headers=tenant, json=body)
    assert r.status_code == 422, r.text
    sid = (await client.post("/api/catalog/services", headers=tenant, json={"name": "Ok", "price": 10})).json()["id"]
    r = await client.put(f"/api/catalog/services/{sid}", headers=tenant, json=body)
    assert r.status_code == 422, r.text


async def _service(client, h):
    return (await client.post("/api/catalog/services", headers=h, json={"name": "Screen", "price": 100})).json()["id"]


@pytest.mark.asyncio
async def test_commission_fixed_is_saved_as_flat(client, tenant):
    sid = await _service(client, tenant)
    r = await client.post(f"/api/catalog/services/{sid}/commissions", headers=tenant,
                          json={"calc_type": "fixed", "amount": 15})
    assert r.status_code == 201, r.text
    assert r.json()["calc_type"] == "flat" and r.json()["amount"] == 15


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"calc_type": "percent", "amount": 150},
    {"calc_type": "percent", "amount": -5},
    {"calc_type": "bogus", "amount": 5},
    {"calc_type": "percent", "amount": 10, "percent_basis": "price"},
    {"calc_type": "flat", "amount": -1},
])
async def test_commission_bad_values_are_refused(client, tenant, body):
    sid = await _service(client, tenant)
    r = await client.post(f"/api/catalog/services/{sid}/commissions", headers=tenant, json=body)
    assert r.status_code == 422, r.text
