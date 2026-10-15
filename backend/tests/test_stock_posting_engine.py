"""Stock postings (security phase 4, audit B9/B10/B11).

- settlement="cash" took the goods in but never took the money out of the till, and deleting
  such a posting never gave it back;
- the moving average was weighted against one warehouse's quantity and rounded to 2dp, so the
  posting screen and PO-receive produced different cost bases for the same part;
- postings created no FIFO layer, so exact-COGS consumption skipped them;
- the supplier was looked up by id alone, so another shop's supplier id could be settled against.
"""
import pytest
from sqlalchemy import select


async def _account(client, h, name="Till"):
    await client.post("/api/cash/accounts", json={"name": name, "payment_option": "cash"}, headers=h)
    return [x for x in (await client.get("/api/cash/accounts", headers=h)).json()["items"]
            if x["name"] == name][0]["id"]


async def _acct_balance(client, h, aid):
    return [x for x in (await client.get("/api/cash/accounts", headers=h)).json()["items"]
            if x["id"] == aid][0]["balance"] or 0.0


async def _product(client, h, name):
    r = await client.post("/api/inventory/parts", json={"name": name, "uom": "pcs"}, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _warehouse_id(client, h):
    return (await client.get("/api/settings/warehouses?kind=product", headers=h)).json()["items"][0]["id"]


async def _post(client, h, wid, pid, qty, cost, **extra):
    r = await client.post("/api/stock-docs/postings", json={
        "warehouse_id": wid, "lines": [{"part_id": pid, "qty": qty, "unit_cost": cost}], **extra},
        headers=h)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_cash_posting_pays_from_the_till_and_delete_refunds_it(client, tenant):
    aid = await _account(client, tenant)
    base = await _acct_balance(client, tenant, aid)
    pid = await _product(client, tenant, "Screen")
    wid = await _warehouse_id(client, tenant)

    doc = await _post(client, tenant, wid, pid, 4, 12.5, settlement="cash", account_id=aid)
    assert round(await _acct_balance(client, tenant, aid) - base, 2) == -50.0

    r = await client.delete(f"/api/stock-docs/postings/{doc['id']}", headers=tenant)
    assert r.status_code == 200, r.text
    assert round(await _acct_balance(client, tenant, aid) - base, 2) == 0.0


@pytest.mark.asyncio
async def test_average_cost_uses_global_on_hand_at_4dp_and_creates_a_fifo_layer(client, tenant, db):
    from backend.models import Part, StockItemBatch
    pid = await _product(client, tenant, "Cable")
    wid = await _warehouse_id(client, tenant)

    await _post(client, tenant, wid, pid, 3, 1)
    await _post(client, tenant, wid, pid, 3, 2)
    await _post(client, tenant, wid, pid, 3, 2)
    part = (await db.execute(select(Part).where(Part.id == pid))).scalar_one()
    await db.refresh(part)
    # (3*1 + 3*2 + 3*2) / 9 = 1.6667 — not 1.67, and weighted over all 9 on hand
    assert float(part.avg_cost) == pytest.approx(1.6667, abs=1e-6)

    layers = (await db.execute(select(StockItemBatch).where(StockItemBatch.part_id == pid))).scalars().all()
    assert sorted(float(b.qty_remaining) for b in layers) == [3.0, 3.0, 3.0]


@pytest.mark.asyncio
async def test_another_shops_supplier_is_not_settled(client, tenant, db):
    from backend.models import Organisation, Supplier
    other = Organisation(name="Other shop")
    db.add(other)
    await db.flush()
    foreign = Supplier(org_id=other.id, name="Theirs", balance=0)
    db.add(foreign)
    await db.commit()
    foreign_id = foreign.id

    pid = await _product(client, tenant, "Battery")
    wid = await _warehouse_id(client, tenant)
    r = await client.post("/api/stock-docs/postings", json={
        "warehouse_id": wid, "supplier_id": foreign_id,
        "lines": [{"part_id": pid, "qty": 1, "unit_cost": 99}]}, headers=tenant)
    assert r.status_code in (201, 404), r.text
    db.expire_all()
    sup = (await db.execute(select(Supplier).where(Supplier.id == foreign_id))).scalar_one()
    assert float(sup.balance or 0) == 0.0
