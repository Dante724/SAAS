"""A confirmed Stripe subscription payment moves the shop's paid-until date (sub_end) forward.

Owner rule ("Yes to both"): only signature-checked Stripe Billing events on Tekly's own account
count: ``invoice.paid`` / ``invoice.payment_succeeded`` for a paid invoice, and
``customer.subscription.updated`` with status ``active``. sub_end becomes the UTC date of the
subscription's ``current_period_end`` (an invoice's subscription line ``period.end``) and never
moves back. Each event id applies once (billing_events), every change writes one admin action-log
row (before/after, event id as target), and a shop locked after its grace opens again at once.
A failed, refunded or unpaid payment, a non-active status, a bad signature, an unknown customer or
another shop's ids change nothing. The admin's hand edits keep working and can set any date.
"""
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.config import settings
from backend.models import AdminActionLog, BillingEvent, Organisation
from backend.services.dates import platform_today

pytestmark = pytest.mark.asyncio

WHSEC = "whsec_test_sub_end"
DAY = 86400


@pytest.fixture(autouse=True)
def stripe_cfg(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_BILLING_WEBHOOK_SECRET", WHSEC)


def _sign(payload: bytes, secret=WHSEC, ts=None) -> str:
    ts = int(ts if ts is not None else time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _event(etype, obj, eid=None, created=None):
    return {"id": eid or f"evt_{uuid.uuid4().hex[:12]}", "type": etype,
            "created": created or int(time.time()), "data": {"object": obj}}


async def _post(client, event, sig=True, **kw):
    payload = json.dumps(event).encode()
    headers = {"Content-Type": "application/json"}
    if sig:
        headers["Stripe-Signature"] = _sign(payload, **kw)
    return await client.post("/api/billing/webhook", content=payload, headers=headers)


def _ts(days):
    """A Stripe timestamp `days` from now (midday UTC, so the date is unambiguous)."""
    d = platform_today() + timedelta(days=days)
    return int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp())


def _day(days):
    return platform_today() + timedelta(days=days)


async def _shop(client, db, n, sub_end=None, **fields):
    email = f"pay-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Paying Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    org_id = r.json()["org_id"]
    org = await db.get(Organisation, org_id)
    await db.refresh(org)
    org.sub_status, org.sub_end = "active", sub_end
    org.stripe_customer_id, org.stripe_subscription_id = f"cus_{n}", f"sub_{n}"
    for k, v in fields.items():
        setattr(org, k, v)
    await db.commit()
    return {"email": email, "id": org_id, "h": {"Authorization": f"Bearer {r.json()['access_token']}"}}


async def _sub_end(db, org_id):
    db.expire_all()
    return (await db.execute(select(Organisation.sub_end).where(Organisation.id == org_id))).scalar_one()


async def _renewals(db, org_id=None):
    q = select(AdminActionLog).where(AdminActionLog.action == "stripe_renewal")
    if org_id:
        q = q.where(AdminActionLog.org_id == org_id)
    db.expire_all()
    return (await db.execute(q.order_by(AdminActionLog.id))).scalars().all()


def _invoice(n, period_end, status="paid", **kw):
    return {"object": "invoice", "id": f"in_{uuid.uuid4().hex[:8]}", "customer": f"cus_{n}",
            "subscription": f"sub_{n}", "status": status, "paid": status == "paid",
            "period_end": _ts(-30),       # the invoice's own (previous) period: not the paid-until date
            "lines": {"data": [{"type": "subscription", "period": {"start": _ts(0), "end": period_end}}]},
            **kw}


def _subscription(n, period_end, status="active", **kw):
    return {"object": "subscription", "id": f"sub_{n}", "customer": f"cus_{n}", "status": status,
            "current_period_end": period_end, "metadata": {},
            "items": {"data": [{"id": f"si_{n}", "price": {"id": "price_unknown"}}]}, **kw}


# ── Payments move sub_end ────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("etype", ["invoice.paid", "invoice.payment_succeeded"])
async def test_paid_invoice_moves_sub_end_to_the_period_end(client, db, etype):
    s = await _shop(client, db, f"a{etype[-4:]}", sub_end=_day(2))
    r = await _post(client, _event(etype, _invoice(f"a{etype[-4:]}", _ts(32)), eid=f"evt_paid_{etype}"))
    assert r.status_code == 200, r.text
    assert await _sub_end(db, s["id"]) == _day(32)


async def test_newer_invoice_api_shape_is_read_too(client, db):
    """API 2025+: the subscription id sits under parent.subscription_details; lines carry parent."""
    s = await _shop(client, db, "b1", sub_end=_day(1))
    inv = _invoice("b1", _ts(31))
    del inv["subscription"], inv["paid"]
    inv["parent"] = {"type": "subscription_details", "subscription_details": {"subscription": "sub_b1"}}
    inv["lines"]["data"][0] = {"parent": {"type": "subscription_item_details",
                                          "subscription_item_details": {"subscription": "sub_b1"}},
                               "period": {"start": _ts(0), "end": _ts(31)}}
    await _post(client, _event("invoice.paid", inv))
    assert await _sub_end(db, s["id"]) == _day(31)


async def test_active_subscription_update_moves_sub_end(client, db):
    s = await _shop(client, db, "c1", sub_end=_day(3))
    await _post(client, _event("customer.subscription.updated", _subscription("c1", _ts(33))))
    assert await _sub_end(db, s["id"]) == _day(33)


async def test_period_end_on_the_item_is_read(client, db):
    """Newer API versions put current_period_end on the subscription item."""
    s = await _shop(client, db, "c2", sub_end=_day(3))
    sub = _subscription("c2", None)
    del sub["current_period_end"]
    sub["items"]["data"][0]["current_period_end"] = _ts(40)
    await _post(client, _event("customer.subscription.updated", sub))
    assert await _sub_end(db, s["id"]) == _day(40)


async def test_a_shop_with_no_term_end_gets_one(client, db):
    s = await _shop(client, db, "c3", sub_end=None)
    await _post(client, _event("invoice.paid", _invoice("c3", _ts(30))))
    assert await _sub_end(db, s["id"]) == _day(30)


# ── Exactly once, never backwards, audited ───────────────────────────────────────────────────────
async def test_each_change_writes_one_audit_row_with_before_and_after(client, db):
    s = await _shop(client, db, "d1", sub_end=_day(1))
    await _post(client, _event("invoice.paid", _invoice("d1", _ts(31)), eid="evt_audit_d1"))
    rows = await _renewals(db, s["id"])
    assert len(rows) == 1
    row = rows[0]
    assert row.admin_id is None and row.outcome == "allowed" and row.target == "evt_audit_d1"
    assert row.before == {"sub_end": _day(1).isoformat()}
    assert row.after == {"sub_end": _day(31).isoformat()}
    assert "invoice.paid" in row.reason and "evt_audit_d1" in row.reason
    from backend.services import admin_log
    assert await admin_log.verify_chain(db) is True


async def test_a_replayed_event_changes_nothing_and_writes_no_second_row(client, db):
    s = await _shop(client, db, "e1", sub_end=_day(1))
    ev = _event("invoice.paid", _invoice("e1", _ts(31)), eid="evt_replay_e1")
    assert (await _post(client, ev)).json() == {"ok": True}
    assert await _sub_end(db, s["id"]) == _day(31)
    org = await db.get(Organisation, s["id"])
    org.sub_end = _day(5)                       # e.g. the admin corrected it by hand since
    await db.commit()
    again = await _post(client, ev)             # Stripe redelivers the same event
    assert again.status_code == 200 and again.json().get("duplicate") is True
    assert await _sub_end(db, s["id"]) == _day(5)
    assert len(await _renewals(db, s["id"])) == 1
    assert (await db.execute(select(func.count()).select_from(BillingEvent)
                             .where(BillingEvent.id == "evt_replay_e1"))).scalar() == 1


async def test_an_older_event_after_a_newer_one_does_not_move_sub_end_back(client, db):
    s = await _shop(client, db, "f1", sub_end=_day(1))
    now = int(time.time())
    await _post(client, _event("invoice.paid", _invoice("f1", _ts(61)), created=now))
    await _post(client, _event("invoice.paid", _invoice("f1", _ts(31)), created=now - 3600))
    await _post(client, _event("customer.subscription.updated", _subscription("f1", _ts(31)), created=now - 60))
    assert await _sub_end(db, s["id"]) == _day(61)
    assert len(await _renewals(db, s["id"])) == 1


async def test_a_later_date_than_stripes_is_kept(client, db):
    """A hand-set term end past Stripe's period end is never pulled back."""
    s = await _shop(client, db, "f2", sub_end=_day(200))
    await _post(client, _event("invoice.paid", _invoice("f2", _ts(30))))
    assert await _sub_end(db, s["id"]) == _day(200)
    assert await _renewals(db, s["id"]) == []


async def test_a_second_event_for_the_same_payment_writes_no_row(client, db):
    """invoice.paid and invoice.payment_succeeded both arrive for one payment."""
    s = await _shop(client, db, "f3", sub_end=_day(1))
    inv = _invoice("f3", _ts(31))
    await _post(client, _event("invoice.payment_succeeded", inv))
    await _post(client, _event("invoice.paid", inv))
    await _post(client, _event("customer.subscription.updated", _subscription("f3", _ts(31))))
    assert await _sub_end(db, s["id"]) == _day(31)
    assert len(await _renewals(db, s["id"])) == 1


# ── Nothing else counts ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("label", ["payment_failed", "refunded", "unpaid_invoice", "open_invoice",
                                   "past_due", "trialing", "incomplete", "canceled", "deleted"])
