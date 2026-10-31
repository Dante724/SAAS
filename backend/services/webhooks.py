"""Outbound webhooks: the event catalogue, signing, the transactional outbox, delivery and retries.

Events reach the outbox two ways, and both write WebhookDelivery rows inside the caller's
transaction (they commit or roll back with the change that caused them):

- ``emit_event()``, called by request handlers (order.created, order.status_changed, ...).
- ORM hooks (``_register_orm_events``) for records that are created from many places:
  contact.created, inquiry.created, appointment.created, invoice.created / invoice.paid and
  order.updated. Payloads are rendered again just before commit, so they show the committed state.

After commit the outbox hands the rows to ``deliver_pending``; ``core.scheduled`` retries failures
on the backoff schedule below. Each attempt re-checks the URL (SSRF), connects to the IP that
passed, signs with every active secret, and is bounded in time and response size.

Signature (B1), see services/webhook_verify_example.py for the receiver side and a test vector:
    Tekly-Signature: t=<unix>,v1=<hex hmac_sha256(secret, f"{t}.{raw_body}")>[,v1=...]
    Tekly-Webhook-Id: <delivery id>, stable across retries
    Tekly-Event: <event name>
The old ``X-Tekly-Signature: sha256=<hmac(secret, body)>`` and ``X-RepairOS-*`` headers are still
sent for one release cycle and are deprecated.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import secrets as _secrets
import time
import uuid
import weakref
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("tekly.webhooks")

API_VERSION = "2026-10-25"

# ── The event catalogue: the one list of what can be subscribed to ───────────────────────────
EVENT_CATALOGUE: dict[str, str] = {
    "order.created": "A work order was created, in the app or through the API.",
    "order.updated": "Work order details changed. The event lists which ones.",
    "order.status_changed": "A work order or estimate moved to another status.",
    "order.closed": "A work order was closed or dropped.",
    "estimate.accepted": "A customer accepted an estimate.",
    "payment.recorded": "An online payment succeeded.",
    "appointment.created": "An appointment was booked online, by staff or through the API.",
    "appointment.updated": "An appointment was rescheduled or its note changed through the API. The event lists which fields.",
    "appointment.cancelled": "An appointment was cancelled through the API.",
    "contact.created": "A customer or company contact was added.",
    "contact.updated": "A contact's details were changed through API v2.",
    "inquiry.created": "A new inquiry was added: web form, missed call, staff, Tekly Go or the API.",
    "inquiry.updated": "An inquiry's details or status changed.",
    "inquiry.converted": "An inquiry was turned into a work order or estimate.",
    "invoice.created": "An invoice was created.",
    "invoice.paid": "An invoice was paid in full.",
    "time_entry.stopped": "A timer on a job was stopped.",
    "tag.created": "A tag was added.",
    "tag.updated": "A tag was renamed or recoloured.",
    "tag.deleted": "A tag was deleted.",
    "bundle.created": "A bundle (kit of services and products) was added.",
    "bundle.updated": "A bundle changed.",
    "bundle.deleted": "A bundle was deleted.",
    "product.created": "A product was added through the API.",
    "product.updated": "A product was changed through the API.",
}
EVENTS = list(EVENT_CATALOGUE)
TEST_EVENT = "webhook.test"

# Old names still accepted on subscriptions. The public API docs used to promise
# `booking.created` before bookings were renamed appointments; an endpoint subscribed to the
# old name still receives the event, labelled with the name it subscribed to.
# `lead.created` is the name inquiries had before WS-C (inquiries everywhere); it is the same
# event as `inquiry.created`, delivered once, under the name the endpoint subscribed to.
EVENT_ALIASES = {"booking.created": "appointment.created", "lead.created": "inquiry.created"}
ALIASES = EVENT_ALIASES          # WS-C name

MAX_ENDPOINTS_PER_ORG = 10                    # B7
SECRET_PREFIX = "whsec_"
ROTATION_OVERLAP = timedelta(hours=24)        # B6: the old secret keeps signing this long

# B4: one attempt is bounded in time and in what we read back.
DELIVERY_DEADLINE_S = 10.0
REQUEST_TIMEOUT_S = 5.0
MAX_RESPONSE_BYTES = 4096
MAX_CONCURRENT_DELIVERIES = 20

# B5: wait before attempt 2, 3, ... (1m, 5m, 30m, 2h, 8h, 24h); then it stays failed.
RETRY_SCHEDULE_S = (60, 300, 1800, 7200, 28800, 86400)
MAX_ATTEMPTS = 1 + len(RETRY_SCHEDULE_S)
AUTO_DISABLE_AFTER = timedelta(hours=24)
STUCK_SENDING_AFTER = timedelta(minutes=2)
DISABLED_REASON = "Turned off after 24 hours of failed deliveries"


def _delivery_name(endpoint_events: str, event: str):
    """The event name to deliver to this endpoint, or None when it isn't subscribed."""
    if not endpoint_events or endpoint_events.strip() == "*":
        return event
    wanted = {e.strip() for e in endpoint_events.split(",") if e.strip()}
    if event in wanted:
        return event
    for old, new in EVENT_ALIASES.items():
        if new == event and old in wanted:
            return old
    return None


