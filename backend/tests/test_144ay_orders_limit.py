"""Build 144ay — the Work Orders page bulk-loads /orders?limit=1000 then paginates client-side.
The list_orders cap must allow that; a tighter cap 422s and the whole page renders the raw error."""
import pytest


@pytest.mark.asyncio
async def test_orders_accepts_bulk_limit_1000(client, tenant):
    r = await client.get("/api/orders?limit=1000", headers=tenant)
    assert r.status_code == 200, r.text
    assert "items" in r.json()


@pytest.mark.asyncio
async def test_orders_still_rejects_absurd_limit(client, tenant):
    # the bound still exists — just raised to the page's real need, not removed
    r = await client.get("/api/orders?limit=100000", headers=tenant)
    assert r.status_code == 422, r.text
