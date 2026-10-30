"""Customer communication + estimate → job fixes from the 30 Oct 2026 flow check
(docs/audits/2026-10-30-estimate-to-invoice-flow.md): G1, G3, R1, I6, E1–E3, J1, J3.

Test mode has no email/SMS provider, so sends go through the simulated adapter, which prints
"[notify:<channel>:simulated] -> …". The API must still say honestly that nothing went out.
"""
import uuid

from sqlalchemy import select

from backend.models import Device, Employee, Role
from backend.services.auth import hash_password

PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
       "hKmMIQAAAABJRU5ErkJggg==")


async def _shop(client):
    r = await client.post("/api/auth/register", json={"org_name": "Comms", "name": "Owner",
                                                      "email": f"comms-{uuid.uuid4().hex[:8]}@t.dev",
                                                      "password": "pw-test-12345"})
    H = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    loc = (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"]
    c = (await client.post("/api/clients", json={"name": "Dana", "phone": "+15550101010",
                                                 "email": "dana@example.com"}, headers=H)).json()
    return H, me, loc, c["id"]


async def _limited(client, db, org_id, **perms):
    """A teammate on an explicit role: everything a front-desk role has, minus `perms` set False."""
    from backend.services.permissions import full_access_permissions
    p = full_access_permissions()
    p.update(perms)
    role = Role(org_id=org_id, name=f"Limited {uuid.uuid4().hex[:4]}", permissions=p, perms_explicit=True)
    db.add(role)
    await db.flush()
    email = f"lim-{uuid.uuid4().hex[:8]}@t.dev"
    emp = Employee(org_id=org_id, name="Limited", email=email, password_hash=hash_password("pw-123456789"),
                   active=True, role_id=role.id)
    db.add(emp)
    await db.commit()
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-123456789"})).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


async def _estimate(client, H, loc, cid, **extra):
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                                "is_estimate": True, **extra}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={"kind": "custom", "line_type": "labour",
                                                            "description": "w", "qty": 1, "unit_price": 50}, headers=H)
    tok = (await client.post(f"/api/orders/{o['id']}/public-link", headers=H)).json()["token"]
    return o, tok


def _sent_lines(out, needle):
    return [ln for ln in out.splitlines() if ln.startswith("[notify:") and needle in ln]


# ── G3: one "message the customer" path that really sends, shared by both boxes ──
async def test_thread_message_is_sent_and_says_honestly_it_was_simulated(client, capsys):
    H, _, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    capsys.readouterr()
    r = await client.post(f"/api/orders/{o['id']}/thread", json={"body": "Part arrives tomorrow"}, headers=H)
    assert r.status_code == 201, r.text
    assert _sent_lines(capsys.readouterr().out, "Part arrives tomorrow")
    d = r.json()["delivery"]
    # no provider is set up in tests: the API must not claim it went out
    assert d["status"] == "not_set_up" and "not sent" in d["message"].lower()


async def test_both_message_boxes_share_one_thread_and_both_send(client, capsys):
    H, _, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    capsys.readouterr()
    r = await client.post(f"/api/orders/{o['id']}/customer-reply", json={"body": "From the activity box"}, headers=H)
    assert r.status_code == 201, r.text
    assert r.json()["delivery"]["status"] in ("sent", "not_set_up")
    assert _sent_lines(capsys.readouterr().out, "From the activity box")
    await client.post(f"/api/orders/{o['id']}/thread", json={"body": "From the conversation box"}, headers=H)
    bodies = [m["body"] for m in (await client.get(f"/api/orders/{o['id']}/thread", headers=H)).json()["items"]]
    assert "From the activity box" in bodies and "From the conversation box" in bodies
    # the customer sees both on their page, once each
    tok = (await client.post(f"/api/orders/{o['id']}/public-link", headers=H)).json()["token"]
    page = (await client.get(f"/api/public/track/{tok}")).json()
    shown = [u["body"] for u in page["updates"]]
    assert shown.count("From the activity box") == 1 and shown.count("From the conversation box") == 1


