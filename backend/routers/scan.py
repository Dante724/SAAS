"""Phase 2 — universal scan resolve. A shop-floor scanner (USB keyboard-wedge or
the camera) sends one code; this figures out what it is and where to go: a work
order (barcode / number), a product (sku / barcode / internal code), or an asset
(serial / imei / vin / hin). Org-scoped; first match wins in a sensible order."""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Device, Employee, Order, Part
from backend.services.auth import get_current_employee

router = APIRouter(prefix="/api/scan", tags=["scan"])

_TRACK_RE = re.compile(r"/track/([A-Za-z0-9_-]{8,})")


class ScanIn(BaseModel):
    code: str


@router.post("/resolve")
async def resolve(data: ScanIn, db: AsyncSession = Depends(get_db),
                  current: Employee = Depends(get_current_employee)):
    code = (data.code or "").strip()
    if not code:
        raise HTTPException(422, "code required")
    up = code.upper()
    org = current.org_id

    # 0) a customer ticket's tracking-link QR (".../track/<token>") — the public page QR
    m = _TRACK_RE.search(code)
    if m:
        o = (await db.execute(select(Order).where(
            Order.org_id == org, Order.public_token == m.group(1)))).scalars().first()
        if o:
            return {"matched": True, "type": "order", "id": o.id, "route": "/orders",
                    "label": o.order_number, "is_estimate": bool(o.is_estimate)}

    # 1) work order — barcode is stored upper-cased; order_number as typed (WO-00001)
    o = (await db.execute(select(Order).where(
        Order.org_id == org, or_(Order.barcode == up, Order.order_number == code)))).scalars().first()
    if o:
        return {"matched": True, "type": "order", "id": o.id, "route": "/orders",
                "label": o.order_number, "is_estimate": bool(o.is_estimate)}

    # 2) product/part — sku / barcode / internal code
    p = (await db.execute(select(Part).where(
        Part.org_id == org, or_(Part.sku == code, Part.barcode == code, Part.code == code)))).scalars().first()
    if p:
        return {"matched": True, "type": "product", "id": p.id, "route": "/inventory", "label": p.name}

    # 3) asset — serial / imei / vin(upper) / hin(upper)
    from backend.services import identifiers as _ids
    conds = [col == v for col, v in ((Device.serial, _ids.normalize("serial", code)),
                                      (Device.imei, _ids.normalize("imei", code)),
                                      (Device.vin, _ids.normalize("vin", code)),
                                      (Device.hin, _ids.normalize("hin", code))) if v]
    d = (await db.execute(select(Device).where(Device.org_id == org, or_(*conds)))).scalars().first()
    if d:
        label = " ".join(x for x in [d.brand, d.model] if x) or (d.serial or d.imei or "Asset")
        return {"matched": True, "type": "asset", "id": d.id, "route": "/assets", "label": label}

    return {"matched": False, "code": code}
