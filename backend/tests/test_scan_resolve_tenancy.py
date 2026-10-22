"""Scanning an identifier never matches another shop's asset (phase 4, found by the asset council).

_match_existing allowed `owner_type == "company"` with no org filter, so a serial scanned in one
shop returned a company-owned asset belonging to any other shop.
"""
import pytest

from backend.models import Device, Organisation


@pytest.mark.asyncio
async def test_company_asset_of_another_shop_is_not_matched(client, tenant, db):
    other = Organisation(name="Other shop")
    db.add(other)
    await db.flush()
    db.add(Device(org_id=other.id, owner_type="company", asset_type="Device", serial="SHARED-SN-123",
                  template_key="general"))
    await db.commit()
    r = await client.post("/api/assets/scan-resolve", json={"identifier": "SHARED-SN-123"}, headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json().get("matched") is not True


@pytest.mark.asyncio
async def test_own_company_asset_still_matches(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    db.add(Device(org_id=me["org_id"], owner_type="company", asset_type="Device", serial="MINE-SN-9",
                  template_key="general"))
    await db.commit()
    r = await client.post("/api/assets/scan-resolve", json={"identifier": "MINE-SN-9"}, headers=tenant)
    assert r.status_code == 200 and r.json().get("matched") is True, r.text


@pytest.mark.asyncio
async def test_conversions_list_is_scoped_to_the_shop(client, tenant, db):
    from backend.models import Part
    other = Organisation(name="Other shop 2")
    db.add(other)
    await db.flush()
    p = Part(org_id=other.id, name="Their converted phone")
    db.add(p)
    await db.flush()
    db.add(Device(org_id=other.id, owner_type="company", asset_type="Device", serial="X-1",
                  converted_to_part_id=p.id, template_key="general"))
    await db.commit()
    r = await client.get("/api/inventory/conversions", headers=tenant)
    assert r.status_code == 200, r.text
    items = r.json().get("items", r.json())
    assert not any(i.get("product_id") == p.id for i in items)
