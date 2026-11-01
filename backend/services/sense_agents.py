"""Tekly Sense agents: collections, quote follow-up and win-back.

They draft customer messages through the shop's own SMS / WhatsApp / email setup. Off by
default; when on, each message is a draft waiting for the owner's OK unless the owner
switched that off. Everything that protects the customer is checked twice: when the message
is drafted and again when it is about to go (backend/services/notification_rules.run_due
calls send_check), so a STOP, an unsubscribe, the owner switching the agent off or the
platform switch always wins.

Messages are NotificationLog rows with trigger_event "agent.<name>:<entity id>" and status
"draft" (waiting), "scheduled" (approved or auto), then sent / skipped / discarded.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Client, ConsentLog, Invoice, NotificationLog, Order, Organisation

log = logging.getLogger("tekly.sense.agents")

AGENTS = ("collections", "quote_followup", "win_back")
AGENT_DEFAULTS = {
    "collections": {"on": False, "auto_send": False, "wait_days": 3, "channel": "sms"},
    "quote_followup": {"on": False, "auto_send": False, "wait_days": 2, "channel": "sms"},
    "win_back": {"on": False, "auto_send": False, "months": 9, "channel": "email"},
}
DAILY_CAP_DEFAULT = 50
LIVE = ("draft", "scheduled", "sending", "sent")          # counts towards the customer caps
CUSTOMER_GAP = timedelta(hours=72)                        # one agent message per customer per 72h
CUSTOMER_MONTH_MAX = 3                                     # ...and 3 per 30 days
ENTITY_GAP = timedelta(days=7)                             # the same invoice/quote: every 7 days at most
ENTITY_MAX = 2                                             # ...2 reminders in all
WINBACK_GAP = timedelta(days=180)
QUOTE_MAX_AGE = timedelta(days=30)
DRAFT_TTL = timedelta(days=7)                              # an unapproved draft goes stale after this
INVOICE_MAX_AGE = timedelta(days=120)                      # collections leaves very old debts to people
CHANNELS = ("sms", "email")                                # WhatsApp needs approved templates: not yet
MARKETING_CHANNELS = ("email",)                            # win-back: email only (US texts need written consent)


def agent_settings(cfg: dict) -> dict:
    a = cfg.get("agents") or {}
    out = {k: {**d, **{x: y for x, y in (a.get(k) or {}).items() if x in d}} for k, d in AGENT_DEFAULTS.items()}
    out["daily_cap"] = int(a.get("daily_cap") or DAILY_CAP_DEFAULT)
    return out


def platform_off() -> bool:
    return bool(getattr(settings, "SENSE_AGENTS_DISABLED", False))


# ── Unsubscribe links ───────────────────────────────────────────────────────────
def _sig(payload: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"unsub:{payload}".encode(), hashlib.sha256).hexdigest()[:32]


def unsubscribe_token(org_id: str, client_id: str) -> str:
    payload = base64.urlsafe_b64encode(f"{org_id}|{client_id}".encode()).decode().rstrip("=")
    return f"{payload}.{_sig(payload)}"


def read_unsubscribe_token(token: str) -> Optional[tuple[str, str]]:
    try:
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sig(payload)):
            return None
        org_id, client_id = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode().split("|", 1)
        return org_id, client_id
    except Exception:  # noqa: BLE001 — anything malformed is simply not a valid link
        return None


def unsubscribe_url(org_id: str, client_id: str) -> str:
    base = (settings.PUBLIC_BASE_URL or "http://localhost:8000").rstrip("/")   # local dev: the API itself
    return f"{base}/api/public/unsubscribe/{unsubscribe_token(org_id, client_id)}"


# ── Consent and opening hours ───────────────────────────────────────────────────
async def marketing_ok(db: AsyncSession, org_id: str, client_id: str) -> bool:
    """Win-back is marketing: the customer's latest marketing-consent record must be a yes."""
    last = (await db.execute(select(ConsentLog.granted).where(
        ConsentLog.org_id == org_id, ConsentLog.client_id == client_id, ConsentLog.channel == "marketing")
        .order_by(ConsentLog.created_at.desc()).limit(1))).scalar_one_or_none()
    return last is True


