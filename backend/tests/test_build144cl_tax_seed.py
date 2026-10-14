"""Build 144cl — signup seeds a region/country-appropriate default tax rate."""
import pytest
from sqlalchemy import select

from backend.db.database import AsyncSessionLocal
from backend.models import Employee, Tax


async def _reg(client, org, email, **extra):
    r = await client.post("/api/auth/register", json={
        "org_name": org, "name": "O", "email": email, "password": "pw-12345678", **extra})
    assert r.status_code == 201, r.text
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.email == email))).scalar_one()
        return (await db.execute(select(Tax).where(Tax.org_id == emp.org_id))).scalars().all()


@pytest.mark.asyncio
async def test_mena_seeds_5pct_vat_inclusive(client):
    taxes = await _reg(client, "GulfCo", "gulf@cl.dev", region="mena")
    assert len(taxes) == 1
    t = taxes[0]
    assert t.name == "VAT" and t.rate == 5.0 and t.mode == "included" and t.is_default


@pytest.mark.asyncio
async def test_country_override_germany_19(client):
    taxes = await _reg(client, "DECo", "de@cl.dev", region="eu", country="DE")
    assert taxes[0].name == "VAT" and taxes[0].rate == 19.0 and taxes[0].mode == "included"


@pytest.mark.asyncio
async def test_us_seeds_zero_sales_tax_exclusive(client):
    taxes = await _reg(client, "USCo", "us@cl.dev", region="us")
    assert taxes[0].name == "Sales tax" and taxes[0].rate == 0.0 and taxes[0].mode == "imposed"


@pytest.mark.asyncio
async def test_no_region_seeds_nothing(client):
    taxes = await _reg(client, "PlainCo", "plain@cl.dev")
    assert taxes == []


@pytest.mark.asyncio
async def test_ksa_country_seeds_15(client):
    taxes = await _reg(client, "SaudiCo", "sa@cl.dev", region="mena", country="SA")
    assert taxes[0].rate == 15.0
