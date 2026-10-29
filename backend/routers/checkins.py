"""Self check-in — the staff side (docs/SPEC_self_checkin.md).

The Walk-ins view in Inquiries: today's queue per location, Call next, No-show, Convert to a
work order, opening a check-in by scanning the customer's code. And Settings › Appointments ›
Self check-in: the check-in points of each location, their QR codes and posters.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Checkin, CheckinPoint, Device, Employee, Lead, Location, WorkOrderType
from backend.services import checkin as svc
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/checkins", tags=["checkins"])


def _public_base() -> str:
    from backend.config import settings
    return (getattr(settings, "PUBLIC_BASE_URL", "") or "").rstrip("/")


async def _own(db: AsyncSession, current: Employee, cid: str) -> Checkin:
    c = (await db.execute(select(Checkin).where(
        Checkin.id == cid, Checkin.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Check-in not found")
    return c


async def _location_for(db: AsyncSession, current: Employee, location_id: Optional[str]) -> Optional[str]:
    if location_id:
        ok = (await db.execute(select(Location.id).where(
            Location.id == location_id, Location.org_id == current.org_id))).scalar_one_or_none()
        if not ok:
            raise HTTPException(404, "Location not found")
        return location_id
    return current.location_id


async def _item(db: AsyncSession, c: Checkin, lead: Lead, points: dict) -> dict:
    ans, dec = c.answers or {}, c.decoded or {}
    asset = " ".join(str(x) for x in [ans.get("year") or dec.get("model_year"),
                                       ans.get("make") or ans.get("brand") or dec.get("make") or dec.get("manufacturer"),
                                       ans.get("model") or dec.get("model")] if x)
    seen = None
    col = svc._device_column(c.identifier_kind)
    if col is not None and c.identifier_value:
        d = (await db.execute(select(Device).where(Device.org_id == c.org_id, col == c.identifier_value)
                              .limit(1))).scalar_one_or_none()
        if d:
            seen = {"device_id": d.id, "client_id": d.client_id}
    waited = int(((c.called_at or datetime.utcnow()) - c.created_at).total_seconds() // 60) if c.created_at else 0
    return {
        "id": c.id, "lead_id": c.lead_id, "queue_no": c.queue_no, "kind": c.kind, "state": c.state,
        "name": lead.name, "phone": lead.phone, "email": lead.email, "issue": lead.inquiry,
        "asset": asset, "identifier_kind": c.identifier_kind, "identifier": c.identifier_value,
        "reading_kind": c.reading_kind, "reading": c.reading_value, "key_tag": c.key_tag,
        "photos": len(c.photo_ids or []), "answers": ans, "job_type_id": c.job_type_id,
        "point": points.get(c.point_id), "waited_min": waited, "seen_before": seen,
        "order_id": c.converted_order_id, "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
    }


@router.get("")
async def list_checkins(location_id: Optional[str] = None, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(get_current_employee)):
    """Today's check-ins at a location (the user's own by default): waiting in queue order, the
    ones being served, and key drops not yet started."""
    await svc.ensure_points(db, current.org_id)
    loc = await _location_for(db, current, location_id)
    q = select(Checkin, Lead).join(Lead, Lead.id == Checkin.lead_id).where(Checkin.org_id == current.org_id)
    if loc:
        day = await svc.queue_day(db, current.org_id, loc)
        q = q.where(Checkin.location_id == loc, (Checkin.queue_day == day) | (
            (Checkin.kind == "key_drop") & (Checkin.state == "waiting")))
    else:
        q = q.where(Checkin.state.in_(("waiting", "called")))
    rows = (await db.execute(q.order_by(Checkin.queue_day, Checkin.queue_no))).all()
    points = {p.id: p.name for p in (await db.execute(select(CheckinPoint).where(
        CheckinPoint.org_id == current.org_id))).scalars().all()}
    await db.commit()
    items = [await _item(db, c, lead, points) for c, lead in rows]
    return {
        "location_id": loc,
        "waiting": [i for i in items if i["state"] == "waiting" and i["kind"] == "walk_in"],
        "called": [i for i in items if i["state"] == "called"],
        "key_drops": [i for i in items if i["state"] == "waiting" and i["kind"] == "key_drop"],
        "done": [i for i in items if i["state"] in ("converted", "no_show", "cancelled")],
    }


@router.get("/count")
async def waiting_count(location_id: Optional[str] = None, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(get_current_employee)):
    """The sidebar badge: walk-ins waiting plus key drops not yet started."""
    loc = await _location_for(db, current, location_id)
    q = select(func.count()).select_from(Checkin).where(
        Checkin.org_id == current.org_id, Checkin.state == "waiting")
    if loc:
        q = q.where(Checkin.location_id == loc)
    return {"waiting": int((await db.execute(q)).scalar() or 0)}


@router.get("/by-code/{code}")
async def by_code(code: str, db: AsyncSession = Depends(get_db),
                  current: Employee = Depends(get_current_employee)):
    """Open a check-in from the QR on the customer's phone (counter scanner or Tekly Go). A code
    from another shop finds nothing."""
    code = code.strip().rsplit("/", 1)[-1]
    c = (await db.execute(select(Checkin).where(
        Checkin.status_token == code, Checkin.org_id == current.org_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "No check-in with that code")
    lead = (await db.execute(select(Lead).where(Lead.id == c.lead_id))).scalar_one()
    p = (await db.execute(select(CheckinPoint).where(CheckinPoint.id == c.point_id))).scalar_one()
    return await _item(db, c, lead, {p.id: p.name})


@router.post("/{cid}/call")
async def call_next(cid: str, db: AsyncSession = Depends(get_db),
                    current: Employee = Depends(get_current_employee)):
    """Text the customer that it's their turn and move them to Being served. Never creates a
    job: staff convert after talking to them."""
    c = await _own(db, current, cid)
    if c.state not in ("waiting", "called"):
        raise HTTPException(409, "This check-in is already closed")
    c.state, c.called_at, c.updated_at = "called", c.called_at or datetime.utcnow(), datetime.utcnow()
    lead = (await db.execute(select(Lead).where(Lead.id == c.lead_id))).scalar_one()
    if lead.status == "new":
        lead.status = "in_progress"
    loc = (await db.execute(select(Location).where(Location.id == c.location_id))).scalar_one_or_none()
    texted = await svc.text_customer(db, c, lead.phone,
                                     f"You're up at {loc.name if loc else 'the shop'}, please come to the counter.",
                                     "checkin_called")
    from backend.routers.feed import log_system_event
    await log_system_event(db, c.org_id, "lead", c.lead_id, f"Called to the counter by {current.name}")
    await db.commit()
    return {"ok": True, "state": c.state, "texted": texted == "sent"}


@router.post("/{cid}/no-show")
async def no_show(cid: str, db: AsyncSession = Depends(get_db),
                  current: Employee = Depends(get_current_employee)):
    c = await _own(db, current, cid)
    if c.state in ("converted",):
        raise HTTPException(409, "This check-in is already a job")
    c.state, c.updated_at = "no_show", datetime.utcnow()
    lead = (await db.execute(select(Lead).where(Lead.id == c.lead_id))).scalar_one()
    if lead.status in ("new", "in_progress"):
        lead.status = "lost"
    from backend.routers.feed import log_system_event
    await log_system_event(db, c.org_id, "lead", c.lead_id, f"Marked no-show by {current.name}")
    await db.commit()
    return {"ok": True, "state": c.state}


@router.post("/{cid}/convert")
async def convert(cid: str, db: AsyncSession = Depends(get_db),
                  current: Employee = Depends(require_perm("can_create_tickets"))):
    """Make the work order with everything the customer entered. Doing it twice returns the
    same job."""
    c = await _own(db, current, cid)
    return await svc.convert(db, c, actor=current)


# ── Check-in points (Settings › Appointments › Self check-in) ────────────────
def _point_out(p: CheckinPoint, loc_name: str) -> dict:
    return {"id": p.id, "location_id": p.location_id, "location": loc_name, "name": p.name, "mode": p.mode,
            "kiosk": bool(p.kiosk), "job_type_ids": list(p.job_type_ids or []), "welcome_text": p.welcome_text or "",
            "terms_template_id": p.terms_template_id, "require_photo": bool(p.require_photo),
            "show_wait": bool(p.show_wait), "active": bool(p.active), "scans": p.scans or 0,
            "submits": p.submits or 0, "url": f"{_public_base()}/c/{p.token}", "path": f"/c/{p.token}"}


class PointIn(BaseModel):
    location_id: Optional[str] = None
    name: str = Field(max_length=80)
    mode: str = "auto"
    kiosk: bool = False
    job_type_ids: list[str] = []
    welcome_text: Optional[str] = Field(None, max_length=600)
    terms_template_id: Optional[str] = None
    require_photo: bool = False
    show_wait: bool = True
    active: bool = True


async def _check_point(db: AsyncSession, org_id: str, data: PointIn) -> None:
    if data.mode not in svc.MODES:
        raise HTTPException(422, "Mode must be walk-in, key drop or automatic")
    if not data.name.strip():
        raise HTTPException(422, "Give the check-in point a name")
    if data.job_type_ids:
        n = (await db.execute(select(func.count()).select_from(WorkOrderType).where(
            WorkOrderType.org_id == org_id, WorkOrderType.id.in_(data.job_type_ids)))).scalar()
        if n != len(set(data.job_type_ids)):
            raise HTTPException(422, "Unknown job type")
    if data.terms_template_id:
        from backend.models import DocumentTemplate
        ok = (await db.execute(select(DocumentTemplate.id).where(
            DocumentTemplate.id == data.terms_template_id, DocumentTemplate.org_id == org_id))).scalar_one_or_none()
        if not ok:
            raise HTTPException(422, "Unknown terms template")


async def _own_point(db: AsyncSession, current: Employee, pid: str) -> CheckinPoint:
    p = (await db.execute(select(CheckinPoint).where(
        CheckinPoint.id == pid, CheckinPoint.org_id == current.org_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Check-in point not found")
    return p


@router.get("/points")
async def list_points(db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    await svc.ensure_points(db, current.org_id)
    await db.commit()
    locs = {loc.id: loc.name for loc in (await db.execute(select(Location).where(
        Location.org_id == current.org_id))).scalars().all()}
    rows = (await db.execute(select(CheckinPoint).where(CheckinPoint.org_id == current.org_id)
                             .order_by(CheckinPoint.location_id, CheckinPoint.created_at))).scalars().all()
    return {"items": [_point_out(p, locs.get(p.location_id, "")) for p in rows]}


@router.post("/points", status_code=201)
async def create_point(data: PointIn, db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(require_perm("can_manage_settings"))):
    await _check_point(db, current.org_id, data)
    loc = await _location_for(db, current, data.location_id)
    if not loc:
        raise HTTPException(422, "Choose a location")
    p = CheckinPoint(org_id=current.org_id, location_id=loc, name=data.name.strip(), mode=data.mode,
                     kiosk=data.kiosk, job_type_ids=data.job_type_ids, welcome_text=data.welcome_text or None,
                     terms_template_id=data.terms_template_id, require_photo=data.require_photo,
                     show_wait=data.show_wait, active=data.active, token=svc.new_token())
    db.add(p)
    await db.commit()
    name = (await db.execute(select(Location.name).where(Location.id == loc))).scalar_one()
    return _point_out(p, name)


@router.put("/points/{pid}")
async def update_point(pid: str, data: PointIn, db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(require_perm("can_manage_settings"))):
    p = await _own_point(db, current, pid)
    await _check_point(db, current.org_id, data)
    p.name, p.mode, p.kiosk = data.name.strip(), data.mode, data.kiosk
    p.job_type_ids, p.welcome_text, p.terms_template_id = data.job_type_ids, data.welcome_text or None, data.terms_template_id
    p.require_photo, p.show_wait, p.active = data.require_photo, data.show_wait, data.active
    await db.commit()
    name = (await db.execute(select(Location.name).where(Location.id == p.location_id))).scalar_one()
    return _point_out(p, name)


@router.post("/points/{pid}/rotate")
async def rotate_point(pid: str, db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(require_perm("can_manage_settings"))):
    """Replace the QR code. The old one stops working at once ("ask at the counter"); people
    already in the queue keep their status links."""
    p = await _own_point(db, current, pid)
    p.token, p.rotated_at = svc.new_token(), datetime.utcnow()
    await db.commit()
    name = (await db.execute(select(Location.name).where(Location.id == p.location_id))).scalar_one()
    return _point_out(p, name)


@router.get("/points/{pid}/poster")
async def poster(pid: str, db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    """A printable A4/Letter poster (shop, point and the QR) as HTML the app prints through its
    hidden print frame."""
    from html import escape

    from backend.models import Organisation
    from backend.services.codes import qr_svg
    p = await _own_point(db, current, pid)
    org = (await db.execute(select(Organisation).where(Organisation.id == p.org_id))).scalar_one()
    url = f"{_public_base()}/c/{p.token}"
    head = "Leave your keys here" if p.mode == "key_drop" else "Check in here"
    return {"html": (
        "<!doctype html><html><head><meta charset='utf-8'><title>Check-in poster</title><style>"
        "@page{size:auto;margin:16mm}body{font-family:Arial,sans-serif;text-align:center;color:#111}"
        "h1{font-size:40px;margin:24px 0 6px}h2{font-size:22px;font-weight:normal;margin:0 0 24px}"
        ".qr{width:120mm;height:120mm;margin:0 auto}.qr svg{width:100%;height:100%}"
        "p{font-size:16px;margin:18px 0 0}small{color:#555}</style></head><body>"
        f"<h1>{escape(head)}</h1><h2>{escape(org.name)} · {escape(p.name)}</h2>"
        f"<div class='qr'>{qr_svg(url, module=6)}</div>"
        "<p>Scan with your phone camera. We'll text you when it's your turn.</p>"
        f"<p><small>{escape(url)}</small></p></body></html>")}


@router.get("/points/{pid}/qr.svg")
async def qr(pid: str, db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    from fastapi.responses import Response

    from backend.services.codes import qr_svg
    p = await _own_point(db, current, pid)
    return Response(qr_svg(f"{_public_base()}/c/{p.token}", module=8), media_type="image/svg+xml",
                    headers={"Content-Disposition": f'attachment; filename="checkin-{p.name}.svg"'})
