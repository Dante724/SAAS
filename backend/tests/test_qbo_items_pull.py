"""Build 144ag — QuickBooks item mapping, richer invoices, and two-way pull."""
import pytest

from backend.models import Client, IntegrationMapping, Part, QboConnection
from backend.services import qbo


async def _org_id(client, tenant):
    return (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]


async def _connect_qbo(db, monkeypatch, org_id):
    monkeypatch.setattr(qbo, "is_configured", lambda: True)
    db.add(QboConnection(org_id=org_id, realm_id="r1", environment="sandbox", connected=True,
                         access_token="enc"))
    await db.commit()

    async def _no_refresh(db_, conn):   # skip the token-refresh network call
        return None
    monkeypatch.setattr(qbo, "_ensure_fresh", _no_refresh)


@pytest.mark.asyncio
async def test_item_syncs_and_maps(db, monkeypatch, client, tenant):
    org_id = await _org_id(client, tenant)
    await _connect_qbo(db, monkeypatch, org_id)
    pushed = []

    async def fake_push(conn, entity_type, payload, remote_id):
        pushed.append((entity_type, payload))
        return "QB-ITEM-1"
    monkeypatch.setattr(qbo, "_push", fake_push)

    pid = (await client.post("/api/inventory/parts", json={
        "name": "Screen", "sku": "SC-1", "sell_price": 25}, headers=tenant)).json()["id"]
    res = await qbo.sync_entity(db, org_id, "item", pid)
    assert res["status"] == "ok"
    assert pushed[-1][0] == "item" and pushed[-1][1]["Name"] == "Screen" and pushed[-1][1]["Type"] == "Service"
    # mapping recorded so a later invoice can reference the QBO item
    assert await qbo._remote_id(db, org_id, "item", pid) == "QB-ITEM-1"


@pytest.mark.asyncio
async def test_invoice_payload_references_customer_item_and_tax(db, monkeypatch, client, tenant):
    org_id = await _org_id(client, tenant)
    # a client + a part, both already mapped to QBO
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Rex"}, headers=tenant)).json()["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Board", "sell_price": 100}, headers=tenant)).json()["id"]
    db.add(IntegrationMapping(org_id=org_id, integration="qbo", entity_type="contact", local_id=cid, remote_id="QC-9"))
    db.add(IntegrationMapping(org_id=org_id, integration="qbo", entity_type="item", local_id=pid, remote_id="QI-7"))
    await db.commit()

    oid = (await client.post("/api/orders", json={
        "client_id": cid, "location_id": loc, "complaint": "x",
        "lines": [{"line_type": "part", "description": "Board", "qty": 1, "unit_price": 100,
                   "tax_rate": 10, "ref_id": pid}]}, headers=tenant)).json()["id"]
    # close the order onto an invoice (triggers auto-invoice); grab that invoice id
    from sqlalchemy import select

    from backend.models import Invoice
    # ensure an invoice exists for the order (create one directly if the status flow didn't)
    inv = (await db.execute(select(Invoice).where(Invoice.order_id == oid))).scalars().first()
    if not inv:
        inv = Invoice(org_id=org_id, order_id=oid, client_id=cid, invoice_number="INV-T",
                      subtotal=110, total=110)
        db.add(inv)
        await db.commit()

    payload = await qbo._build_payload(db, org_id, "invoice", inv.id)
    assert payload["CustomerRef"] == {"value": "QC-9"}
    line = payload["Line"][0]
    assert line["SalesItemLineDetail"]["ItemRef"] == {"value": "QI-7"}
    assert "TxnTaxDetail" in payload and payload["TxnTaxDetail"]["TotalTax"] > 0


@pytest.mark.asyncio
async def test_pull_links_existing_qbo_records(db, monkeypatch, client, tenant):
    org_id = await _org_id(client, tenant)
    await _connect_qbo(db, monkeypatch, org_id)
    # local records that already exist in QBO
    cid = (await client.post("/api/clients", json={"name": "Acme Co", "email": "ap@acme.com"}, headers=tenant)).json()["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Cable", "sku": "CB-1"}, headers=tenant)).json()["id"]

    async def fake_query(conn, sql):
        if "Customer" in sql:
            return [{"Id": "QC-100", "DisplayName": "Acme Co", "PrimaryEmailAddr": {"Address": "ap@acme.com"}},
                    {"Id": "QC-999", "DisplayName": "Someone Else"}]
        return [{"Id": "QI-200", "Name": "Cable", "Sku": "CB-1"}]
    monkeypatch.setattr(qbo, "_query", fake_query)

    rc = (await client.post("/api/integrations/qbo/pull/contact", headers=tenant)).json()
    assert rc["status"] == "ok" and rc["linked"] == 1
    assert await qbo._remote_id(db, org_id, "contact", cid) == "QC-100"

    ri = (await client.post("/api/integrations/qbo/pull/item", headers=tenant)).json()
    assert ri["linked"] == 1
    assert await qbo._remote_id(db, org_id, "item", pid) == "QI-200"
    # pulling again does not duplicate mappings
    assert (await client.post("/api/integrations/qbo/pull/contact", headers=tenant)).json()["linked"] == 0