def _subscribed(endpoint_events: str, event: str) -> bool:
    return _delivery_name(endpoint_events, event) is not None


def valid_events(raw) -> str:
    """WS-C name for normalize_events (same rules: catalogue events, aliases or *)."""
    return normalize_events(raw)


def normalize_events(value) -> str:
    """B7: validate a subscription (csv string or list). Returns the stored csv, or raises
    ValueError naming the unknown event. Empty means every event."""
    items = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    names = [str(e).strip() for e in items if str(e).strip()]
    if not names or "*" in names:
        if len(names) > 1:
            raise ValueError("Use * on its own to subscribe to every event")
        return "*"
    for n in names:
        if n not in EVENT_CATALOGUE and n not in EVENT_ALIASES:
            raise ValueError(f"Unknown event: {n[:60]}")
    out = ",".join(dict.fromkeys(names))
    if len(out) > 500:
        raise ValueError("Too many events")
    return out


# ── Secrets (B6) ──────────────────────────────────────────────────────────────────────────────

def new_secret() -> str:
    return SECRET_PREFIX + _secrets.token_urlsafe(32)


def store_secret(plain: str) -> str:
    from backend.services.crypto import encrypt
    return encrypt(plain)


def reveal_secret(stored: Optional[str]) -> str:
    from backend.services.crypto import decrypt
    return decrypt(stored) or ""


def secret_hint(stored: Optional[str]) -> Optional[str]:
    plain = reveal_secret(stored)
    if not plain:
        return None
    return (SECRET_PREFIX if plain.startswith(SECRET_PREFIX) else "") + "…" + plain[-4:]


def signing_secrets(ep, now: Optional[datetime] = None) -> list[str]:
    """The current secret, plus the previous one while its rotation overlap lasts."""
    now = now or datetime.utcnow()
    out = [reveal_secret(ep.secret)]
    prev = getattr(ep, "previous_secret", None)
    until = getattr(ep, "previous_secret_expires_at", None)
    if prev and until and until > now:
        out.append(reveal_secret(prev))
    return [s for s in dict.fromkeys(out) if s]


def rotate_secret(ep, now: Optional[datetime] = None) -> str:
    """Give the endpoint a new secret; the old one keeps signing for ROTATION_OVERLAP.
    Returns the new secret (shown to the user once)."""
    now = now or datetime.utcnow()
    plain = new_secret()
    ep.previous_secret = ep.secret
    ep.previous_secret_expires_at = now + ROTATION_OVERLAP
    ep.secret = store_secret(plain)
    return plain


# ── Signing (B1) ──────────────────────────────────────────────────────────────────────────────

def sign(secret: str, timestamp: int, body: str) -> str:
    from backend.services.webhook_verify_example import compute_signature
    return compute_signature(secret, timestamp, body)


def signature_header(secrets: Iterable[str], timestamp: int, body: str) -> str:
    return ",".join([f"t={int(timestamp)}"] + [f"v1={sign(s, timestamp, body)}" for s in secrets])