async def marketing_consented(db: AsyncSession, org_id: str) -> list[str]:
    """Clients whose latest marketing-consent record is a yes, in one query."""
    latest = (select(ConsentLog.client_id, func.max(ConsentLog.created_at).label("at"))
              .where(ConsentLog.org_id == org_id, ConsentLog.channel == "marketing")
              .group_by(ConsentLog.client_id).subquery())
    rows = await db.execute(
        select(ConsentLog.client_id).join(latest, (latest.c.client_id == ConsentLog.client_id) & (latest.c.at == ConsentLog.created_at))
        .where(ConsentLog.org_id == org_id, ConsentLog.channel == "marketing", ConsentLog.granted == True))  # noqa: E712
    return list(set(rows.scalars().all()))


async def only_location(db: AsyncSession, org_id: str) -> Optional[str]:
    """A shop with exactly one location: its id (so its own opening hours apply), else None."""
    from backend.models import Location
    ids = (await db.execute(select(Location.id).where(Location.org_id == org_id).limit(2))).scalars().all()
    return ids[0] if len(ids) == 1 else None


def _tz(org):
    from backend.services.sense import _tz as tz
    return tz(org)


_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _day_window(working_hours, day) -> Optional[tuple[int, int]]:
    """(open, close) minutes for a local date, or None when the location is closed that day.
    No hours set: the default 09:00–18:00."""
    from backend.services.notification_rules import WORK_END_MIN, WORK_START_MIN
    if not isinstance(working_hours, dict):
        return WORK_START_MIN, WORK_END_MIN
    d = working_hours.get(_WEEKDAYS[day.weekday()])
    if d is None:
        return WORK_START_MIN, WORK_END_MIN
    if not isinstance(d, dict) or d.get("closed"):
        return None

    def m(v, default):
        try:
            h, mi = str(v).split(":")
            return int(h) * 60 + int(mi)
        except Exception:  # noqa: BLE001
            return default
    return m(d.get("open"), WORK_START_MIN), m(d.get("close"), WORK_END_MIN)


async def next_open(db: AsyncSession, org: Organisation, when_utc: datetime, location_id: Optional[str] = None,
                    marketing: bool = False) -> datetime:
    """The first moment at or after `when_utc` inside the location's opening hours, in the
    shop's own time zone, skipping closed days (and Sundays for marketing). Naive UTC out."""
    from backend.models import Location
    hours = None
    if location_id:
        loc = (await db.execute(select(Location).where(Location.id == location_id))).scalar_one_or_none()
        hours = getattr(loc, "working_hours", None) if loc else None
    tz = _tz(org)
    local = when_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    for _ in range(14):
        win = None if (marketing and local.weekday() == 6) else _day_window(hours, local.date())
        if win:
            start, end = win
            mins = local.hour * 60 + local.minute
            if mins < start:
                local = local.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
                return local.astimezone(timezone.utc).replace(tzinfo=None)
            if mins < end:
                return local.astimezone(timezone.utc).replace(tzinfo=None)
        nxt = (local + timedelta(days=1)).date()
        local = datetime.combine(nxt, datetime.min.time(), tzinfo=tz)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


# ── Drafting ────────────────────────────────────────────────────────────────────
def _first(name: Optional[str]) -> str:
    clean = re.sub(r"[<>&\"]", "", name or "").strip()    # a name never turns an email into markup
    return (clean.split(" ") or ["there"])[0] or "there"


def sender_footer(org) -> Optional[str]:
    """Who the marketing message is from and how to reach them (CAN-SPAM / CASL / PECR need a
    postal address). None when the shop has no address: then win-back doesn't send."""
    addr = (getattr(org, "company_address", None) or "").strip()
    if not addr:
        return None
    contact = " · ".join(x for x in (getattr(org, "company_phone", None), getattr(org, "company_email", None)) if x)
    return f"{org.name}, {addr}" + (f" · {contact}" if contact else "")


def _money(org, v) -> str:
    return f"{getattr(org, 'currency', '') or ''}{float(v or 0):,.2f}"


