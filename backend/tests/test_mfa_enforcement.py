"""Security: org-mandated 2FA is enforced by the server, not just suggested to the client.

Regression for the audit finding where login returned a full token plus a
`mfa_enrollment_required` flag, and nothing server-side stopped an un-enrolled user from using
the whole API. 2FA is opt-in per org (owner's decision); new orgs default to off.
"""
import pytest
from sqlalchemy import select

from backend.models import Organisation
from backend.services import mfa


async def _require_mfa(db, on=True):
    org = (await db.execute(select(Organisation))).scalars().first()
    org.require_mfa = on
    await db.commit()


@pytest.mark.asyncio
async def test_new_orgs_do_not_require_mfa(client, tenant):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    assert me["mfa_required"] is False
    assert (await client.get("/api/orders", headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_unenrolled_user_is_limited_to_enrollment_until_2fa_is_on(client, tenant, db):
    await _require_mfa(db)          # existing token, issued before the policy was switched on
    blocked = await client.get("/api/orders", headers=tenant)
    assert blocked.status_code == 403
    assert blocked.headers.get("x-mfa-enrollment-required") == "1"
    assert (await client.post("/api/clients", json={"name": "x"}, headers=tenant)).status_code == 403

    # …but can still see who they are and enroll
    assert (await client.get("/api/auth/me", headers=tenant)).status_code == 200
    enroll = await client.post("/api/mfa/enroll", json={}, headers=tenant)
    assert enroll.status_code == 200
    code = mfa.totp(enroll.json()["secret"])
    assert (await client.post("/api/mfa/activate", json={"code": code}, headers=tenant)).status_code == 200

    # same token now works everywhere
    assert (await client.get("/api/orders", headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_policy_off_means_no_enforcement(client, tenant, db):
    await _require_mfa(db, on=False)
    assert (await client.get("/api/orders", headers=tenant)).status_code == 200
