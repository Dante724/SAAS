"""Memberships, supplier warranty, the pilot report and estimate expiry run on the shop's calendar
day, not the server's (hardening council Q2 and Q6).

Deterministic whatever the machine's TZ: the clock is frozen through dates._clock at 10:30 UTC,
when Kiritimati (UTC+14) is already on the next day and Pago Pago (UTC-11) is still on the
previous one. The two shops are two days apart, so a server date (one value, in any zone) can be
right for at most one of them: each test checks both.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from backend.models import Client, Membership, MembershipPeriod, Organisation, SerializedItem
from backend.services import dates
from backend.services import memberships as svc

pytestmark = pytest.mark.asyncio

AHEAD, BEHIND = "Pacific/Kiritimati", "Pacific/Pago_Pago"


def _moment():
    """10:30 UTC on the day that puts it within 12h of the real clock (tokens issued before the
    freeze stay valid). Returns (frozen instant, {zone: that zone's calendar day})."""
    now = datetime.now(timezone.utc)
    at = now.replace(hour=10, minute=30, second=0, microsecond=0)
    if now - at > timedelta(hours=12):
        at += timedelta(days=1)
    elif at - now > timedelta(hours=12):
        at -= timedelta(days=1)
    d = at.date()
    return at, {AHEAD: d + timedelta(days=1), BEHIND: d - timedelta(days=1)}


def _freeze(monkeypatch, at):
    monkeypatch.setattr(dates, "_clock", lambda: at)


async def _shop(client, tz):
    email = f"sc-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Far Harbor", "name": "Owner", "email": email, "password": "pw-test-12345",
        "timezone": tz})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["org_id"]


async def _member(client, H, billing="yearly"):
    oil = (await client.post("/api/catalog/services", json={"name": "Oil change", "price": 80},
                             headers=H)).json()["id"]
    plan = (await client.post("/api/memberships/plans", headers=H, json={
        "name": "Boat Care", "price_monthly": 45, "price_yearly": 449,
        "items": [{"kind": "service", "service_id": oil, "qty": 2}]})).json()
    cid = (await client.post("/api/clients", json={"name": "Sam", "phone": "5550142233"},
                             headers=H)).json()["id"]
    boat = (await client.post("/api/assets", json={"owner_type": "client", "client_id": cid,
                                                   "brand": "Bayliner", "model": "VR5"}, headers=H)).json()["id"]
    r = await client.post("/api/memberships", json={"plan_id": plan["id"], "client_id": cid,
                                                    "device_id": boat, "billing": billing}, headers=H)
    assert r.status_code == 201, r.text
    return r.json(), oil, cid, boat


async def _close_with_oil(client, H, cid, boat, oil):
    oid = (await client.post("/api/orders", json={"client_id": cid, "device_id": boat, "complaint": "Service"},
                             headers=H)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", headers=H, json={
        "qty": 1, "kind": "service", "ref_id": oil, "description": "Oil change", "unit_price": 80})
    assert r.status_code == 201, r.text
    await client.post("/api/statuses", json={"name": "Done", "key": f"done-{oid[:6]}",
                                             "status_group": "closed", "is_terminal": True}, headers=H)
    r = await client.post(f"/api/orders/{oid}/complete", json={}, headers=H)
    assert r.status_code == 200, r.text
    return oid


# ── selling ──────────────────────────────────────────────────────────────────
async def test_a_plan_sold_starts_and_falls_due_on_the_shops_day(client, monkeypatch):
    at, day = _moment()
    for tz in (AHEAD, BEHIND):
        H, _ = await _shop(client, tz)
        _freeze(monkeypatch, at)
        m, *_ = await _member(client, H)
        assert m["starts_on"] == day[tz].isoformat(), tz
        assert m["renews_on"] == svc.add_months(day[tz], 12).isoformat(), tz
        inv = (await client.get(f"/api/finance/invoices/{m['invoice_id']}", headers=H)).json()
        assert inv["due_at"][:10] == day[tz].isoformat(), tz
        monkeypatch.undo()


# ── usage: council Q2 repro (`2.0 == 0` at TZ=UTC+14) ───────────────────────
async def test_a_job_on_the_plans_first_day_counts_in_this_plan_year(client, monkeypatch):
    """The job is opened on the shop's first day of the plan. It must count against this plan
    year, not slide back into the year before (which left the member's count untouched)."""
    at, day = _moment()
    for tz in (AHEAD, BEHIND):
        H, _ = await _shop(client, tz)
        _freeze(monkeypatch, at)
        m, oil, cid, boat = await _member(client, H)
        await _close_with_oil(client, H, cid, boat, oil)
        card = (await client.get(f"/api/memberships/for?device_id={boat}", headers=H)).json()
        assert card["live"][0]["starts_on"] == day[tz].isoformat(), tz
        assert card["live"][0]["items"][0]["left"] == 1, tz
        assert card["live"][0]["saved"] == 80, tz
        monkeypatch.undo()


