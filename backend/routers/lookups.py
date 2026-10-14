"""Identifier lookup API (under integrations). VIN/HIN/IMEI structural decoders.

The industry-template registry moved to backend/services/industry.py and is served
by /api/settings/industry-template (see routers/industry.py). This module now only
exposes the identifier decoders that back scanning."""
from fastapi import APIRouter, Depends

from backend.models import Employee
from backend.services.auth import get_current_employee
from backend.services.lookups import decode_hin, decode_imei, decode_vin

router = APIRouter(prefix="/api/integrations/lookup", tags=["integrations"])


@router.get("/vin/{vin}")
async def vin_lookup(vin: str, current: Employee = Depends(get_current_employee)):
    return await decode_vin(vin)


@router.get("/hin/{hin}")
async def hin_lookup(hin: str, current: Employee = Depends(get_current_employee)):
    return decode_hin(hin)


@router.get("/imei/{imei}")
async def imei_lookup(imei: str, current: Employee = Depends(get_current_employee)):
    return await decode_imei(imei)
