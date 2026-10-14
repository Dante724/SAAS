"""Security: login / 2FA brute-force limits can't be dodged, and TOTP codes work once.

Regressions for the audit findings:
- rate limits keyed on the left-most X-Forwarded-For value, which a client can rotate per request;
- no per-account ceiling, so guesses spread over many IPs were unlimited;
- TOTP guesses only throttled by that bypassable limiter, and a valid code could be replayed.
"""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.config import settings
from backend.models import Employee, Organisation
from backend.services import mfa, ratelimit
from backend.services.auth import hash_password


def _req(peer, xff=None):
    return SimpleNamespace(client=SimpleNamespace(host=peer),
                           headers={"x-forwarded-for": xff} if xff else {})


@pytest.mark.parametrize("hops, xff, expected", [
    (0, "6.6.6.6", "10.0.0.1"),                       # direct: header ignored entirely
    (1, "6.6.6.6, 203.0.113.9", "203.0.113.9"),       # one proxy: what IT saw, not the spoofed head
    (2, "6.6.6.6, 198.51.100.7, 10.0.0.2", "198.51.100.7"),
    (1, None, "10.0.0.1"),                            # proxy configured but header absent
])
def test_client_ip_trusts_only_proxy_appended_hops(monkeypatch, hops, xff, expected):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", hops)
    assert ratelimit.client_ip(_req("10.0.0.1", xff)) == expected


@pytest.fixture
def fresh_limits(monkeypatch):
    monkeypatch.setattr(ratelimit, "_redis", False)
    ratelimit._HITS.clear()
    yield
    ratelimit._HITS.clear()


async def _try(client, email, pw="wrong-pass", xff=None, code=None):
    data = {"username": email, "password": pw}
    if code is not None:
        data["client_secret"] = code
    return await client.post("/api/auth/token", data=data,
                             headers={"X-Forwarded-For": xff} if xff else {})


@pytest.mark.asyncio
async def test_rotating_x_forwarded_for_does_not_reset_the_limit(client, fresh_limits):
    email = f"victim-{uuid.uuid4().hex[:6]}@bf.test"
    codes = [(await _try(client, email, xff=f"6.6.6.{i}")).status_code for i in range(12)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


@pytest.mark.asyncio
async def test_per_account_ceiling_holds_across_many_ips(client, fresh_limits, monkeypatch):
    """Even with a correctly configured proxy (so each attacker IP is real and distinct)."""
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
    email = f"victim-{uuid.uuid4().hex[:6]}@bf.test"
    codes = [(await _try(client, email, xff=f"203.0.113.{i}")).status_code for i in range(32)]
    assert 429 in codes and codes.index(429) == 30


@pytest.fixture
async def mfa_user(db):
    org = (await db.execute(select(Organisation))).scalars().first()
    if org is None:
        pytest.skip("needs an org")
    secret = mfa.new_secret()
    email = f"mfa-{uuid.uuid4().hex[:6]}@bf.test"
    db.add(Employee(org_id=org.id, name="M", email=email, password_hash=hash_password("pw-mfa-12345"),
                    active=True, mfa_enabled=True, mfa_secret=secret))
    await db.commit()
    return SimpleNamespace(email=email, secret=secret)


@pytest.mark.asyncio
async def test_totp_code_cannot_be_replayed(client, tenant, mfa_user, fresh_limits):
    code = mfa.totp(mfa_user.secret)
    assert (await _try(client, mfa_user.email, "pw-mfa-12345", code=code)).status_code == 200
    again = await _try(client, mfa_user.email, "pw-mfa-12345", code=code)
    assert again.status_code == 401 and again.headers.get("x-mfa-required") == "1"


@pytest.mark.asyncio
async def test_next_totp_step_still_accepted(client, tenant, mfa_user, fresh_limits, monkeypatch):
    import time as _time
    t0 = _time.time()
    assert (await _try(client, mfa_user.email, "pw-mfa-12345", code=mfa.totp(mfa_user.secret, at=int(t0)))).status_code == 200
    monkeypatch.setattr(mfa.time, "time", lambda: t0 + 30)
    nxt = mfa.totp(mfa_user.secret, at=int(t0 + 30))
    assert (await _try(client, mfa_user.email, "pw-mfa-12345", code=nxt)).status_code == 200


@pytest.mark.asyncio
async def test_wrong_totp_codes_are_capped_per_account(client, tenant, mfa_user, fresh_limits):
    codes = [(await _try(client, mfa_user.email, "pw-mfa-12345", code="000000", xff=f"6.6.6.{i}")).status_code
             for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
