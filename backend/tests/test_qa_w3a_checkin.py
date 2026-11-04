"""QA wave 3, W3-A check-in (5-shop re-run on Postgres, 1 Nov 2026).

1. Two or three staff pressing Convert at once made 2–3 jobs, clients and devices.
2. The 5-phones-per-IP limit was shared by every shop, blocked kiosk points after 5 walk-ins and
   counted failed attempts.
3. A customer's double tap on the public form queued them twice.
4. Re-submitting a check-in (with its status token) replaced the issue they first wrote.
5. A check-in the customer cancelled could still be marked no-show.

The races hold both requests at an asyncio.Barrier after they have read the check-in, so each
one sees the state before the other writes; that's what the shops hit on Postgres.
"""
import asyncio

import pytest
from sqlalchemy import func, select

from backend.tests.test_self_checkin import _body, _register, _setup


def _hold_after(monkeypatch, module, name, parties):
    """Make `module.name` wait for `parties` callers before returning."""
    barrier = asyncio.Barrier(parties)
    orig = getattr(module, name)

    async def held(*a, **kw):
        out = await orig(*a, **kw)
        await asyncio.wait_for(barrier.wait(), 10)
        return out
    monkeypatch.setattr(module, name, held)


@pytest.mark.asyncio
@pytest.mark.parametrize("parties", [2, 3])
async def test_concurrent_converts_make_one_job_client_and_device(client, tenant, db, monkeypatch, parties):
    from backend.models import Client, Device, Order
    from backend.routers import checkins as staff
    s = await _setup(client, tenant)
    assert (await client.post(f"/api/public/checkin/{s['token']}", json=_body(s))).status_code == 201
    cid = (await client.get("/api/checkins", headers=tenant)).json()["waiting"][0]["id"]
    _hold_after(monkeypatch, staff, "_own", parties)
    rs = await asyncio.gather(*[client.post(f"/api/checkins/{cid}/convert", headers=tenant)
                                for _ in range(parties)])
    assert all(r.status_code == 200 for r in rs), [r.text for r in rs]
    assert len({r.json()["order_id"] for r in rs}) == 1
    assert sum(1 for r in rs if r.json().get("already")) == parties - 1
    oid = rs[0].json()["order_id"]
    org = (await db.execute(select(Order.org_id).where(Order.id == oid))).scalar_one()
    assert (await db.execute(select(func.count()).select_from(Order).where(Order.org_id == org))).scalar() == 1
    assert (await db.execute(select(func.count()).select_from(Client).where(Client.org_id == org))).scalar() == 1
    assert (await db.execute(select(func.count()).select_from(Device).where(Device.org_id == org))).scalar() == 1
    monkeypatch.undo()
    again = await client.post(f"/api/checkins/{cid}/convert", headers=tenant)
    assert again.json()["order_id"] == oid and again.json()["already"] is True


@pytest.mark.asyncio
async def test_phone_limit_is_per_point_not_shared_by_every_shop(client, tenant):
    a = await _setup(client, tenant)
    url = f"/api/public/checkin/{a['token']}"
    codes = [(await client.post(url, json=_body(a, phone=f"+1 555 010 00{i:02d}"))).status_code for i in range(5)]
    assert codes == [201] * 5
    # Another shop's point on the same Wi-Fi / IP is not blocked by the first shop's walk-ins
    other = await _register(client)
    b = await _setup(client, other)
    r = await client.post(f"/api/public/checkin/{b['token']}", json=_body(b, phone="+1 555 010 0099"))
    assert r.status_code == 201, r.text
    # …and the first point still stops a sixth number from that IP (abuse protection holds)
    assert (await client.post(url, json=_body(a, phone="+1 555 010 0098"))).status_code == 429


@pytest.mark.asyncio
async def test_kiosk_point_takes_a_whole_day_of_walk_ins_from_one_device(client, tenant):
    s = await _setup(client, tenant)
    p = s["point"]
    r = await client.put(f"/api/checkins/points/{p['id']}", json={**p, "kiosk": True}, headers=tenant)
    assert r.status_code == 200, r.text
    url = f"/api/public/checkin/{s['token']}"
    codes = [(await client.post(url, json=_body(s, phone=f"+1 555 020 00{i:02d}"))).status_code for i in range(12)]
    assert codes == [201] * 12
    # one number still can't be checked in over and over
    codes = [(await client.post(url, json=_body(s, phone="+1 555 020 0000", issue=f"Also {i}"))).status_code
             for i in range(3)]
    assert codes == [201, 201, 429]