async def test_failed_refunded_unpaid_or_inactive_does_not_extend(client, db, label):
    n = f"g{label[:6]}"
    s = await _shop(client, db, n, sub_end=_day(2))
    ev = {
        "payment_failed": lambda: _event("invoice.payment_failed", _invoice(n, _ts(32), status="open")),
        "refunded": lambda: _event("charge.refunded", {"object": "charge", "customer": f"cus_{n}",
                                                       "invoice": "in_x", "refunded": True}),
        "unpaid_invoice": lambda: _event("invoice.paid", _invoice(n, _ts(32), status="uncollectible")),
        "open_invoice": lambda: _event("invoice.payment_succeeded", _invoice(n, _ts(32), status="open")),
        "past_due": lambda: _event("customer.subscription.updated", _subscription(n, _ts(32), "past_due")),
        "trialing": lambda: _event("customer.subscription.updated", _subscription(n, _ts(32), "trialing")),
        "incomplete": lambda: _event("customer.subscription.updated", _subscription(n, _ts(32), "incomplete")),
        "canceled": lambda: _event("customer.subscription.updated", _subscription(n, _ts(32), "canceled")),
        "deleted": lambda: _event("customer.subscription.deleted", _subscription(n, _ts(32), "canceled")),
    }[label]()
    assert (await _post(client, ev)).status_code == 200
    assert await _sub_end(db, s["id"]) == _day(2)
    assert await _renewals(db, s["id"]) == []


