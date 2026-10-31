"""Customer messaging rule (services/messaging_rule).

144cv cut messaging the moment an order closed. Now a closed order stays messageable for
``oe_msg_days_after_close`` days (default 14) so warranty questions reach the shop; each status
can force messaging on or off; when it's closed the track page offers "Start a new request",
which files an inquiry linked to the customer. Reopening an order restores messaging."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.services.dates import now_utc


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _order_with_token(client, h):
    loc = await _loc(client, h)
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+15550101"}, headers=h)).json()["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                            headers=h)).json()
    token = (await client.get(f"/api/orders/{o['id']}", headers=h)).json()["public_token"]
    o["client_id"] = cid
    return o, token


async def _msg(client, token, body="hi"):
    return await client.post(f"/api/public/track/{token}/message", json={"body": body})


async def _set_days(client, h, days):
    r = await client.put("/api/staff/public-pages", json={"oe_msg_days_after_close": days}, headers=h)
    assert r.status_code == 200, r.text


async def _status(client, h, key):
    items = (await client.get("/api/statuses", headers=h)).json()["items"]
    return next(s for s in items if s["key"] == key)


async def _put_status(client, h, st, **changes):
    body = {k: st[k] for k in ("name", "status_group", "color", "is_default", "triggers_invoice",
                               "triggers_notification", "triggers_payment", "locks_edits",
                               "time_limit_min", "moves_asset_to", "converts_to_order", "shows_accept",
                               "require_comment", "require_payment", "require_checklist", "record_type")}
    body.update(changes)
    r = await client.put(f"/api/statuses/{st['id']}", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_closed_order_stays_open_for_grace_window(client, tenant):
    o, token = await _order_with_token(client, tenant)
    assert (await _msg(client, token)).status_code == 200
    assert (await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=tenant)).status_code == 200

    page = (await client.get(f"/api/public/track/{token}")).json()
    assert page["allow_messages"] is True and page["messages_until"]
    assert page["can_new_request"] is False
    assert (await _msg(client, token, "screen flickers again")).status_code == 200


@pytest.mark.asyncio
async def test_zero_days_closes_on_close_and_reopen_restores(client, tenant):
    await _set_days(client, tenant, 0)
    assert (await client.get("/api/staff/public-pages", headers=tenant)).json()["oe_msg_days_after_close"] == 0
    o, token = await _order_with_token(client, tenant)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=tenant)

    assert (await _msg(client, token, "still there?")).status_code == 403
    page = (await client.get(f"/api/public/track/{token}")).json()
    assert page["allow_messages"] is False and page["can_new_request"] is True

    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "in_repair"}, headers=tenant)
    assert (await _msg(client, token, "one more")).status_code == 200


@pytest.mark.asyncio
async def test_window_expires(client, tenant, db):
    from backend.models import Order
    o, token = await _order_with_token(client, tenant)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=tenant)
    row = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one()
    row.closed_at = now_utc() - timedelta(days=15)
    await db.commit()
    assert (await _msg(client, token)).status_code == 403
    assert (await client.get(f"/api/public/track/{token}")).json()["can_new_request"] is True


@pytest.mark.asyncio
async def test_status_can_force_messaging_off_and_on(client, tenant):
    o, token = await _order_with_token(client, tenant)
    waiting = await _status(client, tenant, "waiting_parts")
    assert waiting["allow_messages"] is None                       # automatic by default
    assert (await _put_status(client, tenant, waiting, allow_messages=False))["allow_messages"] is False
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "waiting_parts"}, headers=tenant)
    assert (await _msg(client, token)).status_code == 403

    # a PUT that doesn't mention allow_messages (older client) keeps the choice
    waiting = await _status(client, tenant, "waiting_parts")
    body = {k: v for k, v in waiting.items() if k not in ("allow_messages",)}
    await client.put(f"/api/statuses/{waiting['id']}", json=body, headers=tenant)
    assert (await _status(client, tenant, "waiting_parts"))["allow_messages"] is False

    await _set_days(client, tenant, 0)
    delivered = await _status(client, tenant, "delivered")
    await _put_status(client, tenant, delivered, allow_messages=True)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "delivered"}, headers=tenant)
    assert (await _msg(client, token)).status_code == 200          # forced on despite closed + 0 days


@pytest.mark.asyncio
async def test_shop_switch_off_wins_and_hides_new_request(client, tenant):
    o, token = await _order_with_token(client, tenant)
    await client.put("/api/staff/public-pages", json={"oe_allow_messages": False}, headers=tenant)
    r = await _msg(client, token)
    assert r.status_code == 403 and "turned off" in r.json()["detail"]
    page = (await client.get(f"/api/public/track/{token}")).json()
    assert page["allow_messages"] is False and page["can_new_request"] is False
    assert (await client.post(f"/api/public/track/{token}/new-request", json={"body": "x"})).status_code == 409


@pytest.mark.asyncio
async def test_new_request_files_linked_inquiry(client, tenant, db):
    from backend.models import Lead
    await _set_days(client, tenant, 0)
    o, token = await _order_with_token(client, tenant)
    # open order: use the message box, not a new request
    assert (await client.post(f"/api/public/track/{token}/new-request", json={"body": "x"})).status_code == 409
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=tenant)
    assert (await client.post(f"/api/public/track/{token}/new-request", json={"body": " "})).status_code == 400
    r = await client.post(f"/api/public/track/{token}/new-request", json={"body": "Battery drains fast"})
    assert r.status_code == 201, r.text

    lead = (await db.execute(select(Lead).where(Lead.inquiry.contains("Battery drains fast")))).scalar_one()
    assert lead.client_id == o["client_id"] and lead.name == "C" and lead.phone == "+15550101"
    assert "Follow-up to" in lead.inquiry and lead.status == "new"


@pytest.mark.asyncio
async def test_portal_detail_follows_same_rule(client, tenant):
    await _set_days(client, tenant, 0)
    o, _ = await _order_with_token(client, tenant)
    await client.patch(f"/api/orders/{o['id']}/status", json={"status": "done"}, headers=tenant)
    link = await client.post(f"/api/clients/{o['client_id']}/portal-link", headers=tenant)
    assert link.status_code == 200, link.text
    ptoken = link.json()["portal_token"]
    card = (await client.get(f"/api/public/portal/{ptoken}/order/{o['id']}")).json()
    assert card["allow_messages"] is False and card["can_new_request"] is True
    r = await client.post(f"/api/public/portal/{ptoken}/order/{o['id']}/message", json={"body": "hi"})
    assert r.status_code == 403
