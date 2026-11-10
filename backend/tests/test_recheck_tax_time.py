"""Launch recheck (B4, B6): what the second review still found wrong with tax and time.

Each test reproduces a case the recheck hit, through the real API."""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.models import Invoice, Location, Order, Tax

pytestmark = pytest.mark.asyncio


async def _signup(client, **extra):
    email = f"r-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Fixit", "name": "Owner", "email": email, "password": "pw-test-12345", **extra})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["org_id"]


async def _loc(db, org_id):
    return (await db.execute(select(Location).where(Location.org_id == org_id)
                             .order_by(Location.created_at))).scalars().first()


async def _job(client, H, loc_id, lines, **extra):
    r = await client.post("/api/orders", json={
        "location_id": loc_id, "complaint": "Cracked screen", **extra,
        "lines": [{"line_type": "labour", "kind": "service", "description": d, "qty": 1, "unit_price": p}
                  for d, p in lines]}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def _wall_now(tz: str) -> datetime:
    from backend.services.dates import utc_to_wall
    return utc_to_wall(datetime.utcnow(), tz)


# ── B4: a new default tax is charged ───────────────────────────────────────────────────────────
async def test_new_default_after_deleting_the_placeholder_is_charged(client, db):
    """Recheck (B): delete the 0% placeholder, add 'NY sales tax 8.875%' as default → a 100 job
    totalled 100 instead of 108.88."""
    H, org_id = await _signup(client, country="US")
    placeholder = (await db.execute(select(Tax).where(Tax.org_id == org_id))).scalar_one()
    assert (await client.delete(f"/api/catalog/taxes/{placeholder.id}", headers=H)).status_code == 200
    r = await client.post("/api/catalog/taxes", json={"name": "NY sales tax", "rate": 8.875,
                                                      "mode": "imposed", "is_default": True}, headers=H)
    assert r.status_code == 201, r.text
    assert r.json()["is_default"] is True
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 100)])
    assert o["total"] == pytest.approx(108.88, abs=0.01)


async def test_new_default_next_to_the_placeholder_is_charged(client, db):
    """Recheck (C): add a new default next to the 0% placeholder → still 100."""
    H, org_id = await _signup(client, country="US")
    r = await client.post("/api/catalog/taxes", json={"name": "NY sales tax", "rate": 8.875,
                                                      "mode": "imposed", "is_default": True}, headers=H)
    assert r.status_code == 201, r.text
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 100)])
    assert o["total"] == pytest.approx(108.88, abs=0.01)
    taxes = (await client.get("/api/catalog/taxes", headers=H)).json()["items"]
    assert [t["name"] for t in taxes if t["is_default"]] == ["NY sales tax"]


async def test_deleted_tax_leaves_the_stack_and_a_new_one_becomes_default(client, db):
    """Deleting the only tax used to leave it first on the stack: a tax added afterwards without
    'default' ticked was never charged, and no tax showed as the default."""
    H, org_id = await _signup(client, country="US")
    placeholder = (await db.execute(select(Tax).where(Tax.org_id == org_id))).scalar_one()
    await client.delete(f"/api/catalog/taxes/{placeholder.id}", headers=H)
    r = await client.post("/api/catalog/taxes", json={"name": "Sales tax", "rate": 10,
                                                      "mode": "imposed"}, headers=H)
    t = r.json()
    r = await client.put(f"/api/catalog/taxes/{t['id']}", json={"name": "Sales tax", "is_default": True},
                         headers=H)
    assert r.status_code == 200 and r.json()["is_default"] is True
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 100)])
    assert o["total"] == pytest.approx(110.0, abs=0.01)


# ── B4: the job shows the tax it stores ────────────────────────────────────────────────────────
async def test_order_detail_returns_the_stored_tax(client, db):
    """Recheck: GET /api/orders/{id} had no tax, so staff saw line totals with tax folded in."""
    H, org_id = await _signup(client, country="CA", province="AB")
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 100)])
    assert o["tax"] == 5.0
    det = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert (det["total"], det["tax"]) == (105.0, 5.0)


# ── B4: line-subset invoices store their tax ───────────────────────────────────────────────────
async def test_sub_invoice_of_some_lines_stores_their_vat(client, db):
    """Recheck: a GB sub-invoice of a 48 line stored (48, 0, 48); the VAT inside it is 8."""
    H, org_id = await _signup(client, country="GB")
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 48), ("Battery", 60)])
    det = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    line = next(ln for ln in det["lines"] if ln["description"] == "Screen")
    r = await client.post(f"/api/cash/orders/{o['id']}/invoices", json={"line_ids": [line["id"]]}, headers=H)
    assert r.status_code == 201, r.text
    inv = (await db.execute(select(Invoice).where(Invoice.order_id == o["id"]))).scalar_one()
    assert (float(inv.subtotal), float(inv.tax), float(inv.total)) == (40.0, 8.0, 48.0)

    # The draft follows its line: repricing it keeps the VAT split, the total moves, no double count
    r = await client.patch(f"/api/orders/{o['id']}/lines/{line['id']}", json={"unit_price": 72}, headers=H)
    assert r.status_code == 200, r.text
    await db.refresh(inv)
    assert (float(inv.subtotal), float(inv.tax), float(inv.total)) == (60.0, 12.0, 72.0)


