"""API v2 records: locations and employees lookups, contact search and update, work orders
(read, update, status through the app's own path), order lines, invoices and their payments."""
import json
import uuid

import pytest
from sqlalchemy import select

from backend.models import (
    AuditLog,
    Client,
    Employee,
    Location,
    Order,
    OrderChecklistItem,
    OrderLine,
    Organisation,
    Role,
    WebhookDelivery,
    WebhookEndpoint,
)


# ── helpers ──────────────────────────────────────────────────────────────────
async def _register(client):
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": f"v2r-{uuid.uuid4().hex[:8]}@t.dev",
        "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["org_id"]


async def _key(client, H, scopes):
    r = await client.post("/api/api-keys", json={"name": "k", "scopes": scopes}, headers=H)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['key']}"}


async def _job(client, H, total=100.0, client_name="Dana"):
    cid = (await client.post("/api/clients", json={"name": client_name, "phone": "+15550111"}, headers=H)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=H)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": total}, headers=H)
    assert r.status_code in (200, 201), r.text
    return cid, oid


async def _pay(client, H, oid, amount):
    await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"}, headers=H)
    accts = (await client.get("/api/cash/accounts", headers=H)).json()["items"]
    r = await client.post(f"/api/cash/orders/{oid}/pay",
                          json={"method": "cash", "account_id": accts[0]["id"], "amount": amount}, headers=H)
    assert r.status_code in (200, 201), r.text


async def _hook(db, org_id):
    db.add(WebhookEndpoint(org_id=org_id, url="https://hooks.example.com/in", events="*", active=True))
    await db.commit()


async def _events(db, org_id, name):
    rows = (await db.execute(select(WebhookDelivery).where(
        WebhookDelivery.org_id == org_id, WebhookDelivery.event == name))).scalars().all()
    return [json.loads(d.payload)["data"] for d in rows]


# ── scopes ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_resource_scopes(client):
    H, _ = await _register(client)
    _, oid = await _job(client, H)
    loc = await _key(client, H, ["locations:read"])
    emp = await _key(client, H, ["employees:read"])
    orders_r = await _key(client, H, ["orders:read"])
    inv_r = await _key(client, H, ["invoices:read"])
    contacts_r = await _key(client, H, ["contacts:read"])
    broad = await _key(client, H, ["read"])

    assert (await client.get("/api/v2/locations", headers=loc)).status_code == 200
    assert (await client.get("/api/v2/employees", headers=loc)).status_code == 403
    assert (await client.get("/api/v2/orders", headers=loc)).status_code == 403
    assert (await client.get("/api/v2/employees", headers=emp)).status_code == 200
    assert (await client.get("/api/v2/locations", headers=emp)).status_code == 403
    assert (await client.get(f"/api/v2/orders/{oid}", headers=orders_r)).status_code == 200
    assert (await client.get(f"/api/v2/orders/{oid}/lines", headers=orders_r)).status_code == 200
    assert (await client.get("/api/v2/invoices", headers=orders_r)).status_code == 403
    assert (await client.get("/api/v2/invoices", headers=inv_r)).status_code == 200
    assert (await client.get("/api/v2/orders", headers=inv_r)).status_code == 403
    # reads never allow writes
    r = await client.patch(f"/api/v2/orders/{oid}", json={"priority": 1}, headers=orders_r)
    assert r.status_code == 403 and "orders:write" in r.text
    assert (await client.post(f"/api/v2/orders/{oid}/status", json={"status": "in_repair"},
                              headers=orders_r)).status_code == 403
    c = (await client.get("/api/v2/contacts", headers=contacts_r)).json()["data"][0]
    assert (await client.patch(f"/api/v2/contacts/{c['id']}", json={"notes": "x"}, headers=contacts_r)).status_code == 403
    # broad read covers every new resource
    for path in ("/api/v2/locations", "/api/v2/employees", "/api/v2/orders", "/api/v2/invoices", "/api/v2/contacts"):
        assert (await client.get(path, headers=broad)).status_code == 200, path
    # discovery lists them
    res = (await client.get("/api/v2", headers=broad)).json()["data"]["resources"]
    assert {"locations", "employees", "orders", "invoices"} <= set(res)


