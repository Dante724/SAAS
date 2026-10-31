"""Saving a shop's keys keeps the fields left blank. The Integrations form says "leave blank to
keep" and sends only what was typed, but the whole row was replaced, so editing the success URL
silently deleted the saved Stripe secret key."""
import pytest

from backend.services import org_creds


async def _org(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


@pytest.mark.asyncio
async def test_blank_fields_keep_their_saved_value(client, tenant):
    put = lambda data: client.put("/api/integrations/credentials", headers=tenant,  # noqa: E731
                                  json={"provider": "stripe", "data": data})
    assert (await put({"secret_key": "sk_test_keepme", "success_url": "https://a.example/ok"})).status_code == 200
    r = await put({"success_url": "https://b.example/ok", "secret_key": ""})
    assert r.status_code == 200
    fields = {f["key"]: f for f in r.json()["stripe"]["fields"]}
    assert fields["secret_key"]["set"] is True and fields["success_url"]["value"] == "https://b.example/ok"


@pytest.mark.asyncio
async def test_partial_save_merges_and_null_clears(client, tenant, db):
    org_id = await _org(client, tenant)
    await org_creds.set_creds(db, org_id, "stripe", {"secret_key": "sk_test_keepme", "success_url": "https://a.example/ok"})
    await org_creds.set_creds(db, org_id, "stripe", {"success_url": "https://b.example/ok", "secret_key": "  "})
    assert await org_creds.own(db, org_id, "stripe") == {"secret_key": "sk_test_keepme",
                                                         "success_url": "https://b.example/ok"}
    await org_creds.set_creds(db, org_id, "stripe", {"success_url": None})     # explicit clear
    assert await org_creds.own(db, org_id, "stripe") == {"secret_key": "sk_test_keepme"}
    st = await org_creds.status(db, org_id)
    assert next(f for f in st["stripe"]["fields"] if f["key"] == "secret_key")["set"] is True


@pytest.mark.asyncio
async def test_saving_over_unreadable_keys_starts_fresh(client, tenant, db, monkeypatch):
    from cryptography.fernet import Fernet

    from backend.config import settings
    from backend.models import OrgIntegrationCredential
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    org_id = await _org(client, tenant)
    db.add(OrgIntegrationCredential(org_id=org_id, provider="twilio", data_enc="garbage", active=True))
    await db.commit()
    await org_creds.set_creds(db, org_id, "twilio", {"account_sid": "AC1"})
    assert await org_creds.own(db, org_id, "twilio") == {"account_sid": "AC1"}