# ── what the member owes ─────────────────────────────────────────────────────
async def test_owed_and_payment_due_today_follow_the_shops_day(client, db, monkeypatch):
    at, day = _moment()
    for tz in (AHEAD, BEHIND):
        H, _ = await _shop(client, tz)
        _freeze(monkeypatch, at)
        m, *_ = await _member(client, H)
        # Pin the period to the shop's today, whatever day the sale used.
        await db.execute(update(MembershipPeriod).where(MembershipPeriod.membership_id == m["id"])
                         .values(due_on=day[tz]))
        await db.commit()
        got = (await client.get(f"/api/memberships/{m['id']}", headers=H)).json()
        assert got["unpaid"] is True and got["owed"] == 449, tz
        assert got["attention"] == {"kind": "unpaid", "text": "Payment due today"}, tz
        monkeypatch.undo()


async def test_cancel_now_ends_on_the_shops_day(client, db, monkeypatch):
    at, day = _moment()
    for tz in (AHEAD, BEHIND):
        H, _ = await _shop(client, tz)
        _freeze(monkeypatch, at)
        m, *_ = await _member(client, H)
        r = await client.post(f"/api/memberships/{m['id']}/cancel", json={"when": "now"}, headers=H)
        assert r.status_code == 200, r.text
        assert r.json()["ended_on"] == day[tz].isoformat(), tz
        monkeypatch.undo()


async def test_not_now_at_checkout_snoozes_from_the_shops_day(client, db, monkeypatch):
    at, day = _moment()
    for tz in (AHEAD, BEHIND):
        H, org_id = await _shop(client, tz)
        _freeze(monkeypatch, at)
        cid = (await client.post("/api/clients", json={"name": "Lee"}, headers=H)).json()["id"]
        oid = (await client.post("/api/orders", json={"client_id": cid, "complaint": "x"}, headers=H)).json()["id"]
        r = await client.post(f"/api/memberships/order/{oid}/snooze", headers=H)
        assert r.status_code == 200, r.text
        org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
        row = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
        await db.refresh(row)
        assert row.membership_offer_snoozed_until == day[tz] + timedelta(days=org.membership_prompt_snooze_days or 0), tz
        monkeypatch.undo()


# ── the daily sweep ──────────────────────────────────────────────────────────
async def test_the_sweep_runs_on_the_shops_day(client, db, monkeypatch):
    """A pre-sold plan starts on its first day on the shop's clock: Kiritimati's starts today
    (its today), Pago Pago's starts tomorrow (the UTC day) and must wait."""
    at, day = _moment()
    starts = {AHEAD: day[AHEAD], BEHIND: day[BEHIND] + timedelta(days=1)}
    want = {AHEAD: "active", BEHIND: "scheduled"}
    for tz in (AHEAD, BEHIND):
        H, org_id = await _shop(client, tz)
        _freeze(monkeypatch, at)
        m, *_ = await _member(client, H)
        await db.execute(update(Membership).where(Membership.id == m["id"]).values(
            status="scheduled", term_start=starts[tz]))
        await db.commit()
        org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
        await svc.sweep(db, org)
        await db.commit()
        row = (await db.execute(select(Membership).where(Membership.id == m["id"]))).scalar_one()
        await db.refresh(row)
        assert row.status == want[tz], tz
        monkeypatch.undo()


async def test_prepaid_unearned_counts_from_the_shops_day(client, db, monkeypatch):
    at, day = _moment()
    for tz in (AHEAD, BEHIND):
        H, org_id = await _shop(client, tz)
        _freeze(monkeypatch, at)
        m, *_ = await _member(client, H)
        start = day[tz] - timedelta(days=10)
        end = start + timedelta(days=100)
        await db.execute(update(MembershipPeriod).where(MembershipPeriod.membership_id == m["id"]).values(
            period_start=start, period_end=end, paid_at=dates.now_utc()))
        await db.commit()
        assert await svc.prepaid_unearned(db, org_id) == round(449 * 90 / 100, 2), tz
        monkeypatch.undo()


