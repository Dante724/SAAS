"""QA fix F2 (5-shop run, 31 Oct 2026, shop 3): a double-submitted POS return refunded and restocked
twice while the line's returned count went up once. Replayed deterministically: request A has read the
sale, request B returns the item and commits, then A acts on its stale copy."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.models import Employee, Refund, Sale, SaleLine
from backend.routers.sales import SaleReturnIn, return_sale_items
from backend.tests.test_b5_pos_quote import _balance, _product, _stock, _till


@pytest.mark.asyncio
async def test_return_clicked_twice_refunds_and_restocks_once(client, tenant, db):
    till = await _till(client, tenant)
    pid, loc = await _product(client, tenant, db, qty=5, price=40)
    s = (await client.post("/api/sales", json={"account_id": till, "lines": [
        {"kind": "product", "ref_id": pid, "description": "Charger", "qty": 2, "unit_price": 40}]},
        headers=tenant)).json()
    line_id = (await client.get(f"/api/sales/{s['id']}", headers=tenant)).json()["lines"][0]["id"]
    body = SaleReturnIn(account_id=till, lines=[{"line_id": line_id, "qty": 2}])
    emp = (await db.execute(select(Employee))).scalars().first()
    org_id = emp.org_id
    # A: has the sale and its line in hand, nothing returned yet (keep the references: the identity
    # map is weak, and a dropped object would be reloaded fresh and hide the race)
    stale = ((await db.execute(select(Sale).where(Sale.id == s["id"]))).scalar_one(),
             (await db.execute(select(SaleLine).where(SaleLine.id == line_id))).scalar_one())
    async with async_sessionmaker(db.bind, expire_on_commit=False)() as b:   # B: the other click
        emp_b = (await b.execute(select(Employee).where(Employee.id == emp.id))).scalar_one()
        assert (await return_sale_items(s["id"], body, b, emp_b))["refunded"] == pytest.approx(80)
    with pytest.raises(HTTPException) as e:
        await return_sale_items(s["id"], body, db, emp)
    assert e.value.status_code == 400 and stale
    await db.rollback()
    assert await _stock(db, pid, loc) == 5                      # 3 after the sale, +2 back once
    assert await _balance(client, tenant, till) == pytest.approx(0)
    refunds = (await db.execute(select(Refund).where(Refund.org_id == org_id))).scalars().all()
    assert len(refunds) == 1
