"""Build 144ah — a new org starts with client-facing order notifications instead of silence.

Before this, a freshly registered shop had zero notification rules, so customers heard nothing
on status change until staff built rules by hand. Registration now seeds three template-less
client rules (order received / status changed / completed); the engine renders the per-event
default message. Seeding is idempotent.
"""
import pytest


@pytest.mark.asyncio
async def test_new_org_has_starter_client_rules(client, tenant):
    r = await client.get("/api/notifications/rules", headers=tenant)
    assert r.status_code == 200, r.text
    rules = r.json()["items"]
    client_order = [x for x in rules if x["module"] == "order" and x["scope"] == "client"]
    events = sorted(x["event"] for x in client_order)
    assert events == ["closed", "created", "status_changed"], rules
    assert all(x["active"] for x in client_order)


@pytest.mark.asyncio
async def test_seeding_is_idempotent(db, tenant):
    """Re-running the seeder for an org that already has rules adds nothing."""
    from sqlalchemy import func, select
    from backend.models import Employee, NotificationRule
    from backend.services.notification_rules import seed_default_rules

    emp = (await db.execute(select(Employee))).scalars().first()
    org_id = emp.org_id
    before = (await db.execute(select(func.count()).select_from(NotificationRule).where(
        NotificationRule.org_id == org_id))).scalar()
    made = await seed_default_rules(db, org_id)
    assert made == 0
    after = (await db.execute(select(func.count()).select_from(NotificationRule).where(
        NotificationRule.org_id == org_id))).scalar()
    assert after == before