def legacy_signature(secret: str, body: str) -> str:
    return "sha256=" + hmac.new((secret or "").encode(), body.encode(), hashlib.sha256).hexdigest()


def build_headers(delivery_id: str, event_name: str, secrets: list[str], body: str,
                  timestamp: Optional[int] = None) -> dict:
    t = int(time.time()) if timestamp is None else int(timestamp)
    primary = secrets[0] if secrets else ""
    legacy = legacy_signature(primary, body)
    return {
        "Content-Type": "application/json",
        "User-Agent": "Tekly-Webhooks/1.0",
        "Tekly-Signature": signature_header(secrets or [""], t, body),
        "Tekly-Webhook-Id": delivery_id or "",
        "Tekly-Event": event_name,
        # Deprecated: kept one release cycle so receivers built before Tekly-Signature verify.
        "X-Tekly-Event": event_name,
        "X-Tekly-Signature": legacy,
        "X-RepairOS-Event": event_name,
        "X-RepairOS-Signature": legacy,
        "Deprecation": "true",
        "Tekly-Deprecated-Headers": "X-Tekly-Event, X-Tekly-Signature, X-RepairOS-Event, X-RepairOS-Signature",
    }


# ── Envelope and payloads (B1, B16) ───────────────────────────────────────────────────────────

