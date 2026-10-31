"""
Public order tracking — no authentication.

A customer opens /track/{token}. We expose only safe, client-facing data:
order number, status + a friendly stage, device, total, the status timeline,
and event-feed items explicitly marked client-visible. The customer can also
accept or decline an estimate, which is recorded back into the event feed.
"""
import hashlib
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel, Field
from typing import Annotated, Optional
from datetime import date as _date
from datetime import datetime as _dt
from backend.db.database import get_db
from backend.services.public_names import display_name as _public_name
from backend.models import (
    Order, Client, Device, Organisation, Location,
    OrderStatusLog, EventFeedItem, DocumentSignature,
    Feedback, FeedbackSettings,
)
from backend.services.dates import now_utc, shop_today

router = APIRouter(prefix="/api/public", tags=["public"])

# Friendly customer-facing stage labels + ordered pipeline
STAGE_FLOW = ["new", "diagnosed", "waiting_parts", "in_repair", "done", "delivered"]
STAGE_LABEL = {
    "new": "Received",
    "diagnosed": "Diagnosed",
    "waiting_parts": "Waiting for parts",
    "in_repair": "In repair",
    "done": "Ready for pickup",
    "delivered": "Completed",
}


class AcceptIn(BaseModel):
    decision: str          # accept | decline
    signature: Optional[str] = None   # data URL
    name: Optional[str] = None
    reason: Optional[str] = Field(default=None, max_length=1000)   # E5: why they declined
    skip_line_ids: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=200)   # E5: lines left out on accept


async def _load(db: AsyncSession, token: str) -> Order:
    if not token:
        raise HTTPException(404, "Not found")
    row = await db.execute(select(Order).where(Order.public_token == token))
    order = row.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    return order


