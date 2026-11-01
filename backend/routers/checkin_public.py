"""Self check-in — the customer's side, no sign-in (docs/SPEC_self_checkin.md).

Everything is reached by a token: a check-in point's QR token, or a check-in's own status
token. The shop and location always come from the token on the server — the page never sends
an org, location or point id — so one shop's page can't read or write another's queue.
"""
import base64
import binascii
import hashlib
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Checkin, CheckinPoint, Lead, Location, Order, Organisation
from backend.services import checkin as svc

router = APIRouter(prefix="/api/public/checkin", tags=["public"])

MAX_SIGNATURE = 400_000          # data-URL characters
MAX_PHOTO = 7_000_000            # data-URL characters (~5 MB image)
MAX_PHOTOS = 3
MAX_PER_POINT_DAY = 150          # new check-ins a point takes per queue day
MAX_PER_ORG_DAY = 500            # new check-ins a shop takes per 24 h, all points
MAX_PHONES_PER_IP = 5            # distinct numbers one IP may check in per 24 h (each one is a text)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


async def _limit(bucket: str, key: str, limit: int, window: int) -> None:
    from backend.services.ratelimit import aenforce
    await aenforce(bucket, key, limit=limit, window_sec=window)


def _ip(request: Request) -> str:
    from backend.services.ratelimit import client_ip
    return client_ip(request)


async def _limit_phones_per_ip(ip: str, phone: str) -> None:
    """Counts each distinct number once per IP: the first sight of (ip, phone) bumps the IP's
    count, repeats of the same number don't."""
    try:
        await _limit("checkin_ip_phone_seen", f"{ip}:{phone}", 1, 86400)
    except HTTPException:
        return                                   # seen already: not a new number
    await _limit("checkin_ip_phones", ip, MAX_PHONES_PER_IP, 86400)


def _is_png(data_url: str) -> bool:
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except (binascii.Error, ValueError, IndexError):
        return False
    return raw.startswith(PNG_MAGIC)


async def _check_caps(db: AsyncSession, point: CheckinPoint, day) -> None:
    from sqlalchemy import func
    n_point = (await db.execute(select(func.count()).select_from(Checkin).where(
        Checkin.point_id == point.id, Checkin.queue_day == day))).scalar() or 0
    n_org = (await db.execute(select(func.count()).select_from(Checkin).where(
        Checkin.org_id == point.org_id, Checkin.created_at >= datetime.utcnow() - timedelta(days=1)))).scalar() or 0
    if n_point >= MAX_PER_POINT_DAY or n_org >= MAX_PER_ORG_DAY:
        raise HTTPException(429, "Check-in is full for today. Please ask at the counter.")


async def _waiting(db: AsyncSession, point: CheckinPoint, day) -> int:
    from sqlalchemy import func
    return int((await db.execute(select(func.count()).select_from(Checkin).where(
        Checkin.org_id == point.org_id, Checkin.location_id == point.location_id, Checkin.queue_day == day,
        Checkin.kind == "walk_in", Checkin.state == "waiting"))).scalar() or 0)