async def _candidates(db: AsyncSession, org: Organisation, agent: str, cfg: dict, now: datetime) -> list[tuple]:
    """(entity id, client, order id or None, subject, body) for one agent, oldest need first."""
    out = []
    if agent == "collections":
        owed = Invoice.total - func.coalesce(Invoice.amount_paid, 0)
        rows = (await db.execute(
            # the person who pays (when someone else pays for the job), else the customer
            select(Invoice, Client).join(Client, Client.id == func.coalesce(Invoice.payer_client_id, Invoice.client_id))
            .where(Invoice.org_id == org.id, Client.org_id == org.id,
                   Invoice.status.notin_(("paid", "cancelled", "draft")), owed > 0,
                   Invoice.due_at <= now - timedelta(days=int(cfg["wait_days"])),
                   Invoice.due_at >= now - INVOICE_MAX_AGE)
            .order_by(Invoice.due_at).limit(500))).all()
        locs = {}
        oids = [inv.order_id for inv, _ in rows if inv.order_id]
        if oids:
            locs = dict((await db.execute(select(Order.id, Order.location_id).where(
                Order.org_id == org.id, Order.id.in_(oids)))).all())
        for inv, c in rows:
            due = float(inv.total or 0) - float(inv.amount_paid or 0)
            body = (f"Hi {_first(c.name)}, a friendly reminder from {org.name}: invoice {inv.invoice_number} "
                    f"for {_money(org, due)} is now due. Reply here if you have any questions.")
            out.append((inv.id, c, inv.order_id, f"Invoice {inv.invoice_number} from {org.name}", body,
                        locs.get(inv.order_id) or await only_location(db, org.id)))
    elif agent == "quote_followup":
        rows = (await db.execute(
            select(Order, Client).join(Client, Client.id == Order.client_id)
            .where(Order.org_id == org.id, Client.org_id == org.id, Order.is_estimate == True,  # noqa: E712
                   Order.archived == False, Order.estimate_approved_at.is_(None),  # noqa: E712
                   Order.converted_order_id.is_(None),
                   Order.created_at <= now - timedelta(days=int(cfg["wait_days"])),
                   Order.created_at >= now - QUOTE_MAX_AGE)
            .order_by(Order.created_at).limit(500))).all()
        for o, c in rows:
            body = (f"Hi {_first(c.name)}, just checking in on your quote {o.order_number} from {org.name}. "
                    "Reply here to go ahead or with any questions.")
            out.append((o.id, c, o.id, f"Your quote {o.order_number} from {org.name}", body, o.location_id))
    elif agent == "win_back":
        quiet = timedelta(days=int(cfg["months"]) * 30)
        last = (select(Order.client_id, func.max(Order.created_at).label("last"))
                .where(Order.org_id == org.id, Order.client_id.isnot(None), Order.archived == False)  # noqa: E712
                .group_by(Order.client_id).subquery())
        consented = await marketing_consented(db, org.id)
        if not consented:
            return out
        rows = (await db.execute(
            select(Client, last.c.last).join(last, last.c.client_id == Client.id)
            .where(Client.org_id == org.id, Client.id.in_(consented),
                   last.c.last <= now - quiet, last.c.last >= now - quiet - timedelta(days=365))
            .order_by(last.c.last).limit(500))).all()
        for c, _ in rows:
            body = (f"Hi {_first(c.name)}, it's been a while since we saw you at {org.name}. "
                    "If anything needs a look, reply here and we'll fit you in.")
            out.append((c.id, c, None, f"It's been a while — {org.name}", body, await only_location(db, org.id)))
    return out