def _iso(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(microsecond=0).isoformat() + "Z" if v.tzinfo is None else v.isoformat()
    return v.isoformat() if hasattr(v, "isoformat") else v


def _wall(v):
    """Shop wall-clock times (scheduled_for and friends) are naive local time: no zone suffix,
    so receivers don't read them as UTC."""
    if v is None:
        return None
    return v.replace(microsecond=0, tzinfo=None).isoformat() if isinstance(v, datetime) else v


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def envelope(event_id: str, name: str, org_id: str, data: dict, created_at: datetime) -> str:
    return json.dumps({
        "id": event_id, "event": name, "api_version": API_VERSION, "org_id": org_id,
        "created_at": _iso(created_at), "data": data,
        "at": created_at.isoformat(),          # deprecated: the pre-envelope timestamp field
    }, default=str)


def order_data(o) -> dict:
    # "number" is a deprecated alias of order_number: API-created orders used to send only it.
    return {"id": o.id, "order_number": o.order_number, "number": o.order_number,
            "status": o.status, "total": _num(o.total),
            "is_estimate": bool(o.is_estimate), "client_id": o.client_id, "location_id": o.location_id,
            "assigned_to_id": o.assigned_to_id, "priority": o.priority, "due_at": _iso(o.due_at),
            "created_at": _iso(o.created_at)}


def appointment_data(a) -> dict:
    return {"id": a.id, "scheduled_for": _wall(a.scheduled_for), "duration_min": a.duration_min,
            "status": a.status, "client_id": a.client_id, "client_name": a.client_name,
            "service_id": a.service_id, "employee_id": a.employee_id, "location_id": a.location_id,
            "order_id": a.order_id, "created_at": _iso(a.created_at)}


def contact_data(c) -> dict:
    return {"id": c.id, "name": c.name, "type": c.client_type, "phone": c.phone, "email": c.email,
            "is_supplier": bool(c.is_supplier), "source": c.source, "created_at": _iso(c.created_at)}


def lead_data(ld) -> dict:
    return {"id": ld.id, "name": ld.name, "phone": ld.phone, "email": ld.email, "source": ld.source,
            "status": ld.status, "inquiry": ld.inquiry, "location_id": ld.location_id,
            "client_id": ld.client_id, "lead_type_id": getattr(ld, "lead_type_id", None),
            "device_brand": getattr(ld, "device_brand", None), "device_model": getattr(ld, "device_model", None),
            "serial": getattr(ld, "serial", None), "call_count": getattr(ld, "call_count", None) or 0,
            "converted_order_id": getattr(ld, "converted_order_id", None),
            "created_at": _iso(ld.created_at), "updated_at": _iso(getattr(ld, "updated_at", None))}


def invoice_data(i) -> dict:
    return {"id": i.id, "invoice_number": i.invoice_number, "status": i.status, "order_id": i.order_id,
            "client_id": i.client_id, "total": _num(i.total), "amount_paid": _num(i.amount_paid),
            "issued_at": _iso(i.issued_at), "due_at": _iso(i.due_at), "created_at": _iso(i.created_at)}


def _builders():
    from backend.models import Appointment, Client, Invoice, Lead, Order
    return {
        "order.created": (Order, order_data), "order.updated": (Order, order_data),
        "appointment.created": (Appointment, appointment_data),
        "contact.created": (Client, contact_data), "contact.updated": (Client, contact_data),
        "inquiry.created": (Lead, lead_data),
        "invoice.created": (Invoice, invoice_data), "invoice.paid": (Invoice, invoice_data),
    }


_EXTRA_KEYS = ("changed_fields",)

# session.info keys
_OUTBOX_KEY = "repairos_webhook_outbox"       # delivery ids to send after commit
_SEEN_KEY = "tekly_webhook_seen"              # (event, record id) already emitted in this session
_LIVE_KEY = "tekly_webhook_live"              # deliveries to re-render before commit
_PENDING_KEY = "tekly_webhook_pending"        # ORM-detected events waiting for after_flush_postexec
_UPDATED_KEY = "tekly_webhook_order_updates"  # order id -> (changed fields, deliveries) this txn
_CREATED_KEY = "tekly_webhook_created_orders"  # orders created in this session: no order.updated


def _endpoints_sync(session, org_id: str):
    from backend.models import WebhookEndpoint
    with session.no_autoflush:
        return session.execute(select(WebhookEndpoint).where(
            WebhookEndpoint.org_id == org_id, WebhookEndpoint.active == True,  # noqa: E712
            WebhookEndpoint.kind == "endpoint", WebhookEndpoint.deleted_at.is_(None))).scalars().all()


def _queue(session, org_id: str, ep, name: str, event_id: str, data: dict, now: datetime):
    from backend.models import WebhookDelivery
    d = WebhookDelivery(id=str(uuid.uuid4()), org_id=org_id, endpoint_id=ep.id, event=name,
                        payload=envelope(event_id, name, org_id, data, now), status="pending",
                        attempts=0, created_at=now)
    session.add(d)
    # Transactional outbox: the row commits (or rolls back) with the caller's change, and the
    # POST happens only after commit.
    session.info.setdefault(_OUTBOX_KEY, []).append(d.id)
    return d


def _emit_sync(session, org_id: str, event: str, data: dict, *, obj=None, record_id=None) -> list:
    """Write one delivery per subscribed endpoint. `obj`, when given, is re-rendered before commit."""
    if record_id is not None:
        seen = session.info.setdefault(_SEEN_KEY, set())
        if (event, record_id) in seen:
            return []
        seen.add((event, record_id))
    endpoints = _endpoints_sync(session, org_id)
    if not endpoints:
        return []
    now = datetime.utcnow()
    event_id = "evt_" + uuid.uuid4().hex
    out = []
    for ep in endpoints:
        name = _delivery_name(ep.events, event)
        if name is None:
            continue
        d = _queue(session, org_id, ep, name, event_id, data, now)
        out.append(d)
        if obj is not None:
            session.info.setdefault(_LIVE_KEY, []).append((d, obj, event, event_id, name, now))
    return out


async def emit_event(db: AsyncSession, org_id: str, event: str, payload: dict) -> None:
    """Fan an event out to all subscribed endpoints for the org. Best-effort; caller commits.

    Record events (order.created, appointment.created, ...) are rendered from the record itself,
    so every source sends the same shape; the same record's event is only queued once."""
    try:
        data, obj, record_id = dict(payload or {}), None, None
        spec = _builders().get(event)
        if spec and data.get("id"):
            model, build = spec
            obj = await db.get(model, data["id"])
            if obj is not None:
                record_id = obj.id
                extras = {k: data[k] for k in _EXTRA_KEYS if k in data}
                data = {**build(obj), **extras}
        await db.run_sync(lambda s: _emit_sync(s, org_id, event, data, obj=obj, record_id=record_id))
    except Exception:  # noqa: BLE001 — a webhook must never break the caller's request
        log.exception("emit_event %s failed", event)


# ── ORM-driven events (B16) ───────────────────────────────────────────────────────────────────

ORDER_UPDATE_FIELDS = (
    "status", "priority", "assigned_to_id", "manager_id", "location_id", "client_id", "device_id",
    "complaint", "diagnosis", "conclusion", "due_at", "scheduled_for", "scheduled_to", "total",
    "discount", "type_id", "warranty_until", "is_estimate", "archived",
)


def _changed(obj, field) -> bool:
    from sqlalchemy import inspect as sa_inspect
    h = sa_inspect(obj).attrs[field].history
    if not h.has_changes():
        return False
    if h.added and h.deleted:
        return h.added[0] != h.deleted[0]
    return True


def _collect_after_flush(session, _ctx) -> None:
    """Note record events while the flush's new/dirty state is still visible."""
    try:
        from backend.models import Appointment, Client, Invoice, Lead, Order
        pending = session.info.setdefault(_PENDING_KEY, [])
        created_orders = session.info.setdefault(_CREATED_KEY, set())
        for obj in list(session.new):
            if isinstance(obj, Client):
                pending.append(("contact.created", obj, None))
            elif isinstance(obj, Lead):
                pending.append(("inquiry.created", obj, None))
            elif isinstance(obj, Appointment):
                pending.append(("appointment.created", obj, None))
            elif isinstance(obj, Invoice):
                pending.append(("invoice.created", obj, None))
                if obj.status == "paid":
                    pending.append(("invoice.paid", obj, None))
            elif isinstance(obj, Order):
                created_orders.add(obj.id)
        for obj in list(session.dirty):
            if isinstance(obj, Invoice):
                if obj.status == "paid" and _changed(obj, "status"):
                    pending.append(("invoice.paid", obj, None))
            elif isinstance(obj, Order) and obj.id not in created_orders:
                fields = [f for f in ORDER_UPDATE_FIELDS if _changed(obj, f)]
                if fields:
                    pending.append(("order.updated", obj, fields))
    except Exception:  # noqa: BLE001
        log.exception("webhook event collection failed")


def _emit_after_flush(session, _ctx) -> None:
    """Queue deliveries for the events noted in after_flush (they flush with the next pass)."""
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    try:
        builders = _builders()
        updates = session.info.setdefault(_UPDATED_KEY, {})
        for event, obj, fields in pending:
            _, build = builders[event]
            if event == "order.updated":
                prior = updates.get(obj.id)
                if prior is not None:          # same transaction: one event with every field
                    prior[0].extend(f for f in fields if f not in prior[0])
                    continue
                changed = list(fields)
                ds = _emit_sync(session, obj.org_id, event, {**build(obj), "changed_fields": changed}, obj=obj)
                updates[obj.id] = (changed, ds)
            else:
                _emit_sync(session, obj.org_id, event, build(obj), obj=obj, record_id=obj.id)
    except Exception:  # noqa: BLE001
        log.exception("webhook ORM emit failed")


def _render_before_commit(session) -> None:
    """Re-render record payloads from the state about to be committed."""
    live = session.info.pop(_LIVE_KEY, None)
    updates = session.info.pop(_UPDATED_KEY, None) or {}
    if not live:
        return
    try:
        builders = _builders()
        for d, obj, event, event_id, name, created in live:
            if obj not in session:
                continue
            data = builders[event][1](obj)
            if event == "order.updated" and obj.id in updates:
                data["changed_fields"] = updates[obj.id][0]
            body = envelope(event_id, name, d.org_id, data, created)
            if body != d.payload:
                d.payload = body
    except Exception:  # noqa: BLE001
        log.exception("webhook payload render failed")


def _register_hooks() -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    @event.listens_for(Session, "after_flush")
    def _af(session, ctx):
        _collect_after_flush(session, ctx)

    @event.listens_for(Session, "after_flush_postexec")
    def _afp(session, ctx):
        _emit_after_flush(session, ctx)

    @event.listens_for(Session, "before_commit")
    def _bc(session):
        _render_before_commit(session)

    @event.listens_for(Session, "after_commit")
    def _flush_outbox(session):
        session.info.pop(_LIVE_KEY, None)
        session.info.pop(_UPDATED_KEY, None)
        ids = session.info.pop(_OUTBOX_KEY, None)
        if ids:
            from backend.core.jobs import job_runner
            try:
                job_runner.enqueue(deliver_pending, list(ids), retries=2, name="webhook-outbox")
            except RuntimeError:   # no running loop (sync context): the retry sweep delivers them
                pass

    @event.listens_for(Session, "after_rollback")
    def _drop_outbox(session):
        for k in (_OUTBOX_KEY, _LIVE_KEY, _PENDING_KEY, _UPDATED_KEY, _SEEN_KEY):
            session.info.pop(k, None)


_register_hooks()


# ── Automation-rule webhook actions (B2) ──────────────────────────────────────────────────────

async def automation_endpoint(db: AsyncSession, org_id: str, rule_id: str, url: str):
    """The hidden endpoint an automation rule's webhook action delivers to (created on demand).
    Returns (endpoint, new_secret_or_None)."""
    from backend.models import WebhookEndpoint
    ep = (await db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.org_id == org_id, WebhookEndpoint.kind == "automation",
        WebhookEndpoint.source_id == rule_id))).scalars().first()
    fresh = None
    if ep is None:
        fresh = new_secret()
        ep = WebhookEndpoint(org_id=org_id, url=url, events="*", active=True, kind="automation",
                             source_id=rule_id, secret=store_secret(fresh))
        db.add(ep)
    else:
        # Saving the rule (re)points its target and turns it back on after an auto-disable.
        ep.url, ep.deleted_at = url, None
        ep.active, ep.failing_since, ep.disabled_reason = True, None, None
    await db.flush()
    return ep, fresh