async def test_customer_with_no_contact_gets_an_honest_not_sent(client):
    H, _, loc, _ = await _shop(client)
    c = (await client.post("/api/clients", json={"name": "No contact"}, headers=H)).json()
    o = (await client.post("/api/orders", json={"client_id": c["id"], "location_id": loc, "complaint": "x"},
                           headers=H)).json()
    r = await client.post(f"/api/orders/{o['id']}/thread", json={"body": "hello"}, headers=H)
    assert r.status_code == 201
    assert r.json()["delivery"]["status"] == "no_contact"


async def test_messaging_the_customer_needs_the_reply_permission(client, db):
    H, me, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    H2 = await _limited(client, db, me["org_id"], can_reply_customer=False)
    for path in ("thread", "customer-reply"):
        r = await client.post(f"/api/orders/{o['id']}/{path}", json={"body": "hi"}, headers=H2)
        assert r.status_code == 403, (path, r.text)


# ── R1: publishing a recommendation tells the customer ──
async def test_publishing_a_recommendation_notifies_the_customer(client, capsys):
    H, _, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    rec = (await client.post(f"/api/orders/{o['id']}/recommendations", json={"title": "New battery", "kind": "custom",
                                                                            "price": 40}, headers=H)).json()
    capsys.readouterr()
    r = await client.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/publish", headers=H)
    assert r.status_code == 200, r.text
    assert _sent_lines(capsys.readouterr().out, "/track/")
    assert r.json()["delivery"]["status"] in ("sent", "not_set_up")
    rec2 = (await client.post(f"/api/orders/{o['id']}/recommendations", json={"title": "Case", "kind": "custom",
                                                                             "price": 10}, headers=H)).json()
    assert rec2["status"] == "draft"
    r = await client.post(f"/api/orders/{o['id']}/recommendations/publish-all", headers=H)
    assert r.json()["published"] == 1 and "delivery" in r.json()
    assert _sent_lines(capsys.readouterr().out, "/track/")


# ── G1: staff hear about customer decisions and replies ──
async def test_staff_told_when_customer_declines_or_replies(client):
    H, _, loc, cid = await _shop(client)
    o, tok = await _estimate(client, H, loc, cid)
    await client.post(f"/api/public/track/{tok}/respond", json={"decision": "decline", "name": "Dana"})
    items = (await client.get("/api/tech/notifications", headers=H)).json()["items"]
    assert any("declin" in (n["title"] + n["body"]).lower() for n in items)
    await client.post(f"/api/public/track/{tok}/message", json={"body": "Can you call me?"})
    items = (await client.get("/api/tech/notifications", headers=H)).json()["items"]
    assert any("Can you call me?" in n["body"] for n in items)


# ── I6: Send estimate / Send invoice ──
async def test_send_estimate_sends_the_tracking_link(client, capsys):
    H, _, loc, cid = await _shop(client)
    o, _ = await _estimate(client, H, loc, cid)
    capsys.readouterr()
    r = await client.post(f"/api/orders/{o['id']}/send-estimate", json={"channel": "sms"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["delivery"]["channel"] == "sms"
    assert _sent_lines(capsys.readouterr().out, "/track/")
    r = await client.post(f"/api/orders/{o['id']}/send-estimate", json={"channel": "email"}, headers=H)
    assert r.json()["delivery"]["channel"] == "email"
    assert _sent_lines(capsys.readouterr().out, "/track/")


async def test_send_invoice_by_text_includes_the_link(client, capsys):
    H, _, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={"kind": "custom", "line_type": "labour", "description": "w",
                                                            "qty": 1, "unit_price": 80}, headers=H)
    inv = (await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)).json()
    capsys.readouterr()
    r = await client.post(f"/api/finance/invoices/{inv['id']}/send", json={"channel": "sms"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["delivery"]["status"] == "not_set_up"
    assert _sent_lines(capsys.readouterr().out, "/track/")
    r = await client.post(f"/api/finance/invoices/{inv['id']}/send", json={"channel": "email"}, headers=H)
    assert r.status_code == 200 and r.json()["delivery"]["status"] == "not_set_up"


# ── E1: converting needs permission ──
async def test_convert_needs_permission(client, db):
    H, me, loc, cid = await _shop(client)
    o, _ = await _estimate(client, H, loc, cid)
    H2 = await _limited(client, db, me["org_id"], can_create_tickets=False)
    r = await client.post(f"/api/orders/{o['id']}/convert", headers=H2)
    assert r.status_code == 403
    r = await client.post(f"/api/orders/{o['id']}/convert", headers=H)
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["is_estimate"] is False


# ── E2: one decision per estimate ──
async def test_customer_decides_an_estimate_only_once(client):
    H, _, loc, cid = await _shop(client)
    o, tok = await _estimate(client, H, loc, cid)
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "decline", "name": "Dana"})
    assert r.status_code == 200
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "signature": PNG,
                                                                    "name": "Dana"})
    assert r.status_code == 409
    assert (await client.get(f"/api/public/track/{tok}")).json()["awaiting_approval"] is False
    sigs = (await client.get(f"/api/orders/{o['id']}/signatures", headers=H)).json()
    assert len(sigs.get("items", sigs)) == 1


