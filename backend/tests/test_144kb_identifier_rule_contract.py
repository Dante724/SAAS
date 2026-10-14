"""144kb — the industry-template endpoint exposes the identifier rule the asset
form + WO/estimate create flow use to decide which serial-type fields to show.
This guards the contract the React `useTerminology` hook relies on."""


async def test_endpoint_returns_identifier_rule(client, tenant):
    d = (await client.get("/api/settings/industry-template", headers=tenant)).json()
    ident = d["describe"]["identifier"]
    assert "primary" in ident and isinstance(ident["allowed"], list) and ident["allowed"]


async def test_describe_identifiers_are_template_specific(client, tenant):
    # Phones show IMEI/serial, never VIN/HIN; vehicles show VIN; vessels show HIN.
    async def allowed(tk):
        r = (await client.get(f"/api/settings/industry-template/describe?template_key={tk}", headers=tenant)).json()
        return set(r["identifier"]["allowed"])

    phone = await allowed("mobile_phone")
    assert "imei" in phone and "serial" in phone
    assert "vin" not in phone and "hin" not in phone

    auto = await allowed("auto")
    assert "vin" in auto

    marine = await allowed("marine")
    assert "hin" in marine


async def test_groups_carry_per_group_identifier_override(client, tenant):
    # 144kc: each group ships its effective rule so the create form narrows further
    # when a group overrides the template (Marine → Jet Ski keeps HIN, Auto → Trailer VIN).
    r = (await client.get("/api/settings/industry-template/describe?template_key=marine", headers=tenant)).json()
    groups = {g["key"]: g for g in r["groups"]}
    assert groups, "template groups should be present"
    for g in groups.values():
        assert "identifier" in g and isinstance(g["identifier"]["allowed"], list) and g["identifier"]["allowed"]
    # outboard motors identify by engine serial, not the vessel's HIN — a real override.
    if "outboard_motor" in groups:
        assert groups["outboard_motor"]["identifier"]["primary"] == "engine_serial"
