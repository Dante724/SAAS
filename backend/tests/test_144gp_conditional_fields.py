"""144gp — conditional fields: a hidden field is neither required nor stored;
a visible one still enforces required. Wired across entity forms (client here)."""
import pytest


async def _add_entity_field(client, H, **body):
    r = await client.post("/api/forms/entity-fields", json=body, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_hidden_required_field_does_not_block(client, tenant):
    H = tenant
    # Controlling field + a conditional required field shown only when it == "phone".
    ctl = await _add_entity_field(client, H, entity_type="client", field_label="Device type",
                                  field_type="text")
    cond = await _add_entity_field(client, H, entity_type="client", field_label="IMEI",
                                   field_type="text", required=True,
                                   show_when_field_key=ctl["field_key"], show_when_op="is",
                                   show_when_value="phone")
    assert cond["show_when_op"] == "is"

    # Controlling value != phone → IMEI hidden → client creates fine without it,
    # and even an accidentally-sent hidden value is dropped, not stored.
    ok = await client.post("/api/clients", json={
        "name": "Laptop Larry",
        "custom_data": {ctl["field_key"]: "laptop", cond["field_key"]: "junk"}}, headers=H)
    assert ok.status_code in (200, 201), ok.text
    got = (await client.get(f"/api/clients/{ok.json()['id']}", headers=H)).json()
    assert (got.get("custom_data") or {}).get(cond["field_key"]) in (None, "")

    # Controlling value == phone → IMEI visible + required → blocked without it.
    blocked = await client.post("/api/clients", json={
        "name": "Phone Fran", "custom_data": {ctl["field_key"]: "phone"}}, headers=H)
    assert blocked.status_code == 422, blocked.text
    assert "IMEI" in blocked.text

    # Provide it → allowed + stored.
    good = await client.post("/api/clients", json={
        "name": "Phone Fran", "custom_data": {ctl["field_key"]: "phone", cond["field_key"]: "356938"}},
        headers=H)
    assert good.status_code in (200, 201), good.text
    got = (await client.get(f"/api/clients/{good.json()['id']}", headers=H)).json()
    assert (got.get("custom_data") or {}).get(cond["field_key"]) == "356938"
