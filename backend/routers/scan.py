"""Phase 2 — universal scan resolve. A shop-floor scanner (USB keyboard-wedge or
the camera) sends one code; this figures out what it is and where to go: a work
order (barcode / number), a product (sku / barcode / internal code), or an asset
(serial / imei / vin / hin). Org-scoped; first match wins in a sensible order."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Device, Employee, Order, Part
from backend.services.auth import get_current_employee

router = APIRouter(prefix="/api/scan", tags=["scan"])


class ScanIn(BaseModel):
    code: str = Field(max_length=500)


@router.post("/resolve")
async def resolve(data: ScanIn, db: AsyncSession = Depends(get_db),
                  current: Employee = Depends(get_current_employee)):
    from backend.services import scan_codes
    scan = scan_codes.parse(data.code)
    code = scan.text.strip()           # ii144b: AIM prefix / control characters removed, GS1 read
    if not code:
        raise HTTPException(422, "code required")
    up = code.upper()
    org = current.org_id

    # 1) work order — barcode is stored upper-cased; order_number as typed (WO-00001)
    o = (await db.execute(select(Order).where(
        Order.org_id == org, or_(Order.barcode == up, Order.order_number == code)))).scalars().first()
    if o:
        return {"matched": True, "type": "order", "id": o.id, "route": "/orders",
                "label": o.order_number, "is_estimate": bool(o.is_estimate)}

    # 2) product/part — sku / barcode / internal code
    from backend.services import product_codes
    p = (await db.execute(select(Part).where(
        Part.org_id == org, product_codes.matches_code(org, code)))).scalars().first()   # ii144a: extra barcodes too
    if p:
        return {"matched": True, "type": "product", "id": p.id, "route": "/inventory", "label": p.name,
                "scan": scan.as_dict()}

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


@router.post("/parse")
async def parse_scan(data: ScanIn, db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(get_current_employee)):
    """ii144b — what a scanned code says, without acting on it: the code without scanner prefixes,
    its GTIN, and — for a GS1 label (GS1-128 / GS1 DataMatrix / GS1 QR) — batch, expiry and serial,
    plus the shop's product it belongs to. Intake screens use it to fill a line from one scan."""
    from backend.services import product_codes, scan_codes
    scan = scan_codes.parse(data.code)
    if not scan.text:
        raise HTTPException(422, "code required")
    p = (await db.execute(select(Part).where(
        Part.org_id == current.org_id, Part.active == True,  # noqa: E712
        product_codes.matches_code(current.org_id, data.code)))).scalars().first()
    return {**scan.as_dict(), "product": {"id": p.id, "name": p.name, "serialized": bool(p.serialized),
                                          "expiry_tracking": bool(p.expiry_tracking)} if p else None}