# ── E3: approval and conversion run the normal new-job steps ──
async def test_approval_runs_new_job_steps_and_type_starting_status(client, capsys):
    H, _, loc, cid = await _shop(client)
    sts = (await client.get("/api/statuses", headers=H)).json()
    sitems = sts.get("items", sts) if isinstance(sts, dict) else sts
    wo_in_progress = next(s["key"] for s in sitems
                          if s.get("status_group") == "in_progress" and s.get("record_type", "work_order") == "work_order")
    t = (await client.post("/api/forms/types", json={"name": "Bench", "initial_status": wo_in_progress}, headers=H)).json()
    o, tok = await _estimate(client, H, loc, cid, type_id=t["id"])
    capsys.readouterr()
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "signature": PNG,
                                                                    "name": "Dana"})
    assert r.status_code == 200, r.text
    o2 = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert o2["is_estimate"] is False
    assert o2["status"] == wo_in_progress                    # the job type's starting status
    # the shop's "order received" rule fires for the new job, as for any new job
    assert _sent_lines(capsys.readouterr().out, "received")


# ── J1: job-type gates are settable ──
async def test_job_type_signature_and_checklist_gates_round_trip(client):
    H, _, loc, cid = await _shop(client)
    t = (await client.post("/api/forms/types", json={"name": "Pickup", "requires_signature": True}, headers=H)).json()
    assert t["requires_signature"] is True and t["requires_checklist"] is False
    t = (await client.put(f"/api/forms/types/{t['id']}", json={"name": "Pickup", "requires_checklist": True},
                          headers=H)).json()
    assert t["requires_signature"] is True and t["requires_checklist"] is True   # omitted = kept


# ── J3: device passcode captured at intake, stored encrypted, shown only on request ──
async def test_device_passcode_is_captured_encrypted_and_revealed_on_request(client, db, monkeypatch):
    from cryptography.fernet import Fernet

    from backend.config import settings
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    H, me, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                                "asset_brand": "Apple", "asset_model": "iPhone 13",
                                                "device_passcode": "482913"}, headers=H)).json()
    dev_id = o["device"]["id"]
    row = (await db.execute(select(Device).where(Device.id == dev_id))).scalar_one()
    assert row.passcode_enc and "482913" not in row.passcode_enc
    a = (await client.get(f"/api/assets/{dev_id}", headers=H)).json()
    assert a["has_passcode"] is True and "482913" not in str(a)
    assert "482913" not in str((await client.get(f"/api/orders/{o['id']}", headers=H)).json())
    r = await client.get(f"/api/assets/{dev_id}/passcode", headers=H)
    assert r.status_code == 200 and r.json()["passcode"] == "482913"
    H2 = await _limited(client, db, me["org_id"], can_view_asset_info=False)
    assert (await client.get(f"/api/assets/{dev_id}/passcode", headers=H2)).status_code == 403
    r = await client.put(f"/api/assets/{dev_id}/passcode", json={"passcode": None}, headers=H)
    assert r.status_code == 200 and r.json()["has_passcode"] is False


