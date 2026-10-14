"""144jr — RegionPack API. The FE reads the effective pack to render the right
currency, tax label, e-invoice availability, and dunning channel for the shop."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, Organisation
from backend.services import regions
from backend.services.auth import get_current_employee

router = APIRouter(prefix="/api/regions", tags=["regions"])


@router.get("/pack")
async def my_pack(db: AsyncSession = Depends(get_db),
                  current: Employee = Depends(get_current_employee)):
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    if not org:
        return regions.pack_for(None)
    return regions.pack_for(org.country, org.region, org.currency_code, org.currency)


@router.get("/catalog")
async def region_catalog(current: Employee = Depends(get_current_employee)):
    return {"items": regions.catalog()}