async def test_bad_or_missing_signature_is_400_and_changes_nothing(client, db):
    s = await _shop(client, db, "h1", sub_end=_day(2))
    ev = _event("invoice.paid", _invoice("h1", _ts(32)))
    assert (await _post(client, ev, sig=False)).status_code == 400
    assert (await _post(client, ev, secret="whsec_wrong")).status_code == 400
    assert (await _post(client, ev, ts=time.time() - 3600)).status_code == 400
    payload = json.dumps(ev).encode()
    tampered = json.dumps({**ev, "id": "evt_other"}).encode()
    r = await client.post("/api/billing/webhook", content=tampered, headers={"Stripe-Signature": _sign(payload)})
    assert r.status_code == 400
    assert await _sub_end(db, s["id"]) == _day(2)
    assert await _renewals(db, s["id"]) == []
    assert (await db.execute(select(func.count()).select_from(BillingEvent)
                             .where(BillingEvent.id == ev["id"]))).scalar() == 0


async def test_unknown_customer_changes_no_shop(client, db):
    s = await _shop(client, db, "i1", sub_end=_day(2))
    before = len(await _renewals(db))
    r = await _post(client, _event("invoice.paid", _invoice("nobody", _ts(32))))
    assert r.json().get("unmatched") is True
    r = await _post(client, _event("customer.subscription.updated", _subscription("nobody", _ts(32))))
    assert r.json().get("unmatched") is True
    assert await _sub_end(db, s["id"]) == _day(2)
    assert len(await _renewals(db)) == before


