"""144p2 — appointment detail actions: convert to WO or estimate, order_id exposed,
status change. Backend already had convert-to-WO + status/reschedule; this covers the
new estimate variant and that the list serializer now carries order_id (so the UI can
show "Converted")."""
from datetime import datetime, timedelta

from backend.services.dates import now_utc


async def _make_appointment(client, tenant):
    cid = (await client.post("/api/clients", json={"name": "Appointment Co", "phone": "+15550999"}, headers=tenant)).json()["id"]
    when = (now_utc() + timedelta(days=1)).replace(microsecond=0).isoformat()
    r = await client.post("/api/appointments", json={"client_id": cid, "client_name": "Appointment Co",
                          "scheduled_for": when, "duration_min": 60, "note": "Cracked screen"}, headers=tenant)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_convert_appointment_to_estimate(client, tenant):
    bid = await _make_appointment(client, tenant)
    r = await client.post(f"/api/appointments/{bid}/to-order?record=estimate", headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["is_estimate"] is True
    # list serializer now exposes order_id + appointment marked done
    lst = (await client.get("/api/appointments", headers=tenant)).json()["items"]
    row = next(b for b in lst if b["id"] == bid)
    assert row["order_id"] and row["status"] == "done"


async def test_convert_appointment_to_work_order_then_block_double(client, tenant):
    bid = await _make_appointment(client, tenant)
    r = await client.post(f"/api/appointments/{bid}/to-order?record=work_order", headers=tenant)
    assert r.status_code == 201 and r.json()["is_estimate"] is False, r.text
    # already linked → 409
    r2 = await client.post(f"/api/appointments/{bid}/to-order", headers=tenant)
    assert r2.status_code == 409


async def test_appointment_status_change(client, tenant):
    bid = await _make_appointment(client, tenant)
    r = await client.patch(f"/api/appointments/{bid}", json={"status": "canceled"}, headers=tenant)
    assert r.status_code == 200 and r.json()["status"] == "canceled", r.text
