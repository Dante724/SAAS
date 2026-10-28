"""Launch review stream A — sign-up, tax and time (blockers B2, B4, B6).

Each test reproduces a case the pre-launch reviewers hit, named after it."""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update

from backend.models import (
    Account,
    Appointment,
    Location,
    NotificationLog,
    NotificationRule,
    Order,
    Organisation,
    Sale,
    SaleLine,
    Tax,
    TaxRule,
)

pytestmark = pytest.mark.asyncio


async def _signup(client, **extra):
    email = f"a-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Fixit", "name": "Owner", "email": email, "password": "pw-test-12345", **extra})
    assert r.status_code == 201, r.text
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return H, r.json()["org_id"]


async def _loc(db, org_id):
    return (await db.execute(select(Location).where(Location.org_id == org_id)
                             .order_by(Location.created_at))).scalars().first()


async def _job(client, H, loc_id, lines):
    r = await client.post("/api/orders", json={
        "location_id": loc_id, "complaint": "Cracked screen",
        "lines": [{"line_type": "labour", "kind": "service", "description": d, "qty": 1, "unit_price": p}
                  for d, p in lines]}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


# ── B2: a new shop can't take its first payment ────────────────────────────────────────────────
async def test_b2_new_shop_has_cash_drawer_and_card_and_takes_first_payment(client, db):
    """Reviewers: paying a brand-new shop's first job returned 400 "Select a cashbox/account"."""
    H, org_id = await _signup(client, country="US")
    accts = (await client.get("/api/cash/accounts", headers=H)).json()["items"]
    by_option = {a["payment_option"]: a for a in accts}
    assert set(by_option) >= {"cash", "card"}
    assert by_option["cash"]["name"] == "Cash drawer"
    loc = await _loc(db, org_id)
    o = await _job(client, H, loc.id, [("Screen", 120)])
    r = await client.post(f"/api/cash/orders/{o['id']}/pay", json={
        "method": "cash", "account_id": by_option["cash"]["id"], "amount": 50}, headers=H)
    assert r.status_code == 200, r.text


async def test_b2_every_new_location_gets_its_own_accounts_and_the_shop_clock(client, db):
    H, org_id = await _signup(client, country="GB")
    r = await client.post("/api/staff/locations", json={"name": "Camden"}, headers=H)
    assert r.status_code == 201, r.text
    loc_id = r.json()["id"]
    rows = (await db.execute(select(Account).where(Account.location_id == loc_id))).scalars().all()
    assert sorted(a.payment_option for a in rows) == ["card", "cash"]
    assert all("Camden" in a.name for a in rows)
    tz = (await db.execute(select(Location.timezone).where(Location.id == loc_id))).scalar()
    assert tz == "Europe/London"


async def test_b2_seeding_is_idempotent(client, db):
    from backend.services.shop_setup import ensure_cash_accounts
    H, org_id = await _signup(client)
    loc = await _loc(db, org_id)
    assert await ensure_cash_accounts(db, loc) == []
    n = len((await db.execute(select(Account).where(Account.org_id == org_id))).scalars().all())
    assert n == 2


# ── B4: tax is never charged ───────────────────────────────────────────────────────────────────
async def test_b4_seeded_tax_is_on_the_sales_stack(client, db):
    """Reviewers: the default tax was seeded with no rule stack, so 0% applied."""
    H, org_id = await _signup(client, country="CA")
    tax = (await db.execute(select(Tax).where(Tax.org_id == org_id))).scalar_one()
    assert (tax.name, tax.rate, tax.mode) == ("GST", 5.0, "imposed")
    rule = (await db.execute(select(TaxRule).where(TaxRule.org_id == org_id))).scalar_one()
    assert (rule.doc_family, rule.tax_id) == ("tickets_sales", tax.id)


async def test_b4_edited_us_rate_is_charged_and_stored(client, db):
    """Reviewers: a US shop edited its rate to 8.875% and jobs still totalled without it ($259.49)."""
    H, org_id = await _signup(client, country="US")
    tax = (await db.execute(select(Tax).where(Tax.org_id == org_id))).scalar_one()
    r = await client.put(f"/api/catalog/taxes/{tax.id}", json={"name": "NY sales tax", "rate": 8.875},
                         headers=H)
    assert r.status_code == 200, r.text
    loc = await _loc(db, org_id)
    o = await _job(client, H, loc.id, [("Screen", 199.00), ("Labour", 39.33)])
    assert o["total"] == pytest.approx(259.48, abs=0.01)   # 238.33 + 8.875%
    stored = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    assert float(stored.tax) == pytest.approx(21.15, abs=0.01)
    # adding a line later keeps the stored tax in step
    await client.post(f"/api/orders/{o['id']}/lines", json={
        "line_type": "labour", "kind": "service", "description": "Case", "unit_price": 20}, headers=H)
    await db.refresh(stored)
    assert float(stored.tax) == pytest.approx(21.15 + 1.78, abs=0.01)


async def test_b4_uk_signup_gets_20_percent_vat_pounds_and_london_time(client, db):
    """Reviewers: with no country at sign-up a UK shop got 21% VAT (the EU default)."""
    H, org_id = await _signup(client, country="GB")
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    assert (org.country, org.currency_code, org.currency, org.timezone) == ("GB", "GBP", "£", "Europe/London")
    tax = (await db.execute(select(Tax).where(Tax.org_id == org_id))).scalar_one()
    assert (tax.rate, tax.mode) == (20.0, "included")
    assert (await _loc(db, org_id)).timezone == "Europe/London"


@pytest.mark.parametrize("country,code,rate,tz", [
    ("US", "USD", 0.0, "America/New_York"), ("CA", "CAD", 5.0, "America/Toronto"),
    ("GB", "GBP", 20.0, "Europe/London"), ("DE", "EUR", 19.0, "Europe/Berlin"),
    ("IN", "INR", 18.0, "Asia/Kolkata")])
async def test_b4_country_picker_drives_currency_tax_and_timezone(client, db, country, code, rate, tz):
    H, org_id = await _signup(client, country=country)
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    tax = (await db.execute(select(Tax).where(Tax.org_id == org_id))).scalar_one()
    assert (org.currency_code, tax.rate, org.timezone) == (code, rate, tz)


async def test_b4_signup_options_list_the_launch_countries(client):
    r = await client.get("/api/auth/signup-options", headers={"cf-ipcountry": "gb"})
    body = r.json()
    codes = [c["code"] for c in body["countries"]]
    assert codes[:4] == ["US", "GB", "CA", "IN"] and "DE" in codes and "FR" in codes
    assert body["country_hint"] == "GB"


async def test_b4_vat_inclusive_price_is_unchanged_but_tax_is_stored(client, db):
    H, org_id = await _signup(client, country="GB")
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 120)])
    assert o["total"] == 120
    stored = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    assert float(stored.tax) == 20.0   # 120 contains 20 of VAT