async def _order_invoice(db: AsyncSession, order: "Order"):
    """144if — the invoice billing this order, if any (direct link or the multi-order link
    table). Newest first. Used to surface the invoice + e-invoice on the public track page."""
    from backend.models import Invoice, InvoiceOrder
    inv_id = (await db.execute(select(InvoiceOrder.invoice_id).where(
        InvoiceOrder.order_id == order.id))).scalars().first()
    if inv_id:
        return (await db.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one_or_none()
    return (await db.execute(select(Invoice).where(Invoice.order_id == order.id)
            .order_by(Invoice.created_at.desc()))).scalars().first()


def _public_invoice_block(inv, fx) -> dict:
    """Client-safe invoice summary for the track page."""
    total = float(inv.total or 0)
    paid = float(inv.amount_paid or 0)
    credited = float(getattr(inv, "credited", 0) or 0)   # issued credit notes lower what's owed
    return {
        "invoice_number": inv.invoice_number,
        "status": inv.status,
        "total": total,
        "total_converted": q2(total * fx) if fx else total,
        "amount_paid": paid,
        "balance_due": q2(max(total - paid - credited, 0)),
        "einvoice_status": getattr(inv, "einvoice_status", "none") or "none",
        "einvoice_number": getattr(inv, "einvoice_number", None),
        # download path is served by the track-scoped endpoint below (no auth, token-gated)
        "einvoice_available": bool(getattr(inv, "einvoice_xml", None)),
    }


async def _record_signature(db, order, decision, signature, who, request):
    """144ig — persist a tamper-evident signed-approval record for an estimate or work
    order: typed name, drawn image, timestamp, signer IP + user agent, and a SHA-256
    hash of the exact document snapshot (number, total, status) the customer approved."""
    ip = request.client.host if (request and request.client) else None
    ua = request.headers.get("user-agent") if request else None
    doc_type = "estimate" if order.is_estimate else "work_order"
    total = float(order.total or 0)
    org = (await db.execute(select(Organisation).where(Organisation.id == order.org_id))).scalar_one_or_none()
    ccy = (getattr(org, "currency_code", None) if org else None) or None
    snapshot = f"{doc_type}|{order.order_number}|{order.id}|{total:.2f}|{order.status}|{decision}|{who or ''}"
    doc_hash = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    sig = DocumentSignature(
        org_id=order.org_id, order_id=order.id, doc_type=doc_type, decision=decision,
        revision=_revision(order),
        signer_name=who, signature_png=signature, doc_hash=doc_hash, doc_total=total,
        currency_code=ccy, signer_ip=ip, user_agent=(ua[:300] if ua else None))
    db.add(sig)
    return sig


async def _configured_target_key(db: AsyncSession, order: "Order", decision: str) -> Optional[str]:
    """144ii — the status the shop configured to move to on accept/decline, per doc type."""
    org = (await db.execute(select(Organisation).where(Organisation.id == order.org_id))).scalar_one_or_none()
    if not org:
        return None
    if order.is_estimate:
        key = org.oe_estimate_accept_status if decision == "accept" else org.oe_estimate_decline_status
    else:
        key = org.oe_wo_accept_status if decision == "accept" else org.oe_wo_decline_status
    return (key or "").strip() or None


async def _signature_required(db: AsyncSession, order: "Order") -> bool:
    org = (await db.execute(select(Organisation).where(Organisation.id == order.org_id))).scalar_one_or_none()
    if not org:
        return False
    return bool(getattr(org, "oe_estimate_require_signature" if order.is_estimate else "oe_wo_require_signature", False))


def _revision(order: "Order") -> int:
    return int(getattr(order, "estimate_revision", None) or 1)


async def _expired(db: AsyncSession, order: "Order") -> bool:
    """E5 — an estimate past its "valid until" date can't be approved online any more. The date
    is the shop's: it lasts to the end of that day in the shop's time zone."""
    vu = getattr(order, "estimate_valid_until", None)
    if not (order.is_estimate and vu):
        return False
    from backend.services.dates import shop_tzname
    try:
        from zoneinfo import ZoneInfo
        today = _dt.now(ZoneInfo(await shop_tzname(db, order.org_id, order.location_id))).date()
    except Exception:  # noqa: BLE001 — an unknown zone name falls back to UTC
        today = _date.today()
    return vu < today


async def _already_decided(db: AsyncSession, order: "Order") -> bool:
    """E2 — the customer has already approved or declined this document (estimate, or work
    order when work-order approval is on). One decision each; after that, staff take over.
    E5 — one decision per version: a new estimate revision can be decided again."""
    doc_type = "estimate" if order.is_estimate else "work_order"
    return (await db.execute(select(DocumentSignature.id).where(
        DocumentSignature.order_id == order.id, DocumentSignature.doc_type == doc_type,
        DocumentSignature.revision == _revision(order)).limit(1))).first() is not None


async def _decide_estimate(db: AsyncSession, order: "Order", decision: str,
                           signature: Optional[str], who: str,
                           request: Optional[Request] = None, reason: Optional[str] = None) -> Optional[str]:
    """Apply a customer's accept/decline to an estimate (or a work order, when the shop lets
    customers approve those). Shared by the public track page and the customer portal. Returns
    the converted work-order id (or None).

    Records a client-visible feed event (+ optional signature) and a signed certificate, tells
    the shop (G1), then moves the document through the normal status change (E3) so status
    rules, notifications, webhooks and auto-conversion with the new-job steps all run.
    """
    verb = "accepted" if decision == "accept" else "declined"
    doc = "estimate" if order.is_estimate else "work order"
    reason = ((reason or "").strip()[:1000] or None) if decision == "decline" else None
    order.estimate_decline_reason = reason      # E5: shown to staff on the job
    db.add(EventFeedItem(
        org_id=order.org_id, entity_type="order", entity_id=order.id,
        item_type="comment", body=f"Customer {verb} the {doc}" + (f": {reason}" if reason else ""),
        visibility="client", author_name=who,
    ))
    if signature:
        order.signature = signature
        db.add(EventFeedItem(
            org_id=order.org_id, entity_type="order", entity_id=order.id,
            item_type="signature", media_data=signature, media_name="customer_signature.png",
            media_mime="image/png", visibility="client", author_name=who,
            body=f"Signature on {verb} {doc}",
        ))

    # 144ig — always record a tamper-evident signed-approval certificate (estimate OR work order)
    await _record_signature(db, order, decision, signature, who, request)
    if decision == "accept" and order.is_estimate:
        # Build 75: record WHEN the customer approved, so the approval is auditable
        # independently of the status the estimate happens to land in.
        order.estimate_approved_at = now_utc()
        from backend.services.webhooks import emit_event   # WS-C: was registered but never fired
        await emit_event(db, order.org_id, "estimate.accepted", {"id": order.id, "number": order.order_number})

    # G1 — the shop hears about it: owner, the job's manager and its assignee.
    from backend.services.customer_messages import notify_staff_about_customer
    approved = decision == "accept"
    await notify_staff_about_customer(
        db, order, kind="estimate.approved" if approved else "estimate.declined",
        title=f"{doc.capitalize()} {'approved' if approved else 'declined'} · {order.order_number}",
        body=f"{who or 'The customer'} {'approved' if approved else 'declined'} the {doc} online."
             + (f" Reason: {reason}" if reason else "")
             + (f" Left out: {', '.join(order._skipped_lines)}." if getattr(order, "_skipped_lines", None) else ""))

    if approved and order.is_estimate:
        try:   # Build 144w: workflow automations for estimate_approved (best-effort).
            from backend.services import automation as _auto
            await _auto.run(db, order.org_id, "estimate_approved", order=order)
        except Exception:  # noqa: BLE001
            pass

    target = await _decision_target(db, order, decision)
    if not target or target.key == order.status:
        return None
    return await _move_for_customer(db, order, target, f"{verb.capitalize()} by customer", who)


async def _decision_target(db: AsyncSession, order: "Order", decision: str):
    """Where the document goes: the status the shop configured for this decision (144ii), else
    the built-in default for estimates (accept → the "converts to order" status, or a closed
    one; decline → a dropped one). Work orders without a configured status stay put."""
    from backend.models import OrderStatus
    cfg_key = await _configured_target_key(db, order, decision)
    if cfg_key:
        return (await db.execute(select(OrderStatus).where(
            OrderStatus.org_id == order.org_id, OrderStatus.key == cfg_key,
            OrderStatus.record_type == ("estimate" if order.is_estimate else "work_order"),
            OrderStatus.active == True))).scalars().first()  # noqa: E712
    if not order.is_estimate:
        return None
    est = (OrderStatus.org_id == order.org_id, OrderStatus.is_estimate == True,  # noqa: E712
           OrderStatus.active == True)  # noqa: E712
    if decision == "accept":
        return ((await db.execute(select(OrderStatus).where(
            *est, OrderStatus.converts_to_order == True))).scalars().first()  # noqa: E712
            or (await db.execute(select(OrderStatus).where(
                *est, OrderStatus.status_group == "closed"))).scalars().first())
    return (await db.execute(select(OrderStatus).where(
        *est, OrderStatus.status_group == "dropped"))).scalars().first()


async def _move_for_customer(db: AsyncSession, order: "Order", target, note: str, who: str) -> Optional[str]:
    """E3 — run the customer's decision through the same status change staff use. A shop gate
    meant for staff (e.g. "needs a comment first") must not undo what the customer decided, so
    when one refuses, the status is set directly and the job says why."""
    from backend.models import OrderStatusLog
    from backend.routers.feed import log_system_event
    from backend.routers.orders import SystemActor, _apply_status_change, _convert_estimate
    actor = SystemActor(order.org_id, name=who or "Customer")
    await db.refresh(order, ["lines"])
    try:
        res = await _apply_status_change(db, actor, order, target.key, note=note)
        return res.get("converted_order_id")
    except HTTPException as exc:
        if not order.is_estimate:
            # A work order's gates (payment, checklist, stock, signature) hold even for the customer.
            await log_system_event(db, order.org_id, "order", order.id,
                                   f"Customer {note.split(' ')[0].lower()}; status not changed: {exc.detail}")
            return None
        prev = order.status
        order.status = target.key
        order.status_entered_at = _dt.utcnow()
        db.add(OrderStatusLog(order_id=order.id, from_status=prev, to_status=target.key, note=note))
        await log_system_event(db, order.org_id, "order", order.id,
                               f"Moved to '{target.name}' for the customer without the usual check: {exc.detail}")
        await db.flush()
        if getattr(target, "converts_to_order", False) and order.is_estimate and not order.converted_order_id:
            return await _convert_estimate(db, actor, order)
        return None


@router.get("/track/{token}")
async def track(token: str, db: AsyncSession = Depends(get_db)):
    order = await _load(db, token)
    return await _track_payload(db, order)


async def _track_payload(db: AsyncSession, order: "Order") -> dict:
    """Everything the customer sees for one job. The track page and the customer portal's job
    view both show this, so the two pages never drift apart."""
    client = (await db.execute(select(Client).where(Client.id == order.client_id))).scalar_one_or_none() if order.client_id else None
    device = (await db.execute(select(Device).where(Device.id == order.device_id))).scalar_one_or_none() if order.device_id else None
    org = (await db.execute(select(Organisation).where(Organisation.id == order.org_id))).scalar_one_or_none()
    loc = (await db.execute(select(Location).where(Location.id == order.location_id))).scalar_one_or_none() if order.location_id else None

    # status timeline (public-safe)
    logs = (await db.execute(
        select(OrderStatusLog).where(OrderStatusLog.order_id == order.id).order_by(OrderStatusLog.changed_at)
    )).scalars().all()

    # only client-visible feed items
    feed = (await db.execute(
        select(EventFeedItem).where(
            EventFeedItem.entity_type == "order",
            EventFeedItem.entity_id == order.id,
            EventFeedItem.visibility == "client",
        ).order_by(desc(EventFeedItem.created_at))
    )).scalars().all()

    # 144ct — staff replies to the customer are client-visible OrderComments (the work-order
    # ConversationPanel posts them). Surface them here so the customer actually sees the reply,
    # not just their own outgoing message. Merged into `updates` below, newest-first.
    from backend.models import OrderComment
    staff_msgs = (await db.execute(
        select(OrderComment).where(
            OrderComment.order_id == order.id,
            OrderComment.visible_to_client == True,  # noqa: E712
        ).order_by(desc(OrderComment.created_at))
    )).scalars().all()

    # The shop's own status names; any status outside the six-step flow is placed by its group,
    # and a dropped job ("Not collected", "Cancelled") stops the progress bar instead of
    # pretending it's back at "Received".
    from backend.models import OrderStatus as _OS
    _sts = {s.key: s for s in (await db.execute(select(_OS).where(
        _OS.org_id == order.org_id,
        _OS.record_type == ("estimate" if getattr(order, "is_estimate", False) else "work_order")))).scalars().all()}
    _cur_st = _sts.get(order.status)
    _group = getattr(_cur_st, "status_group", None)
    stopped = _group == "dropped"
    try:
        stage_index = STAGE_FLOW.index(order.status)
    except ValueError:
        stage_index = {"new": 0, "pending": 2, "in_progress": 3, "ready": 4, "closed": 5}.get(_group or "", 0)

    def _label(key):
        return STAGE_LABEL.get(key) or (_sts[key].name if key in _sts else (key or "").replace("_", " ").capitalize())

    # Display currency for the public page: order snapshot → client pref → base.
    from backend.services import currency as _cur
    from backend.services import industry as _ind
    _ci = await _cur.resolve_for_client(db, order.org_id, order.client_id, order.currency_code)
    _fx = order.fx_rate or _ci["rate"]

    # 144ir — client-safe itemised lines for the estimate/DVI view (no cost data)
    from backend.models import OrderLine as _OL
    _lines = (await db.execute(select(_OL).where(_OL.order_id == order.id))).scalars().all()
    def _line_amt(l):
        base = (l.unit_price or 0) * (l.qty or 1)
        disc = base * (l.discount or 0) / 100 if (l.discount_type or "pct") == "pct" else (l.discount or 0)
        return q2((base - disc) * (_fx or 1))
    # Several items per job: each line says which item it's for; item by item, whole job last.
    from backend.services import multi_asset as _ma
    _inames = await _ma.item_names(db, order.org_id, [l.order_item_id for l in _lines])
    if _inames:
        _lines = _ma.sort_by_item(_lines, _inames, key=lambda l: l.order_item_id)
    # A kit set to print as one line is one row here too (kit_print is the only way customer
    # pages get lines); its amount is the sum of its real lines.
    from backend.services import kit_print as _kp
    def _amt(l):
        return q2(sum(_line_amt(m) for m in l.members)) if getattr(l, "is_kit", False) else _line_amt(l)
    lines_pub = [{"id": _row_id(l),
                  # E5 — what leaving this row out takes off the total (tax included), and its kit:
                  # a kit is approved or left out as a whole
                  "total": q2(sum(float(m.total or 0) for m in _row_members(l)) * (_fx or 1)),
                  "group": getattr(_row_members(l)[0], "bundle_group_id", None),
                  "label": l.description or (l.kind or l.line_type or "Item").title(),
                  "qty": l.qty or 1, "kind": l.kind or l.line_type,
                  "amount": _amt(l),
                  **({"includes": list(l.includes)} if getattr(l, "includes", None) else {}),
                  **({"item": (_inames.get(l.order_item_id) or (None,))[0]} if _inames else {})}
                 for l in _kp.customer_lines(_lines)]

    # 144ix — customer review capture: expose active feedback settings + whether this
    # order already has a submitted rating. Actual submit is token-gated below.
    feedback_block = None
    fset = (await db.execute(select(FeedbackSettings).where(
        FeedbackSettings.org_id == order.org_id))).scalar_one_or_none()
    if fset and fset.active:
        fb_existing = (await db.execute(select(Feedback).where(
            Feedback.order_id == order.id).order_by(Feedback.created_at.desc()))).scalars().first()
        feedback_block = {
            "system": fset.system,
            "heading": fset.heading,
            "subheading": fset.subheading,
            "question": fset.question,
            "clarify_question": fset.clarify_question,
            "bands": fset.bands or {},
            "google_review_url": getattr(fset, "google_review_url", None),
            "submitted": bool(fb_existing and fb_existing.submitted_at),
            "rating": fb_existing.rating if (fb_existing and fb_existing.submitted_at) else None,
        }

    # 144ix — shop branding + pickup details for the public page (white-label + logistics)
    _show_logo = getattr(org, "page_show_logo", True) or getattr(org, "oe_show_logo", True)
    brand_color = (getattr(org, "page_color", None) or getattr(org, "brand_primary_color", None)) if org else None
    shop_address = (loc.address if (loc and getattr(loc, "address", None)) else getattr(org, "company_address", None))
    shop_phone = (loc.phone if (loc and getattr(loc, "phone", None)) else getattr(org, "company_phone", None))

    # Forms → Customize → "Shows on: Customer portal" — only fields the shop ticked, never the rest.
    from backend.routers.forms import shown_values
    custom_fields = await shown_values(db, order.org_id, "show_in_portal", order=order, device=device, client=client)

    return {
        "lines": lines_pub,
        "custom_fields": custom_fields,
        "logo_data": (org.logo_data if (org and _show_logo) else None),
        "brand_color": brand_color,
        "website": getattr(org, "website", None) if org else None,
        "shop_address": shop_address,
        "shop_hours": (loc.working_hours if (loc and getattr(loc, "working_hours", None)) else None),
        "location_name": (loc.name if loc else None),
        "feedback": feedback_block,
        "order_number": order.order_number,
        "currency_code": _ci["code"],
        "currency_symbol": _ci["symbol"],
        "fx_rate": _fx,
        "total_converted": q2((order.total or 0) * _fx) if _fx else order.total,
        "status": order.status,
        "is_estimate": bool(getattr(order, "is_estimate", False)),
        "stage_label": _label(order.status),
        "stage_index": stage_index,
        "stopped": stopped,
        "stages": [{"key": s, "label": STAGE_LABEL[s]} for s in STAGE_FLOW],
        "shop_name": org.name if org else "Repair shop",
        "shop_phone": shop_phone,
        "client_name": client.name if client else None,
        "device": ((" ".join(p for p in [device.brand, device.model] if p) or None) if device else None),
        # 144hc — customer-facing asset noun (Vessel / Vehicle / Unit / Device…)
        "asset_label": _ind.asset_label(getattr(device, "template_key", None)) if device else None,
        "complaint": order.complaint,
        "total": order.total,
        # What the customer has paid and still owes, in their currency — so a settled job
        # says "Paid" instead of a bare "Total" the customer might think they still owe.
        **(await _paid_fields(db, order, _fx)),
        "created_at": order.created_at.isoformat() if order.created_at else None,
        # 144dr — only expose the scheduled date when the org opted in.
        "scheduled_for": (order.scheduled_for.isoformat() if order.scheduled_for else None) if getattr(org, "oe_show_scheduled", False) else None,
        "scheduled_to": (order.scheduled_to.isoformat() if getattr(order, "scheduled_to", None) else None) if getattr(org, "oe_show_scheduled", False) else None,
        "awaiting_approval": (awaiting := await _can_sign(db, order)),
        # E5 — estimate terms: valid until, which version, picking lines, the deposit at approval
        "valid_until": order.estimate_valid_until.isoformat() if (order.is_estimate and order.estimate_valid_until) else None,
        "expired": await _expired(db, order),
        "revision": _revision(order) if order.is_estimate else None,
        "can_choose_lines": bool(awaiting and order.is_estimate and len(lines_pub) > 1
                                 and getattr(org, "oe_estimate_line_choice", True)),
        "deposit": await _deposit_block(db, order, _fx),
        # Per-status messaging rule (services/messaging_rule): box hidden client-side, enforced
        # server-side. When it's closed for this order, the page offers "Start a new request".
        **(await _message_fields(db, order, org)),
        "allow_recommend": getattr(org, "oe_allow_recommend", True),
        "recommendations": await _public_recommendations(db, order),
        "checklist": await _public_checklist(db, order),
        "invoice": (lambda i: _public_invoice_block(i, _fx) if i else None)(await _order_invoice(db, order)),
        "has_signature": bool((await db.execute(select(DocumentSignature.id).where(
            DocumentSignature.order_id == order.id).limit(1))).first()),
        "timeline": [
            {"to": l.to_status, "label": _label(l.to_status),
             "at": l.changed_at.isoformat() if l.changed_at else None}
            for l in logs
        ],
        "updates": sorted(
            [
                {"type": f.item_type, "body": f.body,
                 "media_data": f.media_data if f.item_type in ("photo", "signature", "file") else None,
                 "media_name": f.media_name, "media_mime": getattr(f, "media_mime", None),
                 "at": f.created_at.isoformat() if f.created_at else None}
                for f in feed
            ] + [
                # 144ct — staff reply shown to the customer as a shop message
                {"type": "shop_message", "body": c.body, "media_data": None,
                 "media_name": None, "media_mime": None,
                 "at": c.created_at.isoformat() if c.created_at else None}
                for c in staff_msgs
            ],
            key=lambda u: u["at"] or "", reverse=True,
        ),
    }


@router.post("/track/{token}/respond")
async def respond(token: str, data: AcceptIn, request: Request, db: AsyncSession = Depends(get_db)):
    order = await _load(db, token)
    # E2 — lock the row so two clicks (or two tabs) can't both decide
    order = (await db.execute(select(Order).where(Order.id == order.id).with_for_update())).scalar_one()
    who = data.name or (
        (await db.execute(select(Client).where(Client.id == order.client_id))).scalar_one_or_none().name
        if order.client_id else "Customer"
    )
    converted_id = await _respond(db, order, data, who, request)
    await db.commit()
    return {"ok": True, "decision": data.decision, "converted_order_id": converted_id}


async def _respond(db: AsyncSession, order: "Order", data: AcceptIn, who: str, request: Request) -> Optional[str]:
    """The checks and the decision shared by the track page and the customer portal."""
    if await _already_decided(db, order):
        raise HTTPException(409, "You've already responded to this. Contact the shop to change your answer.")
    if data.decision not in ("accept", "decline"):
        raise HTTPException(400, "Invalid decision")
    if await _expired(db, order):
        raise HTTPException(410, f"This estimate expired on {order.estimate_valid_until:%d %b %Y}. "
                                 "Contact the shop for an up-to-date one.")
    if not await _can_sign(db, order):
        raise HTTPException(403, "Online approval is not available for this document right now")
    if data.decision == "accept" and not data.signature and await _signature_required(db, order):
        raise HTTPException(422, "A signature is required to accept this document")
    if data.decision == "accept" and data.skip_line_ids:
        if not await _line_choice(db, order):
            raise HTTPException(403, "This shop asks you to approve the whole estimate. Contact them to change it.")
        await _skip_lines(db, order, data.skip_line_ids, who)
    return await _decide_estimate(db, order, data.decision, data.signature, who, request, reason=data.reason)


async def _line_choice(db: AsyncSession, order: "Order") -> bool:
    """E5 — the shop lets customers untick estimate lines (Settings → Customer pages)."""
    org = (await db.execute(select(Organisation).where(Organisation.id == order.org_id))).scalar_one_or_none()
    return bool(org and getattr(org, "oe_estimate_line_choice", True))


async def _public_rows(db: AsyncSession, order: "Order") -> list:
    """The estimate's lines as the customer sees them (a kit printed as one line is one row)."""
    from backend.models import OrderLine as _OL
    from backend.services import kit_print as _kp
    from backend.services import multi_asset as _ma
    lines = (await db.execute(select(_OL).where(_OL.order_id == order.id))).scalars().all()
    names = await _ma.item_names(db, order.org_id, [ln.order_item_id for ln in lines])
    if names:
        lines = _ma.sort_by_item(lines, names, key=lambda ln: ln.order_item_id)
    return _kp.customer_lines(lines)


def _row_id(row) -> str:
    """A customer row's id is its first real line's id; the row stands for all of its lines."""
    return row.members[0].id if getattr(row, "is_kit", False) else row.id


def _row_members(row) -> list:
    return list(row.members) if getattr(row, "is_kit", False) else [row]


async def _skip_lines(db: AsyncSession, order: "Order", skip_ids: list[str], who: str) -> None:
    """E5 — line-by-line approval: the customer accepts the estimate without some of its lines.
    What was offered is kept as a revision (with what they left out); the lines they skipped come
    off the estimate before it becomes the job. Lines of one kit go together."""
    if not order.is_estimate:
        raise HTTPException(400, "Lines can only be left out of an estimate")
    from backend.models import OrderLine as _OL
    from backend.models import TimeEntry as _TE
    from backend.routers.orders import _recalc_order
    rows = await _public_rows(db, order)
    by_id = {_row_id(r): r for r in rows}
    skip = list(dict.fromkeys(skip_ids))
    if any(i not in by_id for i in skip):
        raise HTTPException(400, "That line isn't on this estimate. Reload the page and try again.")
    drop = {m.id for i in skip for m in _row_members(by_id[i])}
    kits = {m.bundle_group_id for r in rows for m in _row_members(r) if m.id in drop and m.bundle_group_id}
    if kits:     # a kit is all-or-nothing, even when it prints item by item
        drop |= {m.id for r in rows for m in _row_members(r) if m.bundle_group_id in kits}
    if all(m.id in drop for r in rows for m in _row_members(r)):
        raise HTTPException(400, "Keep at least one line, or decline the estimate instead.")
    skipped = [r.description for r in rows if any(m.id in drop for m in _row_members(r))]
    await snapshot_revision(db, order, decision="approved", skipped=skipped)   # what was offered
    from backend.services.invoice_money import release_line
    for lid in drop:
        await release_line(db, lid)
    await db.execute(_TE.__table__.update().where(_TE.order_line_id.in_(drop)).values(order_line_id=None))
    from backend.models import Recommendation as _R      # an approved extra whose line is left out
    await db.execute(_R.__table__.update().where(_R.order_id == order.id, _R.order_line_id.in_(drop)).values(
        order_line_id=None, status="declined", decline_reason="not_needed", decided_at=_dt.utcnow()))
    await db.execute(_OL.__table__.delete().where(_OL.order_id == order.id, _OL.id.in_(drop)))
    await db.flush()
    db.expire(order, ["lines"])
    await _recalc_order(db, order)
    if float(order.deposit_amount or 0) > float(order.total or 0):
        order.deposit_amount = q2(float(order.total or 0))   # a deposit never asks for more than the job
    db.add(EventFeedItem(org_id=order.org_id, entity_type="order", entity_id=order.id, item_type="status",
                         visibility="internal", body=f"{who or 'The customer'} left out: {', '.join(skipped)}"))
    order._skipped_lines = skipped     # read by the staff alert in _decide_estimate


async def snapshot_revision(db: AsyncSession, order: "Order", *, decision: Optional[str] = None,
                            skipped: Optional[list] = None, note: Optional[str] = None, actor=None):
    """E5 — keep the estimate as it stands now (lines, total, terms, the customer's answer) as history."""
    from backend.models import EstimateRevision
    from backend.models import OrderLine as _OL
    lines = (await db.execute(select(_OL).where(_OL.order_id == order.id))).scalars().all()
    if decision is None:
        last = (await db.execute(select(DocumentSignature.decision).where(
            DocumentSignature.order_id == order.id, DocumentSignature.doc_type == "estimate",
            DocumentSignature.revision == _revision(order)).order_by(DocumentSignature.signed_at.desc())
        )).scalars().first()
        decision = {"accept": "approved", "decline": "declined"}.get(last or "")
    rev = EstimateRevision(
        org_id=order.org_id, order_id=order.id, revision=_revision(order),
        lines=[{"description": ln.description, "qty": float(ln.qty or 0), "unit_price": float(ln.unit_price or 0),
                "total": float(ln.total or 0), "kind": ln.kind or ln.line_type} for ln in lines],
        total=float(order.total or 0), valid_until=order.estimate_valid_until,
        deposit_amount=float(order.deposit_amount or 0), decision=decision,
        decline_reason=order.estimate_decline_reason if decision == "declined" else None,
        skipped=list(skipped or []), note=(note or "")[:500] or None,
        created_by_id=getattr(actor, "id", None), created_by_name=getattr(actor, "name", None))
    db.add(rev)
    await db.flush()
    return rev


async def _paid_toward_deposit(db: AsyncSession, order: "Order") -> float:
    """Money that counts toward the deposit: for a deposit asked at an estimate's approval, what
    was paid since the approval (an earlier diagnostic fee doesn't count); otherwise all of it."""
    from sqlalchemy import func

    from backend.models import Payment
    from backend.services.order_money import net_paid
    since = getattr(order, "estimate_approved_at", None)
    if not since:
        return await net_paid(db, order.id)
    rows = dict((await db.execute(select(Payment.kind, func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.order_id == order.id, Payment.paid_at >= since).group_by(Payment.kind))).all())
    return q2(float(rows.get("payment", 0) or 0) + float(rows.get("prepayment", 0) or 0)
              - abs(float(rows.get("refund", 0) or 0)))


async def _deposit_block(db: AsyncSession, order: "Order", fx) -> Optional[dict]:
    """E5 — the deposit the shop asks for at approval: how much, what's paid, and whether the
    customer can pay it online now (once they've approved, while the job is open and before it's
    invoiced; after that the invoice is what they pay)."""
    amount = q2(float(order.deposit_amount or 0))
    if amount <= 0:
        return None
    from backend.models import OrderStatus as _OS
    st = (await db.execute(select(_OS).where(
        _OS.org_id == order.org_id, _OS.key == order.status,
        _OS.record_type == ("estimate" if order.is_estimate else "work_order")))).scalars().first()
    stopped = bool(order.closed_at or (st and (st.is_terminal or st.status_group in ("closed", "dropped"))))
    if not stopped:
        from backend.services.invoice_money import invoices_for_order
        stopped = bool(await invoices_for_order(db, order.id))
    paid = q2(min(max(0.0, await _paid_toward_deposit(db, order)), amount))
    due = q2(amount - paid)
    approved = (not order.is_estimate) or bool(order.estimate_approved_at)
    rate = fx or 1
    return {"amount": q2(amount * rate), "paid": q2(paid * rate), "due": q2(due * rate),
            "can_pay": bool(due > 0 and approved and not stopped)}


@router.post("/track/{token}/deposit", status_code=201)
async def track_deposit_link(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """E5 — the customer pays the deposit the shop asked for: a pay link for what's still due on it
    (the same link again while it still asks for that amount)."""
    from backend.services.ratelimit import aenforce as _rate, client_ip as _ip
    await _rate("track_deposit", f"{_ip(request)}:{token}", limit=10, window_sec=600)
    order = await _load(db, token)
    order = (await db.execute(select(Order).where(Order.id == order.id).with_for_update())).scalar_one()
    block = await _deposit_block(db, order, 1)
    if not block or not block["can_pay"]:
        raise HTTPException(409, "There's no deposit to pay on this right now.")
    from backend.config import settings
    from backend.models import PaymentIntent
    from backend.services import payments as pay
    existing = (await db.execute(select(PaymentIntent).where(
        PaymentIntent.org_id == order.org_id, PaymentIntent.order_id == order.id,
        PaymentIntent.status.in_(("created", "viewed", "pending"))
    ).order_by(PaymentIntent.created_at.desc()))).scalars().first()
    if existing and abs(float(existing.amount or 0) - block["due"]) < 0.005:
        intent = existing
    else:
        if existing:      # one live link per job: a stale one is closed at the provider first
            from backend.services.order_money import revoke_pay_link
            await revoke_pay_link(db, existing)
        try:
            intent = await pay.create_intent(db, org_id=order.org_id, order_id=order.id,
                                             amount=block["due"], account_id=None)
        except RuntimeError:
            raise HTTPException(502, "Online payment isn't available right now. Please contact the shop.")
        await db.commit()
    base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    rel = intent.checkout_url if (intent.provider != "simulated" and intent.checkout_url) else f"/pay/{intent.id}"
    url = (base + rel) if (base and rel.startswith("/")) else rel
    return {"intent_id": intent.id, "amount": float(intent.amount), "currency": intent.currency, "url": url}


@router.get("/track/{token}/certificate.pdf")
async def track_certificate(token: str, db: AsyncSession = Depends(get_db)):
    """144ig — token-gated download of the signed-approval certificate for this order's
    latest client signature (estimate or work order). No auth: the track token is the
    customer's capability to their own document."""
    from fastapi.responses import Response
    from backend.services.signcert import build_certificate
    order = await _load(db, token)
    sig = (await db.execute(select(DocumentSignature).where(
        DocumentSignature.order_id == order.id).order_by(DocumentSignature.signed_at.desc()))).scalars().first()
    if not sig:
        raise HTTPException(404, "No signed document for this order")
    org = (await db.execute(select(Organisation).where(Organisation.id == order.org_id))).scalar_one_or_none()
    pdf = build_certificate(sig, order, org)
    fname = f"approval-{order.order_number or order.id[:8]}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{fname}"'})


@router.get("/track/{token}/einvoice.xml")
async def track_einvoice(token: str, db: AsyncSession = Depends(get_db)):
    """144if — token-gated public download of the order's issued e-invoice (UBL 2.1 XML).
    No auth: the track token is the customer's capability to see their own document."""
    from fastapi.responses import Response
    order = await _load(db, token)
    inv = await _order_invoice(db, order)
    if not inv or not getattr(inv, "einvoice_xml", None):
        raise HTTPException(404, "No e-invoice available")
    fname = f"einvoice-{inv.einvoice_number or inv.invoice_number or 'document'}.xml"
    return Response(content=inv.einvoice_xml, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ════════════════════════════════════════════════════════════
#  Customer portal — durable per-client magic link (no auth).
#  /portal/{token} lets a customer see ALL their work orders &
#  estimates, drill into one, and approve/decline estimates.
# ════════════════════════════════════════════════════════════
async def _paid_fields(db: AsyncSession, order: "Order", fx) -> dict:
    """amount_paid / balance_due (customer currency) and paid_in_full for one job."""
    from backend.services.order_money import balance_due, net_paid
    total = float(order.total or 0)
    paid = max(0.0, await net_paid(db, order.id))
    due = await balance_due(db, order)          # net of payments, refunds and credit notes
    rate = fx or 1
    return {
        "amount_paid": q2(paid * rate),
        "balance_due": q2(due * rate),
        "paid_in_full": total > 0 and due <= 0,
    }


async def _client_by_portal(db: AsyncSession, token: str) -> Client:
    if not token:
        raise HTTPException(404, "Not found")
    from backend.services import portal_links
    client = await portal_links.client_for(db, token)
    if not client:
        raise HTTPException(404, "Portal not found")
    return client


def _order_card(o: "Order") -> dict:
    return {
        "id": o.id,
        "order_number": o.order_number,
        "is_estimate": bool(o.is_estimate),
        "status": o.status,
        "stage_label": STAGE_LABEL.get(o.status, o.status.replace("_", " ").title()),
        "total": o.total,
        "complaint": o.complaint,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "warranty_until": o.warranty_until.isoformat() if o.warranty_until else None,
        "scheduled_for": o.scheduled_for.isoformat() if o.scheduled_for else None,
    }


@router.get("/portal/{token}")
async def portal_home(token: str, db: AsyncSession = Depends(get_db)):
    client = await _client_by_portal(db, token)
    org = (await db.execute(select(Organisation).where(Organisation.id == client.org_id))).scalar_one_or_none()
    orders = (await db.execute(
        select(Order).where(Order.client_id == client.id).order_by(desc(Order.created_at))
    )).scalars().all()

    # What the job list shows beside each card: the item, the total in the customer's currency,
    # and whether the customer has something to do (approve, or choose recommended work).
    from backend.models import Recommendation as _R
    from backend.services import currency as _cur
    dev_ids = {o.device_id for o in orders if o.device_id}
    devices = {d.id: d for d in (await db.execute(select(Device).where(Device.id.in_(dev_ids)))).scalars().all()} if dev_ids else {}
    open_recs: dict = {}
    if orders and getattr(org, "oe_allow_recommend", True):
        for oid in (await db.execute(select(_R.order_id).where(
                _R.order_id.in_([o.id for o in orders]), _R.status == "sent"))).scalars().all():
            open_recs[oid] = open_recs.get(oid, 0) + 1
    ci = await _cur.resolve_for_client(db, client.org_id, client.id, None)
    # The shop's own status names, as the job page shows them.
    from backend.models import OrderStatus as _OS
    names = {(s.record_type, s.key): s.name for s in (await db.execute(
        select(_OS).where(_OS.org_id == client.org_id))).scalars().all()}

    async def card(o: "Order") -> dict:
        c = _order_card(o)
        c["stage_label"] = STAGE_LABEL.get(o.status) or names.get(
            ("estimate" if o.is_estimate else "work_order", o.status)) or c["stage_label"]
        d = devices.get(o.device_id)
        # Same currency rule as the job page: the order's own currency wins over the client's.
        oci = ci if not o.currency_code or o.currency_code == ci["code"] else \
            await _cur.resolve_for_client(db, client.org_id, client.id, o.currency_code)
        fx = o.fx_rate or oci["rate"] or 1
        c.update({
            "device": (" ".join(p for p in [d.brand, d.model] if p) or None) if d else None,
            "total_converted": q2((o.total or 0) * fx),
            "currency_symbol": oci["symbol"],
            "awaiting_approval": await _can_sign(db, o),
            **(await _paid_fields(db, o, fx)),
            "open_recommendations": open_recs.get(o.id, 0),
        })
        return c

    work_orders = [await card(o) for o in orders if not o.is_estimate]
    estimates = [await card(o) for o in orders if o.is_estimate]
    _show_logo = getattr(org, "page_show_logo", True) or getattr(org, "oe_show_logo", True)
    return {
        "shop_name": org.name if org else "Repair shop",
        "client_name": client.name,
        "logo_data": (org.logo_data if (org and _show_logo) else None),
        "brand_color": (getattr(org, "page_color", None) or getattr(org, "brand_primary_color", None)) if org else None,
        "currency_symbol": ci["symbol"],
        "work_orders": work_orders,
        "estimates": estimates,
        "counts": {
            "work_orders": len(work_orders),
            "estimates": len(estimates),
            "under_warranty": sum(1 for o in orders if o.warranty_until),
        },
    }


@router.get("/portal/{token}/order/{order_id}")
async def portal_order(token: str, order_id: str, db: AsyncSession = Depends(get_db)):
    """One job in the portal: the same payload as the track page, plus the portal's own fields.
    `public_token` lets the portal reach the per-job extras (rating, new request, signed PDF,
    e-invoice) — the portal holder is the job's own customer, who already gets that link."""
    client = await _client_by_portal(db, token)
    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.client_id == client.id)
    )).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    payload = await _track_payload(db, order)
    payload.update({
        "id": order.id,
        "public_token": order.public_token,
        "diagnosis": order.diagnosis,
        "warranty_until": order.warranty_until.isoformat() if order.warranty_until else None,
        "can_approve": payload["awaiting_approval"],
    })
    return payload


