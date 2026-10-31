"""WS-C — inquiries everywhere: missed call → inquiry (Twilio + Aircall), richer inquiries,
the idempotent create Tekly Go uses, and the inquiry settings."""
import pytest
from sqlalchemy import select

from backend.config import settings
from backend.models import Call, Client, Device, Employee, EventFeedItem, Lead, Order, Shift, StaffNotification, Task
from backend.services.dates import now_utc
from backend.tests.test_opd_telephony import (  # noqa: F401 — fake_twilio is a fixture
    connect,
    fake_twilio,
    inbound,
    limited,
    org_of,
    post_signed,
    second_tenant,
)


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _miss(client, org, sid, frm="+14155550100"):
    """A call to the shop's Twilio number with no forwarding number: missed from the start."""
    return await post_signed(client, f"/api/comms/twilio/voice?org_id={org}", inbound(sid=sid, frm=frm))


async def _clock_in(db, emp_id, org):
    from backend.models import Location
    loc = (await db.execute(select(Location).where(Location.org_id == org))).scalars().first()
    db.add(Shift(employee_id=emp_id, location_id=loc.id))
    await db.commit()


# ── P0: missed call → inquiry ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_missed_twilio_call_opens_inquiry_matches_contact_and_creates_task(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Dana Reyes", "phone": "(415) 555-0100"},
                             headers=tenant)).json()["id"]
    r = await _miss(client, org, "CA" + "1" * 32)
    assert r.status_code == 200
    lead = (await db.execute(select(Lead).where(Lead.org_id == org))).scalar_one()
    assert lead.client_id == cid and lead.name == "Dana Reyes" and lead.source == "phone"
    assert lead.call_count == 1 and lead.status == "new" and lead.sla_due_at is not None
    task = (await db.execute(select(Task).where(Task.org_id == org))).scalar_one()
    assert task.lead_id == lead.id and task.client_id == cid and task.title == "Call back Dana Reyes"
    feed = (await db.execute(select(EventFeedItem).where(EventFeedItem.entity_type == "lead",
                                                         EventFeedItem.entity_id == lead.id))).scalars().all()
    assert any("missed call" in (f.body or "").lower() for f in feed)