@pytest.mark.asyncio
async def test_new_routes_in_openapi(client):
    doc = (await client.get("/api/v2/openapi.json")).json()
    for path in ("/api/v2/locations", "/api/v2/employees/{eid}", "/api/v2/orders/{oid}/status",
                 "/api/v2/orders/{oid}/lines", "/api/v2/invoices/{iid}/payments"):
        assert path in doc["paths"], path
    get = doc["paths"]["/api/v2/contacts"]["get"]
    assert get["operationId"] == "v2_list_contacts"
    assert {p["name"] for p in get["parameters"]} >= {"phone", "email", "q"}
    assert "patch" in doc["paths"]["/api/v2/contacts/{cid}"]


# ── lookups ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_locations_lookup(client, db):
    H, org_id = await _register(client)
    A = await _key(client, H, ["locations:read"])
    db.add(Location(org_id=org_id, name="Closed branch", active=False, timezone="Europe/London"))
    await db.commit()
    rows = (await client.get("/api/v2/locations", headers=A)).json()["data"]
    assert len(rows) == 2 and set(rows[0]) == {"id", "name", "address", "phone", "timezone", "active"}
    active = (await client.get("/api/v2/locations?active=true", headers=A)).json()["data"]
    assert [x["active"] for x in active] == [True]
    one = (await client.get(f"/api/v2/locations/{active[0]['id']}", headers=A)).json()["data"]
    assert one["id"] == active[0]["id"]

    H2, _ = await _register(client)
    other = await _key(client, H2, ["read"])
    assert (await client.get(f"/api/v2/locations/{one['id']}", headers=other)).status_code == 404


@pytest.mark.asyncio
async def test_employees_never_leak_private_fields(client, db):
    H, org_id = await _register(client)
    A = await _key(client, H, ["employees:read", "costs:read"])
    role = Role(org_id=org_id, name="Technician")
    db.add(role)
    await db.flush()
    secret_bits = {"email": "sam.private@t.dev", "phone": "+1 555 999 1234", "tax_id": "TAX-778899",
                   "wage_value": 4321.5, "commission_labour": 17.5, "hourly_cost_rate": 33.25,
                   "pin_hash": "pinhash-xyz", "mfa_secret": "MFASECRETBASE32"}
    loc = Location(org_id=org_id, name="Branch", timezone="UTC")
    db.add(loc)
    await db.flush()
    db.add(Employee(org_id=org_id, location_id=loc.id, role_id=role.id, name="Sam Ortega",
                    password_hash="pwhash-abc", active=True, **secret_bits))
    db.add(Employee(org_id=org_id, name="Old Hand", email=f"old-{uuid.uuid4().hex[:6]}@t.dev",
                    password_hash="x", active=False))
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    org.public_employee_name = "first_initial"
    await db.commit()

    r = await client.get("/api/v2/employees?page_size=100", headers=A)
    assert r.status_code == 200
    for value in [*secret_bits.values(), "pwhash-abc"]:
        assert str(value) not in r.text, value
    sam = next(e for e in r.json()["data"] if e["name"] == "Sam Ortega")
    assert set(sam) == {"id", "name", "public_name", "role", "location_id", "active"}
    assert sam["public_name"] == "Sam O." and sam["role"] == "Technician" and sam["location_id"] == loc.id

    by_loc = (await client.get(f"/api/v2/employees?location_id={loc.id}", headers=A)).json()["data"]
    assert [e["name"] for e in by_loc] == ["Sam Ortega"]
    inactive = (await client.get("/api/v2/employees?active=false", headers=A)).json()["data"]
    assert [e["name"] for e in inactive] == ["Old Hand"]
    one = await client.get(f"/api/v2/employees/{sam['id']}", headers=A)
    assert one.json()["data"] == sam and "sam.private" not in one.text

    H2, _ = await _register(client)
    assert (await client.get(f"/api/v2/employees/{sam['id']}", headers=await _key(client, H2, ["read"]))).status_code == 404


