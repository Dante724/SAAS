"""144da — catalog search honours a `limit` so the job-kit picker can browse the whole catalog,
not just the first 8."""
import pytest


@pytest.mark.asyncio
async def test_search_limit_returns_more_than_default(client, tenant):
    for i in range(12):
        r = await client.post("/api/catalog/products", json={"name": f"Widget {i:02d}", "sell_price": 10}, headers=tenant)
        assert r.status_code in (200, 201), r.text
    # default cap is 8
    default = (await client.get("/api/catalog/search?q=&type=product", headers=tenant)).json()["items"]
    assert len(default) == 8
    # browse mode asks for more
    more = (await client.get("/api/catalog/search?q=&type=product&limit=100", headers=tenant)).json()["items"]
    assert len(more) >= 12, f"expected all products, got {len(more)}"
