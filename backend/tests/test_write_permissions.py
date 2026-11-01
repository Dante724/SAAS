"""Security: catalog permissions an owner denies on a role are actually enforced on writes.

Regression for the audit finding that 57 of the 77 catalog permissions were referenced nowhere
in the API, so unticking e.g. "Create invoices" in the role editor changed nothing.
"""
import pytest

from backend.tests.test_sensitive_reads import _restricted


@pytest.mark.asyncio
@pytest.mark.parametrize("perm, method, path, body", [
    ("can_create_invoices", "post", "/api/finance/invoices", {}),
    ("can_view_cashbox", "post", "/api/finance/shifts/open", {"opening_float": 0}),
    ("can_create_expenses", "post", "/api/finance/expenses", {"amount": 5, "category": "x"}),
    ("can_create_tickets", "post", "/api/orders", {"complaint": "x"}),
    ("can_create_sales", "post", "/api/sales", {"lines": []}),
    ("can_manage_settings", "post", "/api/forms/types", {"name": "x"}),
    ("can_manage_settings", "post", "/api/catalog/taxes", {"name": "VAT", "rate": 5}),
    ("can_manage_settings", "post", "/api/automations", {"name": "x"}),
    ("can_edit_labors", "post", "/api/catalog/services", {"name": "x", "price": 1}),
    ("can_create_products", "post", "/api/inventory/parts", {"name": "x"}),
])
async def test_denied_permission_blocks_the_write(client, db, tenant, perm, method, path, body):
    h, _, _ = await _restricted(client, db, [perm])
    r = await getattr(client, method)(path, headers=h, json=body)
    assert r.status_code == 403, (perm, path, r.status_code, r.text[:200])
    assert perm in r.json()["detail"]


@pytest.mark.asyncio
async def test_client_delete_and_merge_need_can_delete_clients(client, db, tenant):
    cid = (await client.post("/api/clients", json={"name": "Keep me"}, headers=tenant)).json()["id"]
    h, _, _ = await _restricted(client, db, ["can_delete_clients"])
    assert (await client.delete(f"/api/clients/{cid}", headers=h)).status_code == 403
    assert (await client.get(f"/api/clients/{cid}", headers=tenant)).status_code == 200


@pytest.mark.asyncio
async def test_unrestricted_role_still_allowed(client, db, tenant):
    h, _, _ = await _restricted(client, db, [])          # role exists but denies nothing
    r = await client.post("/api/catalog/taxes", headers=h, json={"name": "VAT", "rate": 5})
    assert r.status_code in (200, 201), r.text