# ── review fixes ──
async def test_restricted_role_sees_passcode_only_for_devices_on_its_jobs(client, db):
    H, me, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                                "asset_brand": "Apple", "device_passcode": "1111"}, headers=H)).json()
    dev_id = o["device"]["id"]
    from backend.services.permissions import full_access_permissions
    role = Role(org_id=me["org_id"], name="Own only", permissions=full_access_permissions(), perms_explicit=True,
                order_scope="own")
    db.add(role)
    await db.flush()
    email = f"own-{uuid.uuid4().hex[:8]}@t.dev"
    emp = Employee(org_id=me["org_id"], name="Tech", email=email, password_hash=hash_password("pw-123456789"),
                   active=True, role_id=role.id)
    db.add(emp)
    await db.commit()
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-123456789"})).json()
    H2 = {"Authorization": f"Bearer {tok['access_token']}"}
    await client.patch(f"/api/orders/{o['id']}/assign", json={"assigned_to_id": me["id"]}, headers=H)
    assert (await client.get(f"/api/assets/{dev_id}/passcode", headers=H2)).status_code == 403
    assert (await client.put(f"/api/assets/{dev_id}/passcode", json={"passcode": None}, headers=H2)).status_code == 403


async def test_invoice_text_only_goes_to_the_number_on_file(client):
    H, _, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={"kind": "custom", "line_type": "labour", "description": "w",
                                                            "qty": 1, "unit_price": 80}, headers=H)
    inv = (await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)).json()
    r = await client.post(f"/api/finance/invoices/{inv['id']}/send", json={"channel": "sms", "to": "+19005550000"},
                          headers=H)
    assert r.status_code == 422
    r = await client.post(f"/api/finance/invoices/{inv['id']}/send", json={"to": "a@x.dev, b@y.dev"}, headers=H)
    assert r.status_code == 422


async def test_customer_messages_alert_staff_once_per_burst(client):
    H, _, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    tok = (await client.post(f"/api/orders/{o['id']}/public-link", headers=H)).json()["token"]
    for i in range(3):
        assert (await client.post(f"/api/public/track/{tok}/message", json={"body": f"hi {i}"})).status_code == 200
    items = (await client.get("/api/tech/notifications", headers=H)).json()["items"]
    assert len([n for n in items if n["kind"] == "client.message"]) == 1


async def test_signature_must_be_an_image(client):
    H, _, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    assert (await client.post(f"/api/orders/{o['id']}/signature", json={"signature": "x"}, headers=H)).status_code == 422
    assert (await client.post(f"/api/orders/{o['id']}/signature", json={"signature": PNG}, headers=H)).status_code == 200
    assert (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["signature"] == PNG


async def test_approved_estimate_becomes_an_open_job_without_a_warranty_yet(client):
    H, _, loc, cid = await _shop(client)
    part = (await client.post("/api/catalog/products", json={"name": "Screen", "sell_price": 100, "warranty_days": 90},
                              headers=H)).json()
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                                "is_estimate": True}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={"kind": "product", "line_type": "part", "ref_id": part["id"],
                                                            "description": "Screen", "qty": 1, "unit_price": 100}, headers=H)
    tok = (await client.post(f"/api/orders/{o['id']}/public-link", headers=H)).json()["token"]
    await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "signature": PNG, "name": "Dana"})
    o2 = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()
    assert o2["is_estimate"] is False
    assert o2.get("closed_at") is None and o2.get("warranty_until") is None
    feed = (await client.get(f"/api/feed/order/{o['id']}", headers=H)).json()
    fitems = feed.get("items", feed) if isinstance(feed, dict) else feed
    assert sum("Converted from estimate" in str(f.get("body", "")) for f in fitems) == 1


async def test_send_estimate_refused_after_the_customer_answered(client):
    H, _, loc, cid = await _shop(client)
    o, tok = await _estimate(client, H, loc, cid)
    await client.post(f"/api/public/track/{tok}/respond", json={"decision": "decline", "name": "Dana"})
    r = await client.post(f"/api/orders/{o['id']}/send-estimate", json={}, headers=H)
    assert r.status_code == 409