async def run_agents(db: AsyncSession, org_id: str, now: Optional[datetime] = None) -> int:
    """Draft (or schedule) today's agent messages for one shop. Returns how many."""
    from backend.services import sense
    from backend.services.notifications import channel_consented
    from backend.services.notification_rules import with_opt_out
    now = now or datetime.utcnow()
    if platform_off():
        return 0
    s = await sense.get_settings(db, org_id)
    if not s["enabled"]:
        return 0
    cfg = agent_settings(await _config(db, org_id))
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    if org is None or not getattr(org, "active", True):
        return 0
    # Drafts nobody approved within a week are stale (an invoice may have been paid since).
    for d in (await db.execute(select(NotificationLog).where(
            NotificationLog.org_id == org_id, NotificationLog.status == "draft",
            NotificationLog.trigger_event.like("agent.%"), NotificationLog.created_at < now - DRAFT_TTL))).scalars().all():
        d.status, d.error = "expired", "Not approved within 7 days"
    made_today = (await db.execute(select(func.count(NotificationLog.id)).where(
        NotificationLog.org_id == org_id, NotificationLog.trigger_event.like("agent.%"),
        NotificationLog.created_at >= now - timedelta(days=1)))).scalar_one()
    room = cfg["daily_cap"] - made_today
    made = 0
    # Earlier agent messages (discarded and expired ones count too, so a draft the owner threw
    # away isn't redrafted an hour later). Read once, 180 days back (the longest gap).
    prior = (await db.execute(select(NotificationLog.trigger_event, NotificationLog.client_id,
                                     NotificationLog.created_at, NotificationLog.status).where(
        NotificationLog.org_id == org_id, NotificationLog.trigger_event.like("agent.%"),
        NotificationLog.created_at >= now - WINBACK_GAP))).all()
    by_entity: dict[str, list] = {}
    by_client: dict[str, list] = {}
    for ev, cid, when, status in prior:
        if ":" in (ev or ""):
            by_entity.setdefault(ev, []).append(when)
        if cid and status in LIVE:
            by_client.setdefault(cid, []).append(when)
    for agent in AGENTS:
        a = cfg[agent]
        if not a["on"] or room <= 0:
            continue
        cands = await _candidates(db, org, agent, a, now)
        if not cands:
            continue
        # least recently reminded first, so a second invoice gets its turn before a first one again
        cands.sort(key=lambda c: max(by_entity.get(f"agent.{agent}:{c[0]}", [datetime.min])))
        for entity, c, order_id, subject, body, location_id in cands:
            if room <= 0:
                break
            key = f"agent.{agent}:{entity}"
            seen = by_entity.get(key, [])
            gap = WINBACK_GAP if agent == "win_back" else ENTITY_GAP
            if agent != "win_back" and len(seen) >= ENTITY_MAX:
                continue
            if seen and now - max(seen) < gap:
                continue
            mine = by_client.get(c.id, [])
            if any(now - w < CUSTOMER_GAP for w in mine):
                continue
            if sum(1 for w in mine if now - w < timedelta(days=30)) >= CUSTOMER_MONTH_MAX:
                continue
            channel = a["channel"]
            if channel not in (MARKETING_CHANNELS if agent == "win_back" else CHANNELS):
                continue
            recipient = c.email if channel == "email" else c.phone
            if not recipient or not channel_consented(c, channel):
                continue
            text = body
            if agent == "win_back":
                footer = sender_footer(org)
                if footer is None:          # no postal address on file: marketing can't go out
                    continue
                text += f"\n\n{footer}"
            if channel == "email":
                text += f"\n\nDon't want these emails? Unsubscribe: {unsubscribe_url(org_id, c.id)}"
            else:
                text = await with_opt_out(db, org_id, c.id, channel, text)
            db.add(NotificationLog(
                org_id=org_id, scope="client", channel=channel, trigger_event=key, order_id=order_id,
                client_id=c.id, recipient=recipient, subject=subject if channel == "email" else None,
                body=text, created_at=now,
                status="scheduled" if a["auto_send"] else "draft",
                send_at=await next_open(db, org, now, location_id, marketing=agent == "win_back") if a["auto_send"] else None))
            by_entity.setdefault(key, []).append(now)
            by_client.setdefault(c.id, []).append(now)
            made += 1
            room -= 1
    if made:
        await db.commit()
    return made


async def _config(db: AsyncSession, org_id: str) -> dict:
    from backend.services.sense import _row
    row = await _row(db, org_id)
    return (row.config or {}) if row else {}


