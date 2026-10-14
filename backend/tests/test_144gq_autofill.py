"""144gq — auto-fill: a field with source asset.<attr> pre-fills from the device."""
import uuid

import pytest
from sqlalchemy import select

from backend.models import Device, Order


@pytest.mark.asyncio
async def test_autofill_from_asset(client, tenant, db):
    H = tenant
    t = (await client.post("/api/forms/types", json={"name": "Repair"}, headers=H)).json()
    f = (await client.post(f"/api/forms/types/{t['id']}/fields", json={
        "field_label": "Model", "field_type": "text", "autofill_source": "asset.model"}, headers=H)).json()
    assert f["autofill_source"] == "asset.model"

    # A device to pull from (grab org_id from an order we create first).
    o = (await client.post("/api/orders", json={
        "location_id": (await client.get("/api/staff/locations", headers=H)).json()["items"][0]["id"],
        "complaint": "x"}, headers=H)).json()
    org_id = (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one().org_id
    dev = Device(id=str(uuid.uuid4()), org_id=org_id, brand="Apple", model="iPhone 13")
    db.add(dev)
    await db.commit()

    r = await client.get(f"/api/forms/autofill?type_id={t['id']}&device_id={dev.id}", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["values"].get(f["field_key"]) == "iPhone 13"

    # No device → nothing to fill, but the endpoint still succeeds.
    r2 = await client.get(f"/api/forms/autofill?type_id={t['id']}", headers=H)
    assert r2.status_code == 200
    assert r2.json()["values"] == {}