# ── contacts ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_contact_phone_search_is_format_and_country_code_insensitive(client, db):
    H, org_id = await _register(client)
    A = await _key(client, H, ["contacts:write"])
    first = (await client.post("/api/v2/contacts", json={"name": "Plain", "phone": "5550102233"}, headers=A)).json()["data"]
    second = (await client.post("/api/v2/contacts", json={"name": "Formatted", "phone": "(555) 010-2233"},
                                headers=A)).json()["data"]
    await client.post("/api/v2/contacts", json={"name": "Other", "phone": "5550102234"}, headers=A)
    await client.post("/api/v2/contacts", json={"name": "Abroad", "phone": "+44 5550102233"}, headers=A)

    r = await client.get("/api/v2/contacts", params={"phone": "+1 (555) 010-2233"}, headers=A)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [c["id"] for c in body["data"]] == [second["id"], first["id"]]   # every match, newest first
    assert body["total"] == 2
    paged = (await client.get("/api/v2/contacts", params={"phone": "+1 555.010.2233", "page_size": 1, "page": 2},
                              headers=A)).json()
    assert [c["id"] for c in paged["data"]] == [first["id"]] and paged["total"] == 2
    # a national number also matches the same number stored with any country code (the calls rule)
    national = (await client.get("/api/v2/contacts", params={"phone": "555-010-2233"}, headers=A)).json()
    assert national["total"] == 3 and "Abroad" in {c["name"] for c in national["data"]}
    assert (await client.get("/api/v2/contacts", params={"phone": "12"}, headers=A)).status_code == 422
    assert (await client.get("/api/v2/contacts", params={"phone": "+1 555 777 0000"}, headers=A)).json()["data"] == []

    H2, _ = await _register(client)   # never another shop's contact
    B = await _key(client, H2, ["contacts:read"])
    assert (await client.get("/api/v2/contacts", params={"phone": "5550102233"}, headers=B)).json()["data"] == []


@pytest.mark.asyncio
async def test_contact_email_and_text_search(client):
    H, _ = await _register(client)
    A = await _key(client, H, ["contacts:write"])
    dana = (await client.post("/api/v2/contacts", json={"name": "Dana Whitfield", "email": "Dana.W@Example.com"},
                              headers=A)).json()["data"]
    await client.post("/api/v2/contacts", json={"name": "Danny", "email": "danny@example.com"}, headers=A)
    await client.post("/api/v2/contacts", json={"name": "100%_real", "phone": "+1 555 010 9999"}, headers=A)

    r = (await client.get("/api/v2/contacts", params={"email": "dana.w@EXAMPLE.com"}, headers=A)).json()
    assert [c["id"] for c in r["data"]] == [dana["id"]] and r["total"] == 1
    assert (await client.get("/api/v2/contacts", params={"email": "dana.w@example.co"}, headers=A)).json()["data"] == []

    names = lambda r: sorted(c["name"] for c in r.json()["data"])  # noqa: E731
    assert names(await client.get("/api/v2/contacts", params={"q": "dan"}, headers=A)) == ["Dana Whitfield", "Danny"]
    assert names(await client.get("/api/v2/contacts", params={"q": "WHITF"}, headers=A)) == ["Dana Whitfield"]
    assert names(await client.get("/api/v2/contacts", params={"q": "danny@"}, headers=A)) == ["Danny"]
    assert names(await client.get("/api/v2/contacts", params={"q": "0109999"}, headers=A)) == ["100%_real"]
    assert names(await client.get("/api/v2/contacts", params={"q": "%_"}, headers=A)) == ["100%_real"]   # literal
    assert (await client.get("/api/v2/contacts", params={"q": "d"}, headers=A)).status_code == 422
    assert (await client.get("/api/v2/contacts", params={"q": "  "}, headers=A)).status_code == 422
    # filters combine
    assert names(await client.get("/api/v2/contacts", params={"q": "dan", "email": "danny@example.com"},
                                  headers=A)) == ["Danny"]


