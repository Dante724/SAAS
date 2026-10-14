"""144iy — Appointment 2.0: config pickup fields, no-dead-end request lead, phone hello."""
from sqlalchemy import select


async def test_appointment_config_has_pickup_fields(client, tenant, db):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation))).scalars().first()
    org.appointment_enabled = True
    await db.commit()
    r = await client.get(f"/api/public/appointment/{org.id}")
    assert r.status_code == 200, r.text
    locs = r.json()["locations"]
    assert locs and "address" in locs[0] and "phone" in locs[0]


async def test_appointment_request_creates_web_lead(client, tenant, db):
    from backend.models import Lead, Organisation
    org = (await db.execute(select(Organisation))).scalars().first()
    org.appointment_enabled = True
    await db.commit()

    # missing contact → 400
    r = await client.post(f"/api/public/appointment/{org.id}/request", json={"name": "Walk In"})
    assert r.status_code == 400

    r = await client.post(f"/api/public/appointment/{org.id}/request", json={
        "name": "Walk In", "phone": "9876501234",
        "issue": "Cracked screen", "service_name": "Screen repair"})
    assert r.status_code == 201, r.text
    lead_id = r.json()["lead_id"]
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one()
    assert lead.source == "web"
    assert "Screen repair" in (lead.inquiry or "") and "Cracked screen" in (lead.inquiry or "")
    assert lead.sla_due_at is not None


async def test_appointment_hello_recognises_returning_phone(client, tenant, db):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation))).scalars().first()
    org.appointment_enabled = True
    await db.commit()

    # unknown number → not known
    r = await client.post(f"/api/public/appointment/{org.id}/hello", json={"phone": "1112223334"})
    assert r.status_code == 200 and r.json()["known"] is False

    # create a client with a phone, then greet by first name only
    await client.post("/api/clients", json={"name": "Ravi Kumar", "phone": "+91 98765 43210"}, headers=tenant)
    r = await client.post(f"/api/public/appointment/{org.id}/hello", json={"phone": "9876543210"})
    body = r.json()
    assert body["known"] is True and body["name"] == "Ravi"
    # short/garbage input never matches
    r = await client.post(f"/api/public/appointment/{org.id}/hello", json={"phone": "12"})
    assert r.json()["known"] is False