async def queue_automation_webhook(db: AsyncSession, org_id: str, rule, data: dict) -> str:
    """Queue a signed delivery for an automation rule. Returns a short, generic summary."""
    from backend.models import WebhookEndpoint
    ep = (await db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.org_id == org_id, WebhookEndpoint.kind == "automation",
        WebhookEndpoint.source_id == rule.id, WebhookEndpoint.deleted_at.is_(None)))).scalars().first()
    url = (data.pop("_url", None) or "").strip()
    if ep is None:
        if not url:
            return "webhook not set up"
        ep, _ = await automation_endpoint(db, org_id, rule.id, url)
    if not ep.active:
        return "webhook is turned off after repeated failures"
    now = datetime.utcnow()
    event_id = "evt_" + uuid.uuid4().hex

    def _q(s):
        d = _queue(s, org_id, ep, rule.event, event_id, data, now)
        # The old automation body was flat; keep those keys at the top level for one cycle.
        body = json.loads(d.payload)
        for k in ("rule", "order_id", "order_number", "status"):
            body.setdefault(k, data.get(k))
        d.payload = json.dumps(body, default=str)
    await db.run_sync(_q)
    return "webhook queued"


# ── Delivery (B4, B5) ─────────────────────────────────────────────────────────────────────────

_SEMS = weakref.WeakKeyDictionary()