@pytest.mark.asyncio
async def test_contact_patch(client, db):
    H, org_id = await _register(client)
    await _hook(db, org_id)
    A = await _key(client, H, ["contacts:write"])
    c = (await client.post("/api/v2/contacts", json={"name": "Ann", "phone": "1"}, headers=A)).json()["data"]
    r = await client.patch(f"/api/v2/contacts/{c['id']}", json={
        "name": " Ann Lee ", "email": "ann@example.com", "phone": None, "notes": "VIP",
        "addr_city": "Paris", "addr_country": "fr"}, headers=A)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["name"] == "Ann Lee" and d["email"] == "ann@example.com" and d["phone"] is None
    assert d["address_parts"]["city"] == "Paris" and d["address"] == "Paris, FR"
    row = (await db.execute(select(Client).where(Client.id == c["id"]))).scalar_one()
    await db.refresh(row)
    assert row.notes == "VIP"

    (ev,) = await _events(db, org_id, "contact.updated")
    assert ev["id"] == c["id"] and ev["email"] == "ann@example.com"
    audit = (await db.execute(select(AuditLog).where(AuditLog.action == "client.update",
                                                     AuditLog.entity_id == c["id"]))).scalar_one()
    assert audit.diff["via_api_key"]

    bad = [{"name": None}, {"name": "  "}, {"email": "nope"}, {"name": "x" * 201}, {"notes": "x" * 5001},
           {"nickname": "A"}, {"addr_country": "USA"}]
    for body in bad:
        assert (await client.patch(f"/api/v2/contacts/{c['id']}", json=body, headers=A)).status_code == 422, body
    H2, _ = await _register(client)
    other = await _key(client, H2, ["contacts:write"])
    assert (await client.patch(f"/api/v2/contacts/{c['id']}", json={"notes": "x"}, headers=other)).status_code == 404


# ── orders ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_orders_list_get_and_filters(client, db):
    H, org_id = await _register(client)
    A = await _key(client, H, ["orders:read"])
    cid, oid = await _job(client, H, 120)
    _, other = await _job(client, H, 50, client_name="Eve")
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    o.public_token = "tok-abc"
    await db.commit()

    r = await client.get(f"/api/v2/orders/{oid}", headers=A)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["number"] and d["status_name"] and d["total"] == 120 and d["client_id"] == cid
    assert d["tracking_url"].endswith("/track/tok-abc")
    assert d["lines"]["count"] == 1 and d["lines"]["url"] == f"/api/v2/orders/{oid}/lines"
    assert "device" in d

    ids = lambda r: [x["id"] for x in r.json()["data"]]  # noqa: E731
    assert ids(await client.get("/api/v2/orders", headers=A)) == [other, oid]
    assert ids(await client.get(f"/api/v2/orders?client_id={cid}", headers=A)) == [oid]
    assert ids(await client.get(f"/api/v2/orders?location_id={o.location_id}", headers=A)) == [other, oid]
    assert ids(await client.get("/api/v2/orders?status=nope", headers=A)) == []
    assert ids(await client.get("/api/v2/orders?created_since=2000-01-01", headers=A)) == [other, oid]
    assert ids(await client.get("/api/v2/orders?updated_since=2999-01-01T00:00:00Z", headers=A)) == []
    assert (await client.get("/api/v2/orders?updated_since=yesterday", headers=A)).status_code == 422
    # v1 takes updated_since too
    V1 = await _key(client, H, ["read"])
    assert len((await client.get("/api/v1/orders?updated_since=2000-01-01T00:00:00%2B02:00", headers=V1)).json()["data"]) == 2
    assert (await client.get("/api/v1/orders?updated_since=2999-01-01", headers=V1)).json()["data"] == []
    assert (await client.get("/api/v1/orders?updated_since=bad", headers=V1)).status_code == 422

    H2, _ = await _register(client)
    B = await _key(client, H2, ["write"])
    for call in (client.get(f"/api/v2/orders/{oid}", headers=B), client.get(f"/api/v2/orders/{oid}/lines", headers=B),
                 client.patch(f"/api/v2/orders/{oid}", json={"priority": 1}, headers=B),
                 client.post(f"/api/v2/orders/{oid}/status", json={"status": "in_repair"}, headers=B)):
        assert (await call).status_code == 404


