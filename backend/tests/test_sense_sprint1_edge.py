"""Tekly Sense sprint 1 edge cases (from the tester's review): any language, merged line names,
one price per job, long complaints, tax-free margin, deferred work shown once."""
import uuid
from datetime import datetime, timedelta
import pytest
from sqlalchemy import select
from backend.services import sense
from backend.tests.test_sense_sprint1 import _register, _closed_job, _history

pytestmark = pytest.mark.asyncio


async def _job(db, org_id, complaint, lines, days_ago=10, **kw):
    """lines: (desc, price, cost, qty, ref_id, total)"""
    from backend.models import Location, Order, OrderLine
    loc = (await db.execute(select(Location.id).where(Location.org_id == org_id))).scalars().first()
    when = datetime.utcnow() - timedelta(days=days_ago)
    o = Order(org_id=org_id, order_number=f"E-{uuid.uuid4().hex[:8]}", location_id=loc, complaint=complaint,
              status="closed", created_at=when, closed_at=when, **kw)
    db.add(o); await db.flush()
    for d, p, c, q, ref, tot in lines:
        db.add(OrderLine(order_id=o.id, line_type="labour", kind="service", description=d, qty=q,
                         unit_price=p, cost_price=c, ref_id=ref, total=tot if tot is not None else p * q))
    return o


async def _base(client, db, batteries=20):
    H, org = await _register(client)
    await _history(db, org, screens=0, batteries=batteries)
    return H, org


async def test_stopword_only_and_numbers_only_say_nothing(client, db):
    H, org = await _register(client); await _history(db, org)
    for c in ["the and for with", "   ", "13", "on it"]:
        r = await sense.draft_lines(db, org, c)
        assert r == {"learning": False, "items": []}, (c, r)


async def test_non_latin_complaint_gets_suggestions(client, db):
    H, org = await _base(client, db)
    for _ in range(5):
        await _job(db, org, "Разбит экран", [("Замена экрана", 100, 40, 1, None, None)])
    await db.commit()
    r = await sense.draft_lines(db, org, "разбит экран")
    assert [i["description"] for i in r["items"]] == ["Замена экрана"], r


async def test_accented_complaint_matches(client, db):
    H, org = await _base(client, db)
    for _ in range(5):
        await _job(db, org, "Écran cassé", [("Remplacement écran", 100, 40, 1, None, None)])
    await db.commit()
    r = await sense.draft_lines(db, org, "écran cassé")
    assert r["items"] and r["items"][0]["description"] == "Remplacement écran", r


async def test_catalog_and_custom_line_with_same_name_merge(client, db):
    H, org = await _base(client, db)
    for i in range(6):
        await _job(db, org, "cracked screen", [("Screen replacement", 129, 60, 1, "svc-1", None)])
        await _job(db, org, "cracked screen", [("Screen replacement", 129, 60, 1, None, None)])
    await db.commit()
    r = await sense.draft_lines(db, org, "cracked screen")
    names = [i["description"] for i in r["items"]]
    assert names.count("Screen replacement") == 1, r["items"]
    assert r["items"][0]["why"]["used"] == 12


async def test_line_twice_on_one_job_counts_once_and_price_is_per_job(client, db):
    H, org = await _base(client, db)
    await _job(db, org, "cracked screen", [("Labour", 100, 0, 1, None, None)] * 3)
    await _job(db, org, "cracked screen", [("Labour", 200, 0, 1, None, None)])
    await _job(db, org, "cracked screen", [("Labour", 200, 0, 1, None, None)])
    await db.commit()
    it = (await sense.draft_lines(db, org, "cracked screen"))["items"][0]
    assert it["why"]["used"] == 3
    assert it["unit_price"] == 200, it      # pooled median gives 100: one job outvotes two