def _limits():
    """(global semaphore, per-endpoint locks) for the running loop."""
    loop = asyncio.get_running_loop()
    got = _SEMS.get(loop)
    if got is None:
        got = (asyncio.Semaphore(MAX_CONCURRENT_DELIVERIES), {})
        _SEMS[loop] = got
    return got


def next_attempt_delay(attempts: int) -> Optional[int]:
    """Seconds to wait after the `attempts`-th failed attempt, or None once given up."""
    if attempts < 1 or attempts > len(RETRY_SCHEDULE_S):
        return None
    return RETRY_SCHEDULE_S[attempts - 1]


def _fail(delivery, error: str, now: datetime) -> None:
    delivery.status = "failed"
    delivery.error = error
    delay = next_attempt_delay(delivery.attempts)
    delivery.next_attempt_at = now + timedelta(seconds=delay) if delay is not None else None


async def _post(target: str, body: str, headers: dict, extensions: dict) -> int:
    import httpx
    # follow_redirects=False: a public endpoint must not bounce us to an internal one.
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S, follow_redirects=False) as client:
        async with client.stream("POST", target, content=body.encode(), headers=headers,
                                 extensions=extensions) as resp:
            read = 0
            try:
                async for chunk in resp.aiter_raw():  # read at most MAX_RESPONSE_BYTES, then hang up
                    read += len(chunk)
                    if read >= MAX_RESPONSE_BYTES:
                        break
            except httpx.StreamConsumed:              # an in-memory (mock) response: nothing to drain
                pass
            return resp.status_code