@pytest.mark.asyncio
async def test_failed_attempts_do_not_use_up_the_limits(client, tenant):
    s = await _setup(client, tenant)
    url = f"/api/public/checkin/{s['token']}"
    for _ in range(8):   # a customer fumbling the form: no signature, then no issue
        assert (await client.post(url, json=_body(s, agree=False))).status_code == 422
        assert (await client.post(url, json=_body(s, issue=" "))).status_code == 422
    r = await client.post(url, json=_body(s))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_a_double_tap_on_the_public_form_queues_once(client, tenant, monkeypatch):
    from backend.services import checkin as svc
    s = await _setup(client, tenant)
    url = f"/api/public/checkin/{s['token']}"
    _hold_after(monkeypatch, svc, "resolve_form", 2)
    rs = await asyncio.gather(*[client.post(url, json=_body(s)) for _ in range(2)])
    assert all(r.status_code == 201 for r in rs), [r.text for r in rs]
    assert len({r.json()["status_token"] for r in rs}) == 1
    assert len({r.json()["queue_no"] for r in rs}) == 1
    monkeypatch.undo()
    assert len((await client.get("/api/checkins", headers=tenant)).json()["waiting"]) == 1


@pytest.mark.asyncio
async def test_a_tap_with_a_different_signature_is_not_taken_for_someone_elses(client, tenant):
    """The double-tap match needs the exact same form, signature included, so knowing a phone
    number never hands out another person's status token."""
    s = await _setup(client, tenant)
    url = f"/api/public/checkin/{s['token']}"
    a = (await client.post(url, json=_body(s))).json()
    other_sig = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAEklEQVR42mNk+M9Qz0AEYBxVSAQAdMoD/ab5pckAAAAASUVORK5CYII="
    b = (await client.post(url, json=_body(s, signature=other_sig))).json()
    assert b["status_token"] != a["status_token"] and b["queue_no"] != a["queue_no"]


@pytest.mark.asyncio
async def test_resubmitting_keeps_the_original_issue(client, tenant):
    s = await _setup(client, tenant)
    url = f"/api/public/checkin/{s['token']}"
    a = (await client.post(url, json=_body(s, issue="Won't power on"))).json()
    r = await client.post(url, json=_body(s, issue="Hinge is loose", status_token=a["status_token"]))
    assert r.status_code == 201 and r.json()["updated"] is True
    issue = (await client.get(f"/api/public/checkin/s/{a['status_token']}")).json()["summary"]["issue"]
    assert "Won't power on" in issue and "Hinge is loose" in issue
    # sending the same text again doesn't repeat it
    await client.post(url, json=_body(s, issue="Hinge is loose", status_token=a["status_token"]))
    issue = (await client.get(f"/api/public/checkin/s/{a['status_token']}")).json()["summary"]["issue"]
    assert issue.count("Hinge is loose") == 1
    cid = (await client.get("/api/checkins", headers=tenant)).json()["waiting"][0]["id"]
    oid = (await client.post(f"/api/checkins/{cid}/convert", headers=tenant)).json()["order_id"]
    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert "Won't power on" in o["complaint"] and "Hinge is loose" in o["complaint"]


@pytest.mark.asyncio
async def test_a_cancelled_check_in_cannot_be_marked_no_show(client, tenant):
    s = await _setup(client, tenant)
    a = (await client.post(f"/api/public/checkin/{s['token']}", json=_body(s))).json()
    cid = (await client.get("/api/checkins", headers=tenant)).json()["waiting"][0]["id"]
    assert (await client.post(f"/api/public/checkin/s/{a['status_token']}/cancel")).json()["state"] == "cancelled"
    r = await client.post(f"/api/checkins/{cid}/no-show", headers=tenant)
    assert r.status_code == 409, r.text
    assert (await client.get(f"/api/public/checkin/s/{a['status_token']}")).json()["state"] == "cancelled"
    # a no-show is final too: marking it twice is a 409, not a second feed entry
    b = (await client.post(f"/api/public/checkin/{s['token']}", json=_body(s, phone="+1 555 333 4444"))).json()
    cid2 = next(i["id"] for i in (await client.get("/api/checkins", headers=tenant)).json()["waiting"])
    assert (await client.post(f"/api/checkins/{cid2}/no-show", headers=tenant)).status_code == 200
    assert (await client.post(f"/api/checkins/{cid2}/no-show", headers=tenant)).status_code == 409
    assert (await client.get(f"/api/public/checkin/s/{b['status_token']}")).json()["state"] == "no_show"
