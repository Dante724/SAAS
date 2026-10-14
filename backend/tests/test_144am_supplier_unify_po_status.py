"""144am — unify supplier with contact (one form, role switch) + PO status trigger.

Covers:
  * a supplier is a Contact — creating one links a Client(is_supplier), and the client
    detail exposes the supplier facet so the unified form can edit it.
  * toggling the Supplier role on a plain contact creates the mirrored Supplier.
  * the PO status trigger: draft → ordered (manual), then receipt drives partial → received,
    and a received PO can't be un-ordered.
"""
import pytest


@pytest.mark.asyncio
async def test_supplier_is_a_contact(client, tenant):
    # Creating a supplier links a unified Contact and exposes the link both ways.
    s = (await client.post("/api/suppliers", json={"name": "Acme Parts", "phone": "555-1", "lead_days": 5}, headers=tenant)).json()
    assert s["client_id"], "supplier must mirror to a contact"

    lst = (await client.get("/api/suppliers", headers=tenant)).json()["items"]
    assert any(x["id"] == s["id"] and x["client_id"] == s["client_id"] for x in lst)

    # The client detail carries the supplier facet the unified form edits.
    detail = (await client.get(f"/api/clients/{s['client_id']}", headers=tenant)).json()
    assert detail["is_supplier"] is True
    assert detail["supplier"] and detail["supplier"]["id"] == s["id"]
    assert detail["supplier"]["lead_days"] == 5


@pytest.mark.asyncio
async def test_toggle_supplier_role_on_contact(client, tenant):
    # A plain contact gains a supplier profile when the Supplier role is switched on.
    cid = (await client.post("/api/clients", json={"name": "Bob"}, headers=tenant)).json()["id"]
    d0 = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()
    assert d0["is_supplier"] is False and d0["supplier"] is None

    await client.patch(f"/api/clients/{cid}", json={"is_supplier": True}, headers=tenant)
    d1 = (await client.get(f"/api/clients/{cid}", headers=tenant)).json()
    assert d1["is_supplier"] is True and d1["supplier"] and d1["supplier"]["id"]


@pytest.mark.asyncio
async def test_po_status_trigger_draft_to_received(client, tenant):
    sid = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=tenant)).json()["id"]
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Belt", "sku": "B-1"}, headers=tenant)).json()["id"]

    # Start as a draft.
    po_id = (await client.post("/api/inventory/po", headers=tenant, json={
        "supplier_id": sid, "location_id": loc, "draft": True,
        "lines": [{"part_id": pid, "qty_ordered": 10, "unit_cost": 2}]})).json()["po_id"]
    assert (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()["status"] == "draft"

    # Manual trigger: place the order.
    r = await client.post(f"/api/inventory/po/{po_id}/status", json={"status": "ordered"}, headers=tenant)
    assert r.status_code == 200 and r.json()["status"] == "ordered"

    # Receipt drives the status — partial first.
    line_id = (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()["lines"][0]["id"]
    r = await client.post(f"/api/inventory/po/{po_id}/receive", headers=tenant,
                          json={"lines": [{"po_line_id": line_id, "qty": 4}]})
    assert r.status_code == 200 and r.json()["status"] == "partial"

    # Can't un-order once stock has landed.
    bad = await client.post(f"/api/inventory/po/{po_id}/status", json={"status": "draft"}, headers=tenant)
    assert bad.status_code == 409

    # Receive the rest → received (terminal).
    r = await client.post(f"/api/inventory/po/{po_id}/receive", headers=tenant,
                          json={"lines": [{"po_line_id": line_id, "qty": 6}]})
    assert r.status_code == 200 and r.json()["status"] == "received"

    # Status trigger refuses to touch a terminal PO.
    bad = await client.post(f"/api/inventory/po/{po_id}/status", json={"status": "ordered"}, headers=tenant)
    assert bad.status_code == 409


@pytest.mark.asyncio
async def test_cannot_receive_a_draft(client, tenant):
    # Architect-QA fix: receiving must follow "mark as ordered" — a draft can't post stock.
    sid = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=tenant)).json()["id"]
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Belt", "sku": "B-2"}, headers=tenant)).json()["id"]
    po_id = (await client.post("/api/inventory/po", headers=tenant, json={
        "supplier_id": sid, "location_id": loc, "draft": True,
        "lines": [{"part_id": pid, "qty_ordered": 3, "unit_cost": 1}]})).json()["po_id"]
    line_id = (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()["lines"][0]["id"]
    r = await client.post(f"/api/inventory/po/{po_id}/receive", headers=tenant,
                          json={"lines": [{"po_line_id": line_id, "qty": 1}]})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_supplier_with_only_email_does_not_mismatch(client, tenant):
    # Architect-QA fix: a contact missing a phone must NOT be matched by a phone-less supplier.
    # Create a plain contact with an email but no phone.
    cid = (await client.post("/api/clients", json={"name": "Existing", "email": "e@x.com"}, headers=tenant)).json()["id"]
    # A brand-new supplier with only a phone (no email) must create its OWN contact, not
    # attach to the phone-less existing one via a NULL-email match.
    s = (await client.post("/api/suppliers", json={"name": "Phoney", "phone": "999"}, headers=tenant)).json()
    assert s["client_id"] and s["client_id"] != cid