async def _attempt(delivery, url: str, secret, body: str, *, now: Optional[datetime] = None) -> None:
    """One delivery attempt. Records the outcome on the delivery row; never raises.
    `secret` is one secret or the list of active ones (rotation overlap)."""
    now = now or datetime.utcnow()
    delivery.attempts = (delivery.attempts or 0) + 1
    secrets = [secret] if isinstance(secret, str) or secret is None else list(secret)
    secrets = [s for s in secrets if s] or [""]
    try:
        import httpx
    except Exception:  # noqa: BLE001
        delivery.status = "pending"
        delivery.error = "httpx unavailable; delivery skipped"
        return
    from backend.core.ssrf import UnsafeURLError, pinned_target
    # Re-validate on every attempt (DNS can change after the endpoint was saved) and connect to
    # the exact address that passed the check.
    try:
        target, host_header, extensions = await asyncio.to_thread(pinned_target, url, webhook=True)
    except UnsafeURLError:
        _fail(delivery, "Blocked: URL is not an allowed public https address", now)
        return
    try:
        event_name = json.loads(body).get("event", "")
    except ValueError:
        event_name = ""
    headers = {**host_header, **build_headers(getattr(delivery, "id", "") or "", event_name, secrets, body)}
    try:
        code = await asyncio.wait_for(_post(target, body, headers, extensions), DELIVERY_DEADLINE_S)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        _fail(delivery, "Timed out", now)
        return
    except Exception:  # noqa: BLE001 — generic reason only: raw socket errors would turn the
        # delivery log into a port/host scanner for whoever configured the endpoint.
        _fail(delivery, "Connection failed", now)
        return
    delivery.response_code = code
    if 200 <= code < 300:
        delivery.status = "sent"
        delivery.error = None
        delivery.delivered_at = datetime.utcnow()
        delivery.next_attempt_at = None
    else:
        _fail(delivery, f"HTTP {code}", now)


async def _notify_disabled(db, ep) -> None:
    """Tell everyone who can manage settings that an endpoint was switched off."""
    from backend.models import Employee, Role
    from backend.services.permissions import role_allows
    from backend.services.push import notify
    rows = (await db.execute(select(Employee, Role).outerjoin(Role, Role.id == Employee.role_id).where(
        Employee.org_id == ep.org_id, Employee.active == True))).all()  # noqa: E712
    if ep.kind == "automation":
        where, fix = "an automation rule's webhook", "Fix the endpoint, then save the rule again in Settings › Notifications › Automations."
    else:
        where, fix = (ep.description or ep.url)[:80], "Fix the endpoint, then turn it back on in Settings › Integrations."
    for emp, role in rows:
        if role_allows(role, "can_manage_settings"):
            await notify(db, org_id=ep.org_id, employee_id=emp.id, kind="webhook.disabled",
                         title="Webhook turned off",
                         body=f"Deliveries to {where} failed for 24 hours, so it was turned off. {fix}",
                         deep_link="/settings#integrations", commit=False, respect_prefs=False)


async def record_endpoint_health(db, ep, delivery, now: Optional[datetime] = None) -> bool:
    """B5: track continuous failure; switch the endpoint off after AUTO_DISABLE_AFTER.
    Returns True when this call disabled it."""
    now = now or datetime.utcnow()
    if delivery.status == "sent":
        ep.failing_since = None
        return False
    if delivery.status != "failed":
        return False
    if ep.failing_since is None:
        ep.failing_since = now
        return False
    if ep.active and now - ep.failing_since >= AUTO_DISABLE_AFTER:
        ep.active = False
        ep.disabled_reason = DISABLED_REASON
        try:
            await _notify_disabled(db, ep)
        except Exception:  # noqa: BLE001
            log.exception("webhook disable notification failed")
        return True
    return False


