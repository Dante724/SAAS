"""The customer chooses recommended work: only what they tick is added, "not now" saves an
item for later (with a reminder date), a saved item can still be added, a double tap adds
the work once, a quoted price is never wiped, and urgency is set by staff."""
import uuid

import pytest
from sqlalchemy import select

from backend.models import OrderLine


async def _tenant(client):
    r = await client.post("/api/auth/register", json={
        "org_name": "Rec Co", "name": "Admin", "email": f"a-{uuid.uuid4().hex[:8]}@test.dev", "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _order(client, H):
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Ravi"}, headers=H)).json()["id"]
    body = {"client_id": cid, "location_id": loc, "complaint": "screen"}
    return (await client.post("/api/orders", json=body, headers=H)).json()


async def _rec(client, H, oid, title, price, urgency=None):
    r = await client.post(f"/api/orders/{oid}/recommendations", json={
        "title": title, "unit_price": price, "qty": 1, "kind": "service", "urgency": urgency}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


async def _publish(client, H, oid):
    await client.post(f"/api/orders/{oid}/recommendations/publish-all", json={}, headers=H)


async def _track(client, o):
    return (await client.get(f"/api/public/track/{o['public_token']}")).json()


def _decide(client, o, **body):
    return client.post(f"/api/public/track/{o['public_token']}/recommendations/decide", json=body)


@pytest.mark.asyncio
async def test_only_ticked_items_are_added_and_the_rest_stay_open(client):
    H = await _tenant(client)
    o = await _order(client, H)
    a = await _rec(client, H, o["id"], "Brake pads", 80, "now")
    await _rec(client, H, o["id"], "Wiper blades", 20, "soon")
    await _publish(client, H, o["id"])
    recs = {x["title"]: x for x in (await _track(client, o))["recommendations"]}
    assert recs["Brake pads"]["urgency"] == "now" and recs["Wiper blades"]["urgency"] == "soon"
    r = await _decide(client, o, approve_ids=[a["id"]], name="Ravi Kumar")
    assert r.status_code == 200 and r.json()["approved"] == 1
    recs = {x["title"]: x for x in (await _track(client, o))["recommendations"]}
    assert recs["Brake pads"]["status"] == "approved" and recs["Brake pads"]["decided_by_name"] == "Ravi Kumar"
    assert recs["Wiper blades"]["status"] == "sent"          # not silently declined


@pytest.mark.asyncio
async def test_not_now_saves_for_later_and_can_still_be_added(client):
    H = await _tenant(client)
    o = await _order(client, H)
    a = await _rec(client, H, o["id"], "Coolant flush", 45)
    await _publish(client, H, o["id"])
    r = await _decide(client, o, later_ids=[a["id"]])
    assert r.json()["saved_for_later"] == 1
    staff = (await client.get(f"/api/orders/{o['id']}/recommendations", headers=H)).json()
    row = (staff["items"] if isinstance(staff, dict) else staff)[0]
    assert row["status"] == "declined" and row["decline_reason"] == "not_now" and row["remind_after"]
    # changed their mind
    r = await _decide(client, o, approve_ids=[a["id"]])
    assert r.json()["approved"] == 1
    t = await _track(client, o)
    assert t["recommendations"][0]["status"] == "approved" and t["recommendations"][0]["decline_reason"] is None


@pytest.mark.asyncio
async def test_double_tap_adds_the_work_once(client, db):
    H = await _tenant(client)
    o = await _order(client, H)
    a = await _rec(client, H, o["id"], "Battery", 120)
    await _publish(client, H, o["id"])
    first = await _decide(client, o, approve_ids=[a["id"]])
    second = await _decide(client, o, approve_ids=[a["id"]])
    assert first.json()["approved"] == 1 and second.json()["approved"] == 0 and second.json()["skipped"] == [a["id"]]
    db.expire_all()
    lines = (await db.execute(select(OrderLine).where(OrderLine.order_id == o["id"]))).scalars().all()
    assert [ln.description for ln in lines] == ["Battery"]


@pytest.mark.asyncio
async def test_same_item_cannot_be_added_and_saved(client):
    H = await _tenant(client)
    o = await _order(client, H)
    a = await _rec(client, H, o["id"], "Battery", 120)
    await _publish(client, H, o["id"])
    assert (await _decide(client, o, approve_ids=[a["id"]], later_ids=[a["id"]])).status_code == 400


@pytest.mark.asyncio
async def test_adding_extras_keeps_a_quoted_price(client, db):
    from backend.models import Order
    H = await _tenant(client)
    o = await _order(client, H)
    row = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    row.total = 1800                  # priced at check-in, no lines yet
    await db.commit()
    a = await _rec(client, H, o["id"], "Screen protector", 50)
    await _publish(client, H, o["id"])
    r = await _decide(client, o, approve_ids=[a["id"]])
    assert r.json()["total"] == pytest.approx(1850)


@pytest.mark.asyncio
async def test_staff_urgency_is_validated(client):
    H = await _tenant(client)
    o = await _order(client, H)
    r = await client.post(f"/api/orders/{o['id']}/recommendations", json={
        "title": "X", "unit_price": 1, "qty": 1, "kind": "service", "urgency": "panic"}, headers=H)
    assert r.status_code == 422
