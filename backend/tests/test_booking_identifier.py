"""Booking page asks for the ID the customer can find on their item, named for the shop's
industry: VIN for vehicles, HIN for boats, serial number for everything else."""
import pytest
from sqlalchemy import select


async def _org(db, template):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation))).scalars().first()
    org.appointment_enabled = True
    org.industry_template = template
    org.currency = "$"
    await db.commit()
    return org


@pytest.mark.parametrize("template,kind,label,asset", [
    ("auto", "vin", "VIN", "Vehicle"),
    ("powersports", "vin", "VIN", "Vehicle"),
    ("fleet_truck", "vin", "VIN", "Unit"),
    ("marine", "hin", "HIN (hull ID)", "Vessel"),
    ("mobile_phone", "serial", "Serial number", "Device"),
    ("computer", "serial", "Serial number", "Computer"),
    ("general", "serial", "Serial number", "Asset"),
])
async def test_config_names_the_right_id(client, tenant, db, template, kind, label, asset):
    org = await _org(db, template)
    c = (await client.get(f"/api/public/appointment/{org.id}")).json()
    assert c["identifier"]["kind"] == kind and c["identifier"]["label"] == label
    assert c["asset_label"] == asset and c["currency"] == "$"


async def test_request_keeps_the_tidied_id(client, tenant, db):
    from backend.models import Lead
    org = await _org(db, "auto")
    r = await client.post(f"/api/public/appointment/{org.id}/request", json={
        "name": "Sam", "phone": "+15550101", "issue": "Brakes squeal", "identifier": "1hgcm 82633a-004352"})
    assert r.status_code == 201, r.text
    lead = (await db.execute(select(Lead).where(Lead.id == r.json()["lead_id"]))).scalar_one()
    assert "VIN: 1HGCM82633A004352" in lead.inquiry


async def test_marine_note_uses_hin_and_blank_is_skipped(db, client, tenant):
    from backend.routers.public import _booking_id_note
    org = await _org(db, "marine")
    assert _booking_id_note(org, " abc12345d404 ") == "HIN: ABC12345D404"
    assert _booking_id_note(org, "   ") is None
    org.industry_template = "tools"
    assert _booking_id_note(org, "sn-77 a") == "Serial number: SN-77A"
