"""QA fix E3 — company settings refuse a currency or timezone that doesn't exist.

Found in the 5-shop QA run (31 Oct 2026): PUT /api/staff/company saved currency_code "XYZ"
and timezone "Mars/Olympus"; every date and amount after that was wrong.
"""
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"currency_code": "XYZ"},
    {"timezone": "Mars/Olympus"},
    {"currencies": [{"code": "XYZ", "symbol": "?", "rate": 1}]},
])
async def test_company_rejects_unknown_currency_and_timezone(client, tenant, body):
    before = (await client.get("/api/staff/company", headers=tenant)).json()
    r = await client.put("/api/staff/company", json=body, headers=tenant)
    assert r.status_code == 422, r.text
    after = (await client.get("/api/staff/company", headers=tenant)).json()
    assert after.get("currency_code") == before.get("currency_code")
    assert after.get("timezone") == before.get("timezone")


@pytest.mark.asyncio
async def test_company_accepts_real_currency_and_timezone(client, tenant):
    r = await client.put("/api/staff/company", json={
        "currency_code": "aud", "timezone": "Australia/Sydney",
        "currencies": [{"code": "NZD", "symbol": "$", "rate": 1.1}]}, headers=tenant)
    assert r.status_code == 200, r.text
    got = (await client.get("/api/staff/company", headers=tenant)).json()
    assert got["currency_code"] == "AUD" and got["timezone"] == "Australia/Sydney"