# ── follow-ups: warranty on recommendation lines, template passcode field, convert commission ──
async def test_approved_recommendation_line_carries_catalog_warranty(client):
    H, _, loc, cid = await _shop(client)
    svc = (await client.post("/api/catalog/services", json={"name": "Battery fit", "price": 40, "warranty_days": 30},
                             headers=H)).json()
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    rec = (await client.post(f"/api/orders/{o['id']}/recommendations", json={"title": "Battery", "kind": "service",
                                                                            "ref_id": svc["id"], "price": 40},
                             headers=H)).json()
    r = await client.post(f"/api/orders/{o['id']}/recommendations/{rec['id']}/approve-for-customer",
                          json={"how": "phone"}, headers=H)
    assert r.status_code == 200, r.text
    lines = (await client.get(f"/api/orders/{o['id']}", headers=H)).json()["lines"]
    assert [ln.get("warranty_days") for ln in lines if ln["ref_id"] == svc["id"]] == [30]


async def test_template_passcode_field_is_stored_encrypted(client, db, monkeypatch):
    from cryptography.fernet import Fernet

    from backend.config import settings
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    H, _, loc, cid = await _shop(client)
    a = (await client.post("/api/assets", json={"owner_type": "client", "client_id": cid, "brand": "Apple",
                                                "custom_data": {"passcode": "7391", "storage": "128GB"}},
                           headers=H)).json()
    assert a["has_passcode"] is True and "7391" not in str(a) and a["custom_data"] == {"storage": "128GB"}
    row = (await db.execute(select(Device).where(Device.id == a["id"]))).scalar_one()
    assert "passcode" not in (row.custom_data or {}) and "7391" not in (row.passcode_enc or "")
    assert (await client.get(f"/api/assets/{a['id']}/passcode", headers=H)).json()["passcode"] == "7391"


async def test_converting_credits_the_estimate_creator(client, db):
    import pytest as _pt

    from backend.models import CommissionRule, EmployeeBalanceLedger
    H, me, loc, cid = await _shop(client)
    db.add(CommissionRule(org_id=me["org_id"], employee_id=None, name="Create 10%", event="order_created",
                          calc_type="percent", percent_basis="total", amount=10.0))
    await db.commit()
    o, _ = await _estimate(client, H, loc, cid)          # created with no lines → nothing accrues yet
    H2 = await _limited(client, db, me["org_id"])
    assert (await client.post(f"/api/orders/{o['id']}/convert", headers=H2)).status_code == 200
    rows = (await db.execute(select(EmployeeBalanceLedger).where(
        EmployeeBalanceLedger.event == "order_created", EmployeeBalanceLedger.ref_id == o["id"]))).scalars().all()
    assert [r.employee_id for r in rows] == [me["id"]] and rows[0].amount == _pt.approx(5.0)


async def test_approved_recommendations_update_the_draft_invoice(client):
    """Online or by phone, approved recommended work lands on the job's draft invoice."""
    H, _, loc, cid = await _shop(client)
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"}, headers=H)).json()
    await client.post(f"/api/orders/{o['id']}/lines", json={"kind": "custom", "line_type": "labour",
                                                            "description": "w", "qty": 1, "unit_price": 200}, headers=H)
    iid = (await client.post("/api/finance/invoices", json={"order_id": o["id"]}, headers=H)).json()["id"]

    async def total():
        return float((await client.get(f"/api/finance/invoices/{iid}", headers=H)).json()["total"])
    assert await total() == 200

    async def rec(title, price):
        r = (await client.post(f"/api/orders/{o['id']}/recommendations", json={"title": title, "kind": "custom",
                                                                              "unit_price": price}, headers=H)).json()
        await client.post(f"/api/orders/{o['id']}/recommendations/{r['id']}/publish", headers=H)
        return r["id"]
    online = await rec("Case", 40)
    tok = (await client.post(f"/api/orders/{o['id']}/public-link", headers=H)).json()["token"]
    r = await client.post(f"/api/public/track/{tok}/recommendations/decide", json={"approve_ids": [online]})
    assert r.status_code == 200, r.text
    assert await total() == 240
    phone = await rec("Screen guard", 15)
    r = await client.post(f"/api/orders/{o['id']}/recommendations/{phone}/approve-for-customer",
                          json={"how": "phone"}, headers=H)
    assert r.status_code == 200, r.text
    assert await total() == 255
