"""Build 144ay — old Work Orders bundles request /orders?limit=1000. Since batch 2 the page is
server-paged and the limit is clamped to the page ceiling instead of 422ing the whole page."""
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