async def test_b4_invoice_from_a_job_stores_its_tax(client, db):
    H, org_id = await _signup(client, country="CA")
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 100)])
    r = await client.post("/api/orders/bulk", json={"order_ids": [o["id"]], "action": "create_invoice"},
                          headers=H)
    assert r.status_code == 200, r.text
    from backend.models import Invoice
    inv = (await db.execute(select(Invoice).where(Invoice.order_id == o["id"]))).scalar_one()
    assert (float(inv.total), float(inv.tax)) == (105.0, 5.0)


async def test_b4_pos_sale_charges_and_stores_tax(client, db):
    """Reviewers: no tax on POS sales."""
    H, org_id = await _signup(client, country="CA")
    r = await client.post("/api/sales", json={"lines": [
        {"kind": "service", "description": "Screen protector fitting", "qty": 2, "unit_price": 10}]}, headers=H)
    assert r.status_code == 201, r.text
    assert (r.json()["total"], r.json()["tax"]) == (21.0, 1.0)
    sale = (await db.execute(select(Sale).where(Sale.id == r.json()["id"]))).scalar_one()
    line = (await db.execute(select(SaleLine).where(SaleLine.sale_id == sale.id))).scalar_one()
    assert (line.tax_rate, line.tax_mode, float(line.tax)) == (5.0, "imposed", 1.0)
    rc = (await client.get(f"/api/sales/{sale.id}/receipt", headers=H)).json()
    assert "Tax" in rc["text"]


async def test_b4_pos_tax_function_is_callable_for_quotes(client, db):
    """Stream C's POS quote dry-run calls this per cart line."""
    from backend.services.tax import pos_line_tax
    H, org_id = await _signup(client, country="GB")
    q = await pos_line_tax(db, org_id, kind="product", ref_id=None, net_amount=60)
    assert q == {"rate": 20.0, "mode": "included", "tax_id": q["tax_id"], "amount": 10.0}


