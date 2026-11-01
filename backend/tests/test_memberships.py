"""Memberships (docs/SPEC_memberships.md): plans, selling, work-order coverage, billing, the
missed-payment sweep, the checkout offer, tags, roles and tenant isolation."""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from backend.models import Membership, MembershipPeriod, Organisation
from backend.services import memberships as svc
from backend.services.permissions import grant_membership_codes


async def _register(client):
    email = f"mb-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Harbor", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["org_id"]


async def _teammate(client, H, *deny):
    r = await client.post("/api/settings/rbac/roles", json={
        "name": f"Role {uuid.uuid4().hex[:4]}", "permissions": {c: False for c in deny}}, headers=H)
    assert r.status_code in (200, 201), r.text
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Tech", "email": email, "password": "pw-test-12345", "role_id": r.json()["id"]}, headers=H)
    assert r.status_code == 201, r.text
    tok = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _service(client, H, name, price):
    r = await client.post("/api/catalog/services", json={"name": name, "price": price}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _client_and_boat(client, H, name="Sam Rivera", phone="5550142233"):
    cid = (await client.post("/api/clients", json={"name": name, "phone": phone}, headers=H)).json()["id"]
    r = await client.post("/api/assets", json={"owner_type": "client", "client_id": cid,
                                               "brand": "Bayliner", "model": "VR5"}, headers=H)
    assert r.status_code in (200, 201), r.text
    return cid, r.json()["id"]


async def _plan(client, H, oil, **over):
    body = {"name": "Boat Care", "price_monthly": 45, "price_yearly": 449, "items": [
        {"kind": "service", "service_id": oil, "qty": 2},
        {"kind": "discount", "pct": 10, "applies_to": "labour"},
        {"kind": "perk", "label": "Priority slot"}], **over}
    r = await client.post("/api/memberships/plans", json=body, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _order(client, H, cid, did, lines=()):
    r = await client.post("/api/orders", json={"client_id": cid, "device_id": did, "complaint": "Service",
                                               "lines": list(lines)}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _add(client, H, oid, **line):
    r = await client.post(f"/api/orders/{oid}/lines", json={"qty": 1, **line}, headers=H)
    assert r.status_code == 201, r.text
    o = (await client.get(f"/api/orders/{oid}", headers=H)).json()
    return next(x for x in o["lines"] if x["id"] == r.json()["line_id"])


async def _sell(client, H, plan_id, cid, did, billing="yearly", **kw):
    r = await client.post("/api/memberships", json={"plan_id": plan_id, "client_id": cid, "device_id": did,
                                                    "billing": billing, **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _tags(client, H, cid):
    return [t["name"] for t in (await client.get(f"/api/tags/for?entity_type=contact&entity_id={cid}",
                                                 headers=H)).json()["items"]]


async def _close(client, H, oid):
    await client.post("/api/statuses", json={"name": "Done", "key": f"done-{oid[:6]}",
                                             "status_group": "closed", "is_terminal": True}, headers=H)
    r = await client.post(f"/api/orders/{oid}/complete", json={}, headers=H)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_plan_setup_validates_and_lists_what_it_includes(client):
    H, _ = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    r = await client.post("/api/memberships/plans", json={"name": "Nothing", "items": []}, headers=H)
    assert r.status_code == 422 and "price" in r.json()["detail"]
    r = await client.post("/api/memberships/plans", json={"name": "Bad", "price_yearly": 10, "term": "season",
                                                          "season_start": "13-01", "season_end": "03-31"}, headers=H)
    assert r.status_code == 422
    p = await _plan(client, H, oil)
    assert [i["text"] for i in p["items"]] == ["Oil change × 2", "10% off labour", "Priority slot"]
    plans = (await client.get("/api/memberships/plans", headers=H)).json()["items"]
    assert plans[0]["name"] == "Boat Care" and plans[0]["members"] == 0
    starters = (await client.get("/api/memberships/plans/starters?template=auto", headers=H)).json()["items"]
    assert starters[0]["name"] == "Auto Care"
    assert any(i["service_id"] == oil for i in starters[0]["items"])      # matched to the catalog


@pytest.mark.asyncio
async def test_selling_a_yearly_plan_invoices_it_and_tags_the_member(client):
    H, _ = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    plan = await _plan(client, H, oil)
    cid, boat = await _client_and_boat(client, H)
    m = await _sell(client, H, plan["id"], cid, boat)
    assert m["status"] == "active" and m["billing"] == "yearly" and m["invoice_id"]
    assert m["renews_on"] == (svc.add_months(date.today(), 12)).isoformat()
    inv = (await client.get(f"/api/finance/invoices/{m['invoice_id']}", headers=H)).json()
    assert inv["total"] == 449
    assert "Boat Care member" in await _tags(client, H, cid)
    # The member tag is Tekly's: staff can't take it off by hand.
    tag = next(t for t in (await client.get(f"/api/tags/for?entity_type=contact&entity_id={cid}",
                                            headers=H)).json()["items"])
    r = await client.post("/api/tags/detach", json={"tag_id": tag["id"], "entity_type": "contact",
                                                     "entity_id": cid}, headers=H)
    assert r.status_code == 409
    # One plan per asset.
    r = await client.post("/api/memberships", json={"plan_id": plan["id"], "client_id": cid, "device_id": boat,
                                                    "billing": "monthly"}, headers=H)
    assert r.status_code == 409
    # Profile card.
    card = (await client.get(f"/api/memberships/for?client_id={cid}", headers=H)).json()
    assert card["live"][0]["items"][0]["left"] == 2 and card["former"] == []


@pytest.mark.asyncio
async def test_work_order_uses_the_plan_up_and_counts_it_at_close(client):
    H, _ = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    plan = await _plan(client, H, oil)
    cid, boat = await _client_and_boat(client, H)
    await _sell(client, H, plan["id"], cid, boat)
    oid = await _order(client, H, cid, boat)
    first = await _add(client, H, oid, kind="service", ref_id=oil, description="Oil change", unit_price=80)
    assert first["discount"] == 100 and first["membership_id"]
    second = await _add(client, H, oid, kind="service", ref_id=oil, description="Oil change", unit_price=80)
    assert second["discount"] == 100
    third = await _add(client, H, oid, kind="service", ref_id=oil, description="Oil change", unit_price=80)
    assert third["discount"] == 10                 # count used up: only the labour discount is left
    labour = await _add(client, H, oid, line_type="labour", kind="custom", description="Rig", unit_price=200)
    assert labour["discount"] == 10                                       # plan discount
    o = (await client.get(f"/api/orders/{oid}", headers=H)).json()
    assert o["total"] == 72 + 180
    banner = (await client.get(f"/api/memberships/order/{oid}", headers=H)).json()
    assert banner["plans"][0]["plan"] == "Boat Care" and banner["offer"] is None
    await _close(client, H, oid)
    card = (await client.get(f"/api/memberships/for?device_id={boat}", headers=H)).json()
    assert card["live"][0]["items"][0]["left"] == 0
    detail = (await client.get(f"/api/memberships/{card['live'][0]['id']}", headers=H)).json()
    assert detail["value_given"] == 160 + 8 + 20


@pytest.mark.asyncio
async def test_only_staff_allowed_to_apply_benefits_get_them_applied(client):
    H, _ = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    plan = await _plan(client, H, oil)
    cid, boat = await _client_and_boat(client, H)
    await _sell(client, H, plan["id"], cid, boat)
    T = await _teammate(client, H, "can_apply_membership_benefits")
    oid = await _order(client, H, cid, boat)
    line = await _add(client, T, oid, kind="service", ref_id=oil, description="Oil change", unit_price=80,
                      membership_id="forged")
    assert line["discount"] == 0 and not line["membership_id"]
    banner = (await client.get(f"/api/memberships/order/{oid}", headers=T)).json()
    assert banner["coverable"] == 1 and banner["can_apply"] is False
    assert (await client.post(f"/api/memberships/order/{oid}/apply", headers=T)).status_code == 403
    r = await client.post(f"/api/memberships/order/{oid}/apply", headers=H)
    assert r.json()["applied"] == 1
    o = (await client.get(f"/api/orders/{oid}", headers=H)).json()
    assert o["lines"][0]["discount"] == 100


@pytest.mark.asyncio
async def test_a_paused_plan_stops_covering_even_lines_already_on_the_job(client, db):
    H, org_id = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    plan = await _plan(client, H, oil)
    cid, boat = await _client_and_boat(client, H)
    m = await _sell(client, H, plan["id"], cid, boat)
    oid = await _order(client, H, cid, boat)
    await _add(client, H, oid, kind="service", ref_id=oil, description="Oil change", unit_price=80)
    assert (await client.post(f"/api/memberships/{m['id']}/pause", headers=H)).status_code == 200
    await _close(client, H, oid)
    o = (await client.get(f"/api/orders/{oid}", headers=H)).json()
    assert o["lines"][0]["discount"] == 0 and o["total"] == 80


@pytest.mark.asyncio
async def test_monthly_billing_pauses_then_lapses_when_unpaid_and_paying_restores_it(client, db):
    H, org_id = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    plan = await _plan(client, H, oil)
    cid, boat = await _client_and_boat(client, H)
    m = await _sell(client, H, plan["id"], cid, boat, billing="monthly")
    acc = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=H)).json()["id"]
    await client.post(f"/api/finance/invoices/{m['invoice_id']}/pay", headers=H,
                      json={"amount": 45, "method": "cash", "account_id": acc})
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    start = date.today()
    nxt = svc.add_months(start, 1)

    stats = await svc.sweep(db, org, today=nxt)                 # month 2 invoiced
    await db.commit()
    assert stats["invoiced"] == 1
    row = (await db.execute(select(Membership).where(Membership.id == m["id"]))).scalar_one()
    assert row.period_start == nxt and row.status == "active"
    await svc.sweep(db, org, today=nxt + timedelta(days=8))     # past the 7-day grace
    await db.commit()
    await db.refresh(row)
    assert row.status == "paused"
    paused = (await client.get("/api/memberships?view=paused", headers=H)).json()
    assert paused["counts"]["paused"] == 1 and paused["items"][0]["status"] == "paused"

    p2 = (await db.execute(select(MembershipPeriod).where(MembershipPeriod.membership_id == m["id"],
                                                          MembershipPeriod.paid_at.is_(None)))).scalar_one()
    await client.post(f"/api/finance/invoices/{p2.invoice_id}/pay", headers=H,
                      json={"amount": 45, "method": "cash", "account_id": acc})
    got = (await client.get(f"/api/memberships/{m['id']}", headers=H)).json()
    assert got["status"] == "active"

    # Next month, never paid: past lapse_days the plan ends and the tag becomes "Former".
    third = svc.add_months(start, 2)
    await svc.sweep(db, org, today=third)
    await svc.sweep(db, org, today=third + timedelta(days=31))
    await db.commit()
    await db.refresh(row)
    assert row.status == "lapsed" and row.ended_reason == "unpaid"
    assert await _tags(client, H, cid) == ["Former Boat Care member"]
    card = (await client.get(f"/api/memberships/for?client_id={cid}", headers=H)).json()
    assert card["live"] == [] and card["former"][0]["plan"] == "Boat Care"


@pytest.mark.asyncio
async def test_yearly_renewal_is_invoiced_ahead_and_rolls_the_plan_year(client, db):
    H, org_id = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    plan = await _plan(client, H, oil)
    cid, boat = await _client_and_boat(client, H)
    m = await _sell(client, H, plan["id"], cid, boat)
    acc = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=H)).json()["id"]
    await client.post(f"/api/finance/invoices/{m['invoice_id']}/pay", headers=H,
                      json={"amount": 449, "method": "cash", "account_id": acc})
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    row = (await db.execute(select(Membership).where(Membership.id == m["id"]))).scalar_one()
    end = row.term_end
    s = await svc.sweep(db, org, today=end - timedelta(days=40))
    assert s["invoiced"] == 0
    s = await svc.sweep(db, org, today=end - timedelta(days=30))      # renewal notice window
    assert s["invoiced"] == 1
    await svc.sweep(db, org, today=end)
    await db.commit()
    await db.refresh(row)
    assert row.term_start == end and row.term_end == svc.add_months(end, 12)


@pytest.mark.asyncio
async def test_season_plans_presell_and_prorate_late_joiners(client):
    H, _ = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    today = date.today()
    start = today + timedelta(days=30)
    end = start + timedelta(days=120)                 # a summer-style season that hasn't started
    plan = await _plan(client, H, oil, term="season", season_start=f"{start:%m-%d}",
                       season_end=f"{end:%m-%d}", presale_days=45)
    cid, boat = await _client_and_boat(client, H)
    m = await _sell(client, H, plan["id"], cid, boat)
    assert m["status"] == "scheduled" and m["starts_on"] == start.isoformat()
    # Too early to buy.
    p2 = await _plan(client, H, oil, name="Late season", term="season", season_start=f"{start:%m-%d}",
                     season_end=f"{end:%m-%d}", presale_days=10)
    r = await client.post("/api/memberships", json={"plan_id": p2["id"], "client_id": cid, "device_id": boat,
                                                    "billing": "yearly"}, headers=H)
    assert r.status_code == 409 and "goes on sale" in r.json()["detail"]
    # Mid-season: prorated.
    mid_start = today - timedelta(days=180)
    mid_end = svc.add_months(mid_start, 12) - timedelta(days=1)
    p3 = await _plan(client, H, oil, name="Mid", term="season", season_start=f"{mid_start:%m-%d}",
                     season_end=f"{mid_end:%m-%d}")
    cid2, boat2 = await _client_and_boat(client, H, name="Lee", phone="5550100000")
    m3 = await _sell(client, H, p3["id"], cid2, boat2)
    inv = (await client.get(f"/api/finance/invoices/{m3['invoice_id']}", headers=H)).json()
    assert 200 < inv["total"] < 449


@pytest.mark.asyncio
async def test_checkout_offer_respects_threshold_snooze_and_the_setting(client):
    H, _ = await _register(client)
    haul = await _service(client, H, "Haul-out", 300)
    await _plan(client, H, haul, items=[{"kind": "service", "service_id": haul, "qty": 1}])
    cid, boat = await _client_and_boat(client, H)
    oid = await _order(client, H, cid, boat, lines=[{"kind": "service", "ref_id": haul, "description": "Haul-out",
                                                     "qty": 1, "unit_price": 300}])
    offer = (await client.get(f"/api/memberships/order/{oid}", headers=H)).json()["offer"]
    assert offer["name"] == "Boat Care" and offer["saving"] == 300
    await client.put("/api/memberships/settings/shop", json={"membership_prompt_threshold_pct": 80}, headers=H)
    assert (await client.get(f"/api/memberships/order/{oid}", headers=H)).json()["offer"] is None
    await client.put("/api/memberships/settings/shop", json={"membership_prompt_threshold_pct": 25}, headers=H)
    assert (await client.post(f"/api/memberships/order/{oid}/snooze", headers=H)).status_code == 200
    assert (await client.get(f"/api/memberships/order/{oid}", headers=H)).json()["offer"] is None
    cid2, boat2 = await _client_and_boat(client, H, name="Kim", phone="5550111111")
    oid2 = await _order(client, H, cid2, boat2, lines=[{"kind": "service", "ref_id": haul, "description": "Haul-out",
                                                        "qty": 1, "unit_price": 300}])
    await client.put("/api/memberships/settings/shop", json={"membership_checkout_prompt": False}, headers=H)
    assert (await client.get(f"/api/memberships/order/{oid2}", headers=H)).json()["offer"] is None


@pytest.mark.asyncio
async def test_joining_at_checkout_adds_the_plan_to_the_job_and_covers_it(client):
    H, _ = await _register(client)
    haul = await _service(client, H, "Haul-out", 300)
    plan = await _plan(client, H, haul, items=[{"kind": "service", "service_id": haul, "qty": 1}])
    cid, boat = await _client_and_boat(client, H)
    oid = await _order(client, H, cid, boat, lines=[{"kind": "service", "ref_id": haul, "description": "Haul-out",
                                                     "qty": 1, "unit_price": 300}])
    m = await _sell(client, H, plan["id"], cid, boat, order_id=oid)
    assert m["invoice_id"] is None
    o = (await client.get(f"/api/orders/{oid}", headers=H)).json()
    kinds = {x["kind"]: x for x in o["lines"]}
    assert kinds["membership"]["unit_price"] == 449
    assert kinds["service"]["discount"] == 100
    assert o["total"] == 449


@pytest.mark.asyncio
async def test_cancel_now_suggests_a_refund_and_transfer_moves_the_plan(client):
    H, _ = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    plan = await _plan(client, H, oil)
    cid, boat = await _client_and_boat(client, H)
    m = await _sell(client, H, plan["id"], cid, boat)
    acc = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=H)).json()["id"]
    await client.post(f"/api/finance/invoices/{m['invoice_id']}/pay", headers=H,
                      json={"amount": 449, "method": "cash", "account_id": acc})
    # Sold the boat: the plan goes with it to the new owner.
    buyer = (await client.post("/api/clients", json={"name": "New Owner"}, headers=H)).json()["id"]
    r = await client.put(f"/api/assets/{boat}", json={"owner_type": "client", "client_id": buyer}, headers=H)
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/memberships/{m['id']}/transfer", json={"device_id": boat, "client_id": buyer},
                          headers=H)
    assert r.status_code == 200, r.text
    assert await _tags(client, H, cid) == [] and "Boat Care member" in await _tags(client, H, buyer)
    r = await client.post(f"/api/memberships/{m['id']}/cancel", json={"when": "now"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert 400 < r.json()["refund_suggested"] <= 449
    assert await _tags(client, H, buyer) == ["Former Boat Care member"]


@pytest.mark.asyncio
async def test_each_membership_permission_is_enforced(client):
    H, _ = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    plan = await _plan(client, H, oil)
    cid, boat = await _client_and_boat(client, H)
    m = await _sell(client, H, plan["id"], cid, boat)
    T = await _teammate(client, H, "can_sell_memberships", "can_change_memberships",
                        "can_manage_membership_plans", "can_see_profit")
    cid2, boat2 = await _client_and_boat(client, H, name="Ann", phone="5550122222")
    assert (await client.post("/api/memberships", json={"plan_id": plan["id"], "client_id": cid2,
                                                        "device_id": boat2}, headers=T)).status_code == 403
    assert (await client.post(f"/api/memberships/{m['id']}/cancel", json={}, headers=T)).status_code == 403
    assert (await client.post("/api/memberships/plans", json={"name": "X", "price_yearly": 1},
                              headers=T)).status_code == 403
    assert (await client.put("/api/memberships/settings/shop", json={}, headers=T)).status_code == 403
    assert (await client.get("/api/memberships/reports/profitability", headers=T)).status_code == 403
    rep = (await client.get("/api/memberships/reports/profitability", headers=H)).json()
    assert rep["plans"][0]["members"] == 1


@pytest.mark.asyncio
async def test_memberships_are_shop_only(client):
    A, _ = await _register(client)
    B, _ = await _register(client)
    oil = await _service(client, A, "Oil change", 80)
    plan = await _plan(client, A, oil)
    cid, boat = await _client_and_boat(client, A)
    m = await _sell(client, A, plan["id"], cid, boat)
    assert (await client.get(f"/api/memberships/{m['id']}", headers=B)).status_code == 404
    assert (await client.get("/api/memberships", headers=B)).json()["items"] == []
    assert (await client.get("/api/memberships/plans", headers=B)).json()["items"] == []
    cidB, boatB = await _client_and_boat(client, B)
    r = await client.post("/api/memberships", json={"plan_id": plan["id"], "client_id": cidB,
                                                    "device_id": boatB}, headers=B)
    assert r.status_code == 404
    r = await client.post("/api/memberships", json={"plan_id": plan["id"], "client_id": cidB,
                                                    "device_id": boat}, headers=A)
    assert r.status_code == 404


def test_existing_roles_keep_the_access_they_would_expect():
    perms, changed = grant_membership_codes({"can_create_tickets": True, "can_manage_settings": False}, True)
    assert changed and perms["can_sell_memberships"] and perms["can_apply_membership_benefits"]
    assert not perms["can_change_memberships"] and not perms["can_manage_membership_plans"]
    same, changed = grant_membership_codes({"all": "write"}, True)
    assert not changed
    _, changed = grant_membership_codes({"can_create_tickets": True}, False)
    assert not changed                                   # legacy role that denies nothing: unchanged
    legacy, changed = grant_membership_codes({"can_manage_settings": False}, False)
    assert changed and legacy["can_manage_membership_plans"] is False and legacy["can_change_memberships"] is False
    assert "can_sell_memberships" not in legacy


@pytest.mark.asyncio
async def test_list_totals_plan_worth_attention_and_welcome_text(client, db):
    H, org_id = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    plan = await _plan(client, H, oil)
    assert plan["value_yearly"] == 160                                    # 2 oil changes at 80
    cid, boat = await _client_and_boat(client, H)
    await _sell(client, H, plan["id"], cid, boat)                        # yearly 449, unpaid today
    cid2, boat2 = await _client_and_boat(client, H, name="Lee", phone="5550100001")
    await _sell(client, H, plan["id"], cid2, boat2, billing="monthly")   # 45 a month
    r = (await client.get("/api/memberships", headers=H)).json()
    assert r["totals"]["mrr"] == round(449 / 12 + 45, 2)
    assert r["totals"]["owed"] == 449 + 45
    assert all(i["attention"]["text"] == "Payment due today" for i in r["items"])
    from backend.models import NotificationLog
    sent = (await db.execute(select(NotificationLog).where(NotificationLog.client_id == cid))).scalars().all()
    assert any(s.subject == "Welcome to Boat Care" and "Oil change × 2" in s.body for s in sent)
    # Staff without client money access see counts, not money.
    T = await _teammate(client, H, "can_view_client_info")
    r = (await client.get("/api/memberships", headers=T)).json()
    assert r["totals"] is None and "owed" not in r["items"][0]


@pytest.mark.asyncio
async def test_security_review_regressions(client):
    """Fixes from the security review: counts survive plan edits, the plan line can't be removed,
    coverage needs the member's own client, cancelling after use keeps the bill, money stays hidden."""
    H, _ = await _register(client)
    oil = await _service(client, H, "Oil change", 80)
    plan = await _plan(client, H, oil, items=[{"kind": "service", "service_id": oil, "qty": 1}])
    cid, boat = await _client_and_boat(client, H)
    m = await _sell(client, H, plan["id"], cid, boat)
    oid = await _order(client, H, cid, boat)
    await _add(client, H, oid, kind="service", ref_id=oil, description="Oil change", unit_price=80)
    await _close(client, H, oid)
    # Editing (re-saving) the plan doesn't hand the member their oil change back.
    body = {k: v for k, v in plan.items() if k not in ("id", "members", "value_yearly")}
    body["name"] = "Boat Care+"
    body["items"] = [{"kind": "service", "service_id": oil, "qty": 1}]
    assert (await client.put(f"/api/memberships/plans/{plan['id']}", json=body, headers=H)).status_code == 200
    card = (await client.get(f"/api/memberships/for?device_id={boat}", headers=H)).json()
    assert card["live"][0]["items"][0]["left"] == 0
    # Someone else's job with this boat on it isn't covered by Sam's plan.
    other = (await client.post("/api/clients", json={"name": "Mallory"}, headers=H)).json()["id"]
    o2 = await _order(client, H, other, boat)
    line = await _add(client, H, o2, kind="service", ref_id=oil, description="Oil change", unit_price=80)
    assert line["discount"] == 0 and not line["membership_id"]
    # The plan's own line on a job can't be deleted or edited.
    cid2, boat2 = await _client_and_boat(client, H, name="Ann", phone="5550122222")
    o3 = await _order(client, H, cid2, boat2)
    await _sell(client, H, plan["id"], cid2, boat2, order_id=o3)
    fee = next(x for x in (await client.get(f"/api/orders/{o3}", headers=H)).json()["lines"] if x["kind"] == "membership")
    assert (await client.delete(f"/api/orders/{o3}/lines/{fee['id']}", headers=H)).status_code == 409
    assert (await client.patch(f"/api/orders/{o3}/lines/{fee['id']}", json={"unit_price": 0}, headers=H)).status_code == 409
    # Used the plan, then cancelled today: the unpaid plan invoice stays open.
    r = await client.post(f"/api/memberships/{m['id']}/cancel", json={"when": "now"}, headers=H)
    assert r.status_code == 200
    inv = (await client.get(f"/api/finance/invoices/{m['invoice_id']}", headers=H)).json()
    assert inv["status"] != "cancelled"
    # Staff without client money access don't get prices back from changes.
    T = await _teammate(client, H, "can_view_client_info")
    r = (await client.get(f"/api/memberships/{m['id']}", headers=T)).json()
    assert "price" not in r and "periods" not in r
