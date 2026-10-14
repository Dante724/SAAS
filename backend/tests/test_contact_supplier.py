"""Build 114 — unified contacts: a contact can be client, supplier, or both."""
import pytest
from sqlalchemy import select

from backend.models import Client, Supplier


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


@pytest.mark.asyncio
async def test_contact_marked_supplier_creates_supplier_mirror(client, tenant, db):
    r = await client.post("/api/clients", json={
        "name": "Acme Parts", "phone": "+15550001", "is_supplier": True}, headers=tenant)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    # a linked Supplier row is created so POs can reference it
    sup = (await db.execute(select(Supplier).where(Supplier.client_id == cid))).scalars().first()
    assert sup is not None and sup.name == "Acme Parts"


@pytest.mark.asyncio
async def test_clearing_supplier_flag_removes_unused_mirror(client, tenant, db):
    r = await client.post("/api/clients", json={
        "name": "Temp Vendor", "phone": "+15550002", "is_supplier": True}, headers=tenant)
    cid = r.json()["id"]
    assert (await db.execute(select(Supplier).where(Supplier.client_id == cid))).scalars().first()

    # unmark supplier → mirror without PO history is removed
    await client.put(f"/api/clients/{cid}", json={
        "name": "Temp Vendor", "phone": "+15550002", "is_supplier": False}, headers=tenant)
    assert not (await db.execute(select(Supplier).where(Supplier.client_id == cid))).scalars().first()


@pytest.mark.asyncio
async def test_role_filter(client, tenant):
    await client.post("/api/clients", json={"name": "Pure Client", "phone": "+15550010"}, headers=tenant)
    await client.post("/api/clients", json={
        "name": "A Supplier", "phone": "+15550011", "is_supplier": True}, headers=tenant)

    clients_only = (await client.get("/api/clients?role=client", headers=tenant)).json()
    assert all(not c["is_supplier"] for c in clients_only["items"])
    assert any(c["name"] == "Pure Client" for c in clients_only["items"])

    suppliers_only = (await client.get("/api/clients?role=supplier", headers=tenant)).json()
    assert all(c["is_supplier"] for c in suppliers_only["items"])
    assert any(c["name"] == "A Supplier" for c in suppliers_only["items"])


@pytest.mark.asyncio
async def test_creating_supplier_directly_creates_contact(client, tenant, db):
    r = await client.post("/api/suppliers", json={
        "name": "Direct Vendor", "phone": "+15550020", "email": "v@vendor.dev"}, headers=tenant)
    assert r.status_code == 201, r.text
    # a unified contact is created and marked supplier
    c = (await db.execute(select(Client).where(
        Client.phone == "+15550020"))).scalars().first()
    assert c is not None and c.is_supplier is True


@pytest.mark.asyncio
async def test_contact_can_be_both_client_and_supplier(client, tenant, db):
    # a real customer who also sells you parts
    r = await client.post("/api/clients", json={
        "name": "Dual Role", "phone": "+15550030", "is_supplier": True}, headers=tenant)
    cid = r.json()["id"]
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    assert c.is_supplier is True
    # appears in both filtered views
    sup = (await client.get("/api/clients?role=supplier", headers=tenant)).json()
    assert any(x["id"] == cid for x in sup["items"])
