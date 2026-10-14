"""Build 144cg — Orderry-style signup: options endpoint + onboarding fields applied."""
import pytest
from sqlalchemy import select

from backend.db.database import AsyncSessionLocal
from backend.models import Employee, Organisation


@pytest.mark.asyncio
async def test_signup_options_public(client):
    r = await client.get("/api/auth/signup-options")
    assert r.status_code == 200
    body = r.json()
    assert len(body["templates"]) >= 5
    assert {rg["key"] for rg in body["regions"]} >= {"us", "eu", "mena"}
    assert body["employee_brackets"]


@pytest.mark.asyncio
async def test_register_applies_region_defaults(client):
    r = await client.post("/api/auth/register", json={
        "org_name": "Gulf Repairs", "name": "Ali", "email": "ali@gulf.dev", "password": "pw-12345678",
        "region": "mena", "industry_template": "mobile_phone", "employee_count": "6-20",
        "company_phone": "+971 50 111 2222"})
    assert r.status_code == 201, r.text
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.email == "ali@gulf.dev"))).scalar_one()
        org = (await db.execute(select(Organisation).where(Organisation.id == emp.org_id))).scalar_one()
        assert org.region == "mena"
        assert org.currency_code == "AED"           # region default
        assert org.timezone == "Asia/Dubai"
        assert org.industry_template == "mobile_phone"
        assert org.employee_count == "6-20"
        assert org.company_phone == "+971 50 111 2222"


@pytest.mark.asyncio
async def test_register_explicit_currency_overrides_region(client):
    r = await client.post("/api/auth/register", json={
        "org_name": "EuroFix", "name": "Eva", "email": "eva@euro.dev", "password": "pw-12345678",
        "region": "eu", "currency_code": "GBP", "currency": "£"})
    assert r.status_code == 201
    async with AsyncSessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.email == "eva@euro.dev"))).scalar_one()
        org = (await db.execute(select(Organisation).where(Organisation.id == emp.org_id))).scalar_one()
        assert org.currency_code == "GBP"   # explicit wins over EUR default


@pytest.mark.asyncio
async def test_register_still_works_without_onboarding(client):
    # backward compatible: the minimal register payload still succeeds
    r = await client.post("/api/auth/register", json={
        "org_name": "Plain", "name": "P", "email": "plain@x.dev", "password": "pw-12345678"})
    assert r.status_code == 201
