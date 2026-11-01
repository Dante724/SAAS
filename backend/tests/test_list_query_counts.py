"""List endpoints don't issue queries per row (phase 4 performance, audit P1)."""
import pytest
from sqlalchemy import event


class _Counter:
    def __init__(self, engine):
        self.engine, self.n = engine, 0

    def _hit(self, *a, **k):
        self.n += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._hit)


@pytest.mark.asyncio
async def test_products_list_query_count_is_flat(client, tenant, db):
    async def run():
        with _Counter(db.bind.sync_engine) as c:
            r = await client.get("/api/catalog/products", headers=tenant)
            assert r.status_code == 200
        return c.n, len(r.json()["items"])

    for i in range(3):
        await client.post("/api/inventory/parts", json={"name": f"P{i}", "uom": "pcs"}, headers=tenant)
    few, n_few = await run()
    for i in range(3, 23):
        await client.post("/api/inventory/parts", json={"name": f"P{i}", "uom": "pcs"}, headers=tenant)
    many, n_many = await run()
    assert n_many - n_few == 20
    assert many == few, (few, many)


@pytest.mark.asyncio
async def test_clients_list_counts_orders_without_loading_them(client, tenant, db):
    cid = (await client.post("/api/clients", json={"name": "Busy", "phone": "+1555"}, headers=tenant)).json()["id"]
    for _ in range(3):
        await client.post("/api/orders", json={"client_id": cid}, headers=tenant)
    with _Counter(db.bind.sync_engine) as c:
        r = await client.get("/api/clients?limit=200", headers=tenant)
    row = next(x for x in r.json()["items"] if x["id"] == cid)
    assert row["order_count"] == 3
    assert c.n < 15
    assert (await client.get("/api/clients?limit=100000", headers=tenant)).status_code == 422