@pytest.mark.asyncio
async def test_order_lines_cost_only_with_costs_read(client, db):
    H, _ = await _register(client)
    _, oid = await _job(client, H, 80)
    line = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalar_one()
    line.cost_price = 31.5
    await db.commit()
    plain = await _key(client, H, ["orders:read"])
    costs = await _key(client, H, ["orders:read", "costs:read"])
    (a,) = (await client.get(f"/api/v2/orders/{oid}/lines", headers=plain)).json()["data"]
    (b,) = (await client.get(f"/api/v2/orders/{oid}/lines", headers=costs)).json()["data"]
    assert "cost_price" not in a and b["cost_price"] == 31.5
    assert a["description"] == "Fix" and a["qty"] == 1 and a["unit_price"] == 80 and a["total"] == 80
    assert {"discount", "tax", "kind", "tax_rate", "tax_mode"} <= set(a)


@pytest.mark.asyncio
async def test_order_patch_wall_clock_and_ids(client, db):
    H, org_id = await _register(client)
    A = await _key(client, H, ["orders:write"])
    _, oid = await _job(client, H)
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    loc = (await db.execute(select(Location).where(Location.id == o.location_id))).scalar_one()
    loc.timezone = "America/New_York"
    tech = Employee(org_id=org_id, name="Tech", email=f"t-{uuid.uuid4().hex[:6]}@t.dev", password_hash="x", active=True)
    gone = Employee(org_id=org_id, name="Gone", email=f"g-{uuid.uuid4().hex[:6]}@t.dev", password_hash="x", active=False)
    db.add_all([tech, gone])
    await db.commit()

    r = await client.patch(f"/api/v2/orders/{oid}", json={
        "scheduled_for": "2026-10-28T14:30:00Z", "scheduled_to": "2026-10-28T11:30:00",
        "due_at": "2026-10-29T19:00:00+02:00", "priority": 2, "complaint": "  Water damage ",
        "assigned_to_id": tech.id}, headers=A)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["scheduled_for"] == "2026-10-28T10:30:00"      # 14:30Z is 10:30 on the shop's clock (EDT)
    assert d["scheduled_to"] == "2026-10-28T11:30:00"       # no offset: already wall-clock
    assert d["due_at"] == "2026-10-29T17:00:00"             # instants are UTC
    assert d["priority"] == 2 and d["complaint"] == "Water damage" and d["assigned_to_id"] == tech.id

    r = await client.patch(f"/api/v2/orders/{oid}", json={"assigned_to_id": None, "scheduled_to": None}, headers=A)
    assert r.json()["data"]["assigned_to_id"] is None and r.json()["data"]["scheduled_to"] is None
    assert r.json()["data"]["scheduled_for"] == "2026-10-28T10:30:00"
    audit = (await db.execute(select(AuditLog).where(AuditLog.action == "order.update",
                                                     AuditLog.entity_id == oid))).scalars().all()
    assert len(audit) == 2 and all(a.diff["via_api_key"] for a in audit)

    H2, org2 = await _register(client)
    foreign_loc = (await db.execute(select(Location).where(Location.org_id == org2))).scalars().first()
    for body, code in (({"assigned_to_id": str(uuid.uuid4())}, 404), ({"manager_id": gone.id}, 422),
                       ({"location_id": foreign_loc.id}, 404), ({"scheduled_for": "soon"}, 400),
                       ({"scheduled_for": "2026-10-28T12:00:00", "scheduled_to": "2026-10-28T11:00:00"}, 422),
                       ({"priority": 3}, 422), ({"status": "done"}, 422), ({"complaint": "x" * 5001}, 422)):
        assert (await client.patch(f"/api/v2/orders/{oid}", json=body, headers=A)).status_code == code, body


