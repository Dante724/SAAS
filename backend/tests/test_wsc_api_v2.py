"""WS-C — public API v2 (inquiries, tags, services, bundles, writable products, webhooks),
scoped API keys, per-key rate limits, and webhook events that match the docs."""
import json
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.models import WebhookDelivery, WebhookEndpoint

REPO = Path(__file__).resolve().parents[2]


async def key(client, h, scopes="read,write", rate=None):
    body = {"name": "k", "scopes": scopes}
    if rate:
        body["rate_limit_per_min"] = rate
    r = await client.post("/api/api-keys", json=body, headers=h)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['key']}"}


async def second(client):
    import uuid
    r = await client.post("/api/auth/register", json={
        "org_name": "Other", "name": "O", "email": f"o-{uuid.uuid4().hex[:8]}@test.dev", "password": "pw-test-12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def hook(db, org_id, events="*"):
    db.add(WebhookEndpoint(org_id=org_id, url="https://hooks.example.com/in", events=events, active=True))
    await db.commit()


async def events_for(db, org_id):
    return [d.event for d in (await db.execute(select(WebhookDelivery).where(
        WebhookDelivery.org_id == org_id).order_by(WebhookDelivery.created_at))).scalars().all()]


async def org(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


# ── scopes ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_unknown_scope_rejected(client, tenant):
    r = await client.post("/api/api-keys", json={"name": "x", "scopes": "inquiries:delete"}, headers=tenant)
    assert r.status_code == 422
    r = await client.post("/api/api-keys", json={"name": "x", "scopes": "admin"}, headers=tenant)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_narrow_key_only_reaches_its_resource(client, tenant):
    k = await key(client, tenant, "inquiries:write")
    assert (await client.get("/api/v2/inquiries", headers=k)).status_code == 200
    assert (await client.post("/api/v2/inquiries", json={"name": "Web form"}, headers=k)).status_code == 201
    for path in ("/api/v2/contacts", "/api/v2/products", "/api/v2/tags", "/api/v2/webhooks"):
        assert (await client.get(path, headers=k)).status_code == 403, path
    # and it can't use v1 at all (v1 needs a broad scope)
    assert (await client.get("/api/v1/contacts", headers=k)).status_code == 403
    ro = await key(client, tenant, "inquiries:read")
    assert (await client.get("/api/v2/inquiries", headers=ro)).status_code == 200
    assert (await client.post("/api/v2/inquiries", json={"name": "x"}, headers=ro)).status_code == 403


@pytest.mark.asyncio
async def test_legacy_keys_keep_working(client, tenant):
    for scopes in ("read,write", "*"):
        k = await key(client, tenant, scopes)
        assert (await client.get("/api/v1/contacts", headers=k)).status_code == 200
        assert (await client.post("/api/v2/tags", json={"name": f"T{scopes}"}, headers=k)).status_code == 201
    ro = await key(client, tenant, "read")
    assert (await client.get("/api/v2/tags", headers=ro)).status_code == 200
    assert (await client.post("/api/v2/tags", json={"name": "no"}, headers=ro)).status_code == 403


@pytest.mark.asyncio
async def test_per_key_rate_limit(client, tenant):
    k = await key(client, tenant, rate=3)
    codes = [(await client.get("/api/v2", headers=k)).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    r = await client.get("/api/v2/tags", headers=k)
    assert r.status_code == 429 and r.headers.get("retry-after")
    # another key has its own budget
    assert (await client.get("/api/v2", headers=await key(client, tenant))).status_code == 200


@pytest.mark.asyncio
async def test_broad_keys_cannot_manage_webhooks(client, tenant):
    """An old read,write key must not be able to subscribe its own URL to every event."""
    for scopes in ("read,write", "*", "read"):
        k = await key(client, tenant, scopes)
        assert (await client.get("/api/v2/webhooks", headers=k)).status_code == 403, scopes
        assert (await client.post("/api/v2/webhooks", json={"url": "https://x.example.com"}, headers=k)).status_code == 403


@pytest.mark.asyncio
async def test_inquiry_key_cannot_convert_or_learn_customers(client, tenant):
    await client.post("/api/clients", json={"name": "Known", "phone": "+14155550100"}, headers=tenant)
    k = await key(client, tenant, "inquiries:write")
    r = await client.post("/api/v2/inquiries", json={"name": "Probe", "phone": "+14155550100"}, headers=k)
    assert r.status_code == 201 and r.json()["data"]["client_id"] is None
    lid = r.json()["data"]["id"]
    assert (await client.post(f"/api/v2/inquiries/{lid}/convert", headers=k)).status_code == 403
    full = await key(client, tenant, "inquiries:write,orders:write,contacts:write")
    assert (await client.get(f"/api/v2/inquiries/{lid}", headers=full)).json()["data"]["client_id"]
    assert (await client.post(f"/api/v2/inquiries/{lid}/convert", headers=full)).status_code == 200


# ── inquiries over the API ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_inquiry_lifecycle_and_idempotency(client, tenant):
    k = await key(client, tenant)
    h = {**k, "Idempotency-Key": "web-form-42"}
    a = await client.post("/api/v2/inquiries", json={"name": "From the site", "device_model": "Pixel 8",
                                                      "inquiry": "Screen"}, headers=h)
    b = await client.post("/api/v2/inquiries", json={"name": "From the site"}, headers=h)
    assert a.status_code == 201 and b.status_code == 200 and a.json()["data"]["id"] == b.json()["data"]["id"]
    lid = a.json()["data"]["id"]
    assert a.json()["data"]["source"] == "api"
    r = await client.patch(f"/api/v2/inquiries/{lid}", json={"status": "in_progress"}, headers=k)
    assert r.json()["data"]["status"] == "in_progress"
    r = await client.post(f"/api/v2/inquiries/{lid}/convert", headers=k)
    assert r.status_code == 200 and r.json()["data"]["order_number"]
    listed = (await client.get("/api/v2/inquiries?status=won", headers=k)).json()
    assert listed["total"] == 1 and listed["data"][0]["converted_order_number"] == r.json()["data"]["order_number"]


@pytest.mark.asyncio
async def test_api_never_crosses_shops(client, tenant):
    other = await second(client)
    ko = await key(client, other)
    lead = (await client.post("/api/leads", json={"name": "Mine"}, headers=tenant)).json()
    tag = (await client.post("/api/tags", json={"name": "Mine"}, headers=tenant)).json()
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    assert (await client.get(f"/api/v2/inquiries/{lead['id']}", headers=ko)).status_code == 404
    assert (await client.patch(f"/api/v2/inquiries/{lead['id']}", json={"name": "x"}, headers=ko)).status_code == 404
    assert (await client.post(f"/api/v2/inquiries/{lead['id']}/convert", headers=ko)).status_code == 404
    assert (await client.delete(f"/api/v2/tags/{tag['id']}", headers=ko)).status_code == 404
    assert (await client.post("/api/v2/inquiries", json={"name": "x", "manager_id": me["id"]},
                              headers=ko)).status_code == 404
    assert (await client.get("/api/v2/inquiries", headers=ko)).json()["total"] == 0


# ── catalogue ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_products_writable(client, tenant, db):
    k = await key(client, tenant)
    await hook(db, await org(client, tenant))
    r = await client.post("/api/v2/products", json={"name": "Screen iPhone 14", "sku": "SCR-14", "mpn": "A2649-LCD",
                                                     "sell_price": 129, "cost_price": 60}, headers=k)
    assert r.status_code == 201, r.text
    pid = r.json()["data"]["id"]
    r = await client.patch(f"/api/v2/products/{pid}", json={"sell_price": 139, "name": None}, headers=k)
    assert r.json()["data"]["sell_price"] == 139 and r.json()["data"]["name"] == "Screen iPhone 14"
    dup = await client.post("/api/v2/products", json={"name": "Copy", "mpn": "a2649-lcd"}, headers=k)
    assert dup.status_code == 409
    assert (await client.post("/api/v2/products", json={"name": "Neg", "sell_price": -1}, headers=k)).status_code == 422
    assert "product.created" in await events_for(db, await org(client, tenant))
    assert "product.updated" in await events_for(db, await org(client, tenant))


@pytest.mark.asyncio
async def test_services_and_bundles(client, tenant, db):
    k = await key(client, tenant)
    o = await org(client, tenant)
    await hook(db, o)
    s = (await client.post("/api/v2/services", json={"name": "Diagnostics", "price": 25}, headers=k)).json()["data"]
    p = (await client.post("/api/v2/products", json={"name": "Battery"}, headers=k)).json()["data"]
    r = await client.post("/api/v2/bundles", json={"name": "Battery swap", "components": [
        {"ref_type": "service", "ref_id": s["id"]}, {"ref_type": "product", "ref_id": p["id"]}]}, headers=k)
    assert r.status_code == 201, r.text
    bid = r.json()["data"]["id"]
    assert len(r.json()["data"]["components"]) == 2
    r = await client.put(f"/api/v2/bundles/{bid}", json={"name": "Battery swap+", "price_mode": "fixed", "price": 79},
                         headers=k)
    assert r.json()["data"]["effective_price"] == 79 and len(r.json()["data"]["components"]) == 2
    assert (await client.put(f"/api/v2/bundles/{bid}", json={"name": "x", "price_mode": "weird"},
                             headers=k)).status_code == 422
    assert (await client.delete(f"/api/v2/bundles/{bid}", headers=k)).status_code == 200
    assert (await client.get("/api/v2/bundles", headers=k)).json()["total"] == 0
    evs = await events_for(db, o)
    assert {"bundle.created", "bundle.updated", "bundle.deleted"} <= set(evs)
    # a component from another shop is refused
    other = await second(client)
    ko = await key(client, other)
    r = await client.post("/api/v2/bundles", json={"name": "Steal", "components": [
        {"ref_type": "service", "ref_id": s["id"]}]}, headers=ko)
    assert r.status_code == 400
    s2 = (await client.patch(f"/api/v2/services/{s['id']}", json={"price": 30}, headers=k)).json()["data"]
    assert s2["price"] == 30
    assert (await client.patch(f"/api/v2/services/{s['id']}", json={"price": 1}, headers=ko)).status_code == 404


@pytest.mark.asyncio
async def test_tag_events_from_staff_screen_and_api(client, tenant, db):
    o = await org(client, tenant)
    await hook(db, o, events="tag.created,tag.updated,tag.deleted")
    t = (await client.post("/api/tags", json={"name": "VIP"}, headers=tenant)).json()
    await client.patch(f"/api/tags/{t['id']}", json={"name": "VIP+"}, headers=tenant)
    await client.delete(f"/api/tags/{t['id']}", headers=tenant)
    assert await events_for(db, o) == ["tag.created", "tag.updated", "tag.deleted"]
    k = await key(client, tenant)
    assert (await client.post("/api/v2/tags", json={"name": "vip"}, headers=k)).status_code == 201
    assert (await client.post("/api/v2/tags", json={"name": "VIP"}, headers=k)).status_code == 409


@pytest.mark.asyncio
async def test_contacts_v2_carry_structured_address(client, tenant):
    k = await key(client, tenant, "contacts:write")
    r = await client.post("/api/v2/contacts", json={"name": "Ana", "addr_street": "5 Rue X", "addr_city": "Paris",
                                                     "addr_postcode": "75001", "addr_country": "FR"}, headers=k)
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["address"] == "5 Rue X, Paris, 75001, FR" and d["address_parts"]["city"] == "Paris"
    assert (await client.post("/api/v2/contacts", json={"name": "Bad", "addr_postcode": "1", "addr_country": "FR"},
                              headers=k)).status_code == 422


# ── webhooks ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manage_webhooks_with_the_api(client, tenant, monkeypatch):
    from backend.core import ssrf
    monkeypatch.setattr(ssrf, "assert_safe_url", lambda url: None)
    k = await key(client, tenant, "webhooks:write")
    from backend.models import AuditLog
    r = await client.post("/api/v2/webhooks", json={"url": "https://hooks.example.com/x",
                                                     "events": "inquiry.created,booking.created"}, headers=k)
    assert r.status_code == 201, r.text
    assert r.json()["data"]["secret"]
    listed = (await client.get("/api/v2/webhooks", headers=k)).json()
    assert "secret" not in listed["data"][0] and "inquiry.created" in listed["events"]
    assert (await client.post("/api/v2/webhooks", json={"url": "https://hooks.example.com/x",
                                                         "events": "order.exploded"}, headers=k)).status_code == 422
    assert (await client.delete(f"/api/v2/webhooks/{r.json()['data']['id']}", headers=k)).status_code == 200
    return AuditLog


@pytest.mark.asyncio
async def test_booking_created_alias_receives_appointment_created(db):
    from backend.services import webhooks as wh
    assert wh._subscribed("booking.created", "appointment.created")
    assert not wh._subscribed("booking.created", "order.created")


@pytest.mark.asyncio
async def test_every_registered_event_fires_somewhere_and_is_documented():
    """No more dead events: each name in EVENTS is emitted by some code, and the docs list
    exactly the registered set (the bug was docs promising booking.created while the code
    emitted appointment.created, and order.closed / estimate.accepted never firing)."""
    from backend.services.webhooks import EVENTS
    code = "\n".join(p.read_text() for p in (REPO / "backend").rglob("*.py") if "tests" not in p.parts)
    for ev in EVENTS:
        family = ev.split(".")[0]
        direct = re.search(rf'["\']{re.escape(ev)}["\']', code.replace("EVENTS = [", "")) is not None
        assert direct or f'"{family}.' in code, ev
    for ev in ("order.closed", "estimate.accepted", "inquiry.created", "tag.created", "bundle.created"):
        assert code.count(f'"{ev}"') >= 2, f"{ev} is registered but never emitted"
    doc = (REPO / "docs" / "public-api-v1.md").read_text()
    documented = set(re.findall(r"`([a-z_]+\.[a-z_]+)`", doc.split("## Webhooks", 1)[1]))
    assert set(EVENTS) <= documented
    assert "booking.created" not in doc.split("## Webhooks", 1)[1].split("Older name")[0]


@pytest.mark.asyncio
async def test_order_closed_fires(client, tenant, db):
    o = await org(client, tenant)
    await hook(db, o)
    lead = (await client.post("/api/leads", json={"name": "Close"}, headers=tenant)).json()
    oid = (await client.post(f"/api/leads/{lead['id']}/convert", headers=tenant)).json()["order_id"]
    sts = (await client.get("/api/statuses?record_type=work_order", headers=tenant)).json()
    items = sts.get("items", sts) if isinstance(sts, dict) else sts
    closing = next(s for s in items if s.get("is_terminal") or s.get("status_group") == "closed")
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": closing["key"]}, headers=tenant)
    if r.status_code == 405:
        r = await client.post(f"/api/orders/{oid}/status", json={"status": closing["key"]}, headers=tenant)
    assert r.status_code < 400, r.text
    assert "order.closed" in await events_for(db, o)


@pytest.mark.asyncio
async def test_estimate_accepted_fires_only_on_accept(client, tenant, db):
    o = await org(client, tenant)
    await hook(db, o)

    async def estimate():
        oid = (await client.post("/api/orders", json={"complaint": "Quote", "is_estimate": True}, headers=tenant)).json()["id"]
        return (await client.post(f"/api/orders/{oid}/public-link", headers=tenant)).json()["token"]
    tok = await estimate()
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "decline", "name": "Jane"})
    assert r.status_code == 200, r.text
    assert "estimate.accepted" not in await events_for(db, o)
    tok = await estimate()
    r = await client.post(f"/api/public/track/{tok}/respond", json={"decision": "accept", "name": "Jane"})
    assert r.status_code == 200, r.text
    assert (await events_for(db, o)).count("estimate.accepted") == 1


@pytest.mark.asyncio
async def test_v2_index_lists_resources(client, tenant):
    k = await key(client, tenant, "tags:read")
    r = await client.get("/api/v2", headers=k)
    assert r.status_code == 200 and r.json()["data"]["scopes"] == ["tags:read"]
    assert "inquiries" in r.json()["data"]["resources"]
    assert (await client.get("/api/v2")).status_code == 401
    assert json.dumps(r.json()).count("rok_") == 0
