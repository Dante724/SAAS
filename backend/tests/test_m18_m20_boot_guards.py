"""Hardening council M18 + M20: a deployed server refuses to boot with rate limits that can't work.

M18: behind a load balancer with the Dockerfile default FORWARDED_ALLOW_IPS=127.0.0.1 (and
TRUSTED_PROXY_HOPS=0), every anonymous request looks like it comes from the balancer, so all
anonymous users share one rate bucket (11 sign-ups from 11 clients -> the 11th gets 429).
config.py only logged a warning. Now a deployed server needs proxy trust configured:
TRUSTED_PROXY_HOPS >= 1, or FORWARDED_ALLOW_IPS set to the balancer's address, or the explicit
NO_REVERSE_PROXY=true ("clients connect straight to this server").

M20: "REDIS_URL required when WEB_CONCURRENCY > 1" read only the env var, so `--workers N` or
several replicas still counted every limit per process. Now a deployed server needs REDIS_URL,
or the explicit SINGLE_WORKER=true (one worker, one instance: the beta condition).
"""
import pytest
from cryptography.fernet import Fernet

from backend.config import Settings

DEPLOYED = dict(ENVIRONMENT="production", SECRET_KEY="s" * 48, CORS_ORIGINS="https://app.example.com",
                DATABASE_URL="postgresql+asyncpg://u@db/tekly", PUBLIC_BASE_URL="https://app.example.com",
                SMTP_HOST="smtp.example.com", SMTP_FROM="no-reply@example.com",
                TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
                STRIPE_BILLING_SECRET_KEY="sk_test_x", STRIPE_BILLING_WEBHOOK_SECRET="whsec_x",
                **{f"STRIPE_BILLING_PRICE_{p}_{t}": f"price_{p}_{t}".lower()
                   for p in ("SOLO", "SHOP", "PRO", "SHOP_SEAT") for t in ("MONTH", "YEAR")})
REDIS = dict(REDIS_URL="redis://cache:6379/0")
PROXY = dict(TRUSTED_PROXY_HOPS=1)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("FORWARDED_ALLOW_IPS", "WEB_CONCURRENCY", "TRUSTED_PROXY_HOPS", "REDIS_URL",
              "NO_REVERSE_PROXY", "SINGLE_WORKER"):
        monkeypatch.delenv(k, raising=False)


def _boot(**kw):
    return Settings(_env_file=None, **{**DEPLOYED, **kw})


# ── M18: proxy trust ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("env", ["production", "staging"])
def test_deployed_with_loopback_proxy_trust_refuses_to_boot(env, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")              # the Dockerfile default
    with pytest.raises(ValueError) as e:
        _boot(ENVIRONMENT=env, **REDIS)
    msg = str(e.value)
    assert "FORWARDED_ALLOW_IPS" in msg and "TRUSTED_PROXY_HOPS" in msg and "NO_REVERSE_PROXY" in msg


def test_unset_forwarded_allow_ips_counts_as_loopback():
    with pytest.raises(ValueError, match="FORWARDED_ALLOW_IPS"):
        _boot(**REDIS)


@pytest.mark.parametrize("ips", ["127.0.0.1,::1", "localhost", " 127.0.0.1 "])
def test_loopback_only_lists_are_not_proxy_trust(ips, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", ips)
    with pytest.raises(ValueError, match="FORWARDED_ALLOW_IPS"):
        _boot(**REDIS)


@pytest.mark.parametrize("ips", ["10.0.0.0/8", "10.1.2.3", "*", "127.0.0.1,10.0.0.5"])
def test_forwarded_allow_ips_naming_the_balancer_boots(ips, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", ips)
    _boot(**REDIS)


def test_trusted_proxy_hops_boots():
    assert _boot(**REDIS, TRUSTED_PROXY_HOPS=1).TRUSTED_PROXY_HOPS == 1


def test_explicit_no_reverse_proxy_boots():
    assert _boot(**REDIS, NO_REVERSE_PROXY=True).NO_REVERSE_PROXY is True


def test_hops_zero_from_env_example_is_not_an_opt_out(monkeypatch):
    """.env.example ships TRUSTED_PROXY_HOPS=0, so an explicit 0 must not count as "I have no proxy"."""
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "0")
    with pytest.raises(ValueError, match="FORWARDED_ALLOW_IPS"):
        _boot(**REDIS)


# ── M20: shared rate-limit store ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("env", ["production", "staging"])
def test_deployed_without_redis_refuses_to_boot_even_with_one_worker_env(env, monkeypatch):
    """WEB_CONCURRENCY=1 says nothing about `--workers 4` or three replicas."""
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    with pytest.raises(ValueError) as e:
        _boot(ENVIRONMENT=env, **PROXY)
    msg = str(e.value)
    assert "REDIS_URL" in msg and "SINGLE_WORKER" in msg


def test_redis_url_boots():
    _boot(**PROXY, **REDIS)


def test_explicit_single_worker_boots_without_redis(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    assert _boot(**PROXY, SINGLE_WORKER=True).SINGLE_WORKER is True


def test_single_worker_with_several_workers_still_refuses(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    with pytest.raises(ValueError, match="REDIS_URL"):
        _boot(**PROXY, SINGLE_WORKER=True)


# ── Not deployed: nothing changes for development, test, CI and e2e ──────────────────────────
@pytest.mark.parametrize("env", ["development", "test"])
def test_local_environments_need_neither(env, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    s = Settings(_env_file=None, ENVIRONMENT=env)
    assert not s.is_deployed