async def test_b4_invoice_pdf_is_a_usable_tax_invoice(client, db, monkeypatch):
    """Reviewers: the PDF had no currency symbol, no tax lines, no address or VAT number, and a
    UTC stamp."""
    from backend.models import LegalEntity
    from backend.services import printing
    H, org_id = await _signup(client, country="GB")
    db.add(LegalEntity(org_id=org_id, name="Fixit Ltd", address="1 High St, London", vat_no="GB123456789",
                       is_default=True))
    await db.commit()
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 120)])
    await client.post("/api/orders/bulk", json={"order_ids": [o["id"]], "action": "create_invoice"}, headers=H)
    from backend.models import Invoice
    inv = (await db.execute(select(Invoice).where(Invoice.order_id == o["id"]))).scalar_one()
    seen = {}
    real = printing.render_document

    def spy(**kw):
        seen.update(kw)
        return real(**kw)
    monkeypatch.setattr(printing, "render_document", spy)
    r = await client.get(f"/api/print/invoices/{inv.id}", headers=H)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert "1 High St, London" in seen["header_lines"]
    assert ("VAT no.", "GB123456789") in seen["meta"]
    totals = {row[2]: row[3] for row in seen["totals"]}
    assert totals["Total"] == "£120.00"
    assert totals["VAT 20% (included)"] == "£20.00"
    assert seen["rows"][0][3] == "£120.00"
    assert seen["tz"] == "Europe/London"


async def test_b4_imposed_tax_pdf_lists_lines_before_tax(client, db, monkeypatch):
    from backend.services import printing
    H, org_id = await _signup(client, country="CA")
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 100)])
    await client.post("/api/orders/bulk", json={"order_ids": [o["id"]], "action": "create_invoice"}, headers=H)
    from backend.models import Invoice
    inv = (await db.execute(select(Invoice).where(Invoice.order_id == o["id"]))).scalar_one()
    seen = {}
    monkeypatch.setattr(printing, "render_document", lambda **kw: seen.update(kw) or b"%PDF")
    await client.get(f"/api/print/invoices/{inv.id}", headers=H)
    totals = [(row[2], row[3]) for row in seen["totals"]]
    assert totals == [("Subtotal", "$100.00"), ("GST 5%", "$5.00"), ("Total", "$105.00")]
    assert seen["rows"][0][3] == "$100.00"


# ── B6: every location runs on UTC ─────────────────────────────────────────────────────────────
def _utc(local: datetime, tz: str) -> datetime:
    from backend.services.dates import wall_to_utc
    return wall_to_utc(local, tz)


async def test_b6_new_york_3h_reminder_goes_out_3h_before_on_the_shops_clock(client, db):
    """Reviewers: a New York "3 hours before" reminder for an 11:01 appointment went out at 06:01."""
    from backend.services import notification_rules as nr
    H, org_id = await _signup(client, country="US")
    loc = await _loc(db, org_id)
    assert loc.timezone == "America/New_York"
    start = datetime(2026, 11, 4, 11, 1)                     # shop wall clock (EST, UTC-5)
    db.add(Appointment(org_id=org_id, location_id=loc.id, client_name="Dana", client_phone="+12125550100",
                       scheduled_for=start, status="booked"))
    db.add(NotificationRule(org_id=org_id, module="appointment", event="reminder", channel="sms",
                            reminder_offset_min=180, active=True))
    await db.commit()
    # 07:30 in New York: the appointment is 3.5 hours away, nothing yet
    assert await nr.queue_appointment_reminders(db, org_id=org_id, now=_utc(datetime(2026, 11, 4, 7, 30),
                                                                            "America/New_York")) == 0
    # 08:30 in New York: inside the window; due at 08:01 New York = 13:01 UTC
    assert await nr.queue_appointment_reminders(db, org_id=org_id, now=_utc(datetime(2026, 11, 4, 8, 30),
                                                                            "America/New_York")) == 1
    log = (await db.execute(select(NotificationLog).where(NotificationLog.org_id == org_id))).scalar_one()
    assert log.send_at == datetime(2026, 11, 4, 13, 1)


async def test_b6_ist_job_reminder_is_not_four_and_a_half_hours_late(client, db):
    """Reviewers: an IST shop's reminders arrived about 4.5 hours late."""
    from backend.services import notification_rules as nr
    H, org_id = await _signup(client, country="IN")
    loc = await _loc(db, org_id)
    o = await _job(client, H, loc.id, [("Screen", 100)])
    await db.execute(update(Order).where(Order.id == o["id"]).values(scheduled_for=datetime(2026, 11, 4, 15, 0)))
    db.add(NotificationRule(org_id=org_id, module="order", event="reminder", channel="email",
                            reminder_offset_min=60, active=True))
    await db.commit()
    now = _utc(datetime(2026, 11, 4, 14, 10), "Asia/Kolkata")   # 08:40 UTC
    assert await nr.queue_reminders(db, org_id=org_id, now=now) == 1
    log = (await db.execute(select(NotificationLog).where(NotificationLog.org_id == org_id,
                                                          NotificationLog.trigger_event == "reminder"))).scalar_one()
    assert log.send_at == datetime(2026, 11, 4, 8, 30)          # 14:00 IST, not 14:00 UTC


