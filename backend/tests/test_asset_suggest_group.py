"""Brand / model suggestions follow the asset group.

Before: the Brand box on a "Power Tool" asset offered Apple, Samsung, OnePlus — the suggest call
knew nothing about the group, so a tool shop saw the phone list. Now the group (and the shop's
industry) put the names that fit first; the shop's own names for that group lead.
"""
import pytest

from backend.services import asset_catalog as cat
from backend.services import industry as I

TOOL_BRANDS = set(I.STARTER_BRANDS["tools"])
PHONE_BRANDS = set(I.STARTER_BRANDS["mobile_phone"])


def test_fit_templates():
    assert cat.fit_templates("Power Tool") == ("tools",)
    assert cat.fit_templates("power_tool") == ("tools",)
    assert cat.fit_templates("Phone") == ("mobile_phone",)
    # a group several templates share: the shop's own industry first
    assert cat.fit_templates("Truck", "fleet_truck")[0] == "fleet_truck"
    assert set(cat.fit_templates("Truck")) == {"auto", "fleet_truck"}
    # the shop's own group names go by hints
    assert cat.fit_templates("Drills") == ("tools",)
    assert cat.fit_templates("Riding mowers") == ("small_engine",)
    # nothing known → no ranking (old behaviour); the industry fills in when the group says nothing
    assert cat.fit_templates("Equipment") == ()
    assert cat.fit_templates("Device", "general") == ()
    assert cat.fit_templates(None, "tools") == ("tools",)
    assert cat.fit_templates("Equipment", "auto") == ("auto",)


async def _brands(client, tenant, **params):
    qs = "&".join(f"{k}={v}" for k, v in {"field": "brand", **params}.items())
    r = await client.get(f"/api/assets/suggest?{qs}", headers=tenant)
    assert r.status_code == 200, r.text
    return r.json()["items"]


@pytest.mark.asyncio
async def test_power_tool_gets_tool_brands_phone_gets_phone_brands(client, tenant):
    tools = await _brands(client, tenant, group="Power Tool")
    assert tools[:5] and set(tools[:5]) <= TOOL_BRANDS, tools[:5]
    assert tools[:3] == I.STARTER_BRANDS["tools"][:3]          # curated order: DeWalt, Makita, Milwaukee
    assert "Apple" in tools                                     # still reachable, just not first

    phones = await _brands(client, tenant, group="Phone")
    assert set(phones[:5]) <= PHONE_BRANDS, phones[:5]
    assert phones[0] == "Apple"

    # typing still filters, inside the ranked list
    assert (await _brands(client, tenant, group="Power Tool", q="mil")) == ["Milwaukee"]


@pytest.mark.asyncio
async def test_industry_ranks_when_group_is_unknown(client, tenant):
    tools = await _brands(client, tenant, group="Equipment", template_key="tools")
    assert set(tools[:5]) <= TOOL_BRANDS, tools[:5]


@pytest.mark.asyncio
async def test_without_a_group_the_list_is_unchanged(client, tenant):
    plain = await _brands(client, tenant, template_key="general")
    assert plain == sorted(plain, key=str.lower)                # alphabetical built-in list, as before
    assert await _brands(client, tenant, group="Other", template_key="general") == plain


@pytest.mark.asyncio
async def test_shop_names_for_the_group_come_first(client, tenant):
    async def asset(asset_type, brand, model, serial):
        r = await client.post("/api/assets", headers=tenant, json={
            "owner_type": "company", "asset_type": asset_type, "brand": brand, "model": model, "serial": serial})
        assert r.status_code == 201, r.text

    await asset("Power Tool", "Festool", "TS 55", "SN-FT-1")    # this shop's own tool brand
    for i in range(3):                                          # used more, but on phones
        await asset("Phone", "Apple", "iPhone 15", f"SN-AP-{i}")
    await asset("Phone", "Makita", "Odd one", "SN-MK-0")        # a tool brand used on another group

    tools = await _brands(client, tenant, group="Power Tool")
    assert tools[0] == "Festool"                                # used for this group → first
    assert tools[1] == "Makita"                                 # used elsewhere but fits the group
    assert tools.index("Apple") > tools.index("Hilti")          # phone brand after every tool brand

    phones = await _brands(client, tenant, group="Phone")
    assert phones[0] == "Apple"

    # without a group the shop's most-used brand still leads, as before
    assert (await _brands(client, tenant, template_key="general"))[0] == "Apple"

    # models: the ones used for this group first
    await asset("Power Tool", "Makita", "XFD131", "SN-MK-1")
    r = await client.get("/api/assets/suggest?field=model&brand=makita&group=Power Tool", headers=tenant)
    assert r.json()["items"][0] == "XFD131"