# ── reporting ────────────────────────────────────────────────────────────────
async def test_profitability_counts_the_shops_days(client, monkeypatch):
    """Money in and value given are stamped in UTC; the report's start/end are the shop's days."""
    at, day = _moment()
    for tz in (AHEAD, BEHIND):
        H, _ = await _shop(client, tz)
        _freeze(monkeypatch, at)
        m, oil, cid, boat = await _member(client, H)
        acc = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                                 headers=H)).json()["id"]
        r = await client.post(f"/api/finance/invoices/{m['invoice_id']}/pay", headers=H,
                              json={"amount": 449, "method": "cash", "account_id": acc})
        assert r.status_code == 200, r.text
        await client.get(f"/api/memberships/{m['id']}", headers=H)        # marks the period paid
        await _close_with_oil(client, H, cid, boat, oil)
        today, tomorrow = day[tz].isoformat(), (day[tz] + timedelta(days=1)).isoformat()
        rep = (await client.get(f"/api/memberships/reports/profitability?start={today}&end={tomorrow}",
                                headers=H)).json()
        assert (rep["plans"][0]["money_in"], rep["plans"][0]["value_given"]) == (449, 80), tz
        rep = (await client.get("/api/memberships/reports/profitability", headers=H)).json()
        assert (rep["plans"][0]["money_in"], rep["plans"][0]["value_given"]) == (449, 80), tz
        monkeypatch.undo()


# ── Q6: supplier warranty on received serials ────────────────────────────────
async def test_supplier_warranty_runs_from_the_shops_day(client, db, monkeypatch):
    at, day = _moment()
    for tz in (AHEAD, BEHIND):
        H, org_id = await _shop(client, tz)
        _freeze(monkeypatch, at)
        loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
        p = (await client.post("/api/catalog/products", headers=H, json={
            "name": f"Phone {uuid.uuid4().hex[:6]}", "sell_price": 10, "serialized": True,
            "supplier_warranty_days": 90})).json()
        sn = f"SN{uuid.uuid4().hex[:10].upper()}"
        r = await client.post("/api/inventory/postings", headers=H, json={
            "location_id": loc, "lines": [{"part_id": p["id"], "qty": 1, "purchase_price": 5, "serials": [sn]}]})
        assert r.status_code == 201, r.text
        unit = (await db.execute(select(SerializedItem).where(SerializedItem.org_id == org_id,
                                                              SerializedItem.serial_number == sn))).scalar_one()
        assert unit.supplier_warranty_until == day[tz] + timedelta(days=90), tz
        # Last day of the warranty is the shop's today: still covered. The day before: not.
        for until, covered in ((day[tz], True), (day[tz] - timedelta(days=1), False)):
            unit.supplier_warranty_until = until
            await db.commit()
            pp = (await client.get(f"/api/serials/{unit.id}", headers=H)).json()
            assert pp["in_supplier_warranty"] is covered, (tz, until)
        monkeypatch.undo()


# ── Q6: the pilot report's default range ─────────────────────────────────────
async def test_pilot_report_defaults_to_the_shops_last_seven_days(client, monkeypatch):
    at, day = _moment()
    for tz in (AHEAD, BEHIND):
        H, _ = await _shop(client, tz)
        _freeze(monkeypatch, at)
        cid = (await client.post("/api/clients", json={"name": "Maria"}, headers=H)).json()["id"]
        o = (await client.post("/api/orders", json={"client_id": cid, "complaint": "screen"}, headers=H)).json()
        await client.get(f"/api/pilot/job-labels?order_ids={o['id']}", headers=H)
        items = (await client.get("/api/statuses?record_type=work_order", headers=H)).json()["items"]
        s1 = [s["key"] for s in items if not s.get("is_terminal")][1]
        r = await client.patch(f"/api/orders/{o['id']}/status", headers={**H, "X-Tekly-Client": "go/pwa-test"},
                               json={"status": s1})
        assert r.status_code == 200, r.text
        rep = (await client.get("/api/pilot/go-report", headers=H)).json()
        assert rep["range"] == {"from": (day[tz] - timedelta(days=6)).isoformat(),
                                "to": day[tz].isoformat(), "timezone": tz}, tz
        assert rep["headline"]["status_changes_go"] == 1, tz
        monkeypatch.undo()


# ── Q6: estimate "valid until" honours the frozen shop clock ─────────────────
async def test_estimate_expiry_uses_the_shops_day(client, db, monkeypatch):
    from backend.routers.public import _expired
    _, org_id = await _shop(client, AHEAD)
    _freeze(monkeypatch, datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc))   # Kiritimati: 2 Jan 2030

    def est(vu):
        return SimpleNamespace(is_estimate=True, estimate_valid_until=vu, org_id=org_id, location_id=None)
    assert await _expired(db, est(date(2030, 1, 1))) is True
    assert await _expired(db, est(date(2030, 1, 2))) is False