async def test_b6_working_hours_shift_uses_the_shops_clock(client, db):
    from backend.services.notification_rules import _into_working_hours
    H, org_id = await _signup(client, country="US")
    loc = await _loc(db, org_id)
    # 13:30 UTC is 08:30 in New York in November: before the 09:00 opening → 09:00 NY = 14:00 UTC
    assert await _into_working_hours(db, org_id, loc.id, datetime(2026, 11, 4, 13, 30)) == datetime(2026, 11, 4, 14, 0)
    # 15:00 UTC is 10:00 in New York: open, unchanged
    assert await _into_working_hours(db, org_id, loc.id, datetime(2026, 11, 4, 15, 0)) == datetime(2026, 11, 4, 15, 0)
    # 23:30 UTC is 18:30 in New York: after closing → next day 09:00 NY = 14:00 UTC
    assert await _into_working_hours(db, org_id, loc.id, datetime(2026, 11, 4, 23, 30)) == datetime(2026, 11, 5, 14, 0)


async def test_b6_location_on_old_utc_default_follows_the_company_clock(client, db):
    """Existing shops: locations stored as "UTC" read the company timezone (bookings sent through
    the API landed 4 hours late in New York)."""
    from backend.services.dates import parse_wall_dt, shop_tzname
    H, org_id = await _signup(client, country="US")
    loc = await _loc(db, org_id)
    await db.execute(update(Location).where(Location.id == loc.id).values(timezone="UTC"))
    await db.commit()
    assert await shop_tzname(db, org_id, loc.id) == "America/New_York"
    # an API booking for 15:00 UTC is 11:00 on the shop's clock (EDT), not 15:00
    assert await parse_wall_dt(db, org_id, loc.id, "2026-10-28T15:00:00Z") == datetime(2026, 10, 28, 11, 0)


async def test_b6_past_slots_are_not_offered_in_the_shops_timezone(client, db):
    """Reviewers: past slots were still offered (a New York shop's "now" was read in UTC)."""
    from backend.services import booking_slots as bs
    from backend.services.dates import local_now
    H, org_id = await _signup(client, country="US")
    loc = await _loc(db, org_id)
    hours = {d: {"open": "00:00", "close": "23:59"} for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
    await db.execute(update(Location).where(Location.id == loc.id).values(working_hours=hours, timezone="UTC"))
    await db.execute(update(Organisation).where(Organisation.id == org_id).values(
        appointment_enabled=True, appointment_time_slot=60, appointment_lead_min=0))
    await db.commit()
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    ans = await bs.slots_for_day(db, org, local_now("America/New_York").date(), location_id=loc.id)
    now_ny = local_now("America/New_York")
    assert ans["now"].hour == now_ny.hour
    assert all(s > now_ny.strftime("%H:%M") for s in ans["slots"])


async def test_b6_company_timezone_change_moves_following_locations(client, db):
    H, org_id = await _signup(client, country="US")
    r = await client.put("/api/staff/company", json={"timezone": "America/Chicago"}, headers=H)
    assert r.status_code == 200, r.text
    assert (await _loc(db, org_id)).timezone == "America/Chicago"


async def test_b6_server_times_are_sent_with_z(client, db):
    """Reviewers: the activity log was 4–5 hours off and web clock-in showed 10:00 as 04:30, because
    UTC times went out without a zone and the browser read them as local time."""
    H, org_id = await _signup(client, country="IN")
    o = await _job(client, H, (await _loc(db, org_id)).id, [("Screen", 100)])
    await client.post(f"/api/feed/order/{o['id']}", json={"body": "Called the customer"}, headers=H)
    items = (await client.get(f"/api/feed/order/{o['id']}", headers=H)).json()
    items = items.get("items", items) if isinstance(items, dict) else items
    assert items and all(i["created_at"].endswith("Z") for i in items)
    await db.execute(update(Organisation).where(Organisation.id == org_id).values(clock_attendance_enabled=True))
    await db.commit()
    r = await client.post("/api/timeclock/shift/clock-in", json={}, headers=H)
    assert r.status_code in (200, 201), r.text
    shift = (await client.get("/api/timeclock/shift/active", headers=H)).json()["shift"]
    assert shift["started_at"].endswith("Z")
    started = datetime.fromisoformat(shift["started_at"].replace("Z", "+00:00"))
    assert abs((started.replace(tzinfo=None) - datetime.utcnow()).total_seconds()) < 60
    assert timedelta(0) <= timedelta(seconds=shift["elapsed_sec"]) < timedelta(minutes=1)
