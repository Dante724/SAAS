"""Part 10: lost-device logout via Employee.token_version + backward compat."""
import pytest
from jose import jwt

from backend.config import settings


def _tok(**claims):
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


@pytest.mark.asyncio
async def test_logout_all_devices_invalidates_current_token(client, tenant):
    # token works before
    assert (await client.get("/api/auth/me", headers=tenant)).status_code == 200
    # bump the version -> the in-hand token (tv=0) is now stale
    r = await client.post("/api/auth/logout-all-devices", headers=tenant)
    assert r.status_code == 200 and r.json()["token_version"] == 1
    assert (await client.get("/api/auth/me", headers=tenant)).status_code == 401


@pytest.mark.asyncio
async def test_legacy_token_without_tv_still_valid(client, tenant):
    """Tokens minted before the feature (no 'tv' claim) must keep working."""
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    legacy = _tok(sub=me["id"], org_id=me["org_id"])   # note: no tv, no exp checks here
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_stale_tv_rejected_fresh_tv_accepted(client, tenant, db):
    from sqlalchemy import select

    from backend.models import Employee

    me = (await client.get("/api/auth/me", headers=tenant)).json()
    emp = (await db.execute(select(Employee).where(Employee.id == me["id"]))).scalar_one()
    # simulate a device logout having bumped the server version to 2
    emp.token_version = 2
    await db.commit()
    stale = _tok(sub=me["id"], org_id=me["org_id"], tv=0)
    fresh = _tok(sub=me["id"], org_id=me["org_id"], tv=2)
    assert (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {stale}"})).status_code == 401
    assert (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {fresh}"})).status_code == 200