async def test_imposed_tax_sub_invoice_stores_the_tax(client, db):
    H, org_id = await _signup(client, country="CA", province="AB")
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 100), ("Case", 20)])
    det = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    line = next(ln for ln in det["lines"] if ln["description"] == "Screen")
    r = await client.post(f"/api/cash/orders/{o['id']}/invoices", json={"line_ids": [line["id"]]}, headers=H)
    assert r.status_code == 201, r.text
    inv = (await db.execute(select(Invoice).where(Invoice.order_id == o["id"]))).scalar_one()
    assert (float(inv.subtotal), float(inv.tax), float(inv.total)) == (100.0, 5.0, 105.0)


async def test_finance_invoice_for_a_job_stores_its_tax(client, db):
    """Recheck: POST /api/finance/invoices with order_id stored (30, 0, 30): finance always sent
    tax_pct=0, which took the percentage path and dropped the job's VAT."""
    H, org_id = await _signup(client, country="GB")
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 30)])
    r = await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)
    assert r.status_code in (200, 201), r.text
    inv = (await db.execute(select(Invoice).where(Invoice.id == r.json()["id"]))).scalar_one()
    assert (float(inv.subtotal), float(inv.tax), float(inv.total)) == (25.0, 5.0, 30.0)


async def test_finance_invoice_discount_takes_the_tax_down_with_it(client, db):
    H, org_id = await _signup(client, country="GB")
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 120)])
    r = await client.post("/api/finance/invoices", json={"order_id": o["id"], "discount_pct": 10}, headers=H)
    assert r.status_code in (200, 201), r.text
    inv = (await db.execute(select(Invoice).where(Invoice.id == r.json()["id"]))).scalar_one()
    # 120 incl. 20 VAT, less 10% → 108 incl. 18 VAT
    assert (float(inv.subtotal), float(inv.discount), float(inv.tax), float(inv.total)) == (100.0, 10.0, 18.0, 108.0)


async def test_manual_invoice_with_a_tax_percentage_is_unchanged(client, db):
    H, org_id = await _signup(client, country="US")
    r = await client.post("/api/finance/invoices", json={
        "tax_pct": 10, "lines": [{"description": "Consulting", "qty": 1, "unit_price": 50}]}, headers=H)
    assert r.status_code in (200, 201), r.text
    inv = (await db.execute(select(Invoice).where(Invoice.id == r.json()["id"]))).scalar_one()
    assert (float(inv.subtotal), float(inv.tax), float(inv.total)) == (50.0, 5.0, 55.0)


# ── B4: the PDF prints the dates the invoice was given ────────────────────────────────────────
async def test_pdf_prints_a_us_manual_invoices_own_issue_and_due_dates(client, db, monkeypatch):
    """Recheck: issued 2026-11-04 / due 2026-11-18 printed 2026-11-03 / 2026-11-17 in New York."""
    from backend.services import printing
    H, org_id = await _signup(client, country="US")
    r = await client.post("/api/finance/invoices", json={
        "issued_at": "2026-11-04", "due_at": "2026-11-18",
        "lines": [{"description": "Repair", "qty": 1, "unit_price": 50}]}, headers=H)
    assert r.status_code in (200, 201), r.text
    seen = {}
    monkeypatch.setattr(printing, "render_document", lambda **kw: seen.update(kw) or b"%PDF")
    assert (await client.get(f"/api/print/invoices/{r.json()['id']}", headers=H)).status_code == 200
    meta = dict(seen["meta"])
    assert (meta["Issued"], meta["Due"]) == ("2026-11-04", "2026-11-18")


async def test_pdf_still_shows_an_auto_invoice_instant_on_the_shops_clock(client, db, monkeypatch):
    from backend.services import printing
    H, org_id = await _signup(client, country="IN")
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 100)])
    await client.post("/api/orders/bulk", json={"order_ids": [o["id"]], "action": "create_invoice"}, headers=H)
    inv = (await db.execute(select(Invoice).where(Invoice.order_id == o["id"]))).scalar_one()
    inv.issued_at = datetime(2026, 11, 4, 20, 0, 0, 1)       # 01:30 on the 5th in Kolkata
    await db.commit()
    seen = {}
    monkeypatch.setattr(printing, "render_document", lambda **kw: seen.update(kw) or b"%PDF")
    await client.get(f"/api/print/invoices/{inv.id}", headers=H)
    assert dict(seen["meta"])["Issued"] == "2026-11-05"