async def test_archived_estimate_and_old_jobs_excluded(client, db):
    H, org = await _base(client, db)
    for _ in range(5):
        await _job(db, org, "cracked screen", [("Screen replacement", 129, 60, 1, None, None)], archived=True)
        await _job(db, org, "cracked screen", [("Screen replacement", 129, 60, 1, None, None)], is_estimate=True)
        await _job(db, org, "cracked screen", [("Screen replacement", 129, 60, 1, None, None)], days_ago=800)
    await db.commit()
    assert (await sense.draft_lines(db, org, "cracked screen"))["items"] == []
    assert await sense.closed_jobs(db, org) == 25          # 20 + 5 old; archived/estimates not counted


async def test_exactly_min_closed_jobs_is_ready(client, db):
    H, org = await _register(client)
    await _history(db, org, screens=10, batteries=9)
    assert (await sense.draft_lines(db, org, "cracked screen"))["learning"] is True
    await _history(db, org, screens=1, batteries=0)
    r = await sense.draft_lines(db, org, "cracked screen")
    assert r["learning"] is False and r["items"]


async def test_long_natural_complaint_still_matches(client, db):
    H, org = await _register(client); await _history(db, org)
    r = await sense.draft_lines(db, org, "Customer dropped her phone yesterday, screen cracked badly and touch unresponsive in one corner")
    assert r["items"], "long complaints dilute Jaccard below 0.25"


async def test_margin_qty_and_zero_revenue(client, db):
    H, org = await _register(client); await _history(db, org)
    o = await _job(db, org, "x", [("Part", 50, 20, 2, None, 100)]); o.closed_at = None
    z = await _job(db, org, "x", [("Warranty", 0, 30, 1, None, 0)]); z.closed_at = None
    await db.commit()
    assert (await sense.margin_check(db, o))["margin_pct"] == 60.0
    m = await sense.margin_check(db, z)
    assert m["margin_pct"] is None and m["flag"] is False      # a free warranty job costing money isn't flagged


async def test_margin_ignores_imposed_tax(client, db):
    H, org = await _register(client); await _history(db, org)
    o = await _job(db, org, "x", [("Screen", 100, 90, 1, None, 120)])   # 20% tax imposed -> total 120
    o.closed_at = None; await db.commit()
    assert (await sense.margin_check(db, o))["margin_pct"] == 10.0


async def test_deferred_no_device_no_client_and_client_fallback(client, db):
    from backend.models import Recommendation, Device
    H, org = await _register(client)
    cid = (await client.post("/api/clients", json={"name": "Jo", "phone": "5551112222"}, headers=H)).json()["id"]
    bare = await _job(db, org, "x", [])
    assert await sense.deferred_for(db, bare) == []
    dev = Device(org_id=org, client_id=cid, brand="A", model="B"); db.add(dev); await db.flush()
    old = await _job(db, org, "x", [], client_id=cid, device_id=dev.id)
    db.add(Recommendation(org_id=org, order_id=old.id, title="Tyres", status="declined", decline_reason="not_now"))
    old2 = await _job(db, org, "x", [], client_id=cid, device_id=dev.id)
    db.add(Recommendation(org_id=org, order_id=old2.id, title="Tyres", status="declined", decline_reason="not_now"))
    await db.commit()
    new = await _job(db, org, "x", [], client_id=cid)
    titles = [i["title"] for i in await sense.deferred_for(db, new)]
    assert titles == ["Tyres"], titles       # deferred on two visits, shown once


async def test_deferred_work_done_on_a_later_visit_drops_off(client, db):
    from backend.models import Device, Recommendation
    H, org = await _register(client)
    dev = Device(org_id=org, brand="A", model="B"); db.add(dev); await db.flush()
    old = await _job(db, org, "x", [], device_id=dev.id, days_ago=200)
    db.add(Recommendation(org_id=org, order_id=old.id, title="Front tyres", status="declined", decline_reason="not_now"))
    await _job(db, org, "x", [("Front tyres", 340, 200, 1, None, None)], device_id=dev.id, days_ago=50)
    await db.commit()
    new = await _job(db, org, "x", [], device_id=dev.id, days_ago=0)
    assert await sense.deferred_for(db, new) == []