@router.post("/portal/{token}/order/{order_id}/respond")
async def portal_respond(token: str, order_id: str, data: AcceptIn, request: Request, db: AsyncSession = Depends(get_db)):
    client = await _client_by_portal(db, token)
    if data.decision not in ("accept", "decline"):
        raise HTTPException(400, "Invalid decision")
    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.client_id == client.id).with_for_update()
    )).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    converted_id = await _respond(db, order, data, data.name or client.name, request)
    await db.commit()
    return {"ok": True, "decision": data.decision, "converted_order_id": converted_id}


# ── Customer → shop messages (land in the work-order feed) — 144ba ──
class MessageIn(BaseModel):
    body: str
    name: Optional[str] = None


async def _can_sign(db: AsyncSession, order: "Order") -> bool:
    """144ih — may the customer accept/decline+sign this document now? Estimates use
    oe_allow_approval; work orders are opt-in (oe_wo_allow_approval). Each has an
    optional comma-separated status allow-list; empty = any status (Orderry parity)."""
    org = (await db.execute(select(Organisation).where(Organisation.id == order.org_id))).scalar_one_or_none()
    if not org or await _expired(db, order) or await _already_decided(db, order):
        return False
    if order.is_estimate:
        if not getattr(org, "oe_allow_approval", True):
            return False
        raw = getattr(org, "oe_approval_statuses", "") or ""
    else:
        if not getattr(org, "oe_wo_allow_approval", False):
            return False
        raw = getattr(org, "oe_wo_approval_statuses", "") or ""
    allowed = [s.strip() for s in raw.split(",") if s.strip()]
    if not allowed:
        # "any status" still means an open one: a closed or dropped job can't be approved again
        from backend.models import OrderStatus as _OS
        st = (await db.execute(select(_OS).where(
            _OS.org_id == order.org_id, _OS.key == order.status,
            _OS.record_type == ("estimate" if order.is_estimate else "work_order")))).scalars().first()
        return not (st and (st.is_terminal or st.status_group in ("closed", "dropped")))
    return order.status in allowed


