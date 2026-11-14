"""Final review BE-4: whole-document rounding and gift-card redemptions agree.

With rounding on, recap_redemptions compared the UNROUNDED line sum with what gift cards paid
against the ROUNDED total, so any line edit (even a description change) gave the rounding
difference back to the card and a fully paid job owed 0.02 again. Order creation didn't round at
all: 99.98 at create, 100.00 after the first recalculation.
"""
import pytest
from sqlalchemy import update

from backend.models import Organisation
from backend.tests.test_p1_money_audit import _client, _job, _me, _order

pytestmark = pytest.mark.asyncio


async def _rounding_on(client, tenant, db):
    org = (await _me(client, tenant))["org_id"]
    await db.execute(update(Organisation).where(Organisation.id == org).values(rounding_enabled=True))
    await db.commit()


async def test_a_new_job_is_rounded_from_the_start(client, tenant, db):
    await _rounding_on(client, tenant, db)
    oid = await _job(client, tenant, price=99.98)
    assert (await _order(client, tenant, oid))["total"] == 100.0          # was 99.98


async def test_a_line_edit_does_not_give_the_rounding_back_to_the_card(client, tenant, db):
    await _rounding_on(client, tenant, db)
    cid = await _client(client, tenant)
    code = (await client.post("/api/giftcards", json={"value": 150, "client_id": cid}, headers=tenant)).json()["code"]
    oid = await _job(client, tenant, cid, price=99.98)
    o = await _order(client, tenant, oid)
    assert o["total"] == 100.0
    r = await client.post(f"/api/orders/{oid}/redeem-giftcard", json={"code": code}, headers=tenant)
    assert r.status_code == 200 and r.json()["redeemed"] == 100.0, r.text
    line = o["lines"][0]
    r = await client.patch(f"/api/orders/{oid}/lines/{line['id']}", json={"description": "Repair (renamed)"},
                           headers=tenant)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/giftcards/{code}", headers=tenant)).json()["balance"] == 50.0   # was 50.02
    from backend.models import Order
    from backend.services.order_money import balance_due
    db.expire_all()
    order = await db.get(Order, oid)
    assert float(await balance_due(db, order)) == 0.0                                               # was 0.02
