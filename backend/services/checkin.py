"""Self check-in (docs/SPEC_self_checkin.md).

A customer scans a check-in point's QR, fills a form built from the shop's own setup and gets a
place in the queue; staff call them and convert the check-in into a fully filled work order.

The form is never designed twice: it comes from the shop's industry template (asset types, the
identifier per type, a short allow-list of standard fields such as mileage or hours), the job
types the point offers, and the custom fields ticked "Ask at check-in". Only those ever reach
the public page.
"""
import re
import secrets
from datetime import date, datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Checkin,
    CheckinPoint,
    CustomField,
    Device,
    DocumentSignature,
    DocumentTemplate,
    Lead,
    Location,
    Organisation,
    WorkOrderType,
)
from backend.services import industry

MODES = ("walk_in", "key_drop", "auto")
# Readings taken at every visit; the first one the template has is asked for.
READING_KEYS = ("mileage", "hours", "odometer_hours")
# Standard template fields a customer may fill in. Everything else (passcodes, CPU, notes…) is
# for staff.
CUSTOMER_FIELDS = {"make", "brand", "model", "year", "trim", "color", "plate", "plate_registration",
                   "registration", "length", "engine_model", "frame_size", "unit_number"}
IDENTIFIER_KINDS = ("vin", "hin", "imei", "serial", "engine_serial")
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_TERMS = ("I agree to the shop's repair terms and authorise them to inspect the item "
                 "and contact me with an estimate before any paid work.")
FALLBACK_MIN_PER_PERSON = 10


def new_token() -> str:
    return secrets.token_urlsafe(24)


# ── Points ───────────────────────────────────────────────────────────────────
async def ensure_points(db: AsyncSession, org_id: str) -> None:
    """Every active location has at least one point (a "Front door"), so check-in works with
    no setup. Flushes; the caller commits."""
    have = set((await db.execute(select(CheckinPoint.location_id).where(
        CheckinPoint.org_id == org_id))).scalars().all())
    locs = (await db.execute(select(Location).where(
        Location.org_id == org_id, Location.active == True))).scalars().all()  # noqa: E712
    added = False
    for loc in locs:
        if loc.id not in have:
            db.add(CheckinPoint(org_id=org_id, location_id=loc.id, name="Front door", mode="auto",
                                job_type_ids=[], token=new_token()))
            added = True
    if added:
        await db.flush()


async def point_by_token(db: AsyncSession, token: str) -> CheckinPoint:
    """The point behind a public QR. A code that was replaced or switched off gets a friendly
    410, never another shop's data."""
    p = (await db.execute(select(CheckinPoint).where(CheckinPoint.token == token))).scalar_one_or_none()
    if not p or not p.active:
        raise HTTPException(410, "This code isn't used any more. Please ask at the counter.")
    return p


# ── Clock ────────────────────────────────────────────────────────────────────
async def shop_now(db: AsyncSession, org_id: str, location_id: str, now: Optional[datetime] = None) -> datetime:
    """The location's wall clock (naive)."""
    from backend.services.dates import shop_tzname, utc_to_wall
    tz = await shop_tzname(db, org_id, location_id)
    return utc_to_wall(now or datetime.utcnow(), tz)


def is_open(loc: Optional[Location], wall: datetime) -> bool:
    """Open right now by the location's working hours. A location with no hours set counts as
    open (walk-in), so check-in never refuses people because hours weren't filled in."""
    wh = getattr(loc, "working_hours", None) if loc else None
    if not isinstance(wh, dict) or not wh:
        return True
    day = wh.get(_WEEKDAYS[wall.weekday()])
    if not isinstance(day, dict) or day.get("closed"):
        return False

    def _m(hhmm) -> Optional[int]:
        try:
            h, m = str(hhmm).split(":")
            return int(h) * 60 + int(m)
        except (ValueError, TypeError):
            return None
    start, end = _m(day.get("open")), _m(day.get("close"))
    if start is None or end is None:
        return True
    cur = wall.hour * 60 + wall.minute
    return start <= cur < end