# ── Send time ───────────────────────────────────────────────────────────────────
async def _still_due(db: AsyncSession, org_id: str, agent: str, entity: str) -> tuple[bool, Optional[str]]:
    """Is the reason for this message still true? Returns (yes/no, location for opening hours)."""
    if agent == "collections":
        inv = (await db.execute(select(Invoice).where(Invoice.id == entity, Invoice.org_id == org_id))).scalar_one_or_none()
        if inv is None or inv.status in ("paid", "cancelled", "draft") or float(inv.total or 0) - float(inv.amount_paid or 0) <= 0:
            return False, None
        loc = (await db.execute(select(Order.location_id).where(Order.id == inv.order_id))).scalar_one_or_none() if inv.order_id else None
        return True, loc or await only_location(db, org_id)
    if agent == "quote_followup":
        o = (await db.execute(select(Order).where(Order.id == entity, Order.org_id == org_id))).scalar_one_or_none()
        if o is None or o.archived or o.estimate_approved_at or o.converted_order_id:
            return False, None
        return True, o.location_id
    return True, await only_location(db, org_id)


async def send_check(db: AsyncSession, org_id: str, msg: NotificationLog, now: datetime) -> tuple[str, Optional[datetime]]:
    """At the moment of sending: ("go", None), ("later", when) or ("stop", reason)."""
    from backend.services import sense
    head, _, entity = (msg.trigger_event or "").partition(":")
    agent = head.removeprefix("agent.")
    s = await sense.get_settings(db, org_id)
    cfg = agent_settings(await _config(db, org_id))
    if platform_off() or not s["enabled"] or agent not in cfg or not cfg[agent]["on"]:
        return "stop", "The agent was turned off before this was sent"
    if agent == "win_back" and msg.client_id and not await marketing_ok(db, org_id, msg.client_id):
        return "stop", "No marketing consent"
    still, location_id = await _still_due(db, org_id, agent, entity)
    if not still:
        return "stop", "No longer needed (paid, answered or removed)"
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    when = await next_open(db, org, now, location_id, marketing=agent == "win_back")
    if when > now:
        return "later", when
    return "go", None


def email_headers(msg: NotificationLog) -> Optional[dict]:
    """RFC 8058 one-click unsubscribe headers for agent emails (Gmail / Yahoo bulk-sender rules)."""
    if msg.channel != "email" or not msg.client_id or not (msg.trigger_event or "").startswith("agent."):
        return None
    url = unsubscribe_url(msg.org_id, msg.client_id)
    return {"List-Unsubscribe": f"<{url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


# ── Approving ───────────────────────────────────────────────────────────────────
async def approve(db: AsyncSession, org_id: str, ids: list[str], now: Optional[datetime] = None) -> int:
    now = now or datetime.utcnow()
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    when = await next_open(db, org, now)
    rows = (await db.execute(select(NotificationLog).where(
        NotificationLog.org_id == org_id, NotificationLog.id.in_(ids), NotificationLog.status == "draft",
        NotificationLog.trigger_event.like("agent.%")))).scalars().all()
    n = 0
    for r in rows:
        if r.created_at and now - r.created_at > DRAFT_TTL:
            r.status, r.error = "expired", "Not approved within 7 days"
            continue
        r.status, r.send_at = "scheduled", when
        n += 1
    return n


async def discard(db: AsyncSession, org_id: str, ids: list[str]) -> int:
    rows = (await db.execute(select(NotificationLog).where(
        NotificationLog.org_id == org_id, NotificationLog.id.in_(ids), NotificationLog.status == "draft",
        NotificationLog.trigger_event.like("agent.%")))).scalars().all()
    for r in rows:
        r.status, r.error = "discarded", "Discarded by the shop"
    return len(rows)


async def unsubscribe(db: AsyncSession, org_id: str, client_id: str) -> bool:
    c = (await db.execute(select(Client).where(Client.id == client_id, Client.org_id == org_id))).scalar_one_or_none()
    if c is None:
        return False
    if c.consent_email is False and not await marketing_ok(db, org_id, client_id):
        return True                         # already unsubscribed: nothing to record again
    c.consent_email = False
    for ch in ("email", "marketing"):
        db.add(ConsentLog(org_id=org_id, client_id=client_id, channel=ch, granted=False, source="unsubscribe"))
    return True
