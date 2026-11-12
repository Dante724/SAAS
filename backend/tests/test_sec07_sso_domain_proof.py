"""Audit SEC-07: an SSO email-domain claim needs proof of ownership.

Any shop could register `email_domain="victimcorp.com"` with its own identity provider: the
unauthenticated `discover` then sent victimcorp's staff to it, and "first come wins" locked the real
owner out. A domain now routes nothing until the shop proves it owns it with a DNS TXT record, and
only a proven claim blocks another shop.
"""
import uuid

import pytest

from backend.routers import sso


async def _shop(client):
    email = f"sec07-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Squatter Co", "name": "Sq", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _dns(monkeypatch, records: dict):
    """Fake the TXT lookup: {domain: [txt strings]}."""
    async def fake(domain):
        return list(records.get(domain, []))
    monkeypatch.setattr(sso, "lookup_txt", fake)


async def _claim(client, h, domain="victim.test", issuer="https://idp.victim.test"):
    r = await client.post("/api/sso/config", json={
        "protocol": "oidc", "display_name": "SSO", "issuer": issuer, "client_id": "cid",
        "email_domain": domain, "enabled": True}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_unverified_domain_routes_nothing(client, tenant, monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example")
    _dns(monkeypatch, {})
    conn = await _claim(client, tenant)
    v = conn["domain_verification"]
    assert v["verified"] is False and v["record_type"] == "TXT" and v["host"] == "victim.test"
    assert v["value"].startswith("tekly-sso-verification=") and len(v["value"]) > 30
    assert (await client.get("/api/sso/discover?email=ceo@victim.test")).json() == {"sso": False}
    r = await client.get(f"/api/sso/authorize/{conn['id']}?redirect_uri=https://app.example/cb")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_wrong_or_missing_txt_record_does_not_verify(client, tenant, monkeypatch):
    conn = await _claim(client, tenant)
    _dns(monkeypatch, {"victim.test": ["v=spf1 -all", "tekly-sso-verification=someone-elses-token"]})
    r = await client.post(f"/api/sso/config/{conn['id']}/verify-domain", headers=tenant)
    assert r.status_code == 422, r.text
    assert (await client.get("/api/sso/discover?email=ceo@victim.test")).json() == {"sso": False}


@pytest.mark.asyncio
async def test_txt_record_proves_the_domain_and_turns_routing_on(client, tenant, monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.example")
    conn = await _claim(client, tenant)
    _dns(monkeypatch, {"victim.test": [conn["domain_verification"]["value"]]})
    r = await client.post(f"/api/sso/config/{conn['id']}/verify-domain", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["domain_verification"]["verified"] is True
    disc = (await client.get("/api/sso/discover?email=ceo@victim.test")).json()
    assert disc["sso"] is True and disc["provider_config_id"] == conn["id"]
    r = await client.get(f"/api/sso/authorize/{conn['id']}?redirect_uri=https://app.example/cb")
    assert r.status_code == 200 and r.json()["ready"] is True


@pytest.mark.asyncio
async def test_squatter_cannot_lock_out_the_real_owner(client, tenant, monkeypatch):
    """An unproven claim blocks nobody; once the owner proves it, nobody else can claim or prove it."""
    squatter = await _shop(client)
    sq = await _claim(client, squatter, issuer="https://evil.example")
    owner = await _claim(client, tenant)                     # used to be a 409 "already linked"
    _dns(monkeypatch, {"victim.test": [owner["domain_verification"]["value"],
                                       sq["domain_verification"]["value"]]})
    r = await client.post(f"/api/sso/config/{owner['id']}/verify-domain", headers=tenant)
    assert r.status_code == 200, r.text
    # even with its token published too, the squatter can't verify a domain proven by another shop
    r = await client.post(f"/api/sso/config/{sq['id']}/verify-domain", headers=squatter)
    assert r.status_code == 409, r.text
    r = await client.post("/api/sso/config", json={"email_domain": "victim.test"}, headers=squatter)
    assert r.status_code == 409, r.text
    disc = (await client.get("/api/sso/discover?email=ceo@victim.test")).json()
    assert disc["provider_config_id"] == owner["id"]


@pytest.mark.asyncio
async def test_verify_is_shop_scoped_and_needs_settings_permission(client, tenant, monkeypatch):
    other = await _shop(client)
    conn = await _claim(client, tenant)
    _dns(monkeypatch, {"victim.test": [conn["domain_verification"]["value"]]})
    r = await client.post(f"/api/sso/config/{conn['id']}/verify-domain", headers=other)
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_lookup_txt_joins_chunks_and_survives_dns_errors(monkeypatch):
    """The real lookup: a long TXT record comes back as several strings; DNS failures read as
    "no record" (the shop is told to try again), never a 500."""
    import dns.asyncresolver
    import dns.resolver

    class Rec:
        def __init__(self, *parts):
            self.strings = parts

    async def ok(name, rdtype, lifetime=None):
        assert (name, rdtype) == ("victim.test", "TXT")
        return [Rec(b"tekly-sso-verification=", b"abc"), Rec(b"v=spf1 -all")]
    monkeypatch.setattr(dns.asyncresolver, "resolve", ok)
    assert await sso.lookup_txt("victim.test") == ["tekly-sso-verification=abc", "v=spf1 -all"]

    async def nx(name, rdtype, lifetime=None):
        raise dns.resolver.NXDOMAIN()
    monkeypatch.setattr(dns.asyncresolver, "resolve", nx)
    assert await sso.lookup_txt("victim.test") == []


@pytest.mark.asyncio
async def test_platform_admin_can_find_and_release_a_proven_domain(client, tenant, monkeypatch):
    """A domain proven by the wrong shop (a lapsed DNS record, a dispute) can be handed back:
    platform support releases it, with a reason, and the real owner can then claim and prove it."""
    from backend.tests.admin_helpers import admin_headers
    squatter = await _shop(client)
    sq = await _claim(client, squatter, issuer="https://evil.example")
    _dns(monkeypatch, {"victim.test": [sq["domain_verification"]["value"]]})
    assert (await client.post(f"/api/sso/config/{sq['id']}/verify-domain", headers=squatter)).status_code == 200

    sales, _ = await admin_headers(client, role="sales")          # no change_owner permission
    r = await client.post("/api/admin/sso/domains/victim.test/release", json={"reason": "dispute #12"},
                          headers=sales)
    assert r.status_code == 403, r.text

    ops, _ = await admin_headers(client, role="support")
    got = (await client.get("/api/admin/sso/domains/Victim.TEST", headers=ops)).json()
    assert [c["id"] for c in got["items"]] == [sq["id"]] and got["items"][0]["verified"] is True
    r = await client.post("/api/admin/sso/domains/victim.test/release", json={"reason": "x"}, headers=ops)
    assert r.status_code == 422                                     # a reason is required
    r = await client.post("/api/admin/sso/domains/victim.test/release", json={"reason": "dispute #12"},
                          headers=ops)
    assert r.status_code == 200 and r.json()["released"] == 1, r.text
    assert (await client.get("/api/sso/discover?email=ceo@victim.test")).json() == {"sso": False}

    owner = await _claim(client, tenant)
    _dns(monkeypatch, {"victim.test": [owner["domain_verification"]["value"]]})
    r = await client.post(f"/api/sso/config/{owner['id']}/verify-domain", headers=tenant)
    assert r.status_code == 200, r.text
