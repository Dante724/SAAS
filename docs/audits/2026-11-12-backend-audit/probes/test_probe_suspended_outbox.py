"""Probe CLI/NTF: a suspended shop's outbox is still delivered by the background jobs."""
from datetime import timedelta
from sqlalchemy import select


async def test_suspended_org_still_delivers(client, tenant, db):
    from backend.models import NotificationLog, Organisation
    from backend.services import notification_rules as nr
    from backend.services.dates import now_utc
    org_id = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    org.sub_status = "suspended"
    db.add(NotificationLog(org_id=org_id, scope="client", channel="email", trigger_event="manual",
                           recipient="x@example.invalid", subject="s", body="b",
                           send_at=now_utc() - timedelta(minutes=1), status="scheduled"))
    await db.commit()
    r = await client.get("/api/auth/me", headers=tenant)
    print("PROBE staff API while suspended:", r.status_code)
    due = await nr.orgs_with_due(db)
    res = await nr.run_due(db, org_id=org_id)
    print("PROBE suspended org in orgs_with_due:", org_id in due, "run_due:", res)
    assert org_id in due and res["sent"] == 1
