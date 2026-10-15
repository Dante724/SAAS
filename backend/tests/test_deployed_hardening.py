"""Security: staging gets the same fail-closed config as production, and secrets at rest use a
dedicated, rotatable encryption key.

Regressions for the audit findings:
- every fail-closed check applied only to ENVIRONMENT=production, so an internet-facing staging
  ran with a placeholder SECRET_KEY, CORS=*, a default platform admin and simulated payments;
- OAuth tokens were stored in plaintext when TOKEN_ENCRYPTION_KEY was unset (never required),
  and shop credentials were encrypted with a key derived from SECRET_KEY (the JWT key).
"""
import pytest
from cryptography.fernet import Fernet

from backend.config import Settings, settings
from backend.services import crypto, org_creds

STRONG = dict(SECRET_KEY="s" * 48, CORS_ORIGINS="https://app.example.com")


@pytest.mark.parametrize("env", ["production", "staging"])
def test_deployed_envs_refuse_placeholder_secret(env):
    with pytest.raises(ValueError):
        Settings(ENVIRONMENT=env, CORS_ORIGINS="https://app.example.com",
                 TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())


@pytest.mark.parametrize("env", ["production", "staging"])
@pytest.mark.parametrize("key", ["", "not-a-fernet-key"])
def test_deployed_envs_require_a_valid_encryption_key(env, key):
    with pytest.raises(ValueError, match="TOKEN_ENCRYPTION_KEY"):
        Settings(ENVIRONMENT=env, TOKEN_ENCRYPTION_KEY=key, **STRONG)


def test_staging_with_full_config_boots_and_is_hardened():
    s = Settings(ENVIRONMENT="staging", TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), **STRONG)
    assert s.is_deployed and not s.is_production


@pytest.mark.parametrize("env", ["development", "test"])
def test_local_envs_stay_relaxed(env):
    assert not Settings(ENVIRONMENT=env).is_deployed


def test_token_encryption_supports_rotation(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", old)
    ct = crypto.encrypt("refresh-token-1")
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", f"{new},{old}")
    assert crypto.decrypt(ct) == "refresh-token-1"          # old ciphertext still readable
    ct2 = crypto.encrypt("refresh-token-2")
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", new)
    assert crypto.decrypt(ct2) == "refresh-token-2"         # new writes use the new key


def test_shop_credentials_move_off_the_jwt_key_without_losing_old_rows(monkeypatch):
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")
    legacy = org_creds._encrypt({"secret_key": "sk_test_1"})        # written before this change
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", key)
    assert org_creds._decrypt(legacy) == {"secret_key": "sk_test_1"}
    fresh = org_creds._encrypt({"secret_key": "sk_test_2"})
    # new rows don't depend on SECRET_KEY: readable with the dedicated key alone
    assert Fernet(key.encode()).decrypt(fresh.encode())


@pytest.mark.asyncio
async def test_simulate_pay_blocked_in_staging(client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    r = await client.post("/api/payments/intents/anything/simulate-pay")
    assert r.status_code == 403