@pytest.mark.asyncio
async def test_status_change_honours_require_checklist_gate(client, db):
    H, org_id = await _register(client)
    A = await _key(client, H, ["orders:write"])
    r = await client.post("/api/statuses", json={"name": "QC passed", "key": "qc_passed", "status_group": "in_progress",
                                                 "require_checklist": True}, headers=H)
    assert r.status_code in (200, 201), r.text
    _, oid = await _job(client, H)
    db.add(OrderChecklistItem(org_id=org_id, order_id=oid, label="Test screen", done=False))
    await db.commit()

    r = await client.post(f"/api/v2/orders/{oid}/status", json={"status": "qc_passed"}, headers=A)
    assert r.status_code == 409 and "checklist" in r.json()["detail"].lower()
    item = (await db.execute(select(OrderChecklistItem).where(OrderChecklistItem.order_id == oid))).scalar_one()
    item.done = True
    await db.commit()
    r = await client.post(f"/api/v2/orders/{oid}/status", json={"status": "qc_passed"}, headers=A)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_status_change_runs_app_path_and_fires_webhook(client, db):
    H, org_id = await _register(client)
    await _hook(db, org_id)
    A = await _key(client, H, ["orders:write"])
    _, oid = await _job(client, H)
    before = (await db.execute(select(Order.status).where(Order.id == oid))).scalar_one()

    r = await client.post(f"/api/v2/orders/{oid}/status", json={"status": "in_repair", "comment": "On the bench"},
                          headers=A)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data"]["status"] == "in_repair" and body["data"]["status_name"]
    assert body["change"] == {"from": before, "to": "in_repair", "is_closing": False}
    (ev,) = await _events(db, org_id, "order.status_changed")
    assert ev["from"] == before and ev["to"] == "in_repair"
    audit = (await db.execute(select(AuditLog).where(AuditLog.action == "order.status_change",
                                                     AuditLog.entity_id == oid))).scalar_one()
    assert audit.diff["via_api_key"] and audit.diff["status"] == [before, "in_repair"] and audit.actor_id is None
    from backend.models import OrderStatusLog
    log = (await db.execute(select(OrderStatusLog).where(OrderStatusLog.order_id == oid,
                                                         OrderStatusLog.to_status == "in_repair"))).scalar_one()
    assert log.note == "On the bench"

    r = await client.post(f"/api/v2/orders/{oid}/status", json={"status": "teleported"}, headers=A)
    assert r.status_code == 422 and "in_repair" in r.json()["detail"]
    assert (await client.post(f"/api/v2/orders/{oid}/status", json={"status": "done", "extra": 1},
                              headers=A)).status_code == 422

    # closing fires order.closed too, and the close gates still apply
    line = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalar_one()
    line.pending_stock = 1
    await db.commit()
    r = await client.post(f"/api/v2/orders/{oid}/status", json={"status": "done"}, headers=A)
    assert r.status_code == 409 and "pending stock" in r.json()["detail"]
    line.pending_stock = 0
    await db.commit()
    r = await client.post(f"/api/v2/orders/{oid}/status", json={"status": "done"}, headers=A)
    assert r.status_code == 200 and r.json()["change"]["is_closing"] is True
    assert r.json()["data"]["closed_at"]
    assert len(await _events(db, org_id, "order.closed")) == 1
    # a closed order can't be PATCHed, only reopened
    assert (await client.patch(f"/api/v2/orders/{oid}", json={"priority": 1}, headers=A)).status_code == 409
    assert (await client.post(f"/api/v2/orders/{oid}/status", json={"status": "in_repair"},
                              headers=A)).status_code == 200


@pytest.mark.asyncio
async def test_status_transfer_rule_is_a_409(client, db):
    from backend.models import OrderStatus, StatusTransition
    H, org_id = await _register(client)
    A = await _key(client, H, ["orders:write"])
    _, oid = await _job(client, H)
    o = (await db.execute(select(Order).where(Order.id == oid))).scalar_one()
    sts = {s.key: s for s in (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == org_id, OrderStatus.record_type == "work_order"))).scalars().all()}
    # from the current status only "in_repair" is allowed (for all roles)
    db.add(StatusTransition(org_id=org_id, from_status_id=sts[o.status].id, to_status_id=sts["in_repair"].id))
    await db.commit()
    r = await client.post(f"/api/v2/orders/{oid}/status", json={"status": "done"}, headers=A)
    assert r.status_code == 409 and "can't move" in r.json()["detail"]
    assert (await client.post(f"/api/v2/orders/{oid}/status", json={"status": "in_repair"},
                              headers=A)).status_code == 200