# ── B6: overdue is measured on the shop's clock ───────────────────────────────────────────────
async def test_new_york_job_due_in_two_hours_is_not_overdue(client, db):
    """Recheck: a NY job due 2h from now (shop wall clock) came back overdue_by_date=True."""
    H, org_id = await _signup(client, country="US")
    due = (_wall_now("America/New_York") + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 10)], due_at=due)
    assert o["overdue_by_date"] is False
    assert (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["overdue_by_date"] is False
    rows = (await client.get("/api/orders", headers=H)).json()["items"]
    assert next(x for x in rows if x["id"] == o["id"])["overdue_by_date"] is False
    board = (await client.get("/api/orders/board", headers=H)).json()
    cards = [c for col in (board.get("columns") or board.get("groups") or []) for c in col.get("cards", [])] \
        if isinstance(board, dict) else []
    for c in cards:
        if c["id"] == o["id"]:
            assert c["overdue"] is False


async def test_india_job_due_two_hours_ago_is_overdue(client, db):
    """Recheck: an IST job due 2h ago came back overdue_by_date=False."""
    H, org_id = await _signup(client, country="IN")
    due = (_wall_now("Asia/Kolkata") - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 10)], due_at=due)
    assert o["overdue_by_date"] is True
    assert (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["overdue_by_date"] is True
    feed = (await client.get("/api/reports/home-feed", headers=H)).json()
    over = [g for g in feed.get("items", feed.get("groups", [])) if "overdue" in (g.get("key") or g.get("id") or "")]
    assert over and over[0]["count"] >= 1


async def test_tech_today_overdue_uses_the_shops_clock(client, db):
    H, org_id = await _signup(client, country="US")
    me = (await client.get("/api/auth/me", headers=H)).json()
    due = (_wall_now("America/New_York") + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 10)], due_at=due,
               assigned_to_id=me.get("id") or me.get("employee", {}).get("id"))
    r = await client.get("/api/tech/dashboard", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["overdue"] == []


async def test_due_at_sent_with_a_zone_is_stored_on_the_shops_clock(client, db):
    H, org_id = await _signup(client, country="IN")
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 10)], due_at="2026-11-04T10:00:00Z")
    stored = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    assert stored.due_at == datetime(2026, 11, 4, 15, 30)


# ── B6: task times ─────────────────────────────────────────────────────────────────────────────
async def test_task_feed_and_instants_are_sent_with_z(client, db):
    """Recheck: the task activity feed returned bare UTC, so an IST viewer saw 15:56 as 10:26."""
    H, org_id = await _signup(client, country="IN")
    t = (await client.post("/api/tasks", json={"title": "Order parts"}, headers=H)).json()
    await client.post(f"/api/tasks/{t['id']}/comments", json={"body": "Ordered"}, headers=H)
    items = (await client.get(f"/api/tasks/{t['id']}/feed", headers=H)).json()["items"]
    assert items and all(i["created_at"].endswith("Z") for i in items)
    row = next(x for x in (await client.get("/api/tasks", headers=H)).json()["items"] if x["id"] == t["id"])
    assert row["created_at"].endswith("Z")


async def test_task_overdue_uses_the_shops_clock(client, db):
    H, org_id = await _signup(client, country="US")
    due = (_wall_now("America/New_York") + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    t = (await client.post("/api/tasks", json={"title": "Call back", "due_at": due}, headers=H)).json()
    assert t["overdue"] is False
    rows = (await client.get("/api/tasks?overdue=true", headers=H)).json()["items"]
    assert t["id"] not in [x["id"] for x in rows]


async def test_task_finished_before_its_shop_local_due_time_is_on_time(client, db):
    """completed_at (UTC) was compared with due_at (shop wall clock): a New York task finished two
    hours early was logged as late."""
    H, org_id = await _signup(client, country="US")
    due = (_wall_now("America/New_York") + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    t = (await client.post("/api/tasks", json={"title": "Call back", "due_at": due}, headers=H)).json()
    r = await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["completed_on_time"] is True
    assert r.json()["completed_at"].endswith("Z")


async def test_legacy_staff_task_due_at_with_a_zone_is_stored_on_the_shops_clock(client, db):
    """POST /api/staff/tasks stored an offset due_at as an aware/UTC time, off the shop's clock."""
    from backend.models import Task
    H, org_id = await _signup(client, country="IN")
    r = await client.post("/api/staff/tasks", json={"title": "Order parts", "due_at": "2026-11-04T10:00:00Z"},
                          headers=H)
    assert r.status_code == 201, r.text
    t = (await db.execute(select(Task).where(Task.org_id == org_id, Task.title == "Order parts"))).scalar_one()
    assert t.due_at == datetime(2026, 11, 4, 15, 30)