async def claim_delivery(db, delivery, from_statuses) -> bool:
    """Atomically move a delivery to "sending" so exactly one worker (post-commit outbox or the
    retry sweep, in any process) attempts it. Commits the claim."""
    from sqlalchemy import update

    from backend.models import WebhookDelivery
    now = datetime.utcnow()
    res = await db.execute(update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery.id, WebhookDelivery.status.in_(from_statuses))
        .values(status="sending", claimed_at=now).execution_options(synchronize_session=False))
    await db.commit()
    return res.rowcount == 1


async def deliver_one(delivery_id: str, from_statuses=("pending",)) -> Optional[str]:
    """Claim and attempt one delivery in its own session, within the concurrency limits.
    Returns the final status, or None when another worker had it or it isn't deliverable."""
    from backend.db.database import AsyncSessionLocal
    from backend.models import WebhookDelivery, WebhookEndpoint
    sem, locks = _limits()
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(WebhookDelivery, WebhookEndpoint)
               .join(WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.endpoint_id)
               .where(WebhookDelivery.id == delivery_id))).first()
        if not row:
            return None
        delivery, ep = row
        if delivery.status not in from_statuses:
            return None
        if not ep.active or ep.deleted_at is not None:
            delivery.status, delivery.next_attempt_at = "abandoned", None
            delivery.error = delivery.error or "Endpoint is off"
            await db.commit()
            return "abandoned"
        lock = locks.setdefault(ep.id, asyncio.Lock())
        async with sem, lock:                       # ~20 at once overall, 1 per endpoint
            if not await claim_delivery(db, delivery, from_statuses):
                return None
            await db.refresh(delivery)
            await db.refresh(ep)
            await _attempt(delivery, ep.url, signing_secrets(ep), delivery.payload)
            await record_endpoint_health(db, ep, delivery)
            await db.commit()
        return delivery.status


async def deliver_many(ids: list, from_statuses=("pending",)) -> list:
    results = await asyncio.gather(*[deliver_one(i, from_statuses) for i in ids], return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.error("webhook delivery crashed: %s", type(r).__name__)
    return [r for r in results if not isinstance(r, Exception)]


async def deliver_pending(delivery_ids: list) -> int:
    """Attempt committed, still-pending deliveries by id (post-commit outbox worker). Rows that
    were rolled back simply don't exist. Anything that fails is retried by the periodic sweep."""
    results = await deliver_many(list(delivery_ids), ("pending",))
    return sum(1 for r in results if r in ("sent", "failed"))


async def retry_sweep(now: Optional[datetime] = None, limit: int = 100) -> int:
    """B5: reclaim rows stuck in "sending", then attempt every due pending/failed row."""
    from sqlalchemy import and_, or_, update

    from backend.db.database import AsyncSessionLocal
    from backend.models import WebhookDelivery
    now = now or datetime.utcnow()
    async with AsyncSessionLocal() as db:
        stale = now - STUCK_SENDING_AFTER
        await db.execute(update(WebhookDelivery).where(
            WebhookDelivery.status == "sending",
            or_(WebhookDelivery.claimed_at < stale,
                and_(WebhookDelivery.claimed_at.is_(None), WebhookDelivery.created_at < stale)))
            .values(status="failed", error="Interrupted; will retry", next_attempt_at=now)
            .execution_options(synchronize_session=False))
        await db.commit()
        ids = (await db.execute(select(WebhookDelivery.id).where(
            WebhookDelivery.status.in_(("failed", "pending")),
            WebhookDelivery.attempts < MAX_ATTEMPTS,
            or_(WebhookDelivery.next_attempt_at.is_(None), WebhookDelivery.next_attempt_at <= now),
            # a failed row with no next attempt has run out of retries
            or_(WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at.is_not(None),
                WebhookDelivery.attempts == 0),
        ).order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.created_at).limit(limit))).scalars().all()
    if not ids:
        return 0
    results = await deliver_many(list(ids), ("failed", "pending"))
    return sum(1 for r in results if r in ("sent", "failed"))
