"""Warehouse asset-label follows the industry template (build 144f).

Regression: `_asset_label` looked templates up by the raw stored value, so the
default "generic" and any legacy alias (device_repair, auto_repair, …) fell back
to "Assets" and the warehouse label never tracked the chosen template.
"""
import pytest
from sqlalchemy import select

from backend.models import Organisation


async def _label(client, h):
    r = await client.get("/api/settings/warehouses?kind=asset", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["asset_label"]


@pytest.mark.asyncio
async def test_default_generic_org_reads_assets(client, tenant):
    assert await _label(client, tenant) == "Assets"


@pytest.mark.asyncio
async def test_setting_a_template_changes_the_label(client, tenant):
    r = await client.put("/api/settings/industry-template",
                         json={"template_key": "auto"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await _label(client, tenant) == "Vehicles"

    r = await client.put("/api/settings/industry-template",
                         json={"template_key": "mobile_phone"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert await _label(client, tenant) == "Devices"


@pytest.mark.asyncio
async def test_legacy_alias_value_still_resolves(client, tenant, db):
    """An org row holding a legacy alias (not a canonical TEMPLATES key) must
    still resolve to the right word — this is exactly what used to return
    'Assets' before normalization."""
    org = (await db.execute(select(Organisation))).scalars().first()
    org.industry_template = "auto_repair"      # legacy alias for "auto"
    await db.commit()

    assert await _label(client, tenant) == "Vehicles"
