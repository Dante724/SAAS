"""Build 144bc — security hardening.

Covers:
  * receive_po is org-scoped (no cross-tenant IDOR)
  * update_category rejects moving a category under its own descendant (cycle guard)
  * SecurityHeadersMiddleware sets hardening headers on responses
"""
import uuid

import pytest


async def _tenant(client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.dev"
    r = await client.post("/api/auth/register", json={
        "org_name": "Sec Co", "name": "Admin", "email": email, "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _loc(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    return (d if isinstance(d, list) else d["items"])[0]["id"]


async def _make_po(client, H):
    loc = await _loc(client, H)
    sup = (await client.post("/api/suppliers", json={"name": "Acme"}, headers=H)).json()
    part = (await client.post("/api/inventory/parts", json={
        "name": "Screen", "sku": f"SKU-{uuid.uuid4().hex[:6]}"}, headers=H)).json()
    po = (await client.post("/api/inventory/po", json={
        "supplier_id": sup["id"], "location_id": loc,
        "lines": [{"part_id": part["id"], "qty_ordered": 5, "unit_cost": 10}]}, headers=H)).json()
    return {"id": po["po_id"]}


async def test_receive_po_is_org_scoped(client):
    """A second org cannot receive another org's PO (IDOR)."""
    a = await _tenant(client)
    b = await _tenant(client)
    po = await _make_po(client, a)
    # org B tries to receive org A's PO → must not succeed
    r = await client.post(f"/api/inventory/po/{po['id']}/receive",
                          json={"lines": []}, headers=b)
    assert r.status_code in (403, 404), r.text
    # owner can still receive it
    ok = await client.post(f"/api/inventory/po/{po['id']}/receive",
                           json={"lines": []}, headers=a)
    assert ok.status_code in (200, 201), ok.text


async def test_category_cycle_guard(client):
    a = await _tenant(client)
    parent = (await client.post("/api/catalog/categories", json={
        "name": "Parent", "entity_type": "product"}, headers=a)).json()
    child = (await client.post("/api/catalog/categories", json={
        "name": "Child", "entity_type": "product", "parent_id": parent["id"]}, headers=a)).json()
    # moving the parent under its own child must be rejected
    r = await client.patch(f"/api/catalog/categories/{parent['id']}",
                           json={"parent_id": child["id"]}, headers=a)
    assert r.status_code == 400, r.text


async def test_security_headers_present(client):
    r = await client.get("/api/health")
    h = r.headers
    assert h.get("X-Content-Type-Options") == "nosniff"
    assert h.get("X-Frame-Options") == "SAMEORIGIN"
    assert "Content-Security-Policy" in h
    assert "object-src 'none'" in h["Content-Security-Policy"]