@pytest.mark.asyncio
async def test_repeat_caller_merges_into_one_inquiry_and_one_task(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    await client.put("/api/comms/automations/task_on_missed_call", json={"enabled": True}, headers=tenant)
    for i in range(3):
        await _miss(client, org, "CA" + str(i) * 32, frm=("+1 415 555 0100" if i else "+14155550100"))
    leads = (await db.execute(select(Lead).where(Lead.org_id == org))).scalars().all()
    assert len(leads) == 1 and leads[0].call_count == 3
    # the old task-only automation doesn't add a second task on top
    assert len((await db.execute(select(Task).where(Task.org_id == org))).scalars().all()) == 1
    # a different caller is a different inquiry
    await _miss(client, org, "CA" + "9" * 32, frm="+14155550199")
    assert len((await db.execute(select(Lead).where(Lead.org_id == org))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_call_after_merge_window_or_closed_inquiry_opens_a_new_one(client, tenant, fake_twilio, db):
    from datetime import datetime, timedelta
    await connect(client, tenant)
    org = await org_of(client, tenant)
    await _miss(client, org, "CA" + "1" * 32)
    lead = (await db.execute(select(Lead).where(Lead.org_id == org))).scalar_one()
    lead.last_call_at = now_utc() - timedelta(hours=25)
    lead.created_at = now_utc() - timedelta(hours=25)
    await db.commit()
    await _miss(client, org, "CA" + "2" * 32)
    assert len((await db.execute(select(Lead).where(Lead.org_id == org))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_on_duty_staff_are_alerted_and_get_the_task(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    tech_h = await limited(client, tenant, "duty@test.dev", deny=())
    tech = await _me(client, tech_h)
    await _clock_in(db, tech["id"], org)
    await _miss(client, org, "CA" + "1" * 32)
    task = (await db.execute(select(Task).where(Task.org_id == org))).scalar_one()
    assert task.assigned_to_id == tech["id"]
    notes = (await db.execute(select(StaffNotification).where(StaffNotification.org_id == org))).scalars().all()
    assert [n.employee_id for n in notes] == [tech["id"]]
    # the title is generic; the number only goes in the body
    assert "555" not in notes[0].title and "0100" in notes[0].body


@pytest.mark.asyncio
async def test_nobody_on_duty_alerts_the_admins(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    admin = await _me(client, tenant)
    await _miss(client, org, "CA" + "1" * 32)
    notes = (await db.execute(select(StaffNotification).where(StaffNotification.org_id == org))).scalars().all()
    assert [n.employee_id for n in notes] == [admin["id"]]


@pytest.mark.asyncio
async def test_missed_call_automation_can_be_turned_off(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    r = await client.put("/api/leads/missed-call-settings", json={"enabled": False}, headers=tenant)
    assert r.status_code == 200 and r.json()["enabled"] is False
    await _miss(client, org, "CA" + "1" * 32)
    assert (await db.execute(select(Lead).where(Lead.org_id == org))).scalars().all() == []


@pytest.mark.asyncio
async def test_missed_call_settings_reject_another_shops_employee(client, tenant):
    other = await second_tenant(client)
    other_emp = await _me(client, other)
    r = await client.put("/api/leads/missed-call-settings",
                         json={"enabled": True, "fallback_employee_id": other_emp["id"]}, headers=tenant)
    assert r.status_code == 404
    staff = await limited(client, tenant, "nosettings@test.dev")
    assert (await client.put("/api/leads/missed-call-settings", json={"enabled": False},
                             headers=staff)).status_code == 403


@pytest.mark.asyncio
async def test_task_automation_assign_to_must_be_this_shop(client, tenant):
    other = await second_tenant(client)
    r = await client.put("/api/comms/automations/task_on_missed_call",
                         json={"enabled": True, "config": {"assign_to": (await _me(client, other))["id"]}},
                         headers=tenant)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unsigned_or_other_shop_missed_call_creates_nothing(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    r = await client.post(f"/api/comms/twilio/voice?org_id={org}", data=inbound())
    assert r.status_code == 403
    r = await post_signed(client, f"/api/comms/twilio/voice?org_id={org}", inbound(), token="wrong")
    assert r.status_code == 403
    assert (await db.execute(select(Lead))).scalars().all() == []


@pytest.mark.asyncio
async def test_anonymous_caller_is_logged_but_opens_no_inquiry(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    await _miss(client, org, "CA" + "1" * 32, frm="anonymous")
    assert (await db.execute(select(Call).where(Call.org_id == org))).scalar_one().status == "missed"
    assert (await db.execute(select(Lead))).scalars().all() == []


@pytest.mark.asyncio
async def test_simulated_missed_call_opens_inquiry(client, tenant, db):
    org = await org_of(client, tenant)
    r = await client.post("/api/telephony/simulate/inbound", json={"from_number": "+14155550100", "missed": True},
                          headers=tenant)
    assert r.status_code == 201 and r.json()["lead_id"]
    lead = (await db.execute(select(Lead).where(Lead.org_id == org))).scalar_one()
    assert lead.id == r.json()["lead_id"] and lead.call_count == 1


@pytest.mark.asyncio
async def test_generic_call_webhook_needs_token_and_dedupes_replays(client, tenant, db):
    from backend.models import Organisation
    org = await org_of(client, tenant)
    ev = {"provider": "pbx", "from_number": "+14155550100", "status": "no-answer", "external_id": "evt-1"}
    o = await db.get(Organisation, org)
    o.webhook_secret = tok = "whsec-test-0123456789"
    await db.commit()
    assert (await client.post(f"/api/comms/calls/webhook?org_id={org}", json=ev)).status_code == 401
    for _ in range(3):
        r = await client.post(f"/api/comms/calls/webhook?org_id={org}", json=ev, headers={"X-Webhook-Token": tok})
        assert r.status_code == 201, r.text
    assert len((await db.execute(select(Call).where(Call.org_id == org))).scalars().all()) == 1
    assert (await db.execute(select(Lead).where(Lead.org_id == org))).scalar_one().call_count == 1


@pytest.mark.asyncio
async def test_burst_of_repeat_calls_alerts_once(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    for i in range(3):
        await _miss(client, org, "CA" + str(i) * 32)
    notes = (await db.execute(select(StaffNotification).where(StaffNotification.org_id == org))).scalars().all()
    assert len(notes) == 1


# ── P0: richer inquiries on the web ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_inquiry_type_sets_respond_by_and_device_fields_saved(client, tenant):
    t = (await client.post("/api/leads/types", json={"name": "Repair", "sla_minutes": 15}, headers=tenant)).json()
    r = await client.post("/api/leads", json={
        "name": "Sam", "phone": "+14155550111", "lead_type_id": t["id"], "device_brand": "Apple",
        "device_model": "iPhone 14", "serial": "356938035643809", "inquiry": "Cracked screen"}, headers=tenant)
    assert r.status_code == 201, r.text
    lead = r.json()
    assert lead["lead_type"] == "Repair" and lead["device_model"] == "iPhone 14"
    from datetime import datetime
    mins = (datetime.fromisoformat(lead["sla_due_at"]) - datetime.fromisoformat(lead["created_at"])).total_seconds() / 60
    assert 14 <= mins <= 16


@pytest.mark.asyncio
async def test_inquiry_custom_fields_required_rule(client, tenant):
    r = await client.post("/api/forms/entity-fields", json={
        "entity_type": "lead", "field_label": "Budget", "field_type": "number", "required": True}, headers=tenant)
    assert r.status_code == 201, r.text
    key = r.json()["field_key"]
    assert (await client.post("/api/leads", json={"name": "No budget"}, headers=tenant)).status_code == 422
    r = await client.post("/api/leads", json={"name": "Budget", "custom_data": {key: 200}}, headers=tenant)
    assert r.status_code == 201 and r.json()["custom_data"][key] == 200


@pytest.mark.asyncio
async def test_inquiry_rejects_another_shops_ids(client, tenant):
    other = await second_tenant(client)
    t_other = (await client.post("/api/leads/types", json={"name": "X"}, headers=other)).json()
    emp_other = await _me(client, other)
    assert (await client.post("/api/leads", json={"name": "A", "lead_type_id": t_other["id"]},
                              headers=tenant)).status_code == 404
    assert (await client.post("/api/leads", json={"name": "A", "manager_id": emp_other["id"]},
                              headers=tenant)).status_code == 404
    lead = (await client.post("/api/leads", json={"name": "A"}, headers=tenant)).json()
    assert (await client.patch(f"/api/leads/{lead['id']}", json={"manager_id": emp_other["id"]},
                               headers=tenant)).status_code == 404
    # and another shop can't see, change or convert it
    assert (await client.get(f"/api/leads/{lead['id']}", headers=other)).status_code == 404
    assert (await client.patch(f"/api/leads/{lead['id']}", json={"name": "B"}, headers=other)).status_code == 404
    assert (await client.post(f"/api/leads/{lead['id']}/convert", headers=other)).status_code == 404
    assert (await client.get(f"/api/feed/lead/{lead['id']}", headers=other)).status_code == 404


@pytest.mark.asyncio
async def test_activity_timeline_records_changes_and_comments(client, tenant):
    lead = (await client.post("/api/leads", json={"name": "Timeline"}, headers=tenant)).json()
    await client.patch(f"/api/leads/{lead['id']}", json={"status": "in_progress"}, headers=tenant)
    r = await client.post(f"/api/feed/lead/{lead['id']}", json={"item_type": "comment", "body": "Called, no answer"},
                          headers=tenant)
    assert r.status_code == 201
    items = (await client.get(f"/api/feed/lead/{lead['id']}", headers=tenant)).json()["items"]
    bodies = [i["body"] for i in items]
    assert "Called, no answer" in bodies
    assert any("In progress" in (b or "") for b in bodies) and any("logged" in (b or "") for b in bodies)


@pytest.mark.asyncio
async def test_convert_creates_job_with_device_and_twice_is_409(client, tenant, db):
    lead = (await client.post("/api/leads", json={
        "name": "Convert Me", "phone": "+14155550122", "device_brand": "Samsung", "device_model": "S23",
        "serial": "356938035643809", "inquiry": "Battery drains"}, headers=tenant)).json()
    r = await client.post(f"/api/leads/{lead['id']}/convert", headers=tenant)
    assert r.status_code == 200, r.text
    body = r.json()
    order = await db.get(Order, body["order_id"])
    dev = await db.get(Device, body["device_id"])
    assert order.device_id == dev.id and order.complaint == "Battery drains"
    assert dev.imei == "356938035643809" and dev.model == "S23" and dev.client_id == body["client_id"]
    assert (await client.post(f"/api/leads/{lead['id']}/convert", headers=tenant)).status_code == 409
    got = (await client.get(f"/api/leads/{lead['id']}", headers=tenant)).json()
    assert got["status"] == "won" and got["converted_order_number"] == order.order_number


@pytest.mark.asyncio
async def test_convert_needs_permission_to_create_jobs(client, tenant):
    lead = (await client.post("/api/leads", json={"name": "Perm"}, headers=tenant)).json()
    staff = await limited(client, tenant, "noticket@test.dev", deny=("can_create_tickets",))
    assert (await client.post(f"/api/leads/{lead['id']}/convert", headers=staff)).status_code == 403
    assert (await client.post(f"/api/leads/{lead['id']}/convert", headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_cannot_mark_won_without_converting(client, tenant):
    lead = (await client.post("/api/leads", json={"name": "W"}, headers=tenant)).json()
    assert (await client.patch(f"/api/leads/{lead['id']}", json={"status": "won"}, headers=tenant)).status_code == 422


@pytest.mark.asyncio
async def test_board_still_has_four_columns(client, tenant):
    await client.post("/api/leads", json={"name": "Board"}, headers=tenant)
    cols = (await client.get("/api/leads/board", headers=tenant)).json()["columns"]
    assert [c["key"] for c in cols] == ["new", "in_progress", "won", "lost"]
    assert cols[0]["cards"][0]["name"] == "Board"


@pytest.mark.asyncio
async def test_types_in_use_are_archived_not_deleted(client, tenant):
    t = (await client.post("/api/leads/types", json={"name": "Sale"}, headers=tenant)).json()
    await client.post("/api/leads", json={"name": "S", "lead_type_id": t["id"]}, headers=tenant)
    r = await client.delete(f"/api/leads/types/{t['id']}", headers=tenant)
    assert r.json()["archived"] is True
    t2 = (await client.post("/api/leads/types", json={"name": "Unused"}, headers=tenant)).json()
    assert (await client.delete(f"/api/leads/types/{t2['id']}", headers=tenant)).json()["archived"] is False


# ── P1: Tekly Go — paged list + idempotent offline create ────────────────────
@pytest.mark.asyncio
async def test_idempotent_create_returns_the_same_inquiry(client, tenant, db):
    h = {**tenant, "Idempotency-Key": "go-offline-123"}
    a = await client.post("/api/leads", json={"name": "Offline"}, headers=h)
    b = await client.post("/api/leads", json={"name": "Offline"}, headers=h)
    assert a.status_code == 201 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    assert len((await db.execute(select(Lead))).scalars().all()) == 1
    # the same key in another shop is a different inquiry
    other = await second_tenant(client)
    c = await client.post("/api/leads", json={"name": "Offline"}, headers={**other, "Idempotency-Key": "go-offline-123"})
    assert c.status_code == 201 and c.json()["id"] != a.json()["id"]


@pytest.mark.asyncio
async def test_paged_list_filters_and_searches(client, tenant):
    for n in ("Alpha", "Bravo", "Charlie"):
        await client.post("/api/leads", json={"name": n, "serial": f"SN-{n}"}, headers=tenant)
    r = (await client.get("/api/leads?page_size=2", headers=tenant)).json()
    assert r["total"] == 3 and len(r["items"]) == 2
    r = (await client.get("/api/leads?q=bravo", headers=tenant)).json()
    assert [i["name"] for i in r["items"]] == ["Bravo"]
    r = (await client.get("/api/leads?q=SN-Char", headers=tenant)).json()
    assert [i["name"] for i in r["items"]] == ["Charlie"]
    assert (await client.get("/api/leads?status=nope", headers=tenant)).status_code == 422


# ── P2: Aircall (off until switched on) ──────────────────────────────────────
AIRCALL_TOKEN = "45XXYYZZa08-sample-token"


def aircall_event(event, call_id=812, answered_at=None, missed_reason=None, raw="+14155550100",
                  token=AIRCALL_TOKEN, direction="inbound"):
    """Shaped like the samples in Aircall's docs (Webhooks → Payload, Call object)."""
    return {"resource": "call", "event": event, "timestamp": 1585001000, "token": token,
            "data": {"id": call_id, "direct_link": f"https://api.aircall.io/v1/calls/{call_id}",
                     "direction": direction, "status": "done" if event == "call.ended" else "initial",
                     "started_at": 1584998199, "answered_at": answered_at, "ended_at": 1584998240,
                     "duration": 41, "raw_digits": raw, "missed_call_reason": missed_reason,
                     "user": None, "contact": None,
                     "number": {"id": 1234, "name": "Shop line", "digits": "+1 415-555-0000"}}}


async def _aircall_shop(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "AIRCALL_ENABLED", True)
    assert (await client.put("/api/telephony/aircall", json={"webhook_token": AIRCALL_TOKEN},
                             headers=tenant)).status_code == 200
    r = await client.put("/api/telephony/config", json={"provider": "aircall", "enabled": True,
                                                         "caller_id_number": "+14155550000"}, headers=tenant)
    assert r.status_code == 200, r.text
    return await org_of(client, tenant)


@pytest.mark.asyncio
async def test_aircall_is_off_until_switched_on(client, tenant):
    org = await org_of(client, tenant)
    r = await client.post(f"/api/telephony/aircall/webhook?org_id={org}", json=aircall_event("call.ended"))
    assert r.status_code == 404
    assert (await client.put("/api/telephony/aircall", json={"webhook_token": AIRCALL_TOKEN},
                             headers=tenant)).status_code == 409
    assert (await client.get("/api/telephony/config", headers=tenant)).json()["aircall"]["available"] is False


@pytest.mark.asyncio
async def test_aircall_missed_call_opens_inquiry_idempotently(client, tenant, monkeypatch, db):
    org = await _aircall_shop(client, tenant, monkeypatch)
    path = f"/api/telephony/aircall/webhook?org_id={org}"
    assert (await client.post(path, json=aircall_event("call.created"))).status_code == 200
    ended = aircall_event("call.ended", missed_reason="agents_did_not_answer")
    for _ in range(2):   # Aircall retries; a replay changes nothing
        r = await client.post(path, json=ended)
        assert r.status_code == 200, r.text
    call = (await db.execute(select(Call).where(Call.org_id == org))).scalar_one()
    assert call.status == "missed" and call.provider == "aircall" and call.provider_sid == "aircall:812"
    lead = (await db.execute(select(Lead).where(Lead.org_id == org))).scalar_one()
    assert lead.call_count == 1 and lead.phone == "+14155550100"


@pytest.mark.asyncio
async def test_aircall_answered_call_is_completed_not_an_inquiry(client, tenant, monkeypatch, db):
    org = await _aircall_shop(client, tenant, monkeypatch)
    path = f"/api/telephony/aircall/webhook?org_id={org}"
    await client.post(path, json=aircall_event("call.created", call_id=900))
    await client.post(path, json=aircall_event("call.answered", call_id=900, answered_at=1584998210))
    await client.post(path, json=aircall_event("call.ended", call_id=900, answered_at=1584998210))
    call = (await db.execute(select(Call).where(Call.org_id == org))).scalar_one()
    assert call.status == "completed" and call.duration_s == 30
    assert (await db.execute(select(Lead))).scalars().all() == []


@pytest.mark.asyncio
async def test_aircall_needs_a_token_once_switched_on(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "AIRCALL_ENABLED", True)
    assert (await client.put("/api/telephony/config", json={"provider": "aircall", "enabled": True},
                             headers=tenant)).status_code == 409
    assert (await client.put("/api/telephony/aircall", json={"webhook_token": "short"},
                             headers=tenant)).status_code == 422


@pytest.mark.asyncio
async def test_aircall_bad_token_wrong_shop_or_line_refused(client, tenant, monkeypatch, db):
    org = await _aircall_shop(client, tenant, monkeypatch)
    path = f"/api/telephony/aircall/webhook?org_id={org}"
    assert (await client.post(path, json=aircall_event("call.ended", token="guess"))).status_code == 403
    assert (await client.post(path, json={k: v for k, v in aircall_event("call.ended").items()
                                          if k != "token"})).status_code == 403
    other = await second_tenant(client)
    other_org = await org_of(client, other)
    # the token only proves the shop that saved it
    r = await client.post(f"/api/telephony/aircall/webhook?org_id={other_org}", json=aircall_event("call.ended"))
    assert r.status_code == 403
    ev = aircall_event("call.ended")
    ev["data"]["number"]["digits"] = "+1 212 555 0000"
    assert (await client.post(path, json=ev)).status_code == 403
    # unhandled events are acknowledged so Aircall doesn't disable the webhook
    r = await client.post(path, json={**aircall_event("call.tagged"), "event": "call.tagged"})
    assert r.status_code == 200
    assert (await db.execute(select(Call))).scalars().all() == []


@pytest.mark.asyncio
async def test_aircall_token_never_returned(client, tenant, monkeypatch):
    await _aircall_shop(client, tenant, monkeypatch)
    import json
    assert AIRCALL_TOKEN not in json.dumps((await client.get("/api/telephony/config", headers=tenant)).json())


# ── P1 settings switched on ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_default_comment_and_file_visibility(client, tenant):
    order = await _order(client, tenant)
    r = await client.post(f"/api/feed/order/{order}", json={"item_type": "comment", "body": "a"}, headers=tenant)
    assert r.json()["visibility"] == "private"
    assert (await client.put("/api/staff/company", json={"default_comment_visibility": "public"},
                             headers=tenant)).status_code == 200
    r = await client.post(f"/api/feed/order/{order}", json={"item_type": "comment", "body": "b"}, headers=tenant)
    assert r.json()["visibility"] == "client"
    # an explicit choice still wins, and files follow their own setting
    r = await client.post(f"/api/feed/order/{order}", json={"item_type": "comment", "body": "c",
                                                           "visibility": "private"}, headers=tenant)
    assert r.json()["visibility"] == "private"
    r = await client.post(f"/api/feed/order/{order}", json={"item_type": "file", "media_data": "data:text/plain;base64,aGk=",
                                                           "media_name": "a.txt"}, headers=tenant)
    assert r.json()["visibility"] == "private"
    assert (await client.put("/api/staff/company", json={"default_file_visibility": "everyone"},
                             headers=tenant)).status_code == 422


async def _order(client, tenant):
    lead = (await client.post("/api/leads", json={"name": "Job"}, headers=tenant)).json()
    return (await client.post(f"/api/leads/{lead['id']}/convert", headers=tenant)).json()["order_id"]


def test_public_name_modes():
    from types import SimpleNamespace as N

    from backend.services.public_names import display_name
    e = N(name="Jane Marie Doe", public_name=None)
    assert display_name(e, "full") == "Jane Marie Doe"
    assert display_name(e, "first") == "Jane"
    assert display_name(e, "first_initial") == "Jane D."
    assert display_name(N(name="Jane Doe", public_name="Tech J"), "first") == "Tech J"
    assert display_name(N(name="", public_name=None), "first") == "Staff"


@pytest.mark.asyncio
async def test_public_booking_staff_names_follow_setting(client, tenant, db):
    from backend.models import Organisation
    org_id = await org_of(client, tenant)
    org = await db.get(Organisation, org_id)
    org.appointment_enabled = True
    await db.commit()
    slug = org_id                      # the page falls back to the org id when there's no subdomain
    assert (await client.put("/api/staff/company", json={"public_employee_name": "first_initial"},
                             headers=tenant)).status_code == 200
    await limited(client, tenant, "bookable@test.dev", deny=())
    emp = (await db.execute(select(Employee).where(Employee.org_id == org_id))).scalars().first()
    emp.name = "Jane Marie Doe"
    await db.commit()
    r = await client.get(f"/api/public/appointment/{slug}")
    assert r.status_code == 200, r.text
    names = [s["name"] for s in r.json()["staff"]]
    assert "Jane D." in names and "Jane Marie Doe" not in names
    assert (await client.put("/api/staff/company", json={"public_employee_name": "nickname"},
                             headers=tenant)).status_code == 422


@pytest.mark.asyncio
async def test_auto_print_lists_label_templates_set_to_print_on_create(client, tenant):
    lead = (await client.post("/api/leads", json={"name": "Label", "device_brand": "Apple", "device_model": "iPad",
                                                  "serial": "DMPXYZ123"}, headers=tenant)).json()
    order_id = (await client.post(f"/api/leads/{lead['id']}/convert", headers=tenant)).json()["order_id"]
    assert (await client.get(f"/api/auto-print/order-created/{order_id}", headers=tenant)).json()["items"] == []
    r = await client.post("/api/documents/templates", json={
        "name": "Asset sticker", "doc_type": "label", "page_size": "label", "page_w_mm": 50, "page_h_mm": 25,
        "body_html": "<b>{Asset Serial Number}</b>", "auto_print_on": "create"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    items = (await client.get(f"/api/auto-print/order-created/{order_id}", headers=tenant)).json()["items"]
    assert len(items) == 1 and items[0]["count"] == 1 and "DMPXYZ123" in items[0]["html"]
    other = await second_tenant(client)
    assert (await client.get(f"/api/auto-print/order-created/{order_id}", headers=other)).status_code == 404


# ── P2: structured addresses ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_address_fields_fill_the_single_line(client, tenant, db):
    r = await client.post("/api/clients", json={
        "name": "Addr", "addr_street": "1 Market St", "addr_city": "San Francisco", "addr_region": "CA",
        "addr_postcode": "94105", "addr_country": "us"}, headers=tenant)
    assert r.status_code == 201, r.text
    c = await db.get(Client, r.json()["id"])
    assert c.address == "1 Market St, San Francisco, CA 94105, US" and c.addr_country == "US"
    detail = (await client.get(f"/api/clients/{c.id}", headers=tenant)).json()
    assert detail["addr_city"] == "San Francisco" and detail["address"].startswith("1 Market St")


@pytest.mark.asyncio
async def test_old_single_line_kept_until_fields_are_set(client, tenant, db):
    cid = (await client.post("/api/clients", json={"name": "Old", "address": "12 High Street, Leeds"},
                             headers=tenant)).json()["id"]
    await client.patch(f"/api/clients/{cid}", json={"notes": "x"}, headers=tenant)
    c = await db.get(Client, cid)
    assert c.address == "12 High Street, Leeds"
    full = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()
    # a PUT from an older screen that doesn't know the fields leaves the line alone
    put = {k: full.get(k) for k in ("name", "phone", "email", "address", "client_type")}
    assert (await client.put(f"/api/clients/{cid}", json=put, headers=tenant)).status_code == 200
    await db.refresh(c)
    assert c.address == "12 High Street, Leeds"
    await client.patch(f"/api/clients/{cid}", json={"addr_city": "Leeds", "addr_postcode": "LS1 4AP",
                                                    "addr_country": "GB", "addr_street": "12 High Street"}, headers=tenant)
    await db.refresh(c)
    assert c.address == "12 High Street, Leeds, LS1 4AP, GB"


@pytest.mark.asyncio
async def test_postcode_checked_per_country(client, tenant):
    bad = await client.post("/api/clients", json={"name": "Bad", "addr_postcode": "ABC", "addr_country": "US"},
                            headers=tenant)
    assert bad.status_code == 422 and "postcode" in bad.json()["detail"].lower()
    assert (await client.post("/api/clients", json={"name": "Bad", "addr_country": "USA"},
                              headers=tenant)).status_code == 422
    ok = await client.post("/api/clients", json={"name": "Free", "addr_postcode": "anything 1", "addr_country": "KE"},
                           headers=tenant)
    assert ok.status_code == 201


# ── review follow-ups ────────────────────────────────────────────────────────
async def _hook_token(db, org):
    from backend.models import Organisation
    o = await db.get(Organisation, org)
    o.webhook_secret = "whsec-test-0123456789"
    await db.commit()
    return {"X-Webhook-Token": o.webhook_secret}


@pytest.mark.asyncio
async def test_generic_webhook_ringing_then_missed_raises_one_inquiry(client, tenant, db):
    org = await org_of(client, tenant)
    h = await _hook_token(db, org)
    base = {"provider": "pbx", "from_number": "+14155550100", "external_id": "evt-9"}
    await client.post(f"/api/comms/calls/webhook?org_id={org}", json={**base, "status": "ringing"}, headers=h)
    assert (await db.execute(select(Lead).where(Lead.org_id == org))).scalars().all() == []
    r = await client.post(f"/api/comms/calls/webhook?org_id={org}", json={**base, "status": "no-answer"}, headers=h)
    assert r.json()["lead_id"]
    await client.post(f"/api/comms/calls/webhook?org_id={org}", json={**base, "status": "no-answer"}, headers=h)
    assert len((await db.execute(select(Lead).where(Lead.org_id == org))).scalars().all()) == 1
    assert len((await db.execute(select(Task).where(Task.org_id == org))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_convert_never_takes_another_customers_device(client, tenant, db):
    a = (await client.post("/api/clients", json={"name": "Owner A", "phone": "+14155550001"}, headers=tenant)).json()
    first = (await client.post("/api/leads", json={"name": "Owner A", "client_id": a["id"], "serial": "356938035643809"},
                               headers=tenant)).json()
    dev_a = (await client.post(f"/api/leads/{first['id']}/convert", headers=tenant)).json()["device_id"]
    b = (await client.post("/api/leads", json={"name": "Someone B", "phone": "+14155550002",
                                               "serial": "35-693803-564380-9"}, headers=tenant)).json()
    res = (await client.post(f"/api/leads/{b['id']}/convert", headers=tenant)).json()
    assert res["device_id"] != dev_a
    assert (await db.get(Device, res["device_id"])).client_id == res["client_id"]
    # the same customer coming back with a plain serial reuses their device
    again = (await client.post("/api/leads", json={"name": "Owner A", "client_id": a["id"], "serial": "356938035643809"},
                               headers=tenant)).json()
    assert (await client.post(f"/api/leads/{again['id']}/convert", headers=tenant)).json()["device_id"] == dev_a


@pytest.mark.asyncio
async def test_converted_inquiry_cannot_be_reopened_and_task_closes(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    await _miss(client, org, "CA" + "1" * 32)
    lead = (await db.execute(select(Lead).where(Lead.org_id == org))).scalar_one()
    await client.post(f"/api/leads/{lead.id}/convert", headers=tenant)
    assert (await client.patch(f"/api/leads/{lead.id}", json={"status": "new"}, headers=tenant)).status_code == 409
    task = (await db.execute(select(Task).where(Task.org_id == org))).scalar_one()
    await db.refresh(task)
    assert task.status == "done" and task.order_id
    # a new call from the same number opens a fresh inquiry, not the converted one
    await _miss(client, org, "CA" + "2" * 32)
    assert len((await db.execute(select(Lead).where(Lead.org_id == org))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_lost_inquiry_is_not_merged_into(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    await _miss(client, org, "CA" + "1" * 32)
    lead = (await db.execute(select(Lead).where(Lead.org_id == org))).scalar_one()
    await client.patch(f"/api/leads/{lead.id}", json={"status": "lost"}, headers=tenant)
    await _miss(client, org, "CA" + "2" * 32)
    assert len((await db.execute(select(Lead).where(Lead.org_id == org))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_alert_again_after_the_quiet_window(client, tenant, fake_twilio, db):
    from datetime import datetime, timedelta
    await connect(client, tenant)
    org = await org_of(client, tenant)
    await _miss(client, org, "CA" + "1" * 32)
    lead = (await db.execute(select(Lead).where(Lead.org_id == org))).scalar_one()
    lead.last_call_at = now_utc() - timedelta(minutes=10)
    await db.commit()
    await _miss(client, org, "CA" + "2" * 32)
    notes = (await db.execute(select(StaffNotification).where(StaffNotification.org_id == org))).scalars().all()
    assert len(notes) == 2


@pytest.mark.asyncio
async def test_type_change_moves_respond_by_and_archived_type_refused(client, tenant):
    from datetime import datetime
    fast = (await client.post("/api/leads/types", json={"name": "Fast", "sla_minutes": 10}, headers=tenant)).json()
    lead = (await client.post("/api/leads", json={"name": "T", "sla_minutes": 600}, headers=tenant)).json()
    got = (await client.patch(f"/api/leads/{lead['id']}", json={"lead_type_id": fast["id"]}, headers=tenant)).json()
    mins = (datetime.fromisoformat(got["sla_due_at"]) - datetime.fromisoformat(got["created_at"])).total_seconds() / 60
    assert 9 <= mins <= 11
    old = (await client.post("/api/leads/types", json={"name": "Old"}, headers=tenant)).json()
    await client.post("/api/leads", json={"name": "keeps it", "lead_type_id": old["id"]}, headers=tenant)
    await client.delete(f"/api/leads/types/{old['id']}", headers=tenant)      # in use → archived
    assert (await client.post("/api/leads", json={"name": "x", "lead_type_id": old["id"]}, headers=tenant)).status_code == 422


@pytest.mark.asyncio
async def test_typed_line_and_cleared_parts_stay_in_step(client, tenant, db):
    cid = (await client.post("/api/clients", json={"name": "Drift", "addr_street": "1 Market St", "addr_city": "SF",
                                                   "addr_country": "US"}, headers=tenant)).json()["id"]
    await client.patch(f"/api/clients/{cid}", json={"address": "99 New Rd"}, headers=tenant)
    c = await db.get(Client, cid)
    await db.refresh(c)
    assert c.address == "99 New Rd" and c.addr_street is None
    await client.patch(f"/api/clients/{cid}", json={"addr_city": "Oakland"}, headers=tenant)
    await db.refresh(c)
    assert c.address == "Oakland"
    await client.patch(f"/api/clients/{cid}", json={"addr_city": ""}, headers=tenant)
    await db.refresh(c)
    assert c.address is None


@pytest.mark.asyncio
async def test_aircall_junk_cannot_lock_out_the_real_token(client, tenant, monkeypatch, db):
    org = await _aircall_shop(client, tenant, monkeypatch)
    path = f"/api/telephony/aircall/webhook?org_id={org}"
    for _ in range(31):
        await client.post(path, json=aircall_event("call.ended", token="guess-guess-guess"))
    assert (await client.post(path, json=aircall_event("call.ended", token="guess-guess-guess"))).status_code == 429
    assert (await client.post(path, json=aircall_event("call.ended", missed_reason="no_available_agent"))).status_code == 200
