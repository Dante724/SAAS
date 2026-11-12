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
async def test_lookup_txt_reads_dns_over_https_answers(monkeypatch):
    """The real lookup: TXT strings come back quoted, long ones split into chunks."""
    import httpx

    def answer(request):
        assert request.url.params["name"] == "victim.test" and request.url.params["type"] == "TXT"
        return httpx.Response(200, json={"Status": 0, "Answer": [
            {"type": 16, "data": '"tekly-sso-verification=abc"'},
            {"type": 16, "data": '"v=spf1 " "-all"'},
            {"type": 5, "data": "cname.example."}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    assert await sso.lookup_txt("victim.test") == ["tekly-sso-verification=abc", "v=spf1 -all"]

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda r: httpx.Response(503)), **kw))
    assert await sso.lookup_txt("victim.test") == []
