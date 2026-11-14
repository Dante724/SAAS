"""Final review BE-7: "Paid" figures count new money only.

Since FIN-09 a gift-card redemption is a Payment (method gift_card), and paying from a client's
balance is one too (method balance). Neither brings money in: the gift card's money came in when
the card was sold, the balance's when it was topped up. The lead-source report's "Paid" and the
Sense Morning Brief's "yesterday paid" summed every Payment and so counted that money twice,
disagreeing with revenue.money_in_payment() and the cash reports.
"""
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


async def _job_with_payments(db, org_id, client_id, when):
    from backend.models import Location, Order, Payment
    loc = (await db.execute(select(Location.id).where(Location.org_id == org_id))).scalars().first()
    o = Order(org_id=org_id, order_number=f"O-{uuid.uuid4().hex[:6]}", location_id=loc, complaint="x",
              status="new", client_id=client_id, total=175)
    db.add(o)
    await db.flush()
    for method, amount in (("cash", 100), ("gift_card", 50), ("balance", 25)):
        db.add(Payment(org_id=org_id, order_id=o.id, client_id=client_id, method=method, kind="payment",
                       amount=amount, paid_at=when))
    await db.commit()
    return o


async def test_lead_source_paid_counts_only_new_money(client, tenant, db):
    from backend.models import Client
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    c = Client(org_id=me["org_id"], name="Walk-in", source="Google")
    db.add(c)
    await db.commit()
    await _job_with_payments(db, me["org_id"], c.id, datetime.utcnow())
    r = await client.get("/api/reports/run/lead_sources", headers=tenant)
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["rows"] if x[0] == "Google")
    assert row[4] == 100                    # was 175: the gift card and the balance counted again


async def test_brief_yesterday_paid_counts_only_new_money(client, tenant, db):
    from backend.models import Client, Organisation
    from backend.services import sense
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    org = (await db.execute(select(Organisation).where(Organisation.id == me["org_id"]))).scalar_one()
    now = datetime.utcnow()
    y0, y1 = sense._local_day_utc(sense._tz(org), now, 1)
    c = Client(org_id=me["org_id"], name="Walk-in")
    db.add(c)
    await db.commit()
    await _job_with_payments(db, me["org_id"], c.id, y0 + (y1 - y0) / 2)
    b = await sense.build_brief(db, me["org_id"], show_money=True, now=now)
    assert b["yesterday"]["paid"] == 100    # was 175
