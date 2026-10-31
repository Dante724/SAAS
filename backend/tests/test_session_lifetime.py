"""Absolute session lifetime (security phase 4, audit L6).

/api/auth/refresh minted a fresh 24h token from any valid one, so a client (or anyone holding
a stolen token) that refreshed daily never had to sign in again.
"""
import time

import jwt
import pytest

from backend.config import settings
from backend.services.auth import create_access_token


def _claims(tok):
    return jwt.decode(tok, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


@pytest.mark.asyncio
async def test_refresh_keeps_the_original_sign_in_time(client, tenant):
    t0 = _claims(tenant["Authorization"].split()[1])["auth_time"]
    r = await client.post("/api/auth/refresh", headers=tenant)
    assert r.status_code == 200, r.text
    assert _claims(r.json()["access_token"])["auth_time"] == t0


@pytest.mark.asyncio
async def test_refresh_refused_once_the_session_window_has_passed(client, tenant, monkeypatch):
    me = await _me(client, tenant)
    window = settings.REFRESH_TOKEN_EXPIRE_MINUTES
    signed_in = int(time.time()) - window * 60 - 3600
    # A token that is itself still valid, from a sign-in older than the session window.
    monkeypatch.setattr(settings, "REFRESH_TOKEN_EXPIRE_MINUTES", window * 10)
    old = create_access_token({"sub": me["id"], "org_id": me["org_id"], "tv": 0, "auth_time": signed_in})
    monkeypatch.setattr(settings, "REFRESH_TOKEN_EXPIRE_MINUTES", window)
    r = await client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {old}"})
    assert r.status_code == 401 and "sign in again" in r.text


def test_token_never_outlives_its_session():
    signed_in = int(time.time()) - (settings.REFRESH_TOKEN_EXPIRE_MINUTES - 10) * 60
    tok = create_access_token({"sub": "x", "auth_time": signed_in})
    left = _claims(tok)["exp"] - time.time()
    assert 0 < left <= 11 * 60
