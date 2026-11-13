"""Re-review E1 (tenancy N7 SSO hardening; audit SEC-09, SEC-10).

- N7: a domain proof never expired and was never re-checked; the TXT lookup followed CNAMEs (a
  dangling CNAME let whoever controls its target prove a subdomain); verify-domain had no rate
  limit (outbound DNS on demand); `GET /sso/config` wrote (minted tokens and committed on a read);
  the future OIDC callback's email-domain check had no hook.
- SEC-09: anyone signed in who knew a browser's push endpoint URL re-bound that subscription to
  themselves (any shop).
- SEC-10: `/api/public/lookup` with no shop let a caller who knows a phone walk order numbers
  across shops, limited only per IP."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from backend.routers import sso

pytestmark = pytest.mark.asyncio


async def _shop(client):
    email = f"e1-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Shop", "name": "Owner", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _dns(monkeypatch, records: dict):
    async def fake(domain):
        return list(records.get(domain, []))
    monkeypatch.setattr(sso, "lookup_txt", fake)


async def _claim(client, h, domain):
    r = await client.post("/api/sso/config", json={
        "protocol": "oidc", "display_name": "SSO", "issuer": f"https://idp.{domain}", "client_id": "cid",
        "email_domain": domain, "enabled": True}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


async def _verified(client, h, monkeypatch, domain):
    conn = await _claim(client, h, domain)
    _dns(monkeypatch, {domain: [conn["domain_verification"]["value"]]})
    r = await client.post(f"/api/sso/config/{conn['id']}/verify-domain", headers=h)
    assert r.status_code == 200, r.text
    return conn


# ── N7 ─────────────────────────────────────────────────────────────────────────────────────────
async def test_domain_proof_expires(client, monkeypatch, db):
    from backend.models import SsoConnection
    from backend.services.dates import now_utc
    h = await _shop(client)
    domain = f"exp-{uuid.uuid4().hex[:6]}.test"
    conn = await _verified(client, h, monkeypatch, domain)
    assert (await client.get(f"/api/sso/discover?email=a@{domain}")).json()["sso"] is True
    await db.execute(update(SsoConnection).where(SsoConnection.id == conn["id"]).values(
        domain_verified_at=now_utc() - sso.VERIFY_TTL - timedelta(hours=1)))
    await db.commit()
    assert (await client.get(f"/api/sso/discover?email=a@{domain}")).json() == {"sso": False}


async def test_reverify_revokes_a_removed_record_and_refreshes_a_present_one(client, monkeypatch, db):
    from backend.models import SsoConnection
    from backend.services.dates import now_utc
    h = await _shop(client)
    domain = f"rv-{uuid.uuid4().hex[:6]}.test"
    conn = await _verified(client, h, monkeypatch, domain)
    old = now_utc() - timedelta(days=20)
    await db.execute(update(SsoConnection).where(SsoConnection.id == conn["id"]).values(domain_verified_at=old))
    await db.commit()
    r = await client.post(f"/api/sso/config/{conn['id']}/verify-domain", headers=h)     # record still there
    assert r.status_code == 200, r.text
    db.expire_all()
    assert (await db.execute(select(SsoConnection.domain_verified_at).where(
        SsoConnection.id == conn["id"]))).scalar_one() > old
    _dns(monkeypatch, {})                                                                  # record removed
    r = await client.post(f"/api/sso/config/{conn['id']}/verify-domain", headers=h)
    assert r.status_code == 422, r.text
    assert (await client.get(f"/api/sso/discover?email=a@{domain}")).json() == {"sso": False}


async def test_periodic_recheck_revokes_removed_records(client, monkeypatch, db):
    from backend.models import SsoConnection
    from backend.services.dates import now_utc
    h = await _shop(client)
    d_keep, d_gone = f"keep-{uuid.uuid4().hex[:6]}.test", f"gone-{uuid.uuid4().hex[:6]}.test"
    keep = await _verified(client, h, monkeypatch, d_keep)
    gone = await _verified(client, h, monkeypatch, d_gone)
    stale = now_utc() - sso.RECHECK_AFTER - timedelta(hours=1)
    await db.execute(update(SsoConnection).where(SsoConnection.id.in_([keep["id"], gone["id"]]))
                     .values(domain_verified_at=stale))
    await db.commit()
    _dns(monkeypatch, {d_keep: [keep["domain_verification"]["value"]]})
    await sso.recheck_domains(db)
    db.expire_all()
    rows = dict((await db.execute(select(SsoConnection.id, SsoConnection.domain_verified_at).where(
        SsoConnection.id.in_([keep["id"], gone["id"]])))).all())
    assert rows[keep["id"]] > stale and rows[gone["id"]] is None


async def test_txt_lookup_does_not_follow_cnames(monkeypatch):
    import dns.asyncresolver
    import dns.name

    class _Rec:
        strings = (b"tekly-sso-verification=abc",)

    class _Answer(list):
        canonical_name = dns.name.from_text("attacker-host.example")

    async def fake_resolve(name, rdtype, **kw):
        return _Answer([_Rec()])
    monkeypatch.setattr(dns.asyncresolver, "resolve", fake_resolve)
    assert await sso.lookup_txt("sso.victim.test") == []                    # was the attacker's TXT

    class _Own(list):
        canonical_name = dns.name.from_text("sso.victim.test")

    async def own_resolve(name, rdtype, **kw):
        return _Own([_Rec()])
    monkeypatch.setattr(dns.asyncresolver, "resolve", own_resolve)
    assert await sso.lookup_txt("sso.victim.test") == ["tekly-sso-verification=abc"]


async def test_verify_domain_is_rate_limited(client, monkeypatch):
    h = await _shop(client)
    conn = await _claim(client, h, f"rl-{uuid.uuid4().hex[:6]}.test")
    _dns(monkeypatch, {})
    codes = [(await client.post(f"/api/sso/config/{conn['id']}/verify-domain", headers=h)).status_code
             for _ in range(sso.VERIFY_LIMIT + 1)]
    assert codes[:-1] == [422] * sso.VERIFY_LIMIT and codes[-1] == 429, codes


async def test_get_config_does_not_write(client, monkeypatch, db):
    from backend.models import SsoConnection
    h = await _shop(client)
    domain = f"ro-{uuid.uuid4().hex[:6]}.test"
    conn = await _claim(client, h, domain)
    await db.execute(update(SsoConnection).where(SsoConnection.id == conn["id"]).values(domain_token=None))
    await db.commit()
    r = await client.get("/api/sso/config", headers=h)
    assert r.status_code == 200
    item = next(x for x in r.json()["items"] if x["id"] == conn["id"])
    assert item["domain_verification"]["value"] is None
    db.expire_all()
    assert (await db.execute(select(SsoConnection.domain_token).where(
        SsoConnection.id == conn["id"]))).scalar_one() is None                 # was minted + committed
    _dns(monkeypatch, {})
    r = await client.post(f"/api/sso/config/{conn['id']}/verify-domain", headers=h)     # minted here
    assert r.status_code == 422 and "tekly-sso-verification=" in r.json()["detail"]


async def test_oidc_callback_email_domain_hook():
    from types import SimpleNamespace

    from backend.services.dates import now_utc
    conn = SimpleNamespace(email_domain="acme.test", domain_verified_at=now_utc(), enabled=True)
    assert sso.email_matches_connection(conn, "jo@acme.test") is True
    assert sso.email_matches_connection(conn, "jo@ACME.test") is True
    assert sso.email_matches_connection(conn, "jo@evil.test") is False
    assert sso.email_matches_connection(conn, "jo@sub.acme.test") is False
    assert sso.email_matches_connection(conn, "jo@acme.test@evil.test") is False
    unproven = SimpleNamespace(email_domain="acme.test", domain_verified_at=None, enabled=True)
    assert sso.email_matches_connection(unproven, "jo@acme.test") is False


# ── SEC-09 ─────────────────────────────────────────────────────────────────────────────────────
async def test_push_endpoint_cant_be_taken_over_without_its_keys(client, tenant, db):
    from backend.models import PushSubscription
    endpoint = f"https://push.example/{uuid.uuid4().hex}"
    keys = {"p256dh": "BOWNERKEY", "auth": "owner-auth-secret"}
    r = await client.post("/api/push/subscribe", json={"endpoint": endpoint, "keys": keys}, headers=tenant)
    assert r.status_code == 201, r.text
    owner_emp = (await client.get("/api/auth/me", headers=tenant)).json()["id"]
    attacker = await _shop(client)
    r = await client.post("/api/push/subscribe", headers=attacker, json={
        "endpoint": endpoint, "keys": {"p256dh": "BATTACKER", "auth": "guessed"}})
    assert r.status_code == 409, r.text                     # was 201: re-bound to the attacker
    db.expire_all()
    row = (await db.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint)
                            .execution_options(skip_tenant_filter=True))).scalar_one()
    assert row.employee_id == owner_emp and row.auth == "owner-auth-secret"
    # the same browser (it holds the subscription's keys) signing in elsewhere moves it
    r = await client.post("/api/push/subscribe", headers=attacker, json={"endpoint": endpoint, "keys": keys})
    assert r.status_code == 201, r.text
    db.expire_all()
    row = (await db.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint)
                            .execution_options(skip_tenant_filter=True))).scalar_one()
    assert row.employee_id != owner_emp


async def test_own_push_subscription_can_refresh_its_keys(client, tenant):
    endpoint = f"https://push.example/{uuid.uuid4().hex}"
    r = await client.post("/api/push/subscribe", json={"endpoint": endpoint, "keys": {"p256dh": "A", "auth": "a"}},
                          headers=tenant)
    assert r.status_code == 201
    r = await client.post("/api/push/subscribe", json={"endpoint": endpoint, "keys": {"p256dh": "B", "auth": "b"}},
                          headers=tenant)
    assert r.status_code == 201 and r.json()["updated"] is True


# ── SEC-10 ─────────────────────────────────────────────────────────────────────────────────────
async def test_lookup_is_limited_per_phone_across_callers(client, monkeypatch):
    from backend.core import webhook_security
    phone = f"555{uuid.uuid4().int % 10**7:07d}"
    n = {"i": 0}

    def rotating_ip(_request):
        n["i"] += 1
        return f"10.9.{n['i'] // 250}.{n['i'] % 250}"
    monkeypatch.setattr(webhook_security, "client_ip", rotating_ip)
    from backend.routers import public
    codes = [(await client.post("/api/public/lookup", json={"order_number": str(9000 + i), "phone": phone})).status_code
             for i in range(public.LOOKUP_PER_PHONE + 1)]
    assert codes[:-1] == [404] * public.LOOKUP_PER_PHONE and codes[-1] == 429, codes


async def test_lookup_misses_look_the_same(client, tenant, monkeypatch):
    from backend.core import webhook_security
    n = {"i": 0}

    def rotating_ip(_request):
        n["i"] += 1
        return f"10.8.{n['i'] // 250}.{n['i'] % 250}"
    monkeypatch.setattr(webhook_security, "client_ip", rotating_ip)
    phone = f"+1555{uuid.uuid4().int % 10**7:07d}"
    cid = (await client.post("/api/clients", json={"name": "Lu", "phone": phone}, headers=tenant)).json()["id"]
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    o = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                           headers=tenant)).json()
    wrong_phone = await client.post("/api/public/lookup", json={"order_number": o["order_number"], "phone": "5550000000"})
    wrong_num = await client.post("/api/public/lookup", json={"order_number": "99999999", "phone": phone})
    wrong_shop = await client.post("/api/public/lookup", json={"order_number": o["order_number"], "phone": phone,
                                                               "shop": "no-such-shop"})
    assert {r.status_code for r in (wrong_phone, wrong_num, wrong_shop)} == {404}
    assert len({r.text for r in (wrong_phone, wrong_num, wrong_shop)}) == 1
    ok = await client.post("/api/public/lookup", json={"order_number": o["order_number"], "phone": phone})
    assert ok.status_code == 200 and ok.json()["token"]