# ── Status page (declared first so /s/… never reads as a point token) ────────
async def _by_status(db: AsyncSession, status_token: str) -> Checkin:
    c = (await db.execute(select(Checkin).where(Checkin.status_token == status_token))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Check-in not found")
    return c


@router.get("/s/{status_token}")
async def checkin_status(status_token: str, db: AsyncSession = Depends(get_db)):
    """What the customer's status page shows: their place, never anyone else's details."""
    c = await _by_status(db, status_token)
    lead = (await db.execute(select(Lead).where(Lead.id == c.lead_id))).scalar_one()
    point = (await db.execute(select(CheckinPoint).where(CheckinPoint.id == c.point_id))).scalar_one()
    org = (await db.execute(select(Organisation).where(Organisation.id == c.org_id))).scalar_one()
    loc = (await db.execute(select(Location).where(Location.id == c.location_id))).scalar_one_or_none()
    ahead = await svc.ahead_of(db, c)
    wait = None
    if point.show_wait and c.state == "waiting" and c.kind == "walk_in":
        wait = svc.round_wait((ahead + 1) * await svc.minutes_per_person(db, c.org_id, c.location_id))
    track = None
    if c.converted_order_id:
        o = (await db.execute(select(Order).where(Order.id == c.converted_order_id))).scalar_one_or_none()
        token = getattr(o, "public_token", None) if o else None
        track = f"/track/{token}" if token else None
    asset = " ".join(str(x) for x in [(c.decoded or {}).get("model_year") or (c.answers or {}).get("year"),
                                       (c.answers or {}).get("make") or (c.answers or {}).get("brand")
                                       or (c.decoded or {}).get("make"),
                                       (c.answers or {}).get("model") or (c.decoded or {}).get("model")] if x)
    return {
        "first_name": (lead.name or "").split(" ")[0], "queue_no": c.queue_no, "state": c.state,
        "kind": c.kind, "ahead": ahead, "wait_min": wait,
        "shop": {"name": org.name, "logo": getattr(org, "logo_data", None),
                 "color": getattr(org, "brand_primary_color", None)},
        "location": loc.name if loc else "",
        "summary": {"asset": asset, "issue": (lead.inquiry or "")[:200]},
        "code": c.status_token, "track_url": track,
    }


@router.get("/s/{status_token}/qr.svg")
async def checkin_qr(status_token: str, db: AsyncSession = Depends(get_db)):
    """The code the customer shows at the counter; staff scan it to open their check-in."""
    from fastapi.responses import Response

    from backend.services.codes import qr_svg
    c = await _by_status(db, status_token)
    return Response(qr_svg(c.status_token, module=6), media_type="image/svg+xml",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.post("/s/{status_token}/cancel")
async def cancel_checkin(status_token: str, db: AsyncSession = Depends(get_db)):
    c = await _by_status(db, status_token)
    if c.state in ("waiting", "called"):
        c.state = "cancelled"
        c.updated_at = datetime.utcnow()
        lead = (await db.execute(select(Lead).where(Lead.id == c.lead_id))).scalar_one()
        if lead.status in ("new", "in_progress"):
            lead.status = "lost"
        from backend.routers.feed import log_system_event
        await log_system_event(db, c.org_id, "lead", c.lead_id, "Customer left the check-in queue")
        await db.commit()
    return {"ok": True, "state": c.state}


# ── The check-in page ────────────────────────────────────────────────────────
@router.get("/{token}")
async def checkin_page(token: str, db: AsyncSession = Depends(get_db)):
    """The form for this point, the shop's look and whether it's walk-in or key drop now."""
    point = await svc.point_by_token(db, token)
    org = (await db.execute(select(Organisation).where(Organisation.id == point.org_id))).scalar_one()
    loc = (await db.execute(select(Location).where(Location.id == point.location_id))).scalar_one_or_none()
    mode = await svc.effective_mode(db, point)
    form = await svc.resolve_form(db, point)
    day = await svc.queue_day(db, point.org_id, point.location_id)
    waiting = await _waiting(db, point, day)
    wait = None
    if point.show_wait and mode == "walk_in":
        wait = svc.round_wait((waiting + 1) * await svc.minutes_per_person(db, point.org_id, point.location_id))
    point.scans = (point.scans or 0) + 1
    await db.commit()
    return {
        "shop": {"name": org.name, "logo": getattr(org, "logo_data", None),
                 "color": getattr(org, "brand_primary_color", None)},
        "location": {"name": loc.name if loc else "", "phone": getattr(loc, "phone", None) if loc else None},
        "point": {"name": point.name, "kiosk": bool(point.kiosk),
                  "closed": point.mode == "auto" and mode == "key_drop"},
        "mode": mode, "form": form, "waiting": waiting, "wait_min": wait,
    }


class IdentifyIn(BaseModel):
    value: str = Field(max_length=100)
    kind: Optional[str] = Field(None, max_length=20)


@router.post("/{token}/identify")
async def identify(token: str, data: IdentifyIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Tidy and decode a scanned or typed VIN / HIN / IMEI / serial. Returns what the code says
    (make, model, year) and whether it looks mistyped — never whether this shop knows it."""
    from backend.services import identifiers as ids
    from backend.services import lookups
    point = await svc.point_by_token(db, token)
    await _limit("checkin_identify", f"{point.id}:{_ip(request)}", 30, 300)
    kind = data.kind if data.kind in ids.KINDS else ids.guess_kind(data.value)
    norm = ids.normalize(kind, data.value)
    out = {"kind": kind, "normalized": norm, "problem": ids.problem(kind, norm) if norm else None,
           "make": None, "model": None, "year": None}
    if not norm:
        return out
    dec = {}
    if kind == "vin":
        dec = await lookups.decode_vin(norm)
    elif kind == "hin":
        dec = lookups.decode_hin(norm)
    elif kind == "imei":
        dec = await lookups.decode_imei(norm)
    out.update({"make": _name_case(dec.get("make") or dec.get("manufacturer")), "model": dec.get("model"),
                "year": dec.get("model_year")})
    return out


def _name_case(v):
    """Decoders shout ("HONDA"); show "Honda". Short marques (BMW, GMC, KIA) stay as they are."""
    if isinstance(v, str) and len(v) > 3 and v.isupper():
        return "-".join(p.capitalize() for p in v.title().split("-"))
    return v


class CheckinIn(BaseModel):
    name: str = Field(max_length=200)
    phone: str = Field(max_length=40)
    email: Optional[str] = Field(None, max_length=200)
    job_type_id: Optional[str] = None
    asset_group: Optional[str] = Field(None, max_length=40)
    identifier_kind: Optional[str] = Field(None, max_length=20)
    identifier: Optional[str] = Field(None, max_length=100)
    reading: Optional[float] = Field(None, ge=0, le=10_000_000)
    status_token: Optional[str] = Field(None, max_length=100)   # proves it's their own check-in to update
    issue: str = Field("", max_length=2000)
    answers: dict = {}
    signature: Optional[str] = None
    agree: bool = False
    photos: list[str] = []
    key_tag: Optional[str] = Field(None, max_length=40)
    website: Optional[str] = None            # honeypot: people never fill it in


@router.post("/{token}", status_code=201)
async def submit_checkin(token: str, data: CheckinIn, request: Request, db: AsyncSession = Depends(get_db)):
    from backend.services import identifiers as ids
    from backend.services import media
    from backend.services.call_contacts import digits
    point = await svc.point_by_token(db, token)
    ip = _ip(request)
    await _limit("checkin_submit", f"{point.id}:{ip}", 6, 300)
    if data.website:
        raise HTTPException(422, "Couldn't check you in. Please ask at the counter.")
    name = data.name.strip()
    phone_digits = digits(data.phone)
    if not name:
        raise HTTPException(422, "Enter your name")
    if len(phone_digits) < 7:
        raise HTTPException(422, "Enter a mobile number so the shop can reach you")

    mode = await svc.effective_mode(db, point)
    form = await svc.resolve_form(db, point)
    type_ids = [t["id"] for t in form["job_types"]]
    job_type_id = data.job_type_id if data.job_type_id in type_ids else (type_ids[0] if len(type_ids) == 1 else None)
    if type_ids and not job_type_id:
        raise HTTPException(422, "Choose what you need")
    issue = (data.issue or "").strip()
    if not issue:
        raise HTTPException(422, "Tell us what's wrong")
    group = next((g for g in form["groups"] if g["key"] == data.asset_group), None)
    if data.asset_group and not group:
        raise HTTPException(422, "Choose what we're looking at")
    ident_kind = ident_value = None
    if (data.identifier or "").strip():
        allowed = (group or {}).get("identifier", {}).get("allowed") or svc.IDENTIFIER_KINDS
        ident_kind = data.identifier_kind if data.identifier_kind in allowed else (
            ids.guess_kind(data.identifier) if ids.guess_kind(data.identifier) in allowed else allowed[0])
        ident_value = (ids.normalize(ident_kind, data.identifier) if ident_kind in ids.KINDS
                       else data.identifier.strip().upper())[:100]
    answers = svc.clean_answers(form, job_type_id, data.answers)
    if not data.agree or not (data.signature or "").startswith("data:image/png;base64,"):
        raise HTTPException(422, "Sign to agree to the shop's terms")
    if len(data.signature) > MAX_SIGNATURE:
        raise HTTPException(422, "That signature is too large. Clear it and sign again.")
    if not _is_png(data.signature):
        raise HTTPException(422, "Sign to agree to the shop's terms")
    if len(data.photos) > MAX_PHOTOS:
        raise HTTPException(422, f"Add up to {MAX_PHOTOS} photos")
    if mode == "key_drop" and point.require_photo and not data.photos:
        raise HTTPException(422, "Add a photo of the dashboard")
    for p in data.photos:
        if not p.startswith("data:image/") or len(p) > MAX_PHOTO:
            raise HTTPException(422, "Photos must be images under 5 MB")

    # Counted only once the form is valid, so a customer fixing a mistake isn't locked out.
    await _limit("checkin_phone", f"{point.id}:{phone_digits}", 3, 1800)
    await _limit_phones_per_ip(ip, phone_digits)

    decoded = None
    if ident_value and ident_kind in ("vin", "hin", "imei"):
        from backend.services import lookups
        dec = (await lookups.decode_vin(ident_value) if ident_kind == "vin" else
               lookups.decode_hin(ident_value) if ident_kind == "hin" else await lookups.decode_imei(ident_value))
        decoded = {k: _name_case(dec.get(k)) for k in ("make", "model", "model_year", "manufacturer") if dec.get(k)}
    reading_kind = form["reading"]["key"] if form["reading"] and data.reading is not None else None
    day = await svc.queue_day(db, point.org_id, point.location_id)

    # Updating a check-in needs its status token (only its owner has it); a phone number alone
    # never reaches someone else's place, token or signature: that's a new check-in.
    existing = None
    if data.status_token:
        existing = (await db.execute(select(Checkin).join(Lead, Lead.id == Checkin.lead_id).where(
            Checkin.status_token == data.status_token, Checkin.point_id == point.id, Checkin.queue_day == day,
            Checkin.state == "waiting", Lead.phone == data.phone.strip()))).scalars().first()
    if not existing:
        await _check_caps(db, point, day)

    terms_hash = hashlib.sha256(form["terms"].encode()).hexdigest()
    jt_name = next((t["name"] for t in form["job_types"] if t["id"] == job_type_id), None)
    inquiry = " · ".join(x for x in [jt_name, issue] if x)[:2000]
    if existing:
        c = existing
        lead = (await db.execute(select(Lead).where(Lead.id == c.lead_id))).scalar_one()
        lead.name, lead.email, lead.inquiry = name, (data.email or lead.email), inquiry
    else:
        lead = Lead(org_id=point.org_id, location_id=point.location_id, name=name, phone=data.phone.strip(),
                    email=(data.email or "").strip() or None, source="walk_in", inquiry=inquiry,
                    sla_due_at=datetime.utcnow() + timedelta(minutes=15))
        db.add(lead)
        await db.flush()
        c = Checkin(org_id=point.org_id, location_id=point.location_id, point_id=point.id, lead_id=lead.id,
                    queue_day=day, queue_no=0, state="waiting", status_token=svc.new_token())
    lead.device_brand = (answers.get("make") or answers.get("brand") or (decoded or {}).get("make")
                         or (decoded or {}).get("manufacturer"))
    lead.device_model = answers.get("model") or (decoded or {}).get("model")
    lead.serial = ident_value
    c.kind = mode
    c.job_type_id, c.asset_group = job_type_id, (group or {}).get("key")
    c.identifier_kind, c.identifier_value, c.decoded = ident_kind, ident_value, decoded
    c.reading_kind, c.reading_value = reading_kind, (data.reading if reading_kind else None)
    c.answers, c.form_snapshot = answers, form
    c.signature_png, c.signer_name, c.signer_ip, c.terms_hash = data.signature, name, ip[:64], terms_hash
    c.key_tag = (data.key_tag or "").strip() or None
    if data.photos:
        try:
            blobs = [await media.store(db, point.org_id, p, name="checkin.jpg") for p in data.photos]
        except ValueError:
            raise HTTPException(422, "Photos must be images under 5 MB") from None
        c.photo_ids = [b.id for b in blobs]

    if not existing:
        for _ in range(5):   # two people at once: the unique index makes the loser try the next number
            c.queue_no = await svc.next_queue_no(db, point.org_id, point.location_id, day)
            try:
                async with db.begin_nested():
                    db.add(c)
                    await db.flush()
                break
            except IntegrityError:
                continue
        else:
            raise HTTPException(503, "The queue is busy. Try again in a moment.")
        point.submits = (point.submits or 0) + 1

    from backend.routers.feed import log_system_event
    what = "Key drop" if mode == "key_drop" else f"Walk-in #{c.queue_no}"
    await log_system_event(db, point.org_id, "lead", lead.id, f"{what}: {name} checked in at {point.name}")
    if not existing:
        loc = (await db.execute(select(Location).where(Location.id == point.location_id))).scalar_one_or_none()
        asset = " ".join(x for x in [lead.device_brand, lead.device_model] if x) or (form["asset_label"])
        title = (f"Key drop: {name}" + (f", key tag {c.key_tag}" if c.key_tag else "")) if mode == "key_drop" \
            else f"{name} checked in, #{c.queue_no}"
        await svc.alert_staff(db, point.org_id, point.location_id, title, f"{asset} · {issue[:80]}")
        place = loc.name if loc else "the shop"
        body = (f"Hi, we've got your keys and details at {place}. "   # never the typed name: anyone can type one
                f"We'll text you once we've looked at it. {svc.status_url(c)}") if mode == "key_drop" else (
                f"You're checked in at {place}. You're #{c.queue_no}. Track your place: {svc.status_url(c)}")
        texted = await svc.text_customer(db, c, lead.phone, body, "checkin_created")
    else:
        texted = "skipped"
    await db.commit()
    ahead = await svc.ahead_of(db, c)
    wait = None
    if point.show_wait and mode == "walk_in":
        wait = svc.round_wait((ahead + 1) * await svc.minutes_per_person(db, point.org_id, point.location_id))
    return {"status_token": c.status_token, "queue_no": c.queue_no, "kind": c.kind, "ahead": ahead,
            "wait_min": wait, "texted": texted == "sent", "updated": bool(existing)}
