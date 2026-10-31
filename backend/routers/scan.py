"""Phase 2 — universal scan resolve. A shop-floor scanner (USB keyboard-wedge or
the camera) sends one code; this figures out what it is and where to go: a work
order (barcode / number), a product (sku / barcode / internal code), or an asset
(serial / imei / vin / hin). Org-scoped; first match wins in a sensible order."""
import re
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Device, Employee, Order, Part
from backend.core.client_source import current_client, is_go
from backend.services import pilot
from backend.services.auth import get_current_employee

router = APIRouter(prefix="/api/scan", tags=["scan"])

_TRACK_RE = re.compile(r"/track/([A-Za-z0-9_-]{8,})")
_GO_JOB_RE = re.compile(r"^/go/job/([^/?#]+)")


def _job_link_id(code: str):
    """The job id in a Tekly Go job link (the pilot's QR labels) or a web "/orders?open=<id>" link."""
    if not re.match(r"^(https?://|/)", code, re.I):
        return None
    u = urlparse(code)
    m = _GO_JOB_RE.match(u.path)
    if m:
        return m.group(1)
    if u.path.rstrip("/") == "/orders":
        return (parse_qs(u.query).get("open") or [None])[0]
    return None


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
    res, by = await _resolve(db, current, code)
    if res.get("type") == "product":
        res["scan"] = scan.as_dict()   # ii144b: batch, expiry and serial from a GS1 label
    # every scan is a job lookup for the Go pilot report (the code itself isn't kept)
    await pilot.record_lookup(db, current, via="scan", by=by,
                              surface="go_scan" if is_go(current_client()) else "web_scan",
                              order_id=res["id"] if res.get("type") == "order" else None,
                              matched=res["matched"])
    return res


async def _resolve(db: AsyncSession, current: Employee, code: str):
    up = code.upper()
    org = current.org_id

    # a Tekly Go job link (the pilot's QR labels) or a web-app "/orders?open=<id>" link
    jid = _job_link_id(code)
    if jid:
        o = (await db.execute(select(Order).where(Order.org_id == org, Order.id == jid))).scalars().first()
        if o:
            return {"matched": True, "type": "order", "id": o.id, "route": "/orders",
                    "label": o.order_number, "is_estimate": bool(o.is_estimate)}, "link"
        return {"matched": False, "code": code}, "link"

    # 0) a customer ticket's tracking-link QR (".../track/<token>") — the public page QR
    m = _TRACK_RE.search(code)
    if m:
        o = (await db.execute(select(Order).where(
            Order.org_id == org, Order.public_token == m.group(1)))).scalars().first()
        if o:
            return {"matched": True, "type": "order", "id": o.id, "route": "/orders",
                    "label": o.order_number, "is_estimate": bool(o.is_estimate)}, "track"

    # 1) work order — barcode is stored upper-cased; order_number as typed (WO-00001)
    o = (await db.execute(select(Order).where(
        Order.org_id == org, or_(Order.barcode == up, Order.order_number == code)))).scalars().first()
    if o:
        return {"matched": True, "type": "order", "id": o.id, "route": "/orders",
                "label": o.order_number, "is_estimate": bool(o.is_estimate)}, "wo"

    # 2) ii144c: a bin label — screens that count, move or receive stock use it to set which bin the
    #    next scans mean; elsewhere it opens the bin. A typed bin name is tried after products.
    from backend.routers.bins import find_by_code

    def _bin_hit(b):
        return {"matched": True, "type": "bin", "id": b.id, "route": "/inventory?tab=bins", "label": b.name,
                "location_id": b.location_id, "warehouse_id": b.warehouse_id, "barcode": b.barcode}, "bin"
    b = await find_by_code(db, org, code, names=False)
    if b:
        return _bin_hit(b)

    # 3) product/part — sku / barcode / internal code
    from backend.services import product_codes
    p = (await db.execute(select(Part).where(
        Part.org_id == org, product_codes.matches_code(org, code)))).scalars().first()   # ii144a: extra barcodes too
    if p:
        return {"matched": True, "type": "product", "id": p.id, "route": "/inventory", "label": p.name}, "part"

    b = await find_by_code(db, org, code)
    if b:
        return _bin_hit(b)

    # 4) asset — serial / imei / vin(upper) / hin(upper)
    from backend.services import identifiers as _ids
    conds = [col == v for col, v in ((Device.serial, _ids.normalize("serial", code)),
                                      (Device.imei, _ids.normalize("imei", code)),
                                      (Device.vin, _ids.normalize("vin", code)),
                                      (Device.hin, _ids.normalize("hin", code))) if v]
    d = (await db.execute(select(Device).where(Device.org_id == org, or_(*conds)))).scalars().first()
    if d:
        label = " ".join(x for x in [d.brand, d.model] if x) or (d.serial or d.imei or "Asset")
        return {"matched": True, "type": "asset", "id": d.id, "route": "/assets", "label": label}, "asset"

    return {"matched": False, "code": code}, "none"


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
