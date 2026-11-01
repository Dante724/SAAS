"""Build 144be — the reconciliation 'needs your eyes' queue surfaces real money problems the owner
would otherwise miss: a double-paid invoice and an unbalanced drawer both show up as exceptions."""
import pytest


@pytest.mark.asyncio
async def test_reconciliation_flags_double_pay_and_unbalanced_drawer(client, tenant, db):
    # a double-paid invoice: 100 invoice with amount_paid 200. The API now refuses to over-collect
    # (security phase 3), so write the state directly — reconciliation exists to surface exactly
    # this kind of legacy/imported data.
    from sqlalchemy import select

    from backend.models import Invoice
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+1"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=tenant)).json()["id"]
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 100}, headers=tenant)).json()
    await client.post(f"/api/finance/invoices/{inv['id']}/pay", json={"method": "cash", "amount": 100}, headers=tenant)
    row = (await db.execute(select(Invoice).where(Invoice.id == inv["id"]))).scalar_one()
    row.amount_paid = 200
    await db.commit()

    # an unbalanced drawer: open 100, count only 90 on close → short 10
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    sid = (await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 100}, headers=tenant)).json()["id"]
    await client.post(f"/api/finance/shifts/{sid}/close?closing_float=90", headers=tenant)

    r = await client.get("/api/finance/reconciliation", headers=tenant)
    assert r.status_code == 200, r.text
    types = {it["type"] for it in r.json()["items"]}
    assert "double_paid" in types, r.json()
    assert "drawer_unbalanced" in types, r.json()
    # high-severity items sort first
    assert r.json()["count"] >= 2
    assert r.json()["items"][0]["severity"] == "high"


@pytest.mark.asyncio
async def test_clean_books_have_no_exceptions(client, tenant):
    # a balanced drawer and a correctly-paid invoice produce an empty queue
    cid = (await client.post("/api/clients", json={"name": "C", "phone": "+2"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid}, headers=tenant)).json()["id"]
    inv = (await client.post("/api/finance/invoices", json={"order_id": oid, "subtotal": 50}, headers=tenant)).json()
    await client.post(f"/api/finance/invoices/{inv['id']}/pay", json={"method": "cash", "amount": 50}, headers=tenant)
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    sid = (await client.post("/api/finance/shifts/open", json={"location_id": loc, "opening_float": 100}, headers=tenant)).json()["id"]
    await client.post(f"/api/finance/shifts/{sid}/close?closing_float=100", headers=tenant)

    r = await client.get("/api/finance/reconciliation", headers=tenant)
    assert r.status_code == 200 and r.json()["count"] == 0, r.json()
