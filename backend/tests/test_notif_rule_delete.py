"""Build 144as — deleting a notification rule that has already fired (so NotificationLog rows point
at it) must succeed, not raise a FK violation. Surfaced by enabling SQLite FK enforcement."""
import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_delete_rule_with_logs_succeeds(client, tenant, db):
    from backend.models import NotificationRule
    # A new org is seeded with default client rules (build 144ah). Fire one by creating + closing an
    # order so NotificationLog rows reference a rule.
    cid = (await client.post("/api/clients", json={"name": "C", "email": "c@e.com"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "complaint": "x"}, headers=tenant)).json()["id"]
    await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)

    rule = (await db.execute(select(NotificationRule))).scalars().first()
    assert rule is not None
    # This used to 500 with a FOREIGN KEY violation once the rule had logs.
    r = await client.delete(f"/api/notifications/rules/{rule.id}", headers=tenant)
    assert r.status_code == 200, r.text
