"""Audit 29 Oct 2026, M10: when a shop's saved keys couldn't be decrypted (key rotated away,
corrupted row), they silently read as empty and the operator's own payment/SMS keys were used
for that shop instead. Unreadable keys now fail closed, and rows encrypted under the legacy key
derived from SECRET_KEY are re-encrypted by a migration, after which that key is dropped."""
import base64
import hashlib
import importlib.util
import json
import pathlib

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy import select

from backend.config import settings
from backend.models import OrgIntegrationCredential
from backend.services import org_creds
from backend.services.payment_guard import LivePaymentsRefused


def _legacy_fernet():
    digest = hashlib.sha256((settings.SECRET_KEY or "").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


async def _org(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


async def _store_raw(db, org_id, provider, token):
    db.add(OrgIntegrationCredential(org_id=org_id, provider=provider, data_enc=token, active=True))
    await db.commit()


@pytest.mark.asyncio
async def test_unreadable_shop_keys_never_fall_back_to_operator_keys(client, tenant, db, monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_OPERATOR")
    org_id = await _org(client, tenant)
    # encrypted under a key this server no longer has
    await _store_raw(db, org_id, "stripe", Fernet(Fernet.generate_key()).encrypt(b'{"secret_key":"sk_test_shop"}').decode())

    with pytest.raises(LivePaymentsRefused, match="can't be read"):
        await org_creds.effective(db, org_id, "stripe")
    with pytest.raises(LivePaymentsRefused):
        await org_creds.own(db, org_id, "stripe")
    st = await org_creds.status(db, org_id)
    assert st["stripe"]["source"] == "unreadable" and st["stripe"]["configured"] is False


@pytest.mark.asyncio
async def test_unreadable_keys_block_payments_instead_of_using_operator_account(client, tenant, db, monkeypatch):
    from backend.services import payments
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_OPERATOR")
    org_id = await _org(client, tenant)
    await _store_raw(db, org_id, "stripe", "garbage-not-a-token")
    with pytest.raises(RuntimeError, match="can't be read"):
        await payments.resolve_gateway(db, org_id, "stripe")


@pytest.mark.asyncio
async def test_status_endpoint_reports_unreadable_keys(client, tenant, db, monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    org_id = await _org(client, tenant)
    await _store_raw(db, org_id, "twilio", "garbage-not-a-token")
    r = await client.get("/api/integrations/credentials", headers=tenant)
    assert r.status_code == 200 and r.json()["twilio"]["source"] == "unreadable"


def test_legacy_key_is_not_used_once_a_dedicated_key_is_set(monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    legacy = _legacy_fernet().encrypt(b'{"secret_key":"sk_test_1"}').decode()
    with pytest.raises(LivePaymentsRefused):
        org_creds._decrypt(legacy)


def test_reencrypt_moves_legacy_rows_to_the_dedicated_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", key)
    legacy = _legacy_fernet().encrypt(b'{"secret_key":"sk_test_1"}').decode()
    new = org_creds.reencrypt_legacy(legacy)
    assert new and new != legacy
    assert json.loads(Fernet(key.encode()).decrypt(new.encode())) == {"secret_key": "sk_test_1"}
    assert org_creds.reencrypt_legacy(new) is None          # already on the new key: untouched
    assert org_creds.reencrypt_legacy("garbage") is None     # unreadable: left for the shop to re-enter


def test_migration_reencrypts_rows(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", key)
    path = next(pathlib.Path("backend/migrations/versions").glob("aud10m01_*.py"))
    spec = importlib.util.spec_from_file_location("aud10m01", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    legacy = _legacy_fernet().encrypt(b'{"api_key":"k"}').decode()
    eng = sa.create_engine(f"sqlite:///{tmp_path}/c.db")
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE org_integration_credentials (id VARCHAR PRIMARY KEY, data_enc TEXT)"))
        conn.execute(sa.text("INSERT INTO org_integration_credentials VALUES ('c1', :t)"), {"t": legacy})
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
        stored = conn.execute(sa.text("SELECT data_enc FROM org_integration_credentials")).scalar()
    assert json.loads(Fernet(key.encode()).decrypt(stored.encode())) == {"api_key": "k"}


@pytest.mark.asyncio
async def test_round_trip_still_works(client, tenant, db, monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    org_id = await _org(client, tenant)
    await org_creds.set_creds(db, org_id, "twilio", {"account_sid": "AC1", "auth_token": "t"})
    assert (await org_creds.effective(db, org_id, "twilio"))["account_sid"] == "AC1"
    row = (await db.execute(select(OrgIntegrationCredential).where(
        OrgIntegrationCredential.org_id == org_id))).scalar_one()
    assert "AC1" not in row.data_enc


@pytest.mark.asyncio
async def test_screens_that_read_unreadable_keys_answer_409_not_500(client, tenant, db, monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    org_id = await _org(client, tenant)
    await _store_raw(db, org_id, "twilio_voice", "garbage-not-a-token")
    r = await client.get("/api/telephony/config", headers=tenant)
    assert r.status_code == 409 and "Enter them again" in r.json()["detail"]
