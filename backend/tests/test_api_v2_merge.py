"""Merge of api/harden-and-docs (v1 hardening, webhook hardening) with claude/ws-c-inquiries
(API v2, resource scopes, per-key limits): v2 carries every v1 protection, and broad and resource
scopes combine as one model."""
import socket
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.config import settings


# ── helpers ──────────────────────────────────────────────────────────────────
async def _register(client):
    email = f"v2m-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["org_id"]


async def _key(client, H, scopes=None, **kw):
    body = {"name": "k", **kw}
    if scopes is not None:
        body["scopes"] = scopes
    r = await client.post("/api/api-keys", json=body, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def _auth(made):
    return {"Authorization": f"Bearer {made['key']}"}


@pytest.fixture
def dns(monkeypatch):
    real = socket.getaddrinfo

    def fake(host, port, *a, **kw):
        if host.endswith("example.com"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return real(host, port, *a, **kw)
    monkeypatch.setattr(socket, "getaddrinfo", fake)


# ── v1 protections on v2 ─────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("qs", ["page=0", "page=10001", "page_size=0", "page_size=101", "page=x"])
async def test_v2_pagination_is_bounded(client, qs):
    H, _ = await _register(client)
    A = _auth(await _key(client, H, ["read"]))
    for path in ("/api/v2/tags", "/api/v2/inquiries", "/api/v2/services", "/api/v2/products",
                 "/api/v2/bundles", "/api/v2/contacts"):
        assert (await client.get(f"{path}?{qs}", headers=A)).status_code == 422, (path, qs)
    r = await client.get("/api/v2/tags?page=10000&page_size=100", headers=A)
    assert r.status_code == 200 and r.json()["page_size"] == 100


@pytest.mark.asyncio
async def test_v2_unknown_fields_and_lengths_refused(client):
    H, _ = await _register(client)
    A = _auth(await _key(client, H, ["write"]))
    bad = [
        ("/api/v2/tags", {"name": "VIP", "colour": "red"}),
        ("/api/v2/tags", {"name": "x" * 41}),
        ("/api/v2/inquiries", {"name": "A", "complain": "typo"}),
        ("/api/v2/inquiries", {"name": "A", "inquiry": "x" * 4001}),
        ("/api/v2/inquiries", {"name": "A", "source": "carrier-pigeon"}),
        ("/api/v2/inquiries", {"name": "A", "custom_data": {f"f{i}": i for i in range(101)}}),
        ("/api/v2/services", {"name": "Diag", "cost": 3}),
        ("/api/v2/products", {"name": "P", "sell_price": -1}),
        ("/api/v2/contacts", {"name": "C", "email": "not-an-email"}),
        ("/api/v2/contacts", {"name": "C", "client_type": "robot"}),
        ("/api/v2/bundles", {"name": "B", "components": [{"ref_type": "part", "ref_id": "x"}]}),
    ]
    for path, body in bad:
        assert (await client.post(path, json=body, headers=A)).status_code == 422, (path, body)


@pytest.mark.asyncio
async def test_v2_other_shops_ids_are_404(client):
    H, _ = await _register(client)
    H2, _ = await _register(client)
    mine = _auth(await _key(client, H, ["write"]))
    theirs = _auth(await _key(client, H2, ["write"]))
    svc = (await client.post("/api/v2/services", json={"name": "Diag"}, headers=mine)).json()["data"]
    prod = (await client.post("/api/v2/products", json={"name": "Battery"}, headers=mine)).json()["data"]
    tag = (await client.post("/api/v2/tags", json={"name": "Mine"}, headers=mine)).json()["data"]
    contact = (await client.post("/api/v2/contacts", json={"name": "Ann"}, headers=mine)).json()["data"]
    for path in (f"/api/v2/services/{svc['id']}", f"/api/v2/products/{prod['id']}", f"/api/v2/contacts/{contact['id']}"):
        assert (await client.get(path, headers=theirs)).status_code == 404, path
    assert (await client.patch(f"/api/v2/tags/{tag['id']}", json={"name": "x"}, headers=theirs)).status_code == 404
    r = await client.post("/api/v2/bundles", json={"name": "Steal", "components": [
        {"ref_type": "product", "ref_id": prod["id"]}]}, headers=theirs)
    assert r.status_code == 404
    r = await client.post("/api/v2/bundles", json={"name": "Steal", "category_id": "nope", "components": [
        {"ref_type": "product", "ref_id": prod["id"]}]}, headers=mine)
    assert r.status_code == 404
    r = await client.post("/api/v2/inquiries", json={"name": "X", "client_id": contact["id"]}, headers=theirs)
    assert r.status_code == 404
    r = await client.post("/api/v2/inquiries", json={"name": "X", "lead_type_id": str(uuid.uuid4())}, headers=theirs)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_v2_cost_fields_need_costs_read(client):
    H, _ = await _register(client)
    plain = _auth(await _key(client, H, ["write"]))
    costs = _auth(await _key(client, H, ["write", "costs:read"]))
    narrow_costs = _auth(await _key(client, H, ["services:read", "products:read", "costs:read"]))
    s = (await client.post("/api/v2/services", json={"name": "Diag", "price": 25, "cost_price": 9}, headers=plain)).json()["data"]
    p = (await client.post("/api/v2/products", json={"name": "Screen", "sell_price": 99, "cost_price": 40},
                           headers=plain)).json()["data"]
    assert "cost_price" not in s and "cost_price" not in p
    for path in (f"/api/v2/services/{s['id']}", f"/api/v2/products/{p['id']}"):
        assert "cost_price" not in (await client.get(path, headers=plain)).json()["data"]
        assert "cost_price" in (await client.get(path, headers=costs)).json()["data"]
        assert "cost_price" in (await client.get(path, headers=narrow_costs)).json()["data"]
    assert all("cost_price" not in x for x in (await client.get("/api/v2/services", headers=plain)).json()["data"])
    assert (await client.get("/api/v2/products", headers=costs)).json()["data"][0]["cost_price"] == 40


@pytest.mark.asyncio
async def test_v2_idempotency_key_on_writes(client):
    H, _ = await _register(client)
    A = _auth(await _key(client, H, ["write"]))
    h = {**A, "Idempotency-Key": "tag-1"}
    a = await client.post("/api/v2/tags", json={"name": "VIP"}, headers=h)
    b = await client.post("/api/v2/tags", json={"name": "VIP"}, headers=h)
    assert a.status_code == b.status_code == 201 and a.json() == b.json()
    assert b.headers["idempotent-replayed"] == "true"
    assert (await client.post("/api/v2/tags", json={"name": "Other"}, headers=h)).status_code == 422
    assert (await client.get("/api/v2/tags", headers=A)).json()["total"] == 1
    # every POST takes it, including convert
    lead = (await client.post("/api/v2/inquiries", json={"name": "Web"}, headers=A)).json()["data"]
    full = _auth(await _key(client, H, ["inquiries:write", "orders:write", "contacts:write"]))
    hc = {**full, "Idempotency-Key": "conv-1"}
    c1 = await client.post(f"/api/v2/inquiries/{lead['id']}/convert", json={}, headers=hc)
    c2 = await client.post(f"/api/v2/inquiries/{lead['id']}/convert", json={}, headers=hc)
    assert c1.status_code == c2.status_code == 200 and c1.json() == c2.json()
    assert c2.headers["idempotent-replayed"] == "true"


@pytest.mark.asyncio
async def test_v2_writes_are_audited_with_the_key(client, db):
    from backend.models import AuditLog
    H, org = await _register(client)
    made = await _key(client, H, ["write"])
    A = _auth(made)
    t = (await client.post("/api/v2/tags", json={"name": "VIP"}, headers=A)).json()["data"]
    await client.patch(f"/api/v2/tags/{t['id']}", json={"name": "VIP+"}, headers=A)
    s = (await client.post("/api/v2/services", json={"name": "Diag"}, headers=A)).json()["data"]
    await client.patch(f"/api/v2/services/{s['id']}", json={"price": 5}, headers=A)
    await client.post("/api/v2/products", json={"name": "P"}, headers=A)
    await client.post("/api/v2/contacts", json={"name": "C"}, headers=A)
    lead = (await client.post("/api/v2/inquiries", json={"name": "L"}, headers=A)).json()["data"]
    await client.patch(f"/api/v2/inquiries/{lead['id']}", json={"status": "in_progress"}, headers=A)
    await client.delete(f"/api/v2/tags/{t['id']}", headers=A)
    rows = (await db.execute(select(AuditLog).where(AuditLog.org_id == org))).scalars().all()
    via = {r.action for r in rows if (r.diff or {}).get("via_api_key") == made["id"]}
    assert {"tag.create", "tag.update", "tag.delete", "service.create", "service.update", "part.create",
            "client.create", "lead.create", "lead.update"} <= via


@pytest.mark.asyncio
async def test_v2_no_cors_and_https_only(client, monkeypatch):
    H, _ = await _register(client)
    A = _auth(await _key(client, H))
    origin = {"Origin": "https://evil.example"}
    r = await client.get("/api/v2/tags", headers={**A, **origin})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers
    pre = await client.options("/api/v2/tags", headers={
        **origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"})
    assert "access-control-allow-origin" not in pre.headers
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    r = await client.get("/api/v2/openapi.json")
    assert r.status_code == 403 and "HTTPS" in r.text
    from httpx import ASGITransport, AsyncClient

    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as s:
        assert (await s.get("/api/v2/openapi.json")).status_code == 200


@pytest.mark.asyncio
async def test_v2_openapi_is_public_and_v2_only(client):
    r = await client.get("/api/v2/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/api/v2/inquiries" in paths and "/api/v2/webhooks" in paths and "/api/v2/inquiries/{lid}/convert" in paths
    assert all(p == "/api/v2" or p.startswith("/api/v2/") for p in paths)
    assert "/api/v2/openapi.json" not in paths
    for p, ops in paths.items():
        for method, op in ops.items():
            assert op["operationId"].startswith("v2_") and op.get("security"), (method, p)
    assert any(prm["name"] == "Idempotency-Key" for prm in paths["/api/v2/inquiries"]["post"]["parameters"])
    assert (await client.get("/api/v2/docs", follow_redirects=False)).headers["location"].endswith("/api/v2/openapi.json")


# ── combined scopes ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_resource_key_cannot_use_v1(client):
    H, _ = await _register(client)
    for scopes in (["inquiries:read"], ["contacts:write"], ["orders:write", "contacts:write"], ["costs:read"]):
        A = _auth(await _key(client, H, scopes))
        assert (await client.get("/api/v1/contacts", headers=A)).status_code == 403, scopes
        assert (await client.post("/api/v1/contacts", json={"name": "x"}, headers=A)).status_code == 403, scopes
        assert (await client.get("/api/v1", headers=A)).status_code == 403, scopes


@pytest.mark.asyncio
async def test_resource_key_reaches_only_its_resource(client):
    H, _ = await _register(client)
    A = _auth(await _key(client, H, ["tags:write"]))
    assert (await client.get("/api/v2/tags", headers=A)).status_code == 200          # write implies read
    assert (await client.post("/api/v2/tags", json={"name": "T"}, headers=A)).status_code == 201
    for path in ("/api/v2/contacts", "/api/v2/inquiries", "/api/v2/services", "/api/v2/products",
                 "/api/v2/bundles", "/api/v2/webhooks", "/api/v2/inquiry-types"):
        assert (await client.get(path, headers=A)).status_code == 403, path
    assert (await client.post("/api/v2/services", json={"name": "S"}, headers=A)).status_code == 403
    d = (await client.get("/api/v2", headers=A)).json()["data"]          # discovery works for any key
    assert d["scopes"] == ["tags:write"] and d["openapi"] == "/api/v2/openapi.json"
    R = _auth(await _key(client, H, ["tags:read"]))
    assert (await client.post("/api/v2/tags", json={"name": "U"}, headers=R)).status_code == 403


@pytest.mark.asyncio
async def test_webhooks_need_explicit_webhook_scopes(client, dns):
    H, _ = await _register(client)
    for scopes in (["read"], ["write"], ["*"], ["read", "write", "costs:read"], ["inquiries:write"]):
        A = _auth(await _key(client, H, scopes))
        assert (await client.get("/api/v2/webhooks", headers=A)).status_code == 403, scopes
        assert (await client.post("/api/v2/webhooks", json={"url": "https://hooks.example.com/a"},
                                  headers=A)).status_code == 403, scopes
    RO = _auth(await _key(client, H, ["webhooks:read"]))
    assert (await client.get("/api/v2/webhooks", headers=RO)).status_code == 200
    assert (await client.post("/api/v2/webhooks", json={"url": "https://hooks.example.com/a"},
                              headers=RO)).status_code == 403
    WR = _auth(await _key(client, H, ["webhooks:write"]))
    assert (await client.get("/api/v2/webhooks", headers=WR)).status_code == 200      # write implies read
    assert (await client.post("/api/v2/webhooks", json={"url": "https://hooks.example.com/a"},
                              headers=WR)).status_code == 201


@pytest.mark.asyncio
async def test_new_keys_are_read_only_by_default(client):
    H, _ = await _register(client)
    made = await _key(client, H)
    assert made["scopes"] == "read"
    A = _auth(made)
    assert (await client.get("/api/v2/tags", headers=A)).status_code == 200
    assert (await client.get("/api/v1/contacts", headers=A)).status_code == 200
    assert (await client.post("/api/v2/tags", json={"name": "T"}, headers=A)).status_code == 403
    assert (await client.post("/api/v2/inquiries", json={"name": "I"}, headers=A)).status_code == 403
    assert (await client.post("/api/v1/contacts", json={"name": "C"}, headers=A)).status_code == 403


@pytest.mark.asyncio
async def test_scope_validation_and_storage(client):
    H, _ = await _register(client)
    for bad in (["inquiries:delete"], ["webhook:read"], ["read", "admin"], "costs:write", []):
        r = await client.post("/api/api-keys", json={"name": "x", "scopes": bad}, headers=H)
        assert r.status_code == 422, bad
    made = await _key(client, H, "*,tags:write")
    assert made["scopes"] == "read,tags:write,write" and made["rate_limit_per_min"] is None
    assert made["effective_rate_limit_per_min"] == 120
    listing = (await client.get("/api/api-keys", headers=H)).json()
    assert "inquiries:write" in listing["resource_scopes"] and listing["explicit_only"] == ["webhooks"]
    assert (await client.post("/api/api-keys", json={"name": "x", "rate_limit_per_min": 6001}, headers=H)).status_code == 422


@pytest.mark.asyncio
async def test_costs_scope_with_resource_keys_needs_cost_permission(client, db):
    from backend.models import Role
    H, org = await _register(client)
    role = Role(org_id=org, name="nocost", permissions={"can_manage_settings": True, "can_view_purchase_price": False})
    db.add(role)
    await db.commit()
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    assert (await client.post("/api/staff/employees", json={"name": "T", "email": email, "password": "pw-test-12345",
                                                           "role_id": role.id}, headers=H)).status_code == 201
    T = {"Authorization": "Bearer " + (await client.post("/api/auth/token", data={
        "username": email, "password": "pw-test-12345"})).json()["access_token"]}
    assert (await client.post("/api/api-keys", json={"name": "x", "scopes": ["products:read", "costs:read"]},
                              headers=T)).status_code == 403
    assert (await client.post("/api/api-keys", json={"name": "x", "scopes": ["products:read"]},
                              headers=T)).status_code == 201


@pytest.mark.asyncio
async def test_expiry_rotation_and_cap_apply_to_resource_keys(client, db):
    from backend.models import ApiKey
    from backend.services import apikeys as ak
    H, org = await _register(client)
    made = await _key(client, H, ["tags:read"], expires_in_days=1)
    A = _auth(made)
    assert (await client.get("/api/v2/tags", headers=A)).status_code == 200
    rotated = (await client.post(f"/api/api-keys/{made['id']}/rotate", headers=H)).json()
    assert rotated["scopes"] == "tags:read"
    assert (await client.get("/api/v2/tags", headers=A)).status_code == 200            # 24h overlap
    assert (await client.get("/api/v2/tags", headers=_auth(rotated))).status_code == 200
    row = (await db.execute(select(ApiKey).where(ApiKey.id == made["id"]))).scalar_one()
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    await db.commit()
    assert (await client.get("/api/v2/tags", headers=_auth(rotated))).status_code == 401
    for _ in range(ak.MAX_ACTIVE_KEYS):
        await client.post("/api/api-keys", json={"name": "n", "scopes": ["inquiries:read"]}, headers=H)
    assert (await client.post("/api/api-keys", json={"name": "n", "scopes": ["inquiries:read"]},
                              headers=H)).status_code == 409


# ── rate limits ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rate_headers_report_the_binding_limit(client, monkeypatch):
    from backend.core import observability
    observability._KEY_CACHE.clear()
    H, _ = await _register(client)
    A = _auth(await _key(client, H, ["read"], rate_limit_per_min=4))
    r1 = await client.get("/api/v2/tags", headers=A)
    assert r1.headers["x-ratelimit-limit"] == "4" and r1.headers["x-ratelimit-remaining"] == "3"
    r2 = await client.get("/api/v1/contacts", headers=A)               # one budget across v1 and v2
    assert r2.headers["x-ratelimit-limit"] == "4" and r2.headers["x-ratelimit-remaining"] == "2"
    await client.get("/api/v2/tags", headers=A)
    await client.get("/api/v2/tags", headers=A)
    r5 = await client.get("/api/v2/tags", headers=A)
    assert r5.status_code == 429 and r5.headers["x-ratelimit-limit"] == "4"
    assert r5.headers["x-ratelimit-remaining"] == "0" and int(r5.headers["retry-after"]) >= 1
    # when the shop-wide ceiling is the tighter one, the headers report it instead
    B = _auth(await _key(client, H, ["read"], rate_limit_per_min=1000))
    monkeypatch.setattr(settings, "RATE_LIMIT_USER_PER_MIN", 3)
    r = await client.get("/api/v2/tags", headers=B)
    assert r.headers["x-ratelimit-limit"] == "3" and r.headers["x-ratelimit-remaining"] == "2"


# ── webhooks through v2 use the webhook hardening rules ──────────────────────
@pytest.mark.asyncio
async def test_v2_webhooks_follow_the_settings_rules(client, db, dns, monkeypatch):
    from backend.models import WebhookEndpoint
    from backend.services import webhooks as wh
    H, org = await _register(client)
    W = _auth(await _key(client, H, ["webhooks:write"]))
    h = {**W, "Idempotency-Key": "hook-1"}
    body = {"url": "https://hooks.example.com/in", "events": ["inquiry.created", "lead.created"]}
    r = await client.post("/api/v2/webhooks", json=body, headers=h)
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["secret"].startswith("whsec_") and d["secret_hint"].endswith(d["secret"][-4:])
    replay = await client.post("/api/v2/webhooks", json=body, headers=h)
    assert replay.headers["idempotent-replayed"] == "true" and "secret" not in replay.json()["data"]
    row = (await db.execute(select(WebhookEndpoint).where(WebhookEndpoint.id == d["id"]))).scalar_one()
    assert wh.reveal_secret(row.secret) == d["secret"]
    listed = (await client.get("/api/v2/webhooks", headers=W)).json()
    assert all("secret" not in e for e in listed["data"]) and "inquiry.created" in listed["events"]
    assert listed["aliases"]["lead.created"] == "inquiry.created"
    assert (await client.post("/api/v2/webhooks", json={"url": "https://hooks.example.com/x", "events": "order.exploded"},
                              headers=W)).status_code == 422
    for bad in ("http://127.0.0.1/hook", "https://169.254.169.254/latest", "https://user:pw@hooks.example.com/x"):
        assert (await client.post("/api/v2/webhooks", json={"url": bad}, headers=W)).status_code == 400, bad
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    from httpx import ASGITransport, AsyncClient

    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as s:
        r = await s.post("/api/v2/webhooks", json={"url": "http://hooks.example.com/plain"}, headers=W)
        assert r.status_code == 400
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    for i in range(wh.MAX_ENDPOINTS_PER_ORG - 1):
        assert (await client.post("/api/v2/webhooks", json={"url": f"https://hooks.example.com/{i}"},
                                  headers=W)).status_code == 201
    assert (await client.post("/api/v2/webhooks", json={"url": "https://hooks.example.com/over"},
                              headers=W)).status_code == 409
    assert (await client.delete(f"/api/v2/webhooks/{d['id']}", headers=W)).status_code == 200
    await db.refresh(row)
    assert row.deleted_at is not None and row.active is False            # soft delete
    assert (await client.delete(f"/api/v2/webhooks/{d['id']}", headers=W)).status_code == 404


@pytest.mark.asyncio
async def test_inquiry_created_fires_once_for_new_and_old_names(client, db):
    from backend.models import WebhookDelivery, WebhookEndpoint
    H, org = await _register(client)
    db.add(WebhookEndpoint(org_id=org, url="https://hooks.example.com/new", events="inquiry.created", active=True))
    db.add(WebhookEndpoint(org_id=org, url="https://hooks.example.com/old", events="lead.created", active=True))
    db.add(WebhookEndpoint(org_id=org, url="https://hooks.example.com/all", events="*", active=True))
    await db.commit()
    A = _auth(await _key(client, H, ["inquiries:write"]))
    assert (await client.post("/api/v2/inquiries", json={"name": "Web form", "device_model": "Pixel 8"},
                              headers=A)).status_code == 201
    await client.post("/api/leads", json={"name": "Staff made"}, headers=H)
    rows = (await db.execute(select(WebhookDelivery).where(WebhookDelivery.org_id == org))).scalars().all()
    names = sorted(r.event for r in rows)
    assert names == sorted(["inquiry.created", "lead.created", "inquiry.created"] * 2), names
    import json
    payload = json.loads(next(r for r in rows if r.event == "inquiry.created").payload)
    assert payload["data"]["name"] in ("Web form", "Staff made") and "device_model" in payload["data"]
