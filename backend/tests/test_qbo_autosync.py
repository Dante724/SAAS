"""Build 144z — QuickBooks auto-sync fires on invoice/payment events."""
import pytest

from backend.models import QboConnection
from backend.services import qbo


async def _org_id(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]


@pytest.mark.asyncio
async def test_autosync_noops_without_connection(db, client, tenant):
    org_id = await _org_id(client, tenant)
    # no QBO connection + not configured → silent no-op, never raises
    await qbo.autosync(db, org_id, [("invoice", "nope"), ("payment", "nope")])


@pytest.mark.asyncio
async def test_autosync_pushes_each_item_when_connected(db, monkeypatch, client, tenant):
    org_id = await _org_id(client, tenant)
    db.add(QboConnection(org_id=org_id, realm_id="r1", environment="sandbox", connected=True))
    await db.commit()

    seen = []

    async def fake_sync(_db, _org, etype, lid):
        seen.append((etype, lid))
        return {"status": "ok"}

    monkeypatch.setattr(qbo, "is_configured", lambda: True)
    monkeypatch.setattr(qbo, "sync_entity", fake_sync)

    await qbo.autosync(db, org_id, [("contact", "c1"), ("invoice", None), ("payment", "p1")])
    # pushes the non-null items, in order; skips the None invoice
    assert seen == [("contact", "c1"), ("payment", "p1")]


@pytest.mark.asyncio
async def test_payment_confirmation_triggers_autosync(monkeypatch, client, tenant):
    calls = []

    async def fake_autosync(_db, org_id, items):
        calls.append((org_id, items))

    monkeypatch.setattr("backend.services.qbo.autosync", fake_autosync)

    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Pat"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x",
        "lines": [{"line_type": "part", "description": "board", "qty": 1, "unit_price": 100}]},
        headers=tenant)).json()["id"]

    link = (await client.post(f"/api/orders/{oid}/payment-link", headers=tenant)).json()
    iid = link["intent_id"]
    r = await client.post(f"/api/payments/intents/{iid}/simulate-pay")
    assert r.status_code == 200, r.text

    # the payment path called autosync with a ("payment", <id>) item
    assert calls, "autosync was not called on payment confirmation"
    entity_types = [et for _org, items in calls for (et, _lid) in items]
    assert "payment" in entity_types and "invoice" in entity_types
