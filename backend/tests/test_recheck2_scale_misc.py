"""Second recheck: Home's average job and My Work grew with the shop's whole history, My Work's
next step from Ready went back to an in-progress status the shop added later, and dropping a
job wrote a "ready" row on the customer's statement."""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import insert, select, update

from backend.tests.test_launch_b8_scale import MAX_PARAMS, _bulk_orders, _me, _watch

N = 300


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _lines(db, ids, total, tax_rate=0.0, **kw):
    from backend.models import OrderLine
    await db.execute(insert(OrderLine), [dict(id=str(uuid.uuid4()), order_id=oid, kind="service",
                                              line_type="labour", description="Fix", qty=1, unit_price=total,
                                              total=total, tax_rate=tax_rate, **kw) for oid in ids])


# ── Home: average work order ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_home_average_job_does_not_load_every_closed_job(client, tenant, db, monkeypatch):
    """33,500 closed jobs: Home built a dict per job ever closed (2.18 s, 33 MB) on every open."""
    from backend.models import Order
    from backend.services import revenue
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    old = datetime.utcnow() - timedelta(days=400)     # long before this month's figures
    ids = await _bulk_orders(db, me["org_id"], loc, N, status="delivered")
    await db.execute(update(Order).where(Order.id.in_(ids)).values(closed_at=old, created_at=old))
    await _lines(db, ids, 25.0)
    await db.commit()

    sizes = []
    real = revenue.job_figures

    async def _spy(*a, **k):
        out = await real(*a, **k)
        sizes.append(len(out))
        return out

    monkeypatch.setattr(revenue, "job_figures", _spy)
    with _watch(db) as s:
        r = await client.get("/api/reports/company-dashboard", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["monthly"]["avg_work_order"] == 25.0
    assert max(sizes, default=0) < N, f"Home worked out figures for {max(sizes)} closed jobs one by one"
    assert s["max_params"] <= MAX_PARAMS


@pytest.mark.asyncio
async def test_home_average_job_matches_the_per_job_figures(client, tenant, db):
    """The SQL average follows job_figures: credit notes, refunds past an overpayment, tax share,
    and a dropped job's kept money."""
    from backend.models import CreditNote, Invoice, Order, Payment
    from backend.services import revenue
    me = await _me(client, tenant)
    org = me["org_id"]
    loc = await _loc(client, tenant)
    await client.get("/api/statuses", headers=tenant)          # seeds the shop's statuses
    a, b, c, d = await _bulk_orders(db, org, loc, 4, status="done")
    await db.execute(update(Order).where(Order.id == c).values(status="dropped", written_off=60))
    await _lines(db, [a, c], 100.0)
    await _lines(db, [b], 120.0, tax_rate=20.0)                 # 100 ex tax
    inv = Invoice(org_id=org, invoice_number="INV-R2", order_id=a, status="sent", total=100)
    db.add(inv)
    await db.flush()
    db.add(CreditNote(org_id=org, invoice_id=inv.id, status="issued", subtotal=10, total=10))
    db.add_all([Payment(order_id=a, amount=100, method="cash", kind="payment"),
                Payment(order_id=b, amount=150, method="cash", kind="payment"),    # 30 overpaid
                Payment(order_id=b, amount=-50, method="cash", kind="refund"),     # 20 of it revenue
                Payment(order_id=c, amount=40, method="cash", kind="payment"),
                Payment(order_id=c, amount=-10, method="cash", kind="refund")])    # kept 30
    await db.commit()

    # Home averages the closed group (Done/Delivered); the service follows job_figures for any set
    for q in (select(Order.id).where(Order.org_id == org),
              select(Order.id).where(Order.org_id == org, Order.status != "dropped")):
        figs = await revenue.job_figures(db, q)
        want = sum(f["revenue"] for f in figs.values()) / len(figs)
        assert await revenue.average_job_revenue(db, org, q) == pytest.approx(want)
    assert want == pytest.approx((90 + (100 - 20 / 1.2) + 0) / 3)
    r = await client.get("/api/reports/company-dashboard", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["monthly"]["avg_work_order"] == round(want, 2)
    assert d in figs


# ── My Work ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_my_work_returns_a_capped_list_most_urgent_first(client, tenant, db):
    """33,500 open jobs for a solo owner: My Work sent every card (4 s, 355 MB on the event loop)."""
    from backend.models import Order
    from backend.routers.timeclock import MY_WORK_CAP
    me = await _me(client, tenant)
    loc = await _loc(client, tenant)
    n = MY_WORK_CAP + 20
    ids = await _bulk_orders(db, me["org_id"], loc, n)
    await db.execute(update(Order).values(priority=0))
    await db.execute(update(Order).where(Order.id == ids[-1]).values(priority=1))   # oldest-made, urgent
    await _lines(db, ids, 10.0, technician_id=me["id"])
    await db.commit()

    with _watch(db, Order) as s:
        r = await client.get("/api/timeclock/my-work", headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["orders"]) == MY_WORK_CAP and body["orders_total"] == n
    assert len(body["lines"]) == MY_WORK_CAP
    assert body["orders"][0]["order_id"] == ids[-1]
    assert s["loaded"] <= MY_WORK_CAP
    assert s["max_params"] <= MAX_PARAMS


@pytest.mark.asyncio
async def test_my_work_next_step_from_ready_skips_a_status_added_later(client, tenant):
    from backend.tests.test_b11_closeout import _order
    H = tenant
    me = (await client.get("/api/auth/me", headers=H)).json()
    r = await client.post("/api/statuses", json={"name": "Quoted", "status_group": "in_progress"}, headers=H)
    assert r.status_code == 201, r.text
    o = await _order(client, H)
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Fix",
        "qty": 1, "unit_price": 0, "technician_id": me["id"]}, headers=H)
    assert (await client.patch(f"/api/orders/{o['id']}/status", json={"status": "ready"}, headers=H)).status_code == 200
    card = next(c for c in (await client.get("/api/timeclock/my-work", headers=H)).json()["orders"]
                if c["order_id"] == o["id"])
    assert card["next_status"] == "done", card
    # and the new status is still the next step from where it belongs
    assert (await client.patch(f"/api/orders/{o['id']}/status", json={"status": "in_repair"}, headers=H)).status_code == 200
    card = next(c for c in (await client.get("/api/timeclock/my-work", headers=H)).json()["orders"]
                if c["order_id"] == o["id"])
    assert card["next_status"] == "ready", card


# ── customer ledger on a drop ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_drop_adjustment_on_the_ledger_says_dropped(client, tenant, db):
    from backend.models import ClientLedgerEntry, Payment
    from backend.tests.test_wsb_dropped_jobs import _job
    oid, cid = await _job(client, tenant)
    db.add(Payment(order_id=oid, amount=40, method="cash", kind="payment"))
    await db.commit()
    r = await client.patch(f"/api/orders/{oid}/status",
                           json={"status": "dropped", "reason": "Never came back"}, headers=tenant)
    assert r.status_code == 200, r.text
    notes = (await db.execute(select(ClientLedgerEntry.note).where(
        ClientLedgerEntry.client_id == cid, ClientLedgerEntry.entry_type == "charge",
        ClientLedgerEntry.ref_id == oid))).scalars().all()
    assert notes and all("dropped" in n and "written off" in n for n in notes), notes
    assert not any("ready" in n for n in notes), notes
