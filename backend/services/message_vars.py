"""Short message variables for customer notifications: `{client_first_name}`, `{total}`, …

Templates can use these snake_case tokens alongside the document catalog's Orderry-style
names (`{Client First Name}`, resolved by `routers.documents._resolve`). Every send path
renders both through `render_for_order` / `render_for_client`, so a variable listed in the
editor always fills in.

A variable with no value (no phone on file, no birthday, nothing scheduled) is dropped
cleanly: the token and the space that went with it disappear, so "Hi {client_first_name},"
reads "Hi," and never "Hi ," or "Hi —,".
"""
from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings

# (token, picker label) — the order here is the order the editor shows them.
SHORT_VARS: list[tuple[str, str]] = [
    ("{client_name}", "Client name"),
    ("{client_first_name}", "First name"),
    ("{client_last_name}", "Last name"),
    ("{client_phone}", "Client phone"),
    ("{client_birthday}", "Birthday"),
    ("{client_age}", "Age"),
    ("{order_number}", "Job number"),
    ("{status}", "Status"),
    ("{device}", "Item"),
    ("{technician}", "Technician"),
    ("{scheduled_for}", "Scheduled for"),
    ("{total}", "Total"),
    ("{balance_due}", "Balance due"),
    ("{portal_link}", "Customer portal link"),
    ("{shop_name}", "Shop name"),
]
TOKENS = [t for t, _ in SHORT_VARS]
CLIENT_TOKENS = ["{client_name}", "{client_first_name}", "{client_last_name}", "{client_phone}",
                 "{client_birthday}", "{client_age}", "{portal_link}", "{shop_name}"]

_GAP = "\x00"


_TOKEN = re.compile(r"\{[^{}\n]+\}")


def apply(text: str | None, values: dict[str, str], doc_vars: dict | None = None) -> str:
    """Fill `{key}` short tokens from `values` and `{Orderry Name}` tokens from `doc_vars` (the
    document catalog) in ONE pass, so a substituted value is never scanned again: a customer
    named "{portal_link}" stays literal. Empty short values vanish without stray spaces."""
    if not text:
        return text or ""

    def sub(m: re.Match) -> str:
        tok = m.group(0)
        key = tok[1:-1]
        if key in values:
            v = values[key]
            return str(v) if v not in (None, "") else _GAP
        if doc_vars and tok in doc_vars:
            return str(doc_vars[tok])
        return tok
    out = _TOKEN.sub(sub, text)
    if _GAP not in out:
        return out
    # A dropped value takes its leading spaces with it ("Hi {x}," → "Hi,"), and a gap at the
    # start of a line takes its trailing spaces too ("{x} is ready" → "is ready").
    out = re.sub(r"(?:[ \t]*" + _GAP + r")+", _GAP, out)
    # A "Label:" whose only value is missing goes with it ("Hi. Portal: {x}. Bye" → "Hi. Bye").
    out = re.sub(r"(^|[.!?\n])[ \t]*[^\n.!?:{}]{1,40}:" + _GAP + r"[ \t]*[.,;!]?", r"\1", out, flags=re.M)
    out = re.sub(r"(^|\n|\(|\[)" + _GAP + r"[ \t]*", r"\1", out)
    out = re.sub(_GAP + r"(?=[ \t]*([,.!?;:)\]]|\n|$))", "", out)
    out = re.sub(_GAP + r"(?=[ \t])", "", out)   # the following space now separates the words
    return out.replace(_GAP, " ").strip()   # a dropped first/last line leaves no blank edge


def _first_last(name: str | None) -> tuple[str, str]:
    first, _, last = (name or "").strip().partition(" ")
    return first, last.strip()


def _birthday(raw: str | None, today: date | None = None) -> tuple[str, str]:
    """("14 March", "36") from an ISO birthday; the age is blank when the year is unknown."""
    s = (raw or "").strip()
    if len(s) < 5:
        return "", ""
    try:
        month, day = int(s[-5:-3]), int(s[-2:])
        d = date(2000, month, day)
        label = f"{d.day} {d.strftime('%B')}"
    except ValueError:
        return "", ""
    age = ""
    try:
        year = int(s[:4]) if len(s) >= 10 else 0
    except ValueError:
        year = 0
    if year > 1900:
        t = today or date.today()
        n = t.year - year - ((t.month, t.day) < (month, day))
        age = str(n) if 0 <= n < 130 else ""
    return label, age


def _fmt_when(dt: datetime | None) -> str:
    # Stored as shop wall-clock time (see public booking), so no conversion.
    return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b, %H:%M')}" if dt else ""