@pytest.mark.asyncio
async def test_dropped_job_write_off_via_api(client, db):
    H, org_id = await _register(client)
    A = await _key(client, H, ["orders:write"])
    cid, oid = await _job(client, H, 100)
    await _pay(client, H, oid, 30)
    db.add(OrderChecklistItem(org_id=org_id, order_id=oid, label="Unfinished", required=True, done=False))
    await db.commit()

    r = await client.post(f"/api/v2/orders/{oid}/status",
                          json={"status": "dropped", "cancel_reason": "Customer declined"}, headers=A)
    assert r.status_code == 200, r.text           # dropping skips the "finish the work" gates
    d = r.json()["data"]
    assert d["written_off"] == pytest.approx(70) and d["cancel_reason"] == "Customer declined"
    # Workstream B's design: the write-off lowers the job's ledger charge (no separate
    # "write_off" line), so the customer owes nothing.
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    await db.refresh(c)
    assert float(c.balance) == pytest.approx(0)
    wo =(await db.execute(select(AuditLog).where(AuditLog.action == "order.write_off",
                                                  AuditLog.entity_id == oid))).scalar_one()
    assert wo.actor_id is None

    r = await client.post(f"/api/v2/orders/{oid}/status", json={"status": "in_repair"}, headers=A)
    assert r.status_code == 200 and r.json()["data"]["written_off"] == 0


@pytest.mark.asyncio
async def test_status_change_idempotency(client, db):
    H, org_id = await _register(client)
    await _hook(db, org_id)
    A = await _key(client, H, ["orders:write"])
    _, oid = await _job(client, H)
    k = {**A, "Idempotency-Key": "status-1"}
    first = await client.post(f"/api/v2/orders/{oid}/status", json={"status": "in_repair"}, headers=k)
    again = await client.post(f"/api/v2/orders/{oid}/status", json={"status": "in_repair"}, headers=k)
    assert first.status_code == again.status_code == 200
    assert again.headers.get("Idempotent-Replayed") == "true" and again.json() == first.json()
    assert len(await _events(db, org_id, "order.status_changed")) == 1
    diff = await client.post(f"/api/v2/orders/{oid}/status", json={"status": "done"}, headers=k)
    assert diff.status_code == 422
    # a failed (gated) change releases its key, so a fixed retry can use it
    k2 = {**A, "Idempotency-Key": "status-2"}
    line = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalar_one()
    line.pending_stock = 1
    await db.commit()
    assert (await client.post(f"/api/v2/orders/{oid}/status", json={"status": "done"}, headers=k2)).status_code == 409
    line.pending_stock = 0
    await db.commit()
    assert (await client.post(f"/api/v2/orders/{oid}/status", json={"status": "done"}, headers=k2)).status_code == 200