async def _recommend_enabled(db: AsyncSession, org_id: str) -> bool:
    from backend.models import Organisation as _O
    org = (await db.execute(select(_O).where(_O.id == org_id))).scalar_one_or_none()
    return getattr(org, "oe_allow_recommend", True) if org else True


async def _public_checklist(db: AsyncSession, order: "Order") -> list[dict]:
    """Build 144dp — client-visible checklist fields only (with their answers).
    Lets the customer see what was inspected + the result on the track page."""
    import json as _json
    from backend.models import OrderChecklistItem
    rows = (await db.execute(select(OrderChecklistItem).where(
        OrderChecklistItem.order_id == order.id,
        OrderChecklistItem.client_visible == True,  # noqa: E712
        OrderChecklistItem.done == True).order_by(OrderChecklistItem.sort_order))).scalars().all()
    out = []
    for c in rows:
        try:
            v = _json.loads(c.value) if c.value else None
        except Exception:
            v = None
        out.append({"label": c.label, "field_kind": c.field_kind, "value": v})
    return out


async def _public_recommendations(db: AsyncSession, order: "Order") -> list[dict]:
    """Recommendations the customer should see: everything published (sent) or
    already decided. Drafts stay hidden. Public-safe fields only."""
    from backend.models import Recommendation as _R
    if not await _recommend_enabled(db, order.org_id):
        return []
    rows = (await db.execute(select(_R).where(
        _R.order_id == order.id, _R.status.in_(("sent", "approved", "declined")))
        .order_by(_R.created_at))).scalars().all()
    # Several items per job: say which item each one is for, so the customer decides per item.
    from backend.services.multi_asset import item_names
    names = await item_names(db, order.org_id, [r.order_item_id for r in rows])
    return [{
        "id": r.id, "title": r.title, "note": r.note,
        "qty": r.qty, "unit_price": r.unit_price,
        "line_total": q2((r.qty or 0) * (r.unit_price or 0)),
        "media_data": r.media_data, "status": r.status,
        "kind": r.kind, "urgency": r.urgency, "decline_reason": r.decline_reason,
        "decided_by_name": r.decided_by_name,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "item": (names[r.order_item_id][0] if r.order_item_id in names else None),
        "item_order": (names[r.order_item_id][1] if r.order_item_id in names else None),
    } for r in rows]