async def _portal_link(db: AsyncSession, client, issue: bool = True) -> str:
    """The customer's portal magic link; issued on first real send so the message always works.
    A preview (`issue=False`) never mints one: it shows the existing link or nothing."""
    if client is None:
        return ""
    if not client.portal_token:
        if not issue:
            return ""
        import secrets
        client.portal_token = secrets.token_urlsafe(24)
        await db.flush()
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/portal/{client.portal_token}"


def _wanted(texts) -> set[str]:
    joined = " ".join(t for t in texts if t)
    return {t[1:-1] for t in TOKENS if t in joined}


async def client_values(db: AsyncSession, org_id: str, client, *texts: str | None,
                        issue_links: bool = True) -> dict[str, str]:
    """Values for the client-level tokens used in `texts` (birthday greetings, any contact send)."""
    from backend.models import Organisation
    want = _wanted(texts)
    if not want:
        return {}
    first, last = _first_last(getattr(client, "name", None))
    bday, age = _birthday(getattr(client, "birthday", None))
    vals = {"client_name": getattr(client, "name", None) or "", "client_first_name": first,
            "client_last_name": last, "client_phone": getattr(client, "phone", None) or "",
            "client_birthday": bday, "client_age": age}
    if "shop_name" in want:
        org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
        vals["shop_name"] = (org.name if org else "") or ""
    if "portal_link" in want:
        vals["portal_link"] = await _portal_link(db, client, issue_links)
    return {k: v for k, v in vals.items() if k in want}


async def _related(db: AsyncSession, obj, name: str, model, fk):
    """A relationship's value without an implicit (async-unsafe) lazy load."""
    from sqlalchemy import inspect
    if name not in inspect(obj).unloaded:
        return getattr(obj, name)
    return await db.get(model, fk) if fk else None


async def order_values(db: AsyncSession, org_id: str, order, *texts: str | None,
                       issue_links: bool = True) -> dict[str, str]:
    """Values for every short token used in `texts`, for this order. Money is shown in the
    order's display currency (order override → client preference → org base), cents kept."""
    from backend.models import Appointment
    from backend.services import currency as _cur
    want = _wanted(texts)
    if not want:
        return {}
    from backend.models import Client, Device, Employee
    client = (await _related(db, order, "client", Client, order.client_id)
              if want & {t[1:-1] for t in CLIENT_TOKENS} else None)
    vals = (await client_values(db, org_id, client, *texts, issue_links=issue_links)
            if client is not None else {})
    vals["order_number"] = order.order_number or ""
    if "status" in want:
        from backend.routers.documents import _status_name
        vals["status"] = await _status_name(db, org_id, order)
    if "device" in want:
        dev = await _related(db, order, "device", Device, getattr(order, "device_id", None))
        vals["device"] = " ".join(x for x in (getattr(dev, "brand", None), getattr(dev, "model", None)) if x)
    if "technician" in want:
        tech = await _related(db, order, "assigned_to", Employee, order.assigned_to_id)
        from backend.models import Organisation
        from backend.services.public_name import public_staff_name
        mode = (await db.execute(select(Organisation.public_employee_name).where(
            Organisation.id == org_id))).scalar_one_or_none()
        vals["technician"] = public_staff_name(tech, mode)   # customers see the shop's chosen form
    if {"total", "balance_due"} & want:
        ci = await _cur.resolve_for_client(db, org_id, order.client_id, order.currency_code)
        fx = order.fx_rate or ci["rate"] or 1.0

        def money(n):
            return _cur.format_money(float(n or 0) * fx, ci["symbol"], ci["code"])
        from backend.services.order_money import balance_due
        vals["total"] = money(order.total)
        vals["balance_due"] = money(await balance_due(db, order))   # net of refunds and credit notes
    if "scheduled_for" in want:
        when = order.scheduled_for
        if when is None:             # fall back to the job's next booked appointment
            when = (await db.execute(select(func.min(Appointment.scheduled_for)).where(
                Appointment.org_id == org_id, Appointment.order_id == order.id,
                Appointment.status == "booked"))).scalar()
        vals["scheduled_for"] = _fmt_when(when)
    if "shop_name" in want and "shop_name" not in vals:
        from backend.models import Organisation
        org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
        vals["shop_name"] = (org.name if org else "") or ""
    for k in want:
        vals.setdefault(k, "")       # client-level tokens on an order with no client
    return {k: v for k, v in vals.items() if k in want}


async def render_for_order(db: AsyncSession, org_id: str, order, *texts: str | None,
                           doc_vars: dict | None = None, issue_links: bool = True) -> list[str | None]:
    """Render a subject/body pair (or any texts) for one order: short tokens and the document
    catalog in a single pass, with the order's values looked up once for all of them."""
    vals = await order_values(db, org_id, order, *texts, issue_links=issue_links)
    return [apply(t, vals, doc_vars) if t else t for t in texts]