async def test_another_shops_ids_or_metadata_change_nothing(client, db):
    a = await _shop(client, db, "ja", sub_end=_day(2))
    b = await _shop(client, db, "jb", sub_end=_day(2))
    cases = [
        # shop A's customer, but metadata naming shop B
        _invoice("ja", _ts(40), subscription_details={"metadata": {"org_id": b["id"]}}),
        _subscription("ja", _ts(40), metadata={"org_id": b["id"]}),
        # shop B's metadata with shop A's subscription and no customer
        {**_invoice("ja", _ts(40), metadata={"org_id": b["id"]}), "customer": None},
        # shop A's customer with shop B's subscription id
        {**_invoice("ja", _ts(40)), "subscription": "sub_jb"},
        {**_subscription("jb", _ts(40)), "customer": "cus_ja"},
    ]
    for i, obj in enumerate(cases):
        etype = "invoice.paid" if obj["object"] == "invoice" else "customer.subscription.updated"
        assert (await _post(client, _event(etype, obj))).status_code == 200, i
        assert await _sub_end(db, a["id"]) == _day(2), i
        assert await _sub_end(db, b["id"]) == _day(2), i
    assert await _renewals(db, a["id"]) == [] and await _renewals(db, b["id"]) == []


async def test_a_custom_billed_shop_is_left_to_the_admin(client, db):
    s = await _shop(client, db, "k1", sub_end=_day(2), billing_mode="custom")
    await _post(client, _event("invoice.paid", _invoice("k1", _ts(32))))
    assert await _sub_end(db, s["id"]) == _day(2)


# ── A locked shop opens again ────────────────────────────────────────────────────────────────────
async def test_a_shop_locked_after_its_grace_unlocks_when_the_payment_lands(client, db):
    s = await _shop(client, db, "l1", sub_end=_day(-20))
    login = {"username": s["email"], "password": "pw-test-12345"}
    assert (await client.post("/api/auth/token", data=login)).status_code == 403
    assert (await client.get("/api/auth/me", headers=s["h"])).status_code == 403

    await _post(client, _event("invoice.paid", _invoice("l1", _ts(30))))

    r = await client.post("/api/auth/token", data=login)
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = await client.get("/api/auth/me", headers=h)
    assert me.status_code == 200
    assert me.json()["subscription"] == {"lapsed": False, "sub_end": _day(30).isoformat(),
                                         "grace_ends_at": None}
    assert (await client.post("/api/clients", json={"name": "Back open"}, headers=h)).status_code in (200, 201)


async def test_a_shop_in_grace_loses_the_renew_banner(client, db):
    s = await _shop(client, db, "l2", sub_end=_day(-3))
    assert (await client.get("/api/auth/me", headers=s["h"])).json()["subscription"]["lapsed"] is True
    await _post(client, _event("customer.subscription.updated", _subscription("l2", _ts(27))))
    assert (await client.get("/api/auth/me", headers=s["h"])).json()["subscription"]["lapsed"] is False


# ── The admin's hand edits still win ─────────────────────────────────────────────────────────────
async def test_admin_patch_still_sets_any_sub_end(client, db):
    from backend.tests.test_build144ce_platform_admin import _admin_hdr
    ahdr = await _admin_hdr(client)
    s = await _shop(client, db, "m1", sub_end=_day(1))
    await _post(client, _event("invoice.paid", _invoice("m1", _ts(31))))
    assert await _sub_end(db, s["id"]) == _day(31)
    for target in (_day(-2), _day(400), _day(10)):    # backwards, far forward, anything
        r = await client.patch(f"/api/admin/tenants/{s['id']}", headers=ahdr,
                               json={"sub_end": target.isoformat(), "reason": "Hand correction"})
        assert r.status_code == 200, r.text
        assert await _sub_end(db, s["id"]) == target
    r = await client.post(f"/api/admin/tenants/{s['id']}/extend-trial", headers=ahdr,
                          json={"days": 5, "reason": "Goodwill week"})
    assert r.status_code == 200 and r.json()["sub_end"] == _day(15).isoformat(), r.text
    assert len(await _renewals(db, s["id"])) == 1


async def test_a_paid_invoice_does_not_hide_an_earlier_status_change(client, db):
    """invoice.paid changes no status, so it doesn't advance the ordering clock: a subscription
    event created just before it, arriving after it, still applies."""
    s = await _shop(client, db, "n1", sub_end=_day(1))
    now = int(time.time())
    await _post(client, _event("invoice.paid", _invoice("n1", _ts(31)), created=now))
    await _post(client, _event("customer.subscription.updated", _subscription("n1", _ts(31), "past_due"),
                               created=now - 60))
    db.expire_all()
    org = await db.get(Organisation, s["id"])
    assert org.sub_status == "past_due" and org.sub_end == _day(31)