class RecDecideIn(BaseModel):
    approve_ids: list[str] = []
    decline_ids: list[str] = []          # older pages: "Decline all"
    later_ids: list[str] = []            # "Not now": saved for later, the shop can raise it again
    name: Optional[str] = None           # the name the customer typed when confirming


REMIND_AFTER_DAYS = 90


async def _decide_recommendations(db: AsyncSession, order: "Order", data: "RecDecideIn",
                                  request: Optional[Request] = None) -> dict:
    """The customer chooses which recommended items to add. Only what they tick is approved;
    anything they don't touch stays open (nothing is declined silently). "Not now" saves an
    item for later (declined + not_now + a reminder date). An item saved for later can still be
    added while the job is open. Approved items become order lines through the shared money
    seam; the rows are locked so a double tap can't add the same work twice."""
    if not await _recommend_enabled(db, order.org_id):
        raise HTTPException(403, "Recommendations are turned off for this shop")
    from datetime import timedelta as _td

    from backend.models import Recommendation as _R
    from backend.routers.recommendations import approve_recommendation
    approve = list(dict.fromkeys(data.approve_ids or []))
    later = list(dict.fromkeys((data.later_ids or []) + (data.decline_ids or [])))
    if set(approve) & set(later):
        raise HTTPException(400, "An item can't be both added and saved for later")
    ids = approve + later
    if not ids:
        raise HTTPException(400, "Nothing selected")
    name = (data.name or "").strip()[:200] or None
    rows = {r.id: r for r in (await db.execute(select(_R).where(
        _R.order_id == order.id, _R.org_id == order.org_id, _R.id.in_(ids))
        .with_for_update())).scalars().all()}
    approved = later_n = 0
    added = 0.0
    skipped: list[str] = []
    for rid in approve:
        r = rows.get(rid)
        # Open items, and items saved for later (the customer changed their mind) can be added.
        if not r or r.status not in ("sent", "declined"):
            skipped.append(rid)
            continue
        line = await approve_recommendation(db, r)
        r.decline_reason = None
        r.remind_after = None
        r.decided_by_name = name
        added += float(line.total or 0)
        approved += 1
    remind_on = await shop_today(db, order.org_id) + _td(days=REMIND_AFTER_DAYS) if later else None
    for rid in later:
        r = rows.get(rid)
        if not r or r.status != "sent":
            skipped.append(rid)
            continue
        r.status = "declined"
        r.decline_reason = "not_now"
        r.decided_at = now_utc()
        r.remind_after = remind_on
        r.decided_by_name = name
        later_n += 1
    decided = approved + later_n
    if decided:
        order.rec_unread = (order.rec_unread or 0) + decided
        # Add the chosen work to the job's total (a job priced at check-in may have no lines
        # yet, so recomputing from lines would wipe its quoted price).
        prev_total = float(order.total or 0)
        order.total = q2(prev_total + added)
        if added:
            # A draft invoice for the job follows the new total; a sent one keeps its own.
            from backend.services.invoice_money import sync_order_invoices
            await sync_order_invoices(db, order, prev_total)
        parts = []
        if approved:
            parts.append(f"added {approved} recommended item{'s' if approved != 1 else ''}")
        if later_n:
            parts.append(f"saved {later_n} for later")
        db.add(EventFeedItem(org_id=order.org_id, entity_type="order", entity_id=order.id,
                             item_type="status", visibility="internal",
                             body=f"{name or 'The customer'} {' and '.join(parts)}."))
        if approved:
            await customer_approved_work(db, order, name or "Customer")
        # Who confirmed and when is kept on each item (decided_by_name / decided_at); the signed
        # estimate/work-order record is left alone, so adding an extra never reads as "signed".
    return {"approved": approved, "saved_for_later": later_n, "declined": later_n, "skipped": skipped,
            "total": q2(float(order.total or 0))}


async def customer_approved_work(db: AsyncSession, order: "Order", who: str) -> None:
    """The customer said yes to recommended work (online, or by phone through staff). When the shop
    set a status for "customer accepts" on work orders, the job moves there through the normal
    status change, so status rules, notifications, webhooks and the job's own gates all apply."""
    if order.is_estimate:
        return
    from backend.models import OrderStatus as _OS
    cur = (await db.execute(select(_OS).where(
        _OS.org_id == order.org_id, _OS.key == order.status, _OS.record_type == "work_order"))).scalars().first()
    if cur and (cur.is_terminal or cur.status_group in ("closed", "dropped")):
        return      # a finished job stays where it is
    target = await _decision_target(db, order, "accept")
    if cur and target and (target.sort_order or 0) < (cur.sort_order or 0):
        return      # never backwards: a job already past that status stays where it is
    if target and target.key != order.status:
        await _move_for_customer(db, order, target, "Approved by customer", who)


