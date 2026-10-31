"""144jn — per-org integration credentials: encryption, env fallback, and the
core multi-tenant guarantee that one shop's keys never leak to another."""
import pytest

from backend.services import org_creds


async def _org(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    return me.get("org_id") or me.get("orgId")


def test_encrypt_roundtrip():
    tok = org_creds._encrypt({"secret_key": "sk_live_abc", "webhook_secret": "whsec_x"})
    assert tok and "sk_live_abc" not in tok           # ciphertext, not plaintext
    assert org_creds._decrypt(tok)["secret_key"] == "sk_live_abc"
    assert org_creds._decrypt(None) == {}
    # tamper → refused, never "no keys" (audit M10: that fell back to the operator's keys)
    with pytest.raises(org_creds.CredentialsUnreadable):
        org_creds._decrypt("garbage")


async def test_per_org_isolation(client, tenant, db):
    """Shop A's Stripe key is invisible to Shop B."""
    a = await _org(client, tenant)
    # second, independent shop
    import uuid
    r = await client.post("/api/auth/register", json={
        "org_name": "Shop B", "name": "B", "email": f"b-{uuid.uuid4().hex[:8]}@t.dev",
        "password": "pw-test-12345"})
    tenant_b = {"Authorization": f"Bearer {r.json()['access_token']}"}
    b = (await client.get("/api/auth/me", headers=tenant_b)).json()["org_id"]
    assert a != b

    await org_creds.set_creds(db, a, "stripe", {"secret_key": "sk_A_only"})

    # A sees its own key; B sees nothing (no env fallback configured in tests)
    assert (await org_creds.effective(db, a, "stripe")).get("secret_key") == "sk_A_only"
    assert (await org_creds.effective(db, b, "stripe")).get("secret_key") in (None, "")
    assert await org_creds.source(db, a, "stripe") == "org"
    assert await org_creds.source(db, b, "stripe") == "none"


async def test_status_masks_secrets(client, tenant, db):
    org = await _org(client, tenant)
    await org_creds.set_creds(db, org, "twilio", {"account_sid": "AC123", "auth_token": "supersecret", "from": "+100"})
    st = await org_creds.status(db, org)
    tw = st["twilio"]
    assert tw["source"] == "org" and tw["configured"] is True
    fields = {f["key"]: f for f in tw["fields"]}
    assert fields["auth_token"]["secret"] is True and fields["auth_token"]["value"] == "••••"
    assert fields["from"]["value"] == "+100"          # non-secret shown


async def test_credentials_api_scoped(client, tenant):
    """PUT/GET/DELETE credentials round-trip, masked, and org-scoped."""
    r = await client.put("/api/integrations/credentials",
                         json={"provider": "stripe", "data": {"secret_key": "sk_test_xyz"}},
                         headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["stripe"]["source"] == "org"

    got = (await client.get("/api/integrations/credentials", headers=tenant)).json()
    skf = {f["key"]: f for f in got["stripe"]["fields"]}
    assert skf["secret_key"]["value"] == "••••"       # never returns the plaintext key

    r = await client.delete("/api/integrations/credentials/stripe", headers=tenant)
    assert r.status_code == 200
    assert r.json()["stripe"]["source"] == "none"


async def test_payments_config_reflects_org_keys(client, tenant):
    cfg = (await client.get("/api/payments/config", headers=tenant)).json()
    assert cfg["available"]["stripe"] is False        # no key yet → not available
    await client.put("/api/integrations/credentials",
                    json={"provider": "stripe", "data": {"secret_key": "sk_test_1"}}, headers=tenant)
    cfg2 = (await client.get("/api/payments/config", headers=tenant)).json()
    assert cfg2["available"]["stripe"] is True         # this shop's key makes Stripe available