async def effective_mode(db: AsyncSession, point: CheckinPoint, now: Optional[datetime] = None) -> str:
    """walk_in or key_drop. "auto" follows the location's working hours."""
    if point.mode in ("walk_in", "key_drop"):
        return point.mode
    loc = (await db.execute(select(Location).where(Location.id == point.location_id))).scalar_one_or_none()
    wall = await shop_now(db, point.org_id, point.location_id, now)
    return "walk_in" if is_open(loc, wall) else "key_drop"


# ── The form ─────────────────────────────────────────────────────────────────
def _field_out(f: CustomField) -> dict:
    return {"key": f.field_key, "label": f.field_label, "type": f.field_type, "entity": f.entity_type,
            "required": bool(f.required), "placeholder": f.placeholder or "",
            "options": list(f.options or []) if isinstance(f.options, list) else [],
            "show_when": ({"key": f.show_when_field_key, "op": f.show_when_op, "value": f.show_when_value}
                          if f.show_when_field_key and f.show_when_op else None)}


async def _job_types(db: AsyncSession, org_id: str, point: CheckinPoint) -> list[WorkOrderType]:
    q = select(WorkOrderType).where(WorkOrderType.org_id == org_id,
                                    WorkOrderType.active == True)  # noqa: E712
    ids = [i for i in (point.job_type_ids or []) if isinstance(i, str)]
    if ids:
        rows = (await db.execute(q.where(WorkOrderType.id.in_(ids)))).scalars().all()
        by_id = {t.id: t for t in rows}
        return [by_id[i] for i in ids if i in by_id]
    rows = (await db.execute(q.order_by(WorkOrderType.is_default.desc(), WorkOrderType.sort_order,
                                        WorkOrderType.name))).scalars().all()
    return rows[:1]


