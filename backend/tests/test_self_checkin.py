"""Self check-in (docs/SPEC_self_checkin.md).

A customer scans a check-in point's QR, fills a form built from the shop's own setup and joins
the queue; staff call them and convert the check-in into a filled work order. Points are per
location (several per location), reached only by an unguessable token.
"""
import uuid

import pytest

SIG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
PHOTO = SIG.replace("data:image/png", "data:image/png")
ALL_DAY = {d: {"open": "00:00", "close": "24:00"} for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
CLOSED = {d: {"closed": True} for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}


async def _register(client):
    r = await client.post("/api/auth/register", json={
        "org_name": "Other Co", "name": "O", "email": f"o-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _setup(client, h, template="auto", hours=ALL_DAY):
    assert (await client.put("/api/settings/industry-template", json={"template_key": template},
                             headers=h)).status_code == 200
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]
    r = await client.put(f"/api/staff/locations/{loc['id']}", json={"name": loc["name"], "working_hours": hours},
                         headers=h)
    assert r.status_code == 200, r.text
    t = await client.post("/api/forms/types", json={"name": "Brakes", "is_default": True}, headers=h)
    assert t.status_code == 201, t.text
    tid = t.json()["id"]
    f = await client.post(f"/api/forms/types/{tid}/fields", json={
        "field_label": "Noise when braking", "field_type": "select", "options": ["Squeal", "Grind"],
        "required": True, "ask_at_checkin": True}, headers=h)
    assert f.status_code == 201, f.text
    hidden = await client.post(f"/api/forms/types/{tid}/fields", json={
        "field_label": "Internal cost note", "field_type": "text"}, headers=h)
    assert hidden.status_code == 201
    points = (await client.get("/api/checkins/points", headers=h)).json()["items"]
    assert len(points) == 1 and points[0]["name"] == "Front door"
    p = points[0]
    r = await client.put(f"/api/checkins/points/{p['id']}", json={**p, "job_type_ids": [tid]}, headers=h)
    assert r.status_code == 200, r.text
    return {"loc": loc, "type_id": tid, "point": r.json(), "token": r.json()["path"].split("/")[-1],
            "field_key": f.json().get("field_key") or f.json().get("key")}


def _body(s, **kw):
    return {"name": "Sam Patel", "phone": "+1 555 014 2290", "job_type_id": s["type_id"], "asset_group": "car",
            "identifier_kind": "vin", "identifier": "1HGCM82633A004352", "reading": 62400,
            "issue": "Brakes squeal when stopping", "answers": {s["field_key"]: "Squeal", "plate": "ABC 123"},
            "signature": SIG, "agree": True, **kw}


@pytest.mark.asyncio
async def test_form_comes_from_the_template_and_ticked_fields_only(client, tenant):
    s = await _setup(client, tenant)
    r = await client.get(f"/api/public/checkin/{s['token']}")
    assert r.status_code == 200, r.text
    body = r.json()
    form = body["form"]
    assert body["mode"] == "walk_in" and body["point"]["name"] == "Front door"
    assert form["asset_label"] == "Vehicle"
    assert [g["key"] for g in form["groups"]][:2] == ["car", "truck"]
    assert form["groups"][0]["identifier"]["primary"] == "vin"
    assert form["reading"] == {"key": "mileage", "label": "Mileage"}
    keys = {f["key"] for f in form["fields"]}
    assert {"make", "model", "year", "plate"} <= keys and "vin" not in keys
    jt = form["job_types"]
    assert [t["name"] for t in jt] == ["Brakes"]
    labels = [f["label"] for f in jt[0]["fields"]]
    assert labels == ["Noise when braking"]                      # the internal field never shows
    assert "Internal cost note" not in r.text


@pytest.mark.asyncio
async def test_marine_shop_asks_for_hin_and_engine_hours(client, tenant):
    s = await _setup(client, tenant, template="marine")
    form = (await client.get(f"/api/public/checkin/{s['token']}")).json()["form"]
    assert form["asset_label"] == "Vessel"
    boat = next(g for g in form["groups"] if g["key"] == "boat")
    assert boat["identifier"]["primary"] == "hin"
    outboard = next(g for g in form["groups"] if g["key"] == "outboard_motor")
    assert outboard["identifier"]["primary"] == "engine_serial"
    assert {"registration", "length"} <= {f["key"] for f in form["fields"]}


@pytest.mark.asyncio
async def test_check_in_joins_the_queue_and_staff_see_it(client, tenant):
    s = await _setup(client, tenant)
    r = await client.post(f"/api/public/checkin/{s['token']}", json=_body(s))
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["queue_no"] == 1 and first["ahead"] == 0 and first["kind"] == "walk_in"
    assert first["texted"] is False                      # no texting connected: nothing sent
    r2 = await client.post(f"/api/public/checkin/{s['token']}", json=_body(s, name="Lena Ortiz", phone="+1 555 777 0000"))
    assert r2.json()["queue_no"] == 2 and r2.json()["ahead"] == 1

    st = (await client.get(f"/api/public/checkin/s/{r2.json()['status_token']}")).json()
    assert st["first_name"] == "Lena" and st["queue_no"] == 2 and st["ahead"] == 1 and st["wait_min"] >= 5
    assert "phone" not in st and "Sam" not in str(st)    # only their own details

    q = (await client.get("/api/checkins", headers=tenant)).json()
    assert [i["name"] for i in q["waiting"]] == ["Sam Patel", "Lena Ortiz"]
    assert q["waiting"][0]["asset"].endswith("Honda Accord") or "2003" in q["waiting"][0]["asset"] or q["waiting"][0]["asset"]
    assert q["waiting"][0]["reading"] == 62400 and q["waiting"][0]["reading_kind"] == "mileage"
    assert (await client.get("/api/checkins/count", headers=tenant)).json()["waiting"] == 2
    lead = (await client.get(f"/api/leads/{q['waiting'][0]['lead_id']}", headers=tenant)).json()
    assert lead["source"] == "walk_in"


@pytest.mark.asyncio
async def test_required_field_internal_field_and_signature(client, tenant):
    s = await _setup(client, tenant)
    url = f"/api/public/checkin/{s['token']}"
    r = await client.post(url, json=_body(s, answers={"plate": "X"}))
    assert r.status_code == 422 and "Noise when braking" in r.text
    r = await client.post(url, json=_body(s, answers={s["field_key"]: "Rattle"}))
    assert r.status_code == 422                            # not one of the choices
    r = await client.post(url, json=_body(s, signature=None))
    assert r.status_code == 422
    r = await client.post(url, json=_body(s, issue=""))
    assert r.status_code == 422
    r = await client.post(url, json=_body(s, answers={s["field_key"]: "Grind", "internal_cost_note": "99"}))
    assert r.status_code == 201
    item = (await client.get("/api/checkins", headers=tenant)).json()["waiting"][0]
    assert "internal_cost_note" not in item["answers"]     # fields the form doesn't ask are dropped


@pytest.mark.asyncio
async def test_same_phone_again_updates_its_check_in(client, tenant):
    s = await _setup(client, tenant)
    url = f"/api/public/checkin/{s['token']}"
    a = (await client.post(url, json=_body(s))).json()
    b = (await client.post(url, json=_body(s, issue="Also the handbrake"))).json()
    assert b["updated"] is True and b["status_token"] == a["status_token"] and b["queue_no"] == a["queue_no"]
    assert len((await client.get("/api/checkins", headers=tenant)).json()["waiting"]) == 1


@pytest.mark.asyncio
async def test_call_next_only_marks_called_then_convert_fills_the_job(client, tenant, db):
    s = await _setup(client, tenant)
    st = (await client.post(f"/api/public/checkin/{s['token']}", json=_body(s))).json()
    item = (await client.get("/api/checkins", headers=tenant)).json()["waiting"][0]

    r = await client.post(f"/api/checkins/{item['id']}/call", headers=tenant)
    assert r.status_code == 200 and r.json()["state"] == "called"
    q = (await client.get("/api/checkins", headers=tenant)).json()
    assert q["waiting"] == [] and q["called"][0]["order_id"] is None      # no job yet

    r = await client.post(f"/api/checkins/{item['id']}/convert", headers=tenant)
    assert r.status_code == 200, r.text
    oid = r.json()["order_id"]
    again = await client.post(f"/api/checkins/{item['id']}/convert", headers=tenant)
    assert again.json()["order_id"] == oid                                 # idempotent

    o = (await client.get(f"/api/orders/{oid}", headers=tenant)).json()
    assert o["type_id"] == s["type_id"]
    assert o["complaint"].endswith("Brakes squeal when stopping")
    assert (o.get("custom_data") or {}).get("mileage") == 62400
    assert (o.get("custom_data") or {}).get(s["field_key"]) == "Squeal"
    dev = o["device"]
    assert dev["vin"] == "1HGCM82633A004352"
    assert (dev.get("custom_data") or {}).get("plate") == "ABC 123"
    assert (dev.get("custom_data") or {}).get("mileage") == 62400

    from sqlalchemy import select

    from backend.models import DocumentSignature, Order
    assert (await db.execute(select(Order.location_id).where(Order.id == oid))).scalar_one() == s["loc"]["id"]
    sig = (await db.execute(select(DocumentSignature).where(DocumentSignature.order_id == oid))).scalar_one()
    assert sig.doc_type == "check_in" and sig.signer_name == "Sam Patel" and sig.doc_hash

    st2 = (await client.get(f"/api/public/checkin/s/{st['status_token']}")).json()
    assert st2["state"] == "converted"


@pytest.mark.asyncio
async def test_returning_vehicle_reuses_the_device(client, tenant):
    s = await _setup(client, tenant)
    url = f"/api/public/checkin/{s['token']}"
    await client.post(url, json=_body(s))
    one = (await client.get("/api/checkins", headers=tenant)).json()["waiting"][0]
    o1 = (await client.post(f"/api/checkins/{one['id']}/convert", headers=tenant)).json()
    await client.post(url, json=_body(s, issue="Oil change", reading=65000))
    two = (await client.get("/api/checkins", headers=tenant)).json()["waiting"][0]
    assert two["seen_before"] is not None
    o2 = (await client.post(f"/api/checkins/{two['id']}/convert", headers=tenant)).json()
    d1 = (await client.get(f"/api/orders/{o1['order_id']}", headers=tenant)).json()["device"]["id"]
    d2 = (await client.get(f"/api/orders/{o2['order_id']}", headers=tenant)).json()["device"]["id"]
    assert d1 == d2


@pytest.mark.asyncio
async def test_no_show_and_customer_cancel_leave_the_queue(client, tenant):
    s = await _setup(client, tenant)
    url = f"/api/public/checkin/{s['token']}"
    a = (await client.post(url, json=_body(s))).json()
    b = (await client.post(url, json=_body(s, name="B", phone="+1 555 222 3333"))).json()
    assert (await client.post(f"/api/public/checkin/s/{a['status_token']}/cancel")).json()["state"] == "cancelled"
    st = (await client.get(f"/api/public/checkin/s/{b['status_token']}")).json()
    assert st["ahead"] == 0
    item = (await client.get("/api/checkins", headers=tenant)).json()["waiting"][0]
    assert (await client.post(f"/api/checkins/{item['id']}/no-show", headers=tenant)).json()["state"] == "no_show"
    assert (await client.post(f"/api/checkins/{item['id']}/convert", headers=tenant)).status_code == 409
    assert (await client.get("/api/checkins/count", headers=tenant)).json()["waiting"] == 0


@pytest.mark.asyncio
async def test_after_hours_switches_to_key_drop(client, tenant):
    s = await _setup(client, tenant, hours=CLOSED)
    page = (await client.get(f"/api/public/checkin/{s['token']}")).json()
    assert page["mode"] == "key_drop"
    r = await client.post(f"/api/public/checkin/{s['token']}", json=_body(s, key_tag="17", photos=[PHOTO]))
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "key_drop"
    q = (await client.get("/api/checkins", headers=tenant)).json()
    assert q["waiting"] == [] and q["key_drops"][0]["key_tag"] == "17" and q["key_drops"][0]["photos"] == 1
    o = (await client.post(f"/api/checkins/{q['key_drops'][0]['id']}/convert", headers=tenant)).json()
    order = (await client.get(f"/api/orders/{o['order_id']}", headers=tenant)).json()
    assert (order.get("custom_data") or {}).get("key_tag") == "17"
    assert len(order.get("intake_photos") or []) == 1


@pytest.mark.asyncio
async def test_key_drop_point_can_require_a_photo(client, tenant):
    s = await _setup(client, tenant)
    p = s["point"]
    await client.put(f"/api/checkins/points/{p['id']}", json={**p, "mode": "key_drop", "require_photo": True},
                     headers=tenant)
    r = await client.post(f"/api/public/checkin/{s['token']}", json=_body(s))
    assert r.status_code == 422 and "photo" in r.text


@pytest.mark.asyncio
async def test_several_points_per_location_feed_one_queue(client, tenant):
    s = await _setup(client, tenant)
    r = await client.post("/api/checkins/points", json={"location_id": s["loc"]["id"], "name": "Key box",
                                                        "mode": "walk_in", "job_type_ids": [s["type_id"]]},
                          headers=tenant)
    assert r.status_code == 201, r.text
    box = r.json()["path"].split("/")[-1]
    assert box != s["token"]
    a = (await client.post(f"/api/public/checkin/{s['token']}", json=_body(s))).json()
    b = (await client.post(f"/api/public/checkin/{box}", json=_body(s, phone="+1 555 999 1111"))).json()
    assert (a["queue_no"], b["queue_no"]) == (1, 2)
    q = (await client.get("/api/checkins", headers=tenant)).json()["waiting"]
    assert [i["point"] for i in q] == ["Front door", "Key box"]


@pytest.mark.asyncio
async def test_rotating_the_code_retires_the_old_one(client, tenant):
    s = await _setup(client, tenant)
    st = (await client.post(f"/api/public/checkin/{s['token']}", json=_body(s))).json()
    r = await client.post(f"/api/checkins/points/{s['point']['id']}/rotate", headers=tenant)
    new = r.json()["path"].split("/")[-1]
    assert new != s["token"]
    old = await client.get(f"/api/public/checkin/{s['token']}")
    assert old.status_code == 410 and "ask at the counter" in old.text
    assert (await client.get(f"/api/public/checkin/{new}")).status_code == 200
    assert (await client.get(f"/api/public/checkin/s/{st['status_token']}")).status_code == 200   # still works


@pytest.mark.asyncio
async def test_tenants_and_locations_are_isolated(client, tenant):
    s = await _setup(client, tenant)
    st = (await client.post(f"/api/public/checkin/{s['token']}", json=_body(s))).json()
    item = (await client.get("/api/checkins", headers=tenant)).json()["waiting"][0]
    other = await _register(client)
    assert (await client.get("/api/checkins", headers=other)).json()["waiting"] == []
    assert (await client.post(f"/api/checkins/{item['id']}/call", headers=other)).status_code == 404
    assert (await client.post(f"/api/checkins/{item['id']}/convert", headers=other)).status_code == 404
    assert (await client.get(f"/api/checkins/by-code/{st['status_token']}", headers=other)).status_code == 404
    assert (await client.put(f"/api/checkins/points/{s['point']['id']}", json=s["point"],
                             headers=other)).status_code in (403, 404)
    mine = (await client.get(f"/api/checkins/by-code/{st['status_token']}", headers=tenant)).json()
    assert mine["id"] == item["id"]
    assert (await client.get("/api/public/checkin/not-a-token")).status_code == 410
    # The public identify decodes only: it never says whether the shop knows a vehicle
    r = (await client.post(f"/api/public/checkin/{s['token']}/identify", json={"value": "1HGCM82633A004352"})).json()
    assert r["kind"] == "vin" and r["year"] and "matches" not in r and "owner" not in str(r)


@pytest.mark.asyncio
async def test_honeypot_and_phone_rate_limit(client, tenant):
    s = await _setup(client, tenant)
    url = f"/api/public/checkin/{s['token']}"
    assert (await client.post(url, json=_body(s, website="http://spam"))).status_code == 422
    codes = [(await client.post(url, json=_body(s))).status_code for _ in range(4)]
    assert codes[:3] == [201, 201, 201] and codes[3] == 429


@pytest.mark.asyncio
async def test_poster_and_points_need_settings_permission(client, tenant):
    s = await _setup(client, tenant)
    html = await client.get(f"/api/checkins/points/{s['point']['id']}/poster", headers=tenant)
    assert html.status_code == 200 and "<svg" in html.json()["html"] and "Check in here" in html.json()["html"]
    role = (await client.post("/api/settings/rbac/roles", json={
        "name": "Counter", "permissions": {"can_manage_settings": False}}, headers=tenant)).json()["id"]
    email = f"ctr-{uuid.uuid4().hex[:8]}@t.dev"
    await client.post("/api/staff/employees", json={"name": "Clerk", "email": email, "password": "pw-test-12345",
                                                    "role_id": role}, headers=tenant)
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})).json()
    clerk = {"Authorization": f"Bearer {tok['access_token']}"}
    assert (await client.post(f"/api/checkins/points/{s['point']['id']}/rotate", headers=clerk)).status_code == 403
    assert (await client.get("/api/checkins", headers=clerk)).status_code == 200   # staff still work the queue


@pytest.mark.asyncio
async def test_every_location_gets_a_front_door(client, tenant):
    r = await client.post("/api/staff/locations", json={"name": "Harbor yard"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    pts = (await client.get("/api/checkins/points", headers=tenant)).json()["items"]
    assert sorted(p["location"] for p in pts)[0] and len(pts) == 2
    assert len({p["path"] for p in pts}) == 2                 # unique per location