# ── invoices ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_invoice_lines_balance_and_payments(client, db):
    from backend.models import Invoice
    H, org_id = await _register(client)
    A = await _key(client, H, ["invoices:read"])
    cid, oid = await _job(client, H, 100)
    await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "part", "description": "Screen", "qty": 2, "unit_price": 25}, headers=H)
    await _pay(client, H, oid, 60)
    inv = (await db.execute(select(Invoice).where(Invoice.order_id == oid))).scalar_one()
    inv.due_at = inv.issued_at

    await db.commit()

    lst = (await client.get("/api/v2/invoices", headers=A)).json()
    assert lst["total"] == 1 and lst["data"][0]["order_ids"] == [oid] and "lines" not in lst["data"][0]
    r = await client.get(f"/api/v2/invoices/{inv.id}", headers=A)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert sorted((ln["description"], ln["total"]) for ln in d["lines"]) == [("Fix", 100), ("Screen", 50)]
    assert d["total"] == 150 and d["amount_paid"] == 60 and d["balance_due"] == 90 and d["credited"] == 0
    assert d["order_ids"] == [oid] and d["due_at"] and d["display_mode"] == "detailed"

    inv.display_mode = "one_line"
    await db.commit()
    (one,) = (await client.get(f"/api/v2/invoices/{inv.id}", headers=A)).json()["data"]["lines"]
    assert one["qty"] == 1

    pays = (await client.get(f"/api/v2/invoices/{inv.id}/payments", headers=A)).json()["data"]
    assert [(p["kind"], p["method"], p["amount"]) for p in pays] == [("payment", "cash", 60)]
    assert set(pays[0]) == {"id", "kind", "method", "amount", "reference", "paid_at", "invoice_id", "order_id"}

    q = lambda s: client.get(f"/api/v2/invoices?{s}", headers=A)  # noqa: E731
    assert (await q(f"client_id={cid}")).json()["total"] == 1
    assert (await q("status=paid")).json()["total"] == 0
    assert (await q(f"status={d['status']}")).json()["total"] == 1
    assert (await q("issued_since=2999-01-01")).json()["total"] == 0
    assert (await q("updated_since=2000-01-01T00:00:00Z")).json()["total"] == 1
    assert (await q("updated_since=2999-01-01")).json()["total"] == 0
    assert (await q("issued_since=nope")).status_code == 422

    # a refund recorded on the job (not the invoice) shows in payments and counts as an update
    from datetime import datetime, timedelta

    from backend.models import Payment
    old = datetime.utcnow() - timedelta(days=30)
    inv.created_at = inv.issued_at = old
    for pay in (await db.execute(select(Payment).where(Payment.invoice_id == inv.id))).scalars().all():
        pay.paid_at = old
    await db.commit()
    recent = (datetime.utcnow() - timedelta(days=1)).isoformat()
    assert (await q(f"updated_since={recent}")).json()["total"] == 0
    db.add(Payment(order_id=oid, client_id=cid, method="cash", kind="refund", amount=10, reference="Overpayment refund"))
    await db.commit()
    assert (await q(f"updated_since={recent}")).json()["total"] == 1
    pays = (await client.get(f"/api/v2/invoices/{inv.id}/payments", headers=A)).json()["data"]
    assert [(p["kind"], p["amount"], p["invoice_id"]) for p in pays] == [("payment", 60, inv.id), ("refund", 10, None)]

    H2, _ = await _register(client)
    B = await _key(client, H2, ["read"])
    for path in (f"/api/v2/invoices/{inv.id}", f"/api/v2/invoices/{inv.id}/payments"):
        assert (await client.get(path, headers=B)).status_code == 404
    assert (await client.get("/api/v2/invoices", headers=B)).json()["total"] == 0


@pytest.mark.asyncio
async def test_order_comment_unlocks_require_comment_status(client, db):
    H, org_id = await _register(client)
    A = await _key(client, H, ["orders:write"])
    R = await _key(client, H, ["orders:read"])
    r = await client.post("/api/statuses", json={"name": "Waiting on customer", "key": "wait_cust",
                                                 "status_group": "in_progress", "require_comment": True}, headers=H)
    assert r.status_code in (200, 201), r.text
    _, oid = await _job(client, H)
    assert (await client.post(f"/api/v2/orders/{oid}/status", json={"status": "wait_cust"}, headers=A)).status_code == 409
    # Read-only keys can't comment.
    assert (await client.post(f"/api/v2/orders/{oid}/comments", json={"body": "x"}, headers=R)).status_code == 403
    r = await client.post(f"/api/v2/orders/{oid}/comments", headers={**A, "Idempotency-Key": "c-1"},
                          json={"body": "Asked the customer to confirm the quote", "visible_to_client": False})
    assert r.status_code == 201, r.text
    assert r.json()["data"]["visible_to_client"] is False
    again = await client.post(f"/api/v2/orders/{oid}/comments", headers={**A, "Idempotency-Key": "c-1"},
                              json={"body": "Asked the customer to confirm the quote", "visible_to_client": False})
    assert again.status_code == 201 and again.headers.get("Idempotent-Replayed") == "true"
    assert again.json()["data"]["id"] == r.json()["data"]["id"]
    assert (await client.post(f"/api/v2/orders/{oid}/comments", json={"body": ""}, headers=A)).status_code == 422
    assert (await client.post(f"/api/v2/orders/{oid}/status", json={"status": "wait_cust"}, headers=A)).status_code == 200
    # Another shop's order is a 404.
    H2, _ = await _register(client)
    _, other = await _job(client, H2)
    assert (await client.post(f"/api/v2/orders/{other}/comments", json={"body": "x"}, headers=A)).status_code == 404
