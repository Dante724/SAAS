"""Public-API keys stop working when the shop is suspended (security phase 4, audit L5).

Staff tokens were checked against org.sub_status / sub_end on every request, but
get_api_principal only looked at the key row — a suspended shop's integrations kept
reading and writing customer data.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import select


async def _key(client, h):
    r = await client.post("/api/api-keys", json={"name": "zap", "scopes": "*"}, headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    return body.get("key") or body.get("token") or body.get("plaintext")


async def _org(db, client, h):
    from backend.models import Organisation
    me = (await client.get("/api/auth/me", headers=h)).json()
    return (await db.execute(select(Organisation).where(Organisation.id == me["org_id"]))).scalar_one()


@pytest.mark.asyncio
async def test_key_rejected_once_org_is_suspended(client, tenant, db):
    key = await _key(client, tenant)
    assert key and key.startswith("rok_")
    auth = {"Authorization": f"Bearer {key}"}
    assert (await client.get("/api/v1/contacts", headers=auth)).status_code == 200

    org = await _org(db, client, tenant)
    org.sub_status = "suspended"
    await db.commit()
    r = await client.get("/api/v1/contacts", headers=auth)
    assert r.status_code == 403 and "suspended" in r.text


@pytest.mark.asyncio
async def test_key_rejected_after_subscription_end(client, tenant, db):
    key = await _key(client, tenant)
    org = await _org(db, client, tenant)
    org.sub_end = date.today() - timedelta(days=1)
    await db.commit()
    r = await client.get("/api/v1/contacts", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403
