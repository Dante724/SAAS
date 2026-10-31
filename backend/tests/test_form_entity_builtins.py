"""Forms step 2 — Client, Device and Product forms list their built-in fields in the layout, so
they can be moved, hidden and required like the work-order form; required built-ins are enforced
when the record is created."""
import pytest


async def _require(client, H, form, keys, hide=()):
    lay = (await client.get(f"/api/forms/layout?form={form}", headers=H)).json()["items"]
    items = [{"ref": i["ref"], "visible": i["key"] not in hide, "required": "intake" if i["key"] in keys else ""} for i in lay]
    r = await client.put("/api/forms/layout", json={"form": form, "items": items}, headers=H)
    assert r.status_code == 200, r.text
    return {i["key"]: i for i in r.json()["items"]}


@pytest.mark.asyncio
async def test_builtins_listed_for_each_form(client, tenant):
    for form, keys in (("client", {"phone", "email", "address", "source", "notes"}),
                       ("device", {"brand", "model", "identifiers", "color", "condition"}),
                       ("product", {"category", "description", "code", "sku", "pricing"})):
        lay = (await client.get(f"/api/forms/layout?form={form}", headers=tenant)).json()["items"]
        got = {i["key"] for i in lay if i["kind"] == "system"}
        assert keys <= got, (form, got)


@pytest.mark.asyncio
async def test_always_filled_builtins_cannot_be_required(client, tenant):
    by = await _require(client, tenant, "product", {"pricing", "sku"})
    assert by["pricing"]["requirable"] is False and by["pricing"]["require"] == ""
    assert by["sku"]["require"] == "intake"
    by = await _require(client, tenant, "client", {"loyalty_tier"})
    assert by["loyalty_tier"]["require"] == ""


@pytest.mark.asyncio
async def test_required_client_builtins_enforced_on_create(client, tenant):
    H = tenant
    await _require(client, H, "client", {"phone", "email"})
    r = await client.post("/api/clients", json={"name": "Ada"}, headers=H)
    assert r.status_code == 422 and "Phone" in r.text and "Email" in r.text
    r = await client.post("/api/clients", json={"name": "Ada", "phone": "5550100", "email": "ada@example.com"}, headers=H)
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_hidden_builtin_is_never_required(client, tenant):
    H = tenant
    by = await _require(client, H, "client", {"phone"}, hide={"phone"})
    assert by["phone"]["visible"] is False and by["phone"]["require"] == ""
    assert (await client.post("/api/clients", json={"name": "Bo"}, headers=H)).status_code == 201


@pytest.mark.asyncio
async def test_required_device_and_product_builtins_enforced(client, tenant):
    H = tenant
    c = (await client.post("/api/clients", json={"name": "Cy"}, headers=H)).json()
    await _require(client, H, "device", {"identifiers", "color"})
    r = await client.post("/api/assets", json={"owner_type": "client", "client_id": c["id"], "brand": "Apple"}, headers=H)
    assert r.status_code == 422 and "Serial / IMEI / VIN" in r.text and "Color" in r.text
    r = await client.post("/api/assets", json={"owner_type": "client", "client_id": c["id"], "brand": "Apple",
                                               "imei": "356938035643809", "color": "Black"}, headers=H)
    assert r.status_code == 201, r.text

    await _require(client, H, "product", {"sku"})
    r = await client.post("/api/catalog/products", json={"name": "Screen A1"}, headers=H)
    assert r.status_code == 422 and "SKU" in r.text
    r = await client.post("/api/catalog/products", json={"name": "Screen A1", "sku": "SCR-A1"}, headers=H)
    assert r.status_code == 201, r.text