def _terms_text(tpl: Optional[DocumentTemplate]) -> str:
    if not tpl or not (tpl.body_html or "").strip():
        return DEFAULT_TERMS
    text = re.sub(r"<(br|/p|/div|/li)[^>]*>", "\n", tpl.body_html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    import html as _h
    return _h.unescape(text)[:6000] or DEFAULT_TERMS


async def resolve_form(db: AsyncSession, point: CheckinPoint) -> dict:
    """The check-in form for this point, as the customer sees it."""
    org = (await db.execute(select(Organisation).where(Organisation.id == point.org_id))).scalar_one()
    tkey = industry.normalize_template_key(getattr(org, "industry_template", None))
    tpl = industry.describe(tkey)
    schema = industry.field_schema(tkey)
    fields = [{"key": f["key"], "label": f["label"], "type": f["type"]}
              for f in schema if f["key"] in CUSTOMER_FIELDS]
    reading = next(({"key": f["key"], "label": f["label"]} for f in schema if f["key"] in READING_KEYS), None)

    types = await _job_types(db, point.org_id, point)
    order_fields = {}
    if types:
        rows = (await db.execute(select(CustomField).where(
            CustomField.org_id == point.org_id, CustomField.type_id.in_([t.id for t in types]),
            CustomField.entity_type == "order", CustomField.ask_at_checkin == True)  # noqa: E712
            .order_by(CustomField.sort_order))).scalars().all()
        for f in rows:
            if f.field_type != "header":
                order_fields.setdefault(f.type_id, []).append(_field_out(f))
    device_fields = [_field_out(f) for f in (await db.execute(select(CustomField).where(
        CustomField.org_id == point.org_id, CustomField.type_id.is_(None),
        CustomField.entity_type == "device", CustomField.ask_at_checkin == True)  # noqa: E712
        .order_by(CustomField.sort_order))).scalars().all() if f.field_type != "header"]

    terms_tpl = None
    if point.terms_template_id:
        terms_tpl = (await db.execute(select(DocumentTemplate).where(
            DocumentTemplate.id == point.terms_template_id, DocumentTemplate.org_id == point.org_id,
            DocumentTemplate.active == True))).scalar_one_or_none()  # noqa: E712
    groups = [{"key": g["key"], "label": g["label"],
               "identifier": {"primary": g["identifier"]["primary"],
                              "allowed": [k for k in g["identifier"]["allowed"] if k in IDENTIFIER_KINDS]}}
              for g in tpl["groups"]]
    return {
        "asset_label": tpl["asset_label"],
        "groups": groups,
        "fields": fields,
        "reading": reading,
        "job_types": [{"id": t.id, "name": t.name, "fields": order_fields.get(t.id, [])} for t in types],
        "device_fields": device_fields,
        "terms": _terms_text(terms_tpl),
        "require_photo": bool(point.require_photo),
        "welcome_text": point.welcome_text or "",
    }


def _visible(field: dict, values: dict) -> bool:
    rule = field.get("show_when")
    if not rule:
        return True
    cur, want, op = values.get(rule["key"]), rule.get("value"), rule.get("op")
    empty = cur is None or (isinstance(cur, str) and not cur.strip())
    if op == "filled":
        return not empty
    if empty:
        return False
    if op == "is":
        return str(cur) == str(want)
    if op == "is_not":
        return str(cur) != str(want)
    try:
        n, w = float(cur), float(want)
    except (TypeError, ValueError):
        return False
    return n > w if op == "gt" else n < w if op == "lt" else True


def clean_answers(form: dict, job_type_id: Optional[str], answers: Optional[dict]) -> dict:
    """Keep only the fields the form asks for, with sane values; 422 on a missing required one.
    Anything else the client sent is dropped."""
    answers = answers if isinstance(answers, dict) else {}
    jt = next((t for t in form["job_types"] if t["id"] == job_type_id), None)
    allowed = list(form["fields"]) + list(form["device_fields"]) + (jt["fields"] if jt else [])
    out: dict = {}
    for f in allowed:
        v = answers.get(f["key"])
        if isinstance(v, str):
            v = v.strip()[:500]
        elif isinstance(v, bool) or v is None:
            pass
        elif isinstance(v, (int, float)):
            v = float(v) if not isinstance(v, int) else v
        else:
            v = None
        if f["type"] == "number" and v not in (None, ""):
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(422, f"{f['label']} must be a number")
            if v.is_integer():
                v = int(v)  # a year or a count reads "2003", not "2003.0"
        if f["type"] == "select" and v not in (None, "") and f.get("options") and str(v) not in [str(o) for o in f["options"]]:
            raise HTTPException(422, f"Pick one of the choices for {f['label']}")
        if v in (None, ""):
            if f.get("required") and _visible(f, {**out, **answers}):
                raise HTTPException(422, f"{f['label']} is required")
            continue
        out[f["key"]] = v
    return out


# ── Queue ────────────────────────────────────────────────────────────────────
async def queue_day(db: AsyncSession, org_id: str, location_id: str, now: Optional[datetime] = None) -> date:
    return (await shop_now(db, org_id, location_id, now)).date()


async def next_queue_no(db: AsyncSession, org_id: str, location_id: str, day: date) -> int:
    top = (await db.execute(select(func.max(Checkin.queue_no)).where(
        Checkin.org_id == org_id, Checkin.location_id == location_id, Checkin.queue_day == day))).scalar()
    return int(top or 0) + 1


async def ahead_of(db: AsyncSession, c: Checkin) -> int:
    """People still waiting in front of this check-in today."""
    if c.state != "waiting" or c.kind != "walk_in":
        return 0
    return int((await db.execute(select(func.count()).select_from(Checkin).where(
        Checkin.org_id == c.org_id, Checkin.location_id == c.location_id, Checkin.queue_day == c.queue_day,
        Checkin.kind == "walk_in", Checkin.state == "waiting", Checkin.queue_no < c.queue_no))).scalar() or 0)


async def minutes_per_person(db: AsyncSession, org_id: str, location_id: str) -> float:
    """Median wait from check-in to being called over the last 14 days; 10 min with too little
    history."""
    from datetime import timedelta
    since = datetime.utcnow() - timedelta(days=14)
    rows = (await db.execute(select(Checkin.created_at, Checkin.called_at).where(
        Checkin.org_id == org_id, Checkin.location_id == location_id, Checkin.kind == "walk_in",
        Checkin.called_at.isnot(None), Checkin.created_at >= since))).all()
    waits = sorted(max(0.0, (b - a).total_seconds() / 60) for a, b in rows if a and b)
    if len(waits) < 5:
        return FALLBACK_MIN_PER_PERSON
    mid = len(waits) // 2
    med = waits[mid] if len(waits) % 2 else (waits[mid - 1] + waits[mid]) / 2
    return max(2.0, med)


def round_wait(minutes: float) -> int:
    """'about N min', rounded to 5 (at least 5)."""
    return max(5, int(round(minutes / 5.0)) * 5)


# ── Convert ──────────────────────────────────────────────────────────────────
def _device_column(kind: Optional[str]):
    return {"vin": Device.vin, "hin": Device.hin, "imei": Device.imei, "serial": Device.serial}.get(kind or "")


async def convert(db: AsyncSession, c: Checkin, *, actor=None) -> dict:
    """Turn the check-in into a work order with everything filled in. Idempotent: a converted
    check-in returns its order. Commits."""
    from backend.models import AssetGroup, Order
    from backend.services import identifiers as ids
    from backend.services.leads import convert_lead
    if c.converted_order_id:
        o = (await db.execute(select(Order).where(Order.id == c.converted_order_id))).scalar_one()
        return {"ok": True, "order_id": o.id, "order_number": o.order_number, "already": True}
    if c.state in ("cancelled", "no_show"):
        raise HTTPException(409, "This check-in was closed. Reopen the inquiry to convert it.")
    lead = (await db.execute(select(Lead).where(Lead.id == c.lead_id, Lead.org_id == c.org_id))).scalar_one()
    org = (await db.execute(select(Organisation).where(Organisation.id == c.org_id))).scalar_one()
    tkey = industry.normalize_template_key(getattr(org, "industry_template", None))
    answers = dict(c.answers or {})
    snapshot = c.form_snapshot or {}
    device_keys = {f["key"] for f in snapshot.get("fields", [])} | {f["key"] for f in snapshot.get("device_fields", [])}
    decoded = c.decoded or {}

    async def device_for(client):
        kind = c.identifier_kind if c.identifier_kind in IDENTIFIER_KINDS else None
        value = ids.normalize(kind, c.identifier_value) if kind and c.identifier_value and kind in ids.KINDS else (
            (c.identifier_value or "").strip().upper() or None)
        col = _device_column(kind)
        dev = None
        if col is not None and value:
            # This customer's own device first; the same identifier under someone else stays theirs.
            dev = (await db.execute(select(Device).where(Device.org_id == c.org_id, Device.client_id == client.id,
                                                         col == value).limit(1))).scalar_one_or_none()
        make = answers.get("make") or answers.get("brand") or decoded.get("make") or decoded.get("manufacturer")
        model = answers.get("model") or decoded.get("model")
        if dev is None:
            if not (value or make or model):
                return None
            group_id = None
            if c.asset_group:
                group_id = (await db.execute(select(AssetGroup.id).where(
                    AssetGroup.org_id == c.org_id, AssetGroup.key == c.asset_group))).scalars().first()
            dev = Device(org_id=c.org_id, client_id=client.id, owner_type="client",
                         asset_type=industry.asset_label(tkey), template_key=tkey, asset_group_id=group_id,
                         brand=make, model=model)
            if col is not None and value:
                setattr(dev, kind, value)
            elif value:
                dev.custom_data = {kind or "serial": value}
            db.add(dev)
            await db.flush()
        data = dict(dev.custom_data or {})
        for k, v in answers.items():
            if k in device_keys and k not in ("make", "brand", "model", "color"):
                data[k] = v
        if answers.get("color"):
            dev.color = dev.color or answers["color"]
        if decoded.get("model_year") and not data.get("year"):
            data["year"] = decoded["model_year"]
        if c.reading_kind and c.reading_value is not None:
            data[c.reading_kind] = c.reading_value
        dev.custom_data = data
        dev.brand = dev.brand or make
        dev.model = dev.model or model
        return dev.id

    async def extra(order, client):
        order.location_id = c.location_id
        if c.job_type_id:
            order.type_id = c.job_type_id
        data = dict(order.custom_data or {})
        jt_keys = {f["key"] for t in snapshot.get("job_types", []) if t.get("id") == c.job_type_id
                   for f in t.get("fields", [])}
        for k, v in answers.items():
            if k in jt_keys:
                data[k] = v
        if c.reading_kind and c.reading_value is not None:
            data[c.reading_kind] = c.reading_value
        if c.key_tag:
            data["key_tag"] = c.key_tag
        order.custom_data = data
        if c.photo_ids:
            order.intake_photos = list(order.intake_photos or []) + list(c.photo_ids)
        if c.signature_png:
            db.add(DocumentSignature(org_id=c.org_id, order_id=order.id, doc_type="check_in", decision="accept",
                                     signer_name=c.signer_name or lead.name, signature_png=c.signature_png,
                                     doc_hash=c.terms_hash, signer_ip=c.signer_ip))
        c.state = "converted"
        c.converted_order_id = order.id
        c.updated_at = datetime.utcnow()

    return await convert_lead(db, c.org_id, lead, actor=actor, device_for=device_for, extra=extra)


# ── Messages ─────────────────────────────────────────────────────────────────
def status_url(c: Checkin) -> str:
    from backend.config import settings
    base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    return f"{base}/c/s/{c.status_token}"


async def text_customer(db: AsyncSession, c: Checkin, phone: Optional[str], body: str, event: str) -> str:
    """Text the customer through the shop's own texting account. With no account connected
    nothing is sent (the page shows the link instead). Returns sent | skipped | failed."""
    from backend.models import NotificationLog
    from backend.services import org_creds
    from backend.services.notifications import _adapter_for
    if not phone:
        return "skipped"
    creds = await org_creds.effective(db, c.org_id, "twilio", read_only=True, own_only=True)
    if not (creds or {}).get("account_sid"):
        return "skipped"
    log = NotificationLog(org_id=c.org_id, scope="client", channel="sms", trigger_event=event,
                          recipient=phone, body=body, send_at=datetime.utcnow(), status="queued")
    db.add(log)
    try:
        res = await _adapter_for("sms", creds).send(recipient=phone, subject="", body=body)
        log.status, log.error = res.get("status", "sent"), res.get("error")
    except Exception as e:  # noqa: BLE001 — a failed text never blocks the check-in
        log.status, log.error = "failed", str(e)[:300]
    return log.status


async def alert_staff(db: AsyncSession, org_id: str, location_id: str, title: str, body: str) -> int:
    """In-app alert (bell + web push) to the active staff of that location."""
    from backend.models import Employee
    from backend.services import push
    emps = (await db.execute(select(Employee.id).where(
        Employee.org_id == org_id, Employee.active == True,  # noqa: E712
        Employee.location_id == location_id))).scalars().all()
    for emp_id in emps:
        await push.notify(db, org_id=org_id, employee_id=emp_id, kind="checkin.created", title=title,
                          body=body, deep_link="/inquiries?view=walkins", commit=False)
    return len(emps)