async def _org(db: AsyncSession, org_id: str):
    return (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()


async def _message_fields(db: AsyncSession, order: "Order", org) -> dict:
    from backend.services.messaging_rule import messaging_state
    st = await messaging_state(db, order, org)
    return {"allow_messages": st["open"], "messages_until": st["until"],
            # closed for this order (not switched off shop-wide) → offer a new request instead
            "can_new_request": st["reason"] in ("status", "expired")}


async def _post_client_message(db: AsyncSession, order: "Order", body: str, who: str):
    from backend.services.messaging_rule import messaging_state
    st = await messaging_state(db, order, await _org(db, order.org_id))
    if st["reason"] == "off":
        raise HTTPException(403, "Messaging is turned off for this shop")
    if not st["open"]:
        raise HTTPException(403, "This order is closed — messaging is no longer available.")
    body = (body or "").strip()
    if not body:
        raise HTTPException(400, "Message is empty")
    if len(body) > 2000:
        body = body[:2000]
    db.add(EventFeedItem(
        org_id=order.org_id, entity_type="order", entity_id=order.id,
        item_type="client_message", body=body, author_name=who or "Customer", visibility="client"))
    order.client_msg_unread = (order.client_msg_unread or 0) + 1
    # G1 — tell the shop, once per burst: no new alert while the last one for this job is unread
    # and under 10 minutes old, so a chatty (or hostile) page can't flood staff phones.
    from datetime import timedelta as _td

    from backend.models import StaffNotification
    from backend.services.customer_messages import notify_staff_about_customer
    recent = (await db.execute(select(StaffNotification.id).where(
        StaffNotification.org_id == order.org_id, StaffNotification.kind == "client.message",
        StaffNotification.deep_link == f"/tech#/order/{order.id}", StaffNotification.read_at.is_(None),
        StaffNotification.created_at >= _dt.utcnow() - _td(minutes=10)).limit(1))).first()
    if not recent:
        name = "".join(ch for ch in (who or "the customer") if ch.isprintable())[:60] or "the customer"
        await notify_staff_about_customer(db, order, kind="client.message",
                                          title=f"Message from {name} · {order.order_number}",
                                          body=body[:300])


@router.post("/track/{token}/message")
async def track_message(token: str, data: MessageIn, request: Request, db: AsyncSession = Depends(get_db)):
    from backend.services.ratelimit import aenforce as _rate, client_ip as _ip
    await _rate("track_msg", f"{_ip(request)}:{token}", limit=10, window_sec=600)
    order = await _load(db, token)
    who = data.name or (
        (await db.execute(select(Client).where(Client.id == order.client_id))).scalar_one_or_none().name
        if order.client_id else "Customer")
    await _post_client_message(db, order, data.body, who)
    await db.commit()
    return {"ok": True}


class NewRequestIn(BaseModel):
    body: str


@router.post("/track/{token}/new-request", status_code=201)
async def track_new_request(token: str, data: NewRequestIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Messaging closed on a finished order → the customer files a new request instead. It
    lands as an inquiry linked to the same customer and naming the same device, so staff can
    turn it into a work order in one click."""
    from datetime import timedelta as _td

    from backend.models import Lead
    from backend.routers.feed import log_system_event
    from backend.services.messaging_rule import messaging_state
    from backend.services.ratelimit import client_ip as _ip
    from backend.services.ratelimit import aenforce as _rate
    await _rate("track_new_req", f"{_ip(request)}:{token}", limit=3, window_sec=3600)
    order = await _load(db, token)
    st = await messaging_state(db, order, await _org(db, order.org_id))
    if st["reason"] not in ("status", "expired"):
        raise HTTPException(409, "Send a message on this order instead")
    body = (data.body or "").strip()[:2000]
    if not body:
        raise HTTPException(400, "Tell the shop what you need")
    client = (await db.execute(select(Client).where(Client.id == order.client_id))).scalar_one_or_none() if order.client_id else None
    device = (await db.execute(select(Device).where(Device.id == order.device_id))).scalar_one_or_none() if order.device_id else None
    dev = " ".join(p for p in [getattr(device, "brand", None), getattr(device, "model", None)] if p)
    ref = f"Follow-up to {order.order_number or 'a previous order'}" + (f" ({dev})" if dev else "")
    lead = Lead(org_id=order.org_id, location_id=order.location_id,
                name=(client.name if client else "Customer")[:200],
                phone=(client.phone if client else None), email=(client.email if client else None),
                client_id=client.id if client else None, source="web",
                inquiry=f"{ref}: {body}"[:2000], sla_due_at=now_utc() + _td(minutes=60))
    db.add(lead)
    await db.flush()
    await log_system_event(db, order.org_id, "lead", lead.id, f"New request from the track page — {ref}")
    await db.commit()
    return {"ok": True}


# ── 144ix — customer review capture on the track page (no auth; track token gated) ──
class TrackFeedbackIn(BaseModel):
    rating: int
    comment: Optional[str] = None


@router.post("/track/{token}/feedback")
async def track_submit_feedback(token: str, data: TrackFeedbackIn, db: AsyncSession = Depends(get_db)):
    """Submit a rating for this order straight from the track page. Finds the
    order's feedback row (created by the status trigger) or creates one on the fly
    so the review ask works even without a configured trigger. Returns the band
    message + the shop's public-review link for high raters."""
    order = await _load(db, token)
    fset = (await db.execute(select(FeedbackSettings).where(
        FeedbackSettings.org_id == order.org_id))).scalar_one_or_none()
    if not fset or not fset.active:
        raise HTTPException(403, "Feedback is not enabled for this shop")
    fb = (await db.execute(select(Feedback).where(
        Feedback.order_id == order.id).order_by(Feedback.created_at.desc()))).scalars().first()
    if fb and fb.submitted_at:
        raise HTTPException(409, "Feedback already submitted")
    if not fb:
        fb = Feedback(org_id=order.org_id, order_id=order.id,
                      client_id=order.client_id, system=fset.system)
        db.add(fb)
    fb.system = fset.system
    fb.rating = data.rating
    fb.comment = (data.comment or None)
    fb.submitted_at = now_utc()
    from backend.routers.feed import log_system_event
    await log_system_event(db, order.org_id, "order", order.id,
                           f"Customer left feedback: {data.rating}{' — ' + data.comment if data.comment else ''}")
    await db.commit()
    from backend.routers.feedback import _band_message
    return {"ok": True,
            "message": _band_message(fset.system, data.rating, (fset.bands or {})),
            "google_review_url": getattr(fset, "google_review_url", None)}


@router.post("/portal/{token}/order/{order_id}/message")
async def portal_message(token: str, order_id: str, data: MessageIn, request: Request,
                         db: AsyncSession = Depends(get_db)):
    from backend.services.ratelimit import aenforce as _rate, client_ip as _ip
    await _rate("track_msg", f"{_ip(request)}:{token}", limit=10, window_sec=600)
    client = await _client_by_portal(db, token)
    order = (await db.execute(select(Order).where(Order.id == order_id, Order.client_id == client.id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _post_client_message(db, order, data.body, data.name or client.name)
    await db.commit()
    return {"ok": True}


@router.post("/track/{token}/recommendations/decide")
async def track_decide_recs(token: str, data: RecDecideIn, request: Request, db: AsyncSession = Depends(get_db)):
    order = await _load(db, token)
    # lock the job: the decision may move its status, which must not race a second tap
    order = (await db.execute(select(Order).where(Order.id == order.id).with_for_update())).scalar_one()
    result = await _decide_recommendations(db, order, data, request)
    await db.commit()
    return {"ok": True, **result}


@router.post("/portal/{token}/order/{order_id}/recommendations/decide")
async def portal_decide_recs(token: str, order_id: str, data: RecDecideIn, request: Request,
                             db: AsyncSession = Depends(get_db)):
    client = await _client_by_portal(db, token)
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.client_id == client.id).with_for_update())).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    result = await _decide_recommendations(db, order, data, request)
    await db.commit()
    return {"ok": True, **result}


# ════════════════════════════════════════════════════════════
#  Public online appointment (Orderry "Public pages → Online appointment")
#  No auth. A client opens /book/{slug}, picks a service + slot,
#  enters contact details, and the appointment lands in the schedule.
# ════════════════════════════════════════════════════════════
from datetime import datetime, timedelta, time as dt_time
from backend.models import Organisation as _Org, Service, Employee, Appointment, Client, Order
from backend.services.money import q2


async def _org_by_slug(db: AsyncSession, slug: str) -> _Org:
    # slug matches the org subdomain, else the raw org id (fallback)
    org = (await db.execute(select(_Org).where(_Org.subdomain == slug))).scalar_one_or_none()
    if not org:
        org = (await db.execute(select(_Org).where(_Org.id == slug))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Appointment page not found")
    return org


# ── Build 144bo: availability engine helpers ──────────────────────────────
_WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_hhmm(v, default_min):
    """'HH:MM' → minutes past midnight; falls back to default on junk."""
    try:
        h, m = str(v).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return default_min


def _day_window(location, day):
    """Open window for a weekday as (open_min, close_min), or None when the
    branch is closed. Reads Location.working_hours ({mon:{open,close,closed}}).
    A location with no configured hours falls back to 09:00-18:00 so existing
    orgs keep working until they set hours."""
    wh = getattr(location, "working_hours", None) if location else None
    if not wh:
        return (540, 1080)  # 09:00-18:00 default
    d = wh.get(_WEEKDAY_KEYS[day.weekday()]) or {}
    if d.get("closed"):
        return None
    o = _parse_hhmm(d.get("open"), 540)
    c = _parse_hhmm(d.get("close"), 1080)
    return (o, c) if c > o else None


def _local_now(tzname):
    """Build 144br: 'now' as naive local wall-clock in the location's timezone.
    Appointments are stored as naive local time and working hours are local, so all
    lead-time / window / past-slot comparisons must use the same clock — not UTC.
    Falls back to UTC on an unknown tz."""
    from datetime import timezone as _tz
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tzname or "UTC")).replace(tzinfo=None)
    except Exception:
        return datetime.now(_tz.utc).replace(tzinfo=None)


async def _resolve_location(db, org, location_id):
    q = select(Location).where(Location.org_id == org.id, Location.active == True)  # noqa: E712
    if location_id:
        q = q.where(Location.id == location_id)
    return (await db.execute(q)).scalars().first()


async def _busy_windows(db, org, location, day, employee_id):
    """(start,end) datetimes already taken on `day` — appointments, and optionally
    scheduled work orders when appointment_consider_orders is on. Scoped to the
    location and, if given, one employee."""
    day_start = datetime.combine(day, dt_time(0, 0))
    day_end = day_start + timedelta(days=1)
    q = select(Appointment).where(
        Appointment.org_id == org.id, Appointment.status == "booked",
        Appointment.scheduled_for >= day_start, Appointment.scheduled_for < day_end)
    if location:
        q = q.where(Appointment.location_id == location.id)
    if employee_id:
        q = q.where(Appointment.employee_id == employee_id)
    rows = (await db.execute(q)).scalars().all()
    busy = [(b.scheduled_for, b.scheduled_for + timedelta(minutes=b.duration_min or 60)) for b in rows]
    if org.appointment_consider_orders:
        oq = select(Order).where(
            Order.org_id == org.id, Order.scheduled_for.isnot(None),
            Order.scheduled_for >= day_start, Order.scheduled_for < day_end)
        if location:
            oq = oq.where(Order.location_id == location.id)
        for o in (await db.execute(oq)).scalars().all():
            busy.append((o.scheduled_for, o.scheduled_for + timedelta(minutes=60)))
    return busy


def _slot_is_free(cur, slot_end, busy, allow_multiple):
    """When multiple appointments per slot are allowed the window is never 'full'
    (capacity is unbounded); otherwise any overlap makes it busy."""
    if allow_multiple:
        return True
    return not any(cur < be and bs < slot_end for bs, be in busy)


def _day_slots(org, location, day, busy, dur, now):
    """List of 'HH:MM' start times bookable on `day` for a job of `dur` mins."""
    win = _day_window(location, day)
    if win is None:
        return None  # closed
    open_min, close_min = win
    step = org.appointment_time_slot or 60
    lead = timedelta(minutes=org.appointment_lead_min or 0)
    earliest = now + lead
    out = []
    cur = datetime.combine(day, dt_time(0, 0)) + timedelta(minutes=open_min)
    win_end = datetime.combine(day, dt_time(0, 0)) + timedelta(minutes=close_min)
    while cur + timedelta(minutes=dur) <= win_end:
        slot_end = cur + timedelta(minutes=dur)
        if cur >= earliest and _slot_is_free(cur, slot_end, busy, org.appointment_allow_multiple):
            out.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=step)
    return out


# ── Back-compat (144dx): old /api/public/booking/* → /api/public/appointment/* ──
# The customer-facing page URL (/book/{slug}) is unchanged by the rename; this alias
# only covers direct calls to the pre-144dx API path (stale cached bundles, any
# third-party integration). 308 preserves the method and body for the POST flow.
@router.api_route("/booking/{rest:path}", methods=["GET", "POST"], include_in_schema=False)
async def _booking_api_compat(rest: str, request: Request):
    q = ("?" + request.url.query) if request.url.query else ""
    return RedirectResponse(url=f"/api/public/appointment/{rest}{q}", status_code=308)


@router.get("/appointment/{slug}")
async def appointment_config(slug: str, response: Response, db: AsyncSession = Depends(get_db)):
    """Public appointment page bootstrap: branding + bookable services + staff."""
    org = await _org_by_slug(db, slug)
    if not org.appointment_enabled:
        raise HTTPException(404, "Online appointment is disabled")
    # Public, anonymous, keyed only by the shop slug. Branding/services change
    # rarely, so a browser/CDN may serve a ≤60s-stale copy and skip 4 DB queries.
    response.headers["Cache-Control"] = "public, max-age=60"
    # 144ee — only services the org marked bookable appear on the public page.
    services = (await db.execute(select(Service).where(
        Service.org_id == org.id, Service.active == True,
        Service.bookable == True))).scalars().all()  # noqa: E712
    from backend.models import Category as _Cat
    _svc_cats = {c.id: c.name for c in (await db.execute(select(_Cat).where(
        _Cat.org_id == org.id, _Cat.entity_type == "service"))).scalars().all()}
    staff = (await db.execute(select(Employee).where(
        Employee.org_id == org.id, Employee.active == True))).scalars().all()  # noqa: E712
    locs = (await db.execute(select(Location).where(
        Location.org_id == org.id, Location.active == True))).scalars().all()  # noqa: E712
    return {
        "shop_name": org.name,
        "currency": getattr(org, "currency", None) or "$",
        # The item the customer brings, in the shop's industry wording, and the ID to ask for.
        **_booking_asset(org),
        "logo_data": org.logo_data if org.page_show_logo else None,
        "color": org.page_color, "radius": org.page_radius,
        "show_language": org.page_show_language,
        "allow_multiple": org.appointment_allow_multiple,
        "time_slot": org.appointment_time_slot,
        "lead_min": org.appointment_lead_min,
        "window_days": org.appointment_window_days,
        "theme": org.appointment_theme,
        "show_employee_step": getattr(org, "book_show_employee_step", True),  # 144dr
        # 144ek — employee_ids lets the wizard's staff step filter to those who can
        # perform the picked service (server also enforces it on submit).
        # ponytail: required_resource_types is stored + returned but not yet gated in
        # the slot engine (no resource-availability model). upgrade: add resource
        # appointments to _day_slots when shops actually book by resource.
        "services": [{"id": s.id, "name": s.name, "price": s.price,
                      "duration_min": s.duration_min or org.appointment_time_slot,
                      "category_id": s.category_id,
                      "employee_ids": getattr(s, "allowed_employee_ids", None) or [],
                      "category": (_svc_cats.get(s.category_id) if s.category_id else None) or s.category}
                     for s in services],
        "staff": [{"id": e.id, "name": _public_name(e, getattr(org, "public_employee_name", None))}
                  for e in staff],
        "locations": [{"id": l.id, "name": l.name,
                       "address": l.address, "phone": l.phone,
                       "working_hours": l.working_hours or {}} for l in locs],
    }


