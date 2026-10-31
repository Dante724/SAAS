"""Audit 29 Oct 2026, M5: a server where ENVIRONMENT was never set ran as development — the public
default SECRET_KEY (anyone could forge a sign-in), CORS '*', and the simulated gateway whose
simulate-pay link marks orders paid. Only an explicit development or test is relaxed now."""
import pytest
from cryptography.fernet import Fernet

from backend.config import Settings

STRONG = dict(SECRET_KEY="s" * 48, CORS_ORIGINS="https://app.example.com",
              DATABASE_URL="postgresql+asyncpg://u@db/tekly", PUBLIC_BASE_URL="https://app.example.com",
              SMTP_HOST="smtp.example.com", SMTP_FROM="no-reply@example.com",
              TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode())


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)


def test_unset_environment_refuses_the_default_secret_key(no_env):
    with pytest.raises(ValueError, match="SECRET_KEY") as e:
        Settings(_env_file=None)
    assert "ENVIRONMENT is not set" in str(e.value)


def test_unset_environment_refuses_wildcard_cors(no_env):
    with pytest.raises(ValueError, match="CORS"):
        Settings(_env_file=None, **{**STRONG, "CORS_ORIGINS": "*"})


def test_unset_environment_is_treated_as_deployed(no_env):
    s = Settings(_env_file=None, **STRONG)
    assert s.is_deployed
    assert s.ENVIRONMENT == "production"


@pytest.mark.asyncio
async def test_unset_environment_refuses_simulated_gateway_and_simulate_pay(no_env, monkeypatch, client, db):
    import backend.config as config
    from backend.services import payments
    strict = Settings(_env_file=None, **STRONG)
    monkeypatch.setattr(payments, "settings", strict)
    monkeypatch.setattr(config, "settings", strict)
    with pytest.raises(RuntimeError, match="testing only"):
        await payments.resolve_gateway(db, "no-such-org", "simulated")
    r = await client.post("/api/payments/intents/whatever/simulate-pay")
    assert r.status_code == 403


@pytest.mark.parametrize("env", ["development", "test"])
def test_explicit_local_envs_stay_relaxed(env, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", env)
    assert not Settings(_env_file=None).is_deployed
