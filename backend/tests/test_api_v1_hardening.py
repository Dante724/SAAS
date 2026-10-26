"""Public API v1 + API keys hardening (apih1). One or more regression tests per finding A1–A15
in the Oct 2026 API / webhook security review."""
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.config import settings


# ── helpers ──────────────────────────────────────────────────────────────────
async def _register(client):
    email = f"v1h-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Co", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    body = r.json()
    H = {"Authorization": f"Bearer {body['access_token']}"}
    me = (await client.get("/api/auth/me", headers=H)).json()
    return H, body["org_id"], me["id"]


async def _key(client, H, scopes=("read", "write"), **kw):
    r = await client.post("/api/api-keys", json={"name": "zap", "scopes": list(scopes), **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def _auth(made):
    return {"Authorization": f"Bearer {made['key']}"}


async def _location(db, org_id):
    from backend.models import Location
    return (await db.execute(select(Location.id).where(Location.org_id == org_id))).scalars().first()


async def _teammate(client, H, db, org_id, perms):
    from backend.models import Role
    role = Role(org_id=org_id, name=f"r-{uuid.uuid4().hex[:4]}", permissions=perms)
    db.add(role)
    await db.commit()
    email = f"tm-{uuid.uuid4().hex[:8]}@t.dev"
    r = await client.post("/api/staff/employees", json={
        "name": "Teammate", "email": email, "password": "pw-test-12345", "role_id": role.id}, headers=H)
    assert r.status_code == 201, r.text
    r = await client.post("/api/auth/token", data={"username": email, "password": "pw-test-12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _key_row(db, key_id):
    from backend.models import ApiKey
    return (await db.execute(select(ApiKey).where(ApiKey.id == key_id)
                             .execution_options(populate_existing=True))).scalar_one()


# ── A1: pagination bounds ────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/contacts", "/api/v1/orders", "/api/v1/estimates", "/api/v1/products",
                                  "/api/v1/appointments", "/api/v1/invoices", "/api/v1/credit-notes",
                                  "/api/v1/time-entries"])
async def test_a1_page_bounds_on_every_list(client, path):
    H, _, _ = await _register(client)
    A = _auth(await _key(client, H, ["read"]))
    for qs in ("page_size=-1", "page_size=0", "page_size=101", "page_size=100000", "page=0", "page=-3",
               "page=10001"):
        r = await client.get(f"{path}?{qs}", headers=A)
        assert r.status_code == 422, (path, qs, r.status_code)
    r = await client.get(f"{path}?page=10000&page_size=100", headers=A)
    assert r.status_code == 200 and r.json()["page_size"] == 100


def test_a1_list_helper_clamps():
    from backend.routers.v1 import _bounds
    assert _bounds(-5, -1) == (1, 1)
    assert _bounds(0, 10_000) == (1, 100)
    assert _bounds(99_999, 25) == (10_000, 25)


# ── A2: cost fields hidden unless costs:read ─────────────────────────────────
@pytest.mark.asyncio
async def test_a2_cost_fields_hidden_without_costs_scope(client, db):
    H, org, me = await _register(client)
    p = (await client.post("/api/catalog/products", json={"name": "Screen", "sell_price": 99, "cost_price": 40},
                           headers=H)).json()
    plain = _auth(await _key(client, H, ["read", "write"]))
    one = (await client.get(f"/api/v1/products/{p['id']}", headers=plain)).json()["data"]
    assert "cost_price" not in one and one["sell_price"] == 99
    listed = (await client.get("/api/v1/products", headers=plain)).json()["data"]
    assert listed and all("cost_price" not in x for x in listed)

    r = await client.post("/api/v1/time-entries", headers=plain, json={
        "employee_id": me, "started_at": "2026-10-20T09:00:00", "duration_min": 30})
    assert r.status_code == 201, r.text
    assert "cost_rate" not in r.json()["data"] and "billable_rate" not in r.json()["data"]
    te = (await client.get("/api/v1/time-entries", headers=plain)).json()["data"][0]
    assert "cost_rate" not in te and "billable_rate" not in te

    costs = _auth(await _key(client, H, ["read", "costs:read"]))
    assert (await client.get(f"/api/v1/products/{p['id']}", headers=costs)).json()["data"]["cost_price"] == 40
    te = (await client.get("/api/v1/time-entries", headers=costs)).json()["data"][0]
    assert "cost_rate" in te and "billable_rate" in te


@pytest.mark.asyncio
async def test_a2_only_cost_viewers_can_mint_costs_scope(client, db):
    H, org, _ = await _register(client)
    T = await _teammate(client, H, db, org, {"can_view_purchase_price": False})
    r = await client.post("/api/api-keys", json={"name": "x", "scopes": ["read", "costs:read"]}, headers=T)
    assert r.status_code == 403
    r = await client.post("/api/api-keys", json={"name": "x", "scopes": ["read"]}, headers=T)
    assert r.status_code == 201


# ── A3: scopes ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["admin", "", [], ["admin"], ["read", "delete"], "read,superuser", ["*admin*"]])
async def test_a3_unknown_or_empty_scopes_refused(client, bad):
    H, _, _ = await _register(client)
    r = await client.post("/api/api-keys", json={"name": "x", "scopes": bad}, headers=H)
    assert r.status_code == 422, (bad, r.text)


@pytest.mark.asyncio
async def test_a3_default_scope_is_read_only_and_enforced(client, db):
    H, org, _ = await _register(client)
    made = (await client.post("/api/api-keys", json={"name": "x"}, headers=H)).json()
    assert made["scopes"] == "read" and made["scope_list"] == ["read"]
    A = _auth(made)
    assert (await client.get("/api/v1/contacts", headers=A)).status_code == 200
    r = await client.post("/api/v1/contacts", json={"name": "Ann"}, headers=A)
    assert r.status_code == 403 and "write" in r.text


@pytest.mark.asyncio
async def test_a3_write_implies_read_and_legacy_scopes(client, db):
    H, org, _ = await _register(client)
    W = _auth(await _key(client, H, ["write"]))
    assert (await client.get("/api/v1/contacts", headers=W)).status_code == 200
    assert (await client.post("/api/v1/contacts", json={"name": "Ann"}, headers=W)).status_code == 201

    # The legacy comma string and "*" are still accepted on create; "*" becomes read + write.
    legacy = (await client.post("/api/api-keys", json={"name": "x", "scopes": "*"}, headers=H)).json()
    assert legacy["scopes"] == "read,write"

    # A key stored before apih1 with no scopes can no longer read; a stored "*" reads and writes
    # but never sees costs.
    empty = await _key(client, H, ["read"])
    star = await _key(client, H, ["read"])
    (await _key_row(db, empty["id"])).scopes = ""
    (await _key_row(db, star["id"])).scopes = "*"
    await db.commit()
    assert (await client.get("/api/v1/contacts", headers=_auth(empty))).status_code == 403
    assert (await client.post("/api/v1/contacts", json={"name": "Bo"}, headers=_auth(star))).status_code == 201
    p = (await client.post("/api/catalog/products", json={"name": "S", "sell_price": 1, "cost_price": 1},
                           headers=H)).json()
    assert "cost_price" not in (await client.get(f"/api/v1/products/{p['id']}", headers=_auth(star))).json()["data"]


# ── A4: lifecycle — expiry, cap, rotation, creator flag ──────────────────────
@pytest.mark.asyncio
async def test_a4_default_expiry_and_expired_key_rejected(client, db):
    H, _, _ = await _register(client)
    made = await _key(client, H)
    exp = datetime.fromisoformat(made["expires_at"])
    assert timedelta(days=364) < exp - datetime.utcnow() <= timedelta(days=365)
    never = await _key(client, H, expires_in_days=None)
    assert never["expires_at"] is None
    assert (await client.post("/api/api-keys", json={"name": "x", "expires_in_days": 0}, headers=H)).status_code == 422

    A = _auth(made)
    assert (await client.get("/api/v1/contacts", headers=A)).status_code == 200
    (await _key_row(db, made["id"])).expires_at = datetime.utcnow() - timedelta(seconds=1)
    await db.commit()
    r = await client.get("/api/v1/contacts", headers=A)
    assert r.status_code == 401 and "expired" in r.text
    listed = (await client.get("/api/api-keys", headers=H)).json()["items"]
    assert next(k for k in listed if k["id"] == made["id"])["status"] == "expired"


@pytest.mark.asyncio
async def test_a4_active_key_cap(client, db, monkeypatch):
    from backend.routers import apikeys as r_keys
    monkeypatch.setattr(r_keys, "MAX_ACTIVE_KEYS", 3)
    H, _, _ = await _register(client)
    made = [await _key(client, H) for _ in range(3)]
    r = await client.post("/api/api-keys", json={"name": "4th"}, headers=H)
    assert r.status_code == 409
    await client.delete(f"/api/api-keys/{made[0]['id']}", headers=H)
    assert (await client.post("/api/api-keys", json={"name": "4th"}, headers=H)).status_code == 201


def test_a4_cap_is_25():
    from backend.services.apikeys import MAX_ACTIVE_KEYS
    assert MAX_ACTIVE_KEYS == 25


@pytest.mark.asyncio
async def test_a4_rotation_overlap(client, db):
    H, _, _ = await _register(client)
    old = await _key(client, H, ["read"])
    r = await client.post(f"/api/api-keys/{old['id']}/rotate", headers=H)
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["key"] != old["key"] and new["id"] == old["id"] and new["scopes"] == "read"
    assert new["previous_expires_at"]
    # both secrets work during the grace period
    assert (await client.get("/api/v1/contacts", headers=_auth(old))).status_code == 200
    assert (await client.get("/api/v1/contacts", headers=_auth(new))).status_code == 200
    # after it, only the new one
    (await _key_row(db, old["id"])).previous_expires_at = datetime.utcnow() - timedelta(seconds=1)
    await db.commit()
    assert (await client.get("/api/v1/contacts", headers=_auth(old))).status_code == 401
    assert (await client.get("/api/v1/contacts", headers=_auth(new))).status_code == 200
    # a revoke kills both at once
    r2 = (await client.post(f"/api/api-keys/{old['id']}/rotate", headers=H)).json()
    await client.delete(f"/api/api-keys/{old['id']}", headers=H)
    assert (await client.get("/api/v1/contacts", headers=_auth(new))).status_code == 401
    assert (await client.get("/api/v1/contacts", headers=_auth(r2))).status_code == 401
    assert (await client.post(f"/api/api-keys/{old['id']}/rotate", headers=H)).status_code == 409


@pytest.mark.asyncio
async def test_a4_rotate_is_shop_scoped_and_audited(client, db):
    from backend.models import AuditLog
    H, org, _ = await _register(client)
    B, _, _ = await _register(client)
    made = await _key(client, H)
    assert (await client.post(f"/api/api-keys/{made['id']}/rotate", headers=B)).status_code == 404
    assert (await client.post(f"/api/api-keys/{made['id']}/rotate", headers=H)).status_code == 200
    acts = (await db.execute(select(AuditLog.action).where(AuditLog.org_id == org))).scalars().all()
    assert "apikey.rotate" in acts


def test_a4_list_flags_keys_of_departed_creators():
    from backend.routers.apikeys import _ser
    k = SimpleNamespace(id="k", name="n", prefix="rok_x", scopes="read", active=True, expires_at=None,
                        rotated_at=None, previous_expires_at=None, last_used_at=None, created_at=None,
                        created_by_id="e1")
    gone = {"e1": SimpleNamespace(name="Ex", active=False)}
    here = {"e1": SimpleNamespace(name="Ex", active=True)}
    assert _ser(k, gone)["creator_inactive"] is True
    assert _ser(k, here)["creator_inactive"] is False
    assert _ser(k, gone)["created_by_name"] == "Ex"


# ── A5: idempotency ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a5_idempotent_replay_and_conflict(client, db):
    from backend.models import ApiIdempotencyKey, Client
    H, org, _ = await _register(client)
    A = _auth(await _key(client, H))
    h = {**A, "Idempotency-Key": "abc-123"}
    r1 = await client.post("/api/v1/contacts", json={"name": "Ann", "phone": "1"}, headers=h)
    assert r1.status_code == 201, r1.text
    r2 = await client.post("/api/v1/contacts", json={"phone": "1", "name": "Ann"}, headers=h)
    assert r2.status_code == 201 and r2.json() == r1.json()
    assert r2.headers.get("idempotent-replayed") == "true"
    n = (await db.execute(select(Client).where(Client.org_id == org, Client.name == "Ann"))).scalars().all()
    assert len(n) == 1
    r3 = await client.post("/api/v1/contacts", json={"name": "Bob"}, headers=h)
    assert r3.status_code == 422 and "Idempotency-Key" in r3.text
    # the same header on a different route is also a different request
    loc = await _location(db, org)
    r4 = await client.post("/api/v1/orders", json={"location_id": loc}, headers=h)
    assert r4.status_code == 422
    # without the header nothing is stored
    await client.post("/api/v1/contacts", json={"name": "Cy"}, headers=A)
    rows = (await db.execute(select(ApiIdempotencyKey).where(ApiIdempotencyKey.org_id == org))).scalars().all()
    assert len(rows) == 1 and rows[0].status_code == 201


@pytest.mark.asyncio
async def test_a5_failed_request_releases_key_and_keys_are_per_api_key(client, db):
    H, org, _ = await _register(client)
    A = _auth(await _key(client, H))
    B = _auth(await _key(client, H))
    loc = await _location(db, org)
    h = {**A, "Idempotency-Key": "order-1"}
    bad = await client.post("/api/v1/orders", json={"location_id": str(uuid.uuid4())}, headers=h)
    assert bad.status_code == 404
    ok = await client.post("/api/v1/orders", json={"location_id": loc}, headers=h)
    assert ok.status_code == 201, ok.text
    again = await client.post("/api/v1/orders", json={"location_id": loc}, headers=h)
    assert again.json()["data"]["id"] == ok.json()["data"]["id"]
    other = await client.post("/api/v1/orders", json={"location_id": loc}, headers={**B, "Idempotency-Key": "order-1"})
    assert other.status_code == 201 and other.json()["data"]["id"] != ok.json()["data"]["id"]


@pytest.mark.asyncio
async def test_a5_expired_idempotency_key_runs_again(client, db):
    from backend.models import ApiIdempotencyKey
    H, org, _ = await _register(client)
    h = {**_auth(await _key(client, H)), "Idempotency-Key": "k"}
    first = (await client.post("/api/v1/contacts", json={"name": "A"}, headers=h)).json()["data"]["id"]
    row = (await db.execute(select(ApiIdempotencyKey))).scalars().one()
    row.created_at = datetime.utcnow() - timedelta(hours=25)
    await db.commit()
    second = (await client.post("/api/v1/contacts", json={"name": "A"}, headers=h)).json()["data"]["id"]
    assert second != first


@pytest.mark.asyncio
async def test_a5_in_flight_key_is_409(client, db):
    from backend.models import ApiIdempotencyKey, ApiKey
    H, org, _ = await _register(client)
    made = await _key(client, H)
    key = (await db.execute(select(ApiKey).where(ApiKey.id == made["id"]))).scalar_one()
    import hashlib
    import json

    from backend.routers.v1 import ContactIn
    same = hashlib.sha256(json.dumps({"route": "POST /contacts", "body": ContactIn(name="A").model_dump(mode="json")},
                                     sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    db.add(ApiIdempotencyKey(org_id=org, key_id=key.id, idem_key="busy", route="POST /contacts",
                             request_hash=same))      # claimed, no response stored yet
    await db.commit()
    r = await client.post("/api/v1/contacts", json={"name": "A"}, headers={**_auth(made), "Idempotency-Key": "busy"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_a5_idempotency_header_length(client):
    H, _, _ = await _register(client)
    h = {**_auth(await _key(client, H)), "Idempotency-Key": "x" * 256}
    assert (await client.post("/api/v1/contacts", json={"name": "A"}, headers=h)).status_code == 422


# ── A6: durations ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a6_duration_limits(client, db):
    H, org, me = await _register(client)
    A = _auth(await _key(client, H))
    loc = await _location(db, org)
    for d in (0, -5, 1441, 1e9):
        r = await client.post("/api/v1/time-entries", headers=A, json={
            "employee_id": me, "started_at": "2026-10-20T09:00:00", "duration_min": d})
        assert r.status_code == 422, d
    assert (await client.post("/api/v1/time-entries", headers=A, json={
        "employee_id": me, "started_at": "2026-10-20T09:00:00", "duration_min": 1440})).status_code == 201
    for d in (0, 4, -60, 1441):
        assert (await client.post("/api/v1/appointments", headers=A, json={
            "location_id": loc, "scheduled_for": "2099-01-01T10:00:00", "duration_min": d})).status_code == 422
        assert (await client.post("/api/v1/orders", headers=A, json={
            "location_id": loc, "duration_min": d})).status_code == 422


# ── A7: lengths, enums, unknown fields ───────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"name": "x" * 201},
    {"name": "   "},
    {"name": "A", "phone": "1" * 41},
    {"name": "A", "address": "a" * 501},
    {"name": "A", "email": "not-an-email"},
    {"name": "A", "email": "a" * 250 + "@x.io"},
    {"name": "A", "client_type": "alien"},
    {"name": "A", "loyalty_tier": "gold"},
])
async def test_a7_contact_validation(client, body):
    H, _, _ = await _register(client)
    r = await client.post("/api/v1/contacts", json=body, headers=_auth(await _key(client, H)))
    assert r.status_code == 422, (body, r.text)


@pytest.mark.asyncio
async def test_a7_other_body_limits(client, db):
    H, org, me = await _register(client)
    A = _auth(await _key(client, H))
    loc = await _location(db, org)
    assert (await client.post("/api/v1/orders", json={"location_id": loc, "complaint": "c" * 5001},
                              headers=A)).status_code == 422
    assert (await client.post("/api/v1/orders", json={"location_id": loc, "complain": "typo"},
                              headers=A)).status_code == 422
    assert (await client.post("/api/v1/appointments", json={
        "location_id": loc, "scheduled_for": "2099-01-01T10:00:00", "note": "n" * 5001}, headers=A)).status_code == 422
    assert (await client.post("/api/v1/time-entries", json={
        "employee_id": me, "started_at": "2026-10-20T09:00:00", "duration_min": 5, "note": "n" * 5001},
        headers=A)).status_code == 422
    assert (await client.post("/api/api-keys", json={"name": "k" * 121}, headers=H)).status_code == 422
    assert (await client.post("/api/api-keys", json={"name": "k", "admin": True}, headers=H)).status_code == 422
    ok = await client.post("/api/v1/contacts", json={"name": "Org", "client_type": "organization",
                                                     "email": "a@b.co"}, headers=A)
    assert ok.status_code == 201 and ok.json()["data"]["type"] == "organization"


# ── A8: cross-org / unknown ids → 404 ────────────────────────────────────────
@pytest.mark.asyncio
async def test_a8_cross_org_ids_are_404(client, db):
    H, org, me = await _register(client)
    B, org_b, me_b = await _register(client)
    A = _auth(await _key(client, H))
    KB = _auth(await _key(client, B))
    loc, loc_b = await _location(db, org), await _location(db, org_b)
    c_b = (await client.post("/api/v1/contacts", json={"name": "Theirs"}, headers=KB)).json()["data"]["id"]
    o_b = (await client.post("/api/v1/orders", json={"location_id": loc_b}, headers=KB)).json()["data"]["id"]

    # reads
    assert (await client.get(f"/api/v1/contacts/{c_b}", headers=A)).status_code == 404
    assert (await client.get(f"/api/v1/orders/{o_b}", headers=A)).status_code == 404
    # body references
    for body in ({"location_id": loc_b}, {"location_id": loc, "client_id": c_b},
                 {"location_id": loc, "assigned_to_id": me_b}, {"location_id": str(uuid.uuid4())}):
        r = await client.post("/api/v1/orders", json=body, headers=A)
        assert r.status_code == 404, (body, r.text)
    for body in ({"location_id": loc_b}, {"location_id": loc, "client_id": c_b},
                 {"location_id": loc, "employee_id": me_b}, {"location_id": loc, "service_id": str(uuid.uuid4())}):
        r = await client.post("/api/v1/appointments", json={**body, "scheduled_for": "2099-01-01T10:00:00"}, headers=A)
        assert r.status_code == 404, (body, r.text)
    r = await client.post("/api/v1/time-entries", headers=A, json={
        "employee_id": me, "order_id": o_b, "started_at": "2026-10-20T09:00:00", "duration_min": 5})
    assert r.status_code == 404
    r = await client.post("/api/v1/time-entries", headers=A, json={
        "employee_id": me_b, "started_at": "2026-10-20T09:00:00", "duration_min": 5})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_a8_order_create_uses_shop_defaults(client, db):
    from backend.models import Order, OrderStatusLog
    H, org, _ = await _register(client)
    A = _auth(await _key(client, H))
    loc = await _location(db, org)
    r = await client.post("/api/v1/orders", json={"location_id": loc, "complaint": "Cracked"}, headers=A)
    assert r.status_code == 201, r.text
    o = (await db.execute(select(Order).where(Order.id == r.json()["data"]["id"]))).scalar_one()
    assert o.barcode and o.public_token and o.currency_code
    logs = (await db.execute(select(OrderStatusLog).where(OrderStatusLog.order_id == o.id))).scalars().all()
    assert [lg.to_status for lg in logs] == [o.status]


@pytest.mark.asyncio
async def test_a8_integrity_error_is_422(client, monkeypatch):
    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError

    from backend.routers import v1

    class _Db:
        async def commit(self):
            raise IntegrityError("x", {}, Exception("fk"))

        async def rollback(self):
            pass
    with pytest.raises(HTTPException) as e:
        await v1._commit(_Db())
    assert e.value.status_code == 422


# ── A9: audit ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a9_api_writes_are_audited_with_the_key(client, db):
    from backend.models import AuditLog
    H, org, me = await _register(client)
    made = await _key(client, H)
    A = _auth(made)
    loc = await _location(db, org)
    await client.post("/api/v1/contacts", json={"name": "Ann"}, headers=A)
    await client.post("/api/v1/orders", json={"location_id": loc}, headers=A)
    await client.post("/api/v1/appointments", json={"location_id": loc, "scheduled_for": "2099-01-01T10:00:00"}, headers=A)
    await client.post("/api/v1/time-entries", json={"employee_id": me, "started_at": "2026-10-20T09:00:00",
                                                    "duration_min": 5}, headers=A)
    rows = (await db.execute(select(AuditLog).where(AuditLog.org_id == org))).scalars().all()
    via = {r.action for r in rows if (r.diff or {}).get("via_api_key") == made["id"]}
    assert {"client.create", "order.create", "appointment.create", "time_entry.create"} <= via


# ── A10: last_used_at throttled, after authorization ─────────────────────────
@pytest.mark.asyncio
async def test_a10_last_used_throttled_and_after_scope_check(client, db):
    H, _, _ = await _register(client)
    ro = await _key(client, H, ["read"])
    # a request refused for scope does not stamp the key
    assert (await client.post("/api/v1/contacts", json={"name": "A"}, headers=_auth(ro))).status_code == 403
    assert (await _key_row(db, ro["id"])).last_used_at is None
    assert (await client.get("/api/v1/contacts", headers=_auth(ro))).status_code == 200
    first = (await _key_row(db, ro["id"])).last_used_at
    assert first is not None
    await client.get("/api/v1/contacts", headers=_auth(ro))
    assert (await _key_row(db, ro["id"])).last_used_at == first      # within a minute: no write
    row = await _key_row(db, ro["id"])
    row.last_used_at = datetime.utcnow() - timedelta(minutes=2)
    await db.commit()
    await client.get("/api/v1/contacts", headers=_auth(ro))
    assert (await _key_row(db, ro["id"])).last_used_at > datetime.utcnow() - timedelta(seconds=30)


# ── A11: CORS and HTTPS ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a11_no_cors_on_public_api(client):
    H, _, _ = await _register(client)
    A = _auth(await _key(client, H))
    origin = {"Origin": "https://evil.example"}
    r = await client.get("/api/v1/contacts", headers={**A, **origin})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers
    pre = await client.options("/api/v1/contacts", headers={
        **origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    assert "access-control-allow-origin" not in pre.headers
    assert "access-control-allow-origin" not in (await client.get("/api/v1/openapi.json", headers=origin)).headers
    # the app's own routes keep their CORS behaviour
    assert "access-control-allow-origin" in (await client.get("/api/health", headers=origin)).headers


@pytest.mark.asyncio
async def test_a11_plain_http_refused_when_deployed(client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    r = await client.get("/api/v1/openapi.json")          # base_url is http://test
    assert r.status_code == 403 and "HTTPS" in r.text
    from httpx import ASGITransport, AsyncClient

    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as s:
        assert (await s.get("/api/v1/openapi.json")).status_code == 200
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
    assert (await client.get("/api/v1/openapi.json", headers={"X-Forwarded-Proto": "https"})).status_code == 200
    assert (await client.get("/api/v1/openapi.json", headers={"X-Forwarded-Proto": "http"})).status_code == 403
    # the rest of the app is not affected by this guard
    assert (await client.get("/api/health")).status_code != 403


def test_a11_request_scheme(monkeypatch):
    from backend.core.observability import request_scheme
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)
    assert request_scheme({"scheme": "http", "headers": [(b"x-forwarded-proto", b"https")]}) == "http"
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 2)
    sc = {"scheme": "http", "headers": [(b"x-forwarded-proto", b"http, https, http")]}
    assert request_scheme(sc) == "https"


# ── A12: listing keys needs can_manage_settings ──────────────────────────────
@pytest.mark.asyncio
async def test_a12_listing_keys_needs_settings_permission(client, db):
    H, org, _ = await _register(client)
    await _key(client, H)
    T = await _teammate(client, H, db, org, {"can_manage_settings": False})
    assert (await client.get("/api/api-keys", headers=T)).status_code == 403
    assert (await client.get("/api/api-keys", headers=H)).status_code == 200
    made = await _key(client, H)
    assert (await client.post(f"/api/api-keys/{made['id']}/rotate", headers=T)).status_code == 403


# ── A13: appointment times and clashes ───────────────────────────────────────
@pytest.mark.asyncio
async def test_a13_appointment_wall_clock_and_clash(client, db):
    from backend.models import Location
    H, org, me = await _register(client)
    A = _auth(await _key(client, H))
    loc_id = await _location(db, org)
    loc = (await db.execute(select(Location).where(Location.id == loc_id))).scalar_one()
    loc.timezone = "America/New_York"
    await db.commit()
    r = await client.post("/api/v1/appointments", headers=A, json={
        "location_id": loc_id, "scheduled_for": "2099-07-01T14:00:00Z", "employee_id": me, "duration_min": 60})
    assert r.status_code == 201, r.text
    assert r.json()["data"]["scheduled_for"].startswith("2099-07-01T10:00")
    clash = await client.post("/api/v1/appointments", headers=A, json={
        "location_id": loc_id, "scheduled_for": "2099-07-01T10:30:00", "employee_id": me, "duration_min": 30})
    assert clash.status_code == 409
    fine = await client.post("/api/v1/appointments", headers=A, json={
        "location_id": loc_id, "scheduled_for": "2099-07-01T11:00:00", "employee_id": me, "duration_min": 30})
    assert fine.status_code == 201


# ── A14: public v1-only OpenAPI ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a14_openapi_is_public_and_v1_only(client):
    r = await client.get("/api/v1/openapi.json")             # no key needed
    assert r.status_code == 200
    doc = r.json()
    assert doc["openapi"].startswith("3.") and doc["info"]["title"] == "Tekly API"
    paths = doc["paths"]
    assert paths and all(p == "/api/v1" or p.startswith("/api/v1/") for p in paths)
    assert "/api/v1/contacts" in paths and "/api/v1/bookings" not in paths
    assert "/api/v1/openapi.json" not in paths
    schemes = doc["components"]["securitySchemes"]
    assert any(s.get("type") == "http" and s.get("scheme") == "bearer" for s in schemes.values())
    for p, ops in paths.items():
        for method, op in ops.items():
            assert op.get("summary"), (method, p)
            assert op.get("security"), (method, p)
            assert op["operationId"].startswith("v1_")
    post = paths["/api/v1/contacts"]["post"]
    assert any(prm["name"] == "Idempotency-Key" for prm in post["parameters"])
    assert "examples" in doc["components"]["schemas"]["ContactIn"]
    assert "cost_price" in doc["components"]["schemas"]["ProductOut"]["properties"]


@pytest.mark.asyncio
async def test_a14_openapi_served_in_production_and_discovery_points_at_it(client, monkeypatch):
    H, _, _ = await _register(client)
    A = _auth(await _key(client, H))
    d = (await client.get("/api/v1", headers=A)).json()["data"]
    assert d["docs"] == "/api/v1/openapi.json" and d["openapi"] == "/api/v1/openapi.json"
    r = await client.get("/api/v1/docs", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"].endswith("/api/v1/openapi.json")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    from httpx import ASGITransport, AsyncClient

    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as s:
        assert (await s.get("/api/v1/openapi.json")).status_code == 200


# ── A15: rate-limit bucket, IP fallback and headers ──────────────────────────
@pytest.mark.asyncio
async def test_a15_rate_limit_headers_and_retry_after(client, monkeypatch):
    from backend.core import observability
    observability._KEY_CACHE.clear()
    H, _, _ = await _register(client)
    A = _auth(await _key(client, H))
    monkeypatch.setattr(settings, "RATE_LIMIT_USER_PER_MIN", 3)
    r1 = await client.get("/api/v1/contacts", headers=A)
    assert r1.headers["x-ratelimit-limit"] == "3" and r1.headers["x-ratelimit-remaining"] == "2"
    r2 = await client.get("/api/v1/contacts", headers=A)
    assert r2.headers["x-ratelimit-remaining"] == "1"
    await client.get("/api/v1/contacts", headers=A)
    r4 = await client.get("/api/v1/contacts", headers=A)
    assert r4.status_code == 429
    assert int(r4.headers["retry-after"]) >= 1 and r4.headers["x-ratelimit-remaining"] == "0"


@pytest.mark.asyncio
async def test_a15_bucket_is_key_id_and_survives_rotation(client, monkeypatch):
    from backend.core import observability
    from backend.services import ratelimit
    observability._KEY_CACHE.clear()
    H, _, _ = await _register(client)
    made = await _key(client, H)
    seen = []
    real = ratelimit.aenforce

    async def spy(bucket, key, limit, window):
        if bucket == "global":
            seen.append(key)
        return await real(bucket, key, limit, window)
    monkeypatch.setattr(ratelimit, "aenforce", spy)
    await client.get("/api/v1/contacts", headers=_auth(made))
    rotated = (await client.post(f"/api/api-keys/{made['id']}/rotate", headers=H)).json()
    seen.clear()
    await client.get("/api/v1/contacts", headers=_auth(rotated))
    await client.get("/api/v1/contacts", headers=_auth(made))       # old secret, grace period
    assert seen == ["k:" + made["id"], "k:" + made["id"]]


def test_a15_short_forwarded_chain_falls_back_to_peer(monkeypatch):
    from backend.services import ratelimit
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 2)
    req = SimpleNamespace(client=SimpleNamespace(host="10.0.0.9"), headers={"x-forwarded-for": "6.6.6.6"})
    assert ratelimit.client_ip(req) == "10.0.0.9"