# Booking asks for the one ID a customer can find on their item: VIN for vehicles, HIN for
# boats, a serial number for everything else.
_BOOKING_ID = {
    "vin": ("VIN", "17 characters. On the dashboard by the windshield, or the driver's door frame."),
    "hin": ("HIN (hull ID)", "12 characters. Usually on the back of the hull, top right of the transom."),
    "serial": ("Serial number", "Usually on a label underneath or on the back, or in the settings."),
}


def _booking_id_kind(org) -> str:
    from backend.services import industry as _ind
    primary = (_ind.identifier_rule(_ind.normalize_template_key(getattr(org, "industry_template", None))) or {}).get("primary")
    return primary if primary in ("vin", "hin") else "serial"


def _booking_asset(org) -> dict:
    from backend.services import industry as _ind
    d = _ind.describe(getattr(org, "industry_template", None))
    kind = _booking_id_kind(org)
    label, hint = _BOOKING_ID[kind]
    return {"asset_label": d.get("asset_label") or "Item", "identifier": {"kind": kind, "label": label, "hint": hint}}


def _booking_id_note(org, raw: Optional[str]) -> Optional[str]:
    """'VIN: 1HGCM82633A004352' for the appointment note, tidied the same way assets are."""
    from backend.services.identifiers import normalize
    kind = _booking_id_kind(org)
    v = normalize(kind, (raw or "")[:40])
    return f"{_BOOKING_ID[kind][0].split(' (')[0]}: {v}" if v else None


async def _service_duration(db, org, service_id):
    dur = org.appointment_time_slot or 60
    if service_id:
        svc = (await db.execute(select(Service).where(Service.id == service_id))).scalar_one_or_none()
        if svc and svc.duration_min:
            dur = svc.duration_min
    return dur


@router.get("/appointment/{slug}/slots")
async def appointment_slots(slug: str, date: str, response: Response, employee_id: Optional[str] = None,
                        location_id: Optional[str] = None, service_id: Optional[str] = None,
                        db: AsyncSession = Depends(get_db)):
    """Open start times for a day, computed by the availability engine:
    the location's working hours for that weekday, stepped by the org slot size,
    minus taken windows (appointments + optionally scheduled orders), honouring the
    appointment lead time and capacity (single vs. double-appointment)."""
    org = await _org_by_slug(db, slug)
    if not org.appointment_enabled:
        raise HTTPException(404, "Online appointment is disabled")
    # Availability reflects the WHOLE shop's appointments — any appointment anywhere
    # mutates it — so it must always be live, never served stale to other
    # visitors. Cheap indexed read after the 144dh indexes. No shared cache.
    response.headers["Cache-Control"] = "no-store"
    try:
        day = datetime.fromisoformat(date).date()
    except ValueError:
        raise HTTPException(400, "Bad date")

    location = await _resolve_location(db, org, location_id)
    now = _local_now(location.timezone if location else "UTC")
    window_days = org.appointment_window_days or 30
    if day < now.date():
        return {"date": date, "step": org.appointment_time_slot or 60, "slots": [], "reason": "in the past"}
    if day > (now.date() + timedelta(days=window_days)):
        return {"date": date, "step": org.appointment_time_slot or 60, "slots": [],
                "reason": f"beyond the {window_days}-day appointment window"}

    dur = await _service_duration(db, org, service_id)
    busy = await _busy_windows(db, org, location, day, employee_id)
    slots = _day_slots(org, location, day, busy, dur, now)
    if slots is None:
        return {"date": date, "step": org.appointment_time_slot or 60, "slots": [], "reason": "closed"}
    return {"date": date, "step": org.appointment_time_slot or 60, "slots": slots,
            "location_id": location.id if location else None}


@router.get("/appointment/{slug}/days")
async def appointment_days(slug: str, response: Response, start: Optional[str] = None, days: int = 14,
                       location_id: Optional[str] = None, service_id: Optional[str] = None,
                       employee_id: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """Which dates in a range are bookable — powers the client's calendar so
    they can jump to tomorrow, next week or next month without doomscrolling.
    Returns one entry per day with open=true/false and the free-slot count."""
    org = await _org_by_slug(db, slug)
    if not org.appointment_enabled:
        raise HTTPException(404, "Online appointment is disabled")
    # Same rationale as /slots: the calendar reflects the whole shop's live
    # appointments, so it is never cached/shared. Indexed reads keep it cheap.
    response.headers["Cache-Control"] = "no-store"
    location = await _resolve_location(db, org, location_id)
    now = _local_now(location.timezone if location else "UTC")
    try:
        first = datetime.fromisoformat(start).date() if start else now.date()
    except ValueError:
        raise HTTPException(400, "Bad start date")
    window_days = org.appointment_window_days or 30
    horizon = now.date() + timedelta(days=window_days)
    days = max(1, min(days, 62))
    dur = await _service_duration(db, org, service_id)

    out = []
    for i in range(days):
        d = first + timedelta(days=i)
        if d < now.date() or d > horizon:
            out.append({"date": d.isoformat(), "open": False, "free": 0})
            continue
        busy = await _busy_windows(db, org, location, d, employee_id)
        s = _day_slots(org, location, d, busy, dur, now)
        out.append({"date": d.isoformat(), "open": bool(s), "free": len(s or [])})
    return {"days": out, "window_days": window_days,
            "location_id": location.id if location else None}


class PublicAppointmentIn(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    service_id: Optional[str] = None
    employee_id: Optional[str] = None
    location_id: Optional[str] = None
    date: str          # YYYY-MM-DD
    time: str          # HH:MM
    note: Optional[str] = None
    identifier: Optional[str] = None   # VIN / HIN / serial, per the shop's industry


@router.post("/appointment/{slug}", status_code=201)
async def create_public_appointment(slug: str, data: PublicAppointmentIn, request: Request,
                                db: AsyncSession = Depends(get_db)):
    from backend.services.ratelimit import aenforce as _rate, client_ip as _ip
    await _rate("book", f"{_ip(request)}:{slug}", limit=8, window_sec=300)   # Build 144bz: anti-spam
    org = await _org_by_slug(db, slug)
    if not org.appointment_enabled:
        raise HTTPException(404, "Online appointment is disabled")
    if not data.name.strip():
        raise HTTPException(400, "Name is required")
    try:
        start = datetime.fromisoformat(f"{data.date}T{data.time}")
    except ValueError:
        raise HTTPException(400, "Bad date/time")

    # the location taking the appointment + its working hours for that weekday
    loc = await _resolve_location(db, org, data.location_id)
    now = _local_now(loc.timezone if loc else "UTC")
    # lead time + appointment window (server re-validates; never trust the client)
    if start < now + timedelta(minutes=org.appointment_lead_min or 0):
        raise HTTPException(409, "That time is too soon to book — please pick a later slot")
    if start.date() > now.date() + timedelta(days=org.appointment_window_days or 30):
        raise HTTPException(409, "That date is beyond the appointment window")

    # resolve duration from the chosen service, else the org slot size
    dur = await _service_duration(db, org, data.service_id)
    end = start + timedelta(minutes=dur)

    win = _day_window(loc, start.date())
    if win is None:
        raise HTTPException(409, "The shop is closed that day — please pick another")
    open_min, close_min = win
    start_min = start.hour * 60 + start.minute
    if start_min < open_min or (start_min + dur) > close_min:
        raise HTTPException(409, "That time is outside opening hours — please pick another")

    # pick an employee: the requested one, else the first active staff.
    # 144dr — when the org hides the employee step, leave the appointment UNASSIGNED
    # (a request to distribute later) instead of forcing a default assignee.
    emp_id = data.employee_id
    if not emp_id and getattr(org, "book_show_employee_step", True):
        emp = (await db.execute(select(Employee).where(
            Employee.org_id == org.id, Employee.active == True))).scalars().first()  # noqa: E712
        emp_id = emp.id if emp else None
        if not emp_id:
            raise HTTPException(409, "No staff available to take the appointment")

    # 144ek — only staff allowed to perform this service may take its appointment.
    if emp_id and data.service_id:
        svc = (await db.execute(select(Service).where(Service.id == data.service_id))).scalar_one_or_none()
        allowed = (getattr(svc, "allowed_employee_ids", None) or []) if svc else []
        if allowed and emp_id not in allowed:
            raise HTTPException(409, "That employee can't perform this service — please pick another")

    # an employee is never double-booked, even when the org allows multiple
    # concurrent appointments across different staff/resources. (Unassigned
    # appointments are only bounded by location capacity, checked below.)
    if emp_id:
        others = (await db.execute(select(Appointment).where(
            Appointment.org_id == org.id, Appointment.employee_id == emp_id,
            Appointment.status == "booked"))).scalars().all()
        for o in others:
            o_end = o.scheduled_for + timedelta(minutes=o.duration_min or 60)
            if start < o_end and o.scheduled_for < end:
                raise HTTPException(409, "That time slot was just taken — please pick another")

    # location-level capacity: when double-appointment is off, the location itself
    # can only hold one appointment per overlapping window
    if not org.appointment_allow_multiple:
        loc_busy = await _busy_windows(db, org, loc, start.date(), None)
        for bs, be in loc_busy:
            if start < be and bs < end:
                raise HTTPException(409, "That time slot was just taken — please pick another")

    # link to an existing client by phone, else record the name on the appointment
    client_id = None
    if data.phone:
        existing = (await db.execute(select(Client).where(
            Client.org_id == org.id, Client.phone == data.phone))).scalars().first()
        if existing:
            client_id = existing.id

    b = Appointment(org_id=org.id, location_id=loc.id if loc else None,
                client_id=client_id, client_name=data.name.strip(),
                service_id=data.service_id, employee_id=emp_id,
                scheduled_for=start, duration_min=dur,
                note=" | ".join(p for p in [data.note or None, _booking_id_note(org, data.identifier),
                                            f"contact: {data.phone or data.email or ''}".rstrip()] if p))
    db.add(b)
    from backend.routers.feed import log_system_event
    from sqlalchemy.exc import IntegrityError
    try:
        await db.flush()
        await log_system_event(db, org.id, "appointment", b.id,
                               f"Online appointment from {data.name} at {start.strftime('%d %b %H:%M')}")
        await db.commit()
    except IntegrityError:   # Build 144bz: lost the race for this exact slot
        await db.rollback()
        raise HTTPException(409, "That time slot was just taken — please pick another")
    # Build 144bs: fire appointment confirmation rules (module="appointment", event="created")
    try:
        from backend.services import notification_rules as _nr
        await _nr.evaluate_appointment(db, org_id=org.id, appointment_id=b.id, event="created")
    except Exception:
        pass
    return {"ok": True, "appointment_id": b.id, "when": start.strftime("%d %b %Y, %H:%M"),
            "shop_name": org.name}


# ── 144iy — Appointment 2.0 helpers ─────────────────────────────────────────────
class AppointmentRequestIn(BaseModel):
    """No-dead-end fallback: when nothing fits (no slots, no matching service),
    the visitor leaves a request instead of bouncing. Lands as a web Lead in the
    Inquiries board with the shop's SLA clock running."""
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    issue: Optional[str] = None
    service_name: Optional[str] = None
    identifier: Optional[str] = None


@router.post("/appointment/{slug}/request", status_code=201)
async def appointment_request(slug: str, data: AppointmentRequestIn, request: Request,
                          db: AsyncSession = Depends(get_db)):
    from backend.services.ratelimit import aenforce as _rate, client_ip as _ip
    await _rate("book_req", f"{_ip(request)}:{slug}", limit=6, window_sec=300)
    org = await _org_by_slug(db, slug)
    if not org.appointment_enabled:
        raise HTTPException(404, "Online appointment is disabled")
    if not data.name.strip():
        raise HTTPException(400, "Name is required")
    if not (data.phone or data.email):
        raise HTTPException(400, "A phone or email is required so the shop can reach you")
    from backend.models import Lead
    from datetime import timedelta as _td
    inquiry = " · ".join(p for p in [
        (f"Service: {data.service_name.strip()}" if data.service_name else None),
        (data.issue or "").strip() or None, _booking_id_note(org, data.identifier)] if p) or "Online appointment request"
    lead = Lead(org_id=org.id, name=data.name.strip()[:200],
                phone=(data.phone or "")[:40] or None, email=(data.email or "")[:200] or None,
                source="web", inquiry=inquiry[:2000],
                sla_due_at=now_utc() + _td(minutes=60))
    db.add(lead)
    await db.flush()
    from backend.routers.feed import log_system_event
    await log_system_event(db, org.id, "lead", lead.id,
                           f"Web appointment request from {lead.name}")
    await db.commit()
    return {"ok": True, "lead_id": lead.id}


class HelloIn(BaseModel):
    phone: str


@router.post("/appointment/{slug}/hello")
async def appointment_hello(slug: str, data: HelloIn, request: Request,
                        db: AsyncSession = Depends(get_db)):
    """Returning-customer recognition: last-10-digit phone match within this org.
    Returns only a first name (never full contact data) and is rate-limited to
    keep it from being an enumeration oracle."""
    from backend.core.webhook_security import client_ip, enforce_rate_limit, lookup_limiter
    enforce_rate_limit(lookup_limiter, f"hello:{client_ip(request)}")
    org = await _org_by_slug(db, slug)
    from backend.services.call_contacts import digits, find_client   # same rule as calls/chats
    if len(digits(data.phone)) < 7:
        return {"known": False}
    c = await find_client(db, org.id, data.phone)
    if c:
        first = (c.name or "").strip().split(" ")[0]
        return {"known": True, "name": first or None}
    return {"known": False}


# ── Public payment page support (no auth; the link itself is the credential) ──
@router.get("/pay/{intent_id}")
async def public_pay_info(intent_id: str, db: AsyncSession = Depends(get_db)):
    """Minimal info for the hosted pay page. The link holder can see the amount
    and pay; they cannot enumerate other orders."""
    from backend.models import Invoice, Order, PaymentIntent
    i = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == intent_id))).scalar_one_or_none()
    if not i:
        raise HTTPException(404, "Payment not found")
    order_no = None
    if i.order_id:
        o = (await db.execute(select(Order).where(Order.id == i.order_id))).scalar_one_or_none()
        order_no = o.order_number if o else None
    # Build 144dv — when the intent bills an invoice, show its line items exactly as
    # the invoice's Order-display mode resolves them, so the client sees the same
    # breakdown (per-line, per-ticket, or a single line) as the printed document.
    lines, display_mode, invoice_number = None, None, None
    if getattr(i, "invoice_id", None):
        inv = (await db.execute(select(Invoice).where(Invoice.id == i.invoice_id))).scalar_one_or_none()
        if inv:
            from backend.services.invoicing import display_lines
            lines = await display_lines(db, inv)
            display_mode = inv.display_mode
            invoice_number = inv.invoice_number
    return {"intent_id": i.id, "amount": i.amount, "currency": i.currency,
            "status": i.status, "order_number": order_no,
            "invoice_number": invoice_number, "display_mode": display_mode, "lines": lines,
            "provider": i.provider, "checkout_url": i.checkout_url}


# ══ Build 109: public status lookup + embeddable widget ══════════════════════
class LookupIn(BaseModel):
    order_number: str
    phone: str
    shop: Optional[str] = None   # org subdomain/slug (or id) — scopes the lookup to one tenant


@router.post("/lookup")
async def lookup_order(data: LookupIn, request: Request, db: AsyncSession = Depends(get_db)):
    from backend.core.webhook_security import client_ip, enforce_rate_limit, lookup_limiter
    # Build 110: throttle per caller AND per order number, so neither a single IP nor a
    # distributed guess against one order can brute-force the phone pairing.
    enforce_rate_limit(lookup_limiter, f"ip:{client_ip(request)}")
    enforce_rate_limit(lookup_limiter, f"num:{data.order_number.strip().lstrip('#')}")
    """Find an order from its number plus the customer's phone — the two things a
    customer actually has to hand. Both must match: the order number alone is guessable,
    so pairing it with the phone on file is what keeps this from leaking one customer's
    repair to another. Returns the public token, which the widget then renders through
    the existing /track view — no new rendering path, no new way to get status wrong.
    """
    num = data.order_number.strip().lstrip("#")
    phone = "".join(ch for ch in data.phone if ch.isdigit())[-10:]  # last 10 digits, format-agnostic
    if not num or not phone:
        raise HTTPException(422, "Order number and phone are both required")

    # Security: order numbers are unique only WITHIN an org, so a bare
    # ``order_number == num`` with .first() resolves an *arbitrary* tenant's order and
    # can hand back its public_token. We must scope to one org. Prefer explicit
    # resolution via the shop slug (exactly like the /appointment/{slug} routes); when no
    # shop is supplied we fall back to scoping by the client whose phone actually
    # matches, so we still never return an order the caller can't prove is theirs.
    q = select(Order).where(Order.order_number == num)
    if data.shop:
        org = (await db.execute(select(_Org).where(_Org.subdomain == data.shop))).scalar_one_or_none()
        if not org:
            org = (await db.execute(select(_Org).where(_Org.id == data.shop))).scalar_one_or_none()
        if not org:
            # same opaque 404 — don't confirm whether the shop/number/phone exists
            raise HTTPException(404, "No matching order — check the number and phone")
        q = q.where(Order.org_id == org.id)

    candidates = (await db.execute(q)).scalars().all()
    order = None
    for cand in candidates:
        if not cand.client_id:
            continue
        client = (await db.execute(select(Client).where(
            Client.id == cand.client_id, Client.org_id == cand.org_id))).scalar_one_or_none()
        on_file = "".join(ch for ch in (client.phone or "") if ch.isdigit())[-10:] if client else ""
        if on_file and on_file == phone:
            order = cand   # the phone match pins the tenant when no slug was given
            break
    if not order:
        # same 404 whether the shop, number or phone is wrong — don't confirm a number exists
        raise HTTPException(404, "No matching order — check the number and phone")

    if not order.public_token:
        import secrets
        order.public_token = secrets.token_urlsafe(16)
        await db.commit()
    return {"token": order.public_token, "track_path": f"/track/{order.public_token}"}


@router.get("/widget.js")
async def status_widget_js():
    """A self-contained embeddable status checker. A shop drops one <script> tag on their
    site and gets a "check your repair status" box that talks to /lookup then /track —
    no iframe, no build step, no dependency. Served as JS so it can be cached at the edge.
    """
    from fastapi.responses import Response

    js = r"""(function(){
  var API = (document.currentScript && document.currentScript.dataset.api) || '';
  var SHOP = (document.currentScript && document.currentScript.dataset.shop) || '';
  var mount = document.getElementById('tekly-status') || document.getElementById('repairos-status') ||
    (function(){var d=document.createElement('div');
      (document.currentScript||document.body).parentNode.insertBefore(d,document.currentScript);return d;})();
  mount.innerHTML =
    '<div style="font-family:system-ui,sans-serif;max-width:420px;border:1px solid #dce0e8;'
    + 'border-radius:12px;padding:18px;box-shadow:0 1px 4px rgba(0,0,0,.06)">'
    + '<div style="font-weight:700;font-size:16px;margin-bottom:10px">Check your repair status</div>'
    + '<input id="ros-num" placeholder="Order number" style="width:100%;box-sizing:border-box;'
    + 'padding:9px;margin-bottom:8px;border:1px solid #cfd4dd;border-radius:8px">'
    + '<input id="ros-phone" placeholder="Phone number" style="width:100%;box-sizing:border-box;'
    + 'padding:9px;margin-bottom:10px;border:1px solid #cfd4dd;border-radius:8px">'
    + '<button id="ros-go" style="width:100%;padding:10px;border:0;border-radius:8px;'
    + 'background:#2f6bff;color:#fff;font-weight:600;cursor:pointer">Check status</button>'
    + '<div id="ros-out" style="margin-top:12px;font-size:14px"></div></div>';
  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  mount.querySelector('#ros-go').onclick = function(){
    var num=mount.querySelector('#ros-num').value, ph=mount.querySelector('#ros-phone').value;
    var out=mount.querySelector('#ros-out'); out.textContent='Checking…';
    fetch(API+'/api/public/lookup',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({order_number:num,phone:ph,shop:SHOP||undefined})})
      .then(function(r){if(!r.ok)throw new Error('not found');return r.json();})
      .then(function(d){return fetch(API+'/api/public/track/'+d.token);})
      .then(function(r){return r.json();})
      .then(function(s){
        var pct=Math.round(((s.stage_index+1)/s.stages.length)*100);
        out.innerHTML='<div style="font-weight:600;margin-bottom:6px">'+esc(s.stage_label)+'</div>'
          +'<div style="height:8px;background:#eef1f6;border-radius:99px;overflow:hidden">'
          +'<div style="height:100%;width:'+pct+'%;background:#2f6bff"></div></div>'
          +'<div style="color:#667085;margin-top:8px">'+esc(s.device||'')
          +(s.scheduled_for?(' · ready '+new Date(s.scheduled_for).toLocaleDateString()):'')+'</div>';
      })
      .catch(function(){out.innerHTML='<span style="color:#c0392b">No matching order — check the number and phone.</span>';});
  };
})();"""
    return Response(content=js, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=3600"})
