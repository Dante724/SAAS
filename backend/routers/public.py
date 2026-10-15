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
from pydantic import BaseModel
from typing import Optional
from datetime import datetime as _dt
from backend.db.database import get_db
from backend.models import (
    Order, Client, Device, Organisation, Location,
    OrderStatusLog, EventFeedItem, DocumentSignature,
    Feedback, FeedbackSettings,
)

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
    return {
        "invoice_number": inv.invoice_number,
        "status": inv.status,
        "total": total,
        "total_converted": q2(total * fx) if fx else total,
        "amount_paid": paid,
        "balance_due": q2(max(total - paid, 0)),
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


async def _decide_estimate(db: AsyncSession, order: "Order", decision: str,
                           signature: Optional[str], who: str,
                           request: Optional[Request] = None) -> Optional[str]:
    """Apply a customer's accept/decline to an estimate. Shared by the public
    track page and the customer portal. Returns the converted work-order id (or None).
    Records a client-visible feed event (+ optional signature) and moves the
    estimate to the appropriate status, auto-converting on accept when configured.
    """
    verb = "accepted" if decision == "accept" else "declined"
    db.add(EventFeedItem(
        org_id=order.org_id, entity_type="order", entity_id=order.id,
        item_type="comment", body=f"Customer {verb} the estimate of \u20b9{int(order.total or 0)}",
        visibility="client", author_name=who,
    ))
    if signature:
        order.signature = signature
        db.add(EventFeedItem(
            org_id=order.org_id, entity_type="order", entity_id=order.id,
            item_type="signature", media_data=signature, media_name="customer_signature.png",
            media_mime="image/png", visibility="client", author_name=who,
            body=f"Signature on {verb} {'estimate' if order.is_estimate else 'work order'}",
        ))

    # 144ig — always record a tamper-evident signed-approval certificate (estimate OR work order)
    await _record_signature(db, order, decision, signature, who, request)

    from backend.models import OrderStatus, OrderStatusLog
    converted_id = None

    # 144ii — if the shop configured an explicit "move to status" for this doc type + decision,
    # honour it (and convert an estimate when that target converts_to_order). Otherwise fall back
    # to the built-in defaults (estimates only).
    cfg_key = await _configured_target_key(db, order, decision)
    if cfg_key:
        st = (await db.execute(select(OrderStatus).where(
            OrderStatus.org_id == order.org_id, OrderStatus.key == cfg_key,
            OrderStatus.active == True))).scalars().first()  # noqa: E712
        if st:
            if decision == "accept" and order.is_estimate:
                order.estimate_approved_at = _dt.utcnow()
            prev = order.status
            order.status = st.key
            db.add(OrderStatusLog(order_id=order.id, from_status=prev, to_status=st.key,
                                  note=f"{verb.capitalize()} by customer"))
            await db.flush()
            if getattr(st, "converts_to_order", False) and order.is_estimate and not order.converted_order_id:
                from backend.routers.orders import _convert_estimate

                class _Sys:
                    id = None
                    org_id = order.org_id
                converted_id = await _convert_estimate(db, _Sys(), order)
        return converted_id

    if not order.is_estimate:
        return converted_id
    if decision == "accept":
        # Build 75: record WHEN the customer approved, so the approval is auditable
        # independently of the status the estimate happens to land in.
        order.estimate_approved_at = _dt.utcnow()
        # notify the assigned technician of the customer's approval
        try:
            if order.assigned_to_id:
                from backend.services.push import notify
                await notify(db, org_id=order.org_id, employee_id=order.assigned_to_id,
                             kind="estimate.approved", title=f"Estimate approved · {order.order_number}",
                             body="Customer accepted the estimate", deep_link=f"/tech#/order/{order.id}",
                             commit=False)
        except Exception:
            pass
        # Build 144w: workflow automations for estimate_approved (best-effort).
        try:
            from backend.services import automation as _auto
            await _auto.run(db, order.org_id, "estimate_approved", order=order)
        except Exception:
            pass
        target = (await db.execute(select(OrderStatus).where(
            OrderStatus.org_id == order.org_id, OrderStatus.is_estimate == True,  # noqa: E712
            OrderStatus.converts_to_order == True, OrderStatus.active == True))).scalar_one_or_none()  # noqa: E712
        if not target:
            target = (await db.execute(select(OrderStatus).where(
                OrderStatus.org_id == order.org_id, OrderStatus.is_estimate == True,  # noqa: E712
                OrderStatus.status_group == "closed", OrderStatus.active == True))).scalar_one_or_none()  # noqa: E712
        if target:
            prev = order.status
            order.status = target.key
            db.add(OrderStatusLog(order_id=order.id, from_status=prev, to_status=target.key, note="Accepted by customer"))
            await db.flush()
            if getattr(target, "converts_to_order", False) and not order.converted_order_id:
                from backend.routers.orders import _convert_estimate

                class _Sys:
                    id = None
                    org_id = order.org_id
                converted_id = await _convert_estimate(db, _Sys(), order)
    else:  # decline
        target = (await db.execute(select(OrderStatus).where(
            OrderStatus.org_id == order.org_id, OrderStatus.is_estimate == True,  # noqa: E712
            OrderStatus.status_group == "dropped", OrderStatus.active == True))).scalar_one_or_none()  # noqa: E712
        if target:
            prev = order.status
            order.status = target.key
            db.add(OrderStatusLog(order_id=order.id, from_status=prev, to_status=target.key, note="Declined by customer"))
    return converted_id


@router.get("/track/{token}")
async def track(token: str, db: AsyncSession = Depends(get_db)):
    order = await _load(db, token)

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

    try:
        stage_index = STAGE_FLOW.index(order.status)
    except ValueError:
        stage_index = 0

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
    lines_pub = [{"label": l.description or (l.kind or l.line_type or "Item").title(),
                  "qty": l.qty or 1, "kind": l.kind or l.line_type,
                  "amount": _line_amt(l),
                  **({"item": (_inames.get(l.order_item_id) or (None,))[0]} if _inames else {})} for l in _lines]

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
        "stage_label": STAGE_LABEL.get(order.status, order.status.replace("_", " ").title()),
        "stage_index": stage_index,
        "stages": [{"key": s, "label": STAGE_LABEL[s]} for s in STAGE_FLOW],
        "shop_name": org.name if org else "Repair shop",
        "shop_phone": shop_phone,
        "client_name": client.name if client else None,
        "device": ((" ".join(p for p in [device.brand, device.model] if p) or None) if device else None),
        # 144hc — customer-facing asset noun (Vessel / Vehicle / Unit / Device…)
        "asset_label": _ind.asset_label(getattr(device, "template_key", None)) if device else None,
        "complaint": order.complaint,
        "total": order.total,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        # 144dr — only expose the scheduled date when the org opted in.
        "scheduled_for": (order.scheduled_for.isoformat() if order.scheduled_for else None) if getattr(org, "oe_show_scheduled", False) else None,
        "scheduled_to": (order.scheduled_to.isoformat() if getattr(order, "scheduled_to", None) else None) if getattr(org, "oe_show_scheduled", False) else None,
        "awaiting_approval": await _can_sign(db, order),
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
            {"to": l.to_status, "label": STAGE_LABEL.get(l.to_status, l.to_status),
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
    if not await _can_sign(db, order):
        raise HTTPException(403, "Online approval is not available for this document right now")
    if data.decision not in ("accept", "decline"):
        raise HTTPException(400, "Invalid decision")
    if data.decision == "accept" and not data.signature and await _signature_required(db, order):
        raise HTTPException(422, "A signature is required to accept this document")

    who = data.name or (
        (await db.execute(select(Client).where(Client.id == order.client_id))).scalar_one_or_none().name
        if order.client_id else "Customer"
    )
    converted_id = await _decide_estimate(db, order, data.decision, data.signature, who, request)
    await db.commit()
    return {"ok": True, "decision": data.decision, "converted_order_id": converted_id}


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
async def _client_by_portal(db: AsyncSession, token: str) -> Client:
    if not token:
        raise HTTPException(404, "Not found")
    client = (await db.execute(select(Client).where(Client.portal_token == token))).scalar_one_or_none()
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
    work_orders = [_order_card(o) for o in orders if not o.is_estimate]
    estimates = [_order_card(o) for o in orders if o.is_estimate]
    return {
        "shop_name": org.name if org else "Repair shop",
        "client_name": client.name,
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
    client = await _client_by_portal(db, token)
    from backend.models import OrderLine
    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.client_id == client.id)
    )).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    lines = (await db.execute(
        select(OrderLine).where(OrderLine.order_id == order.id).order_by(OrderLine.id)
    )).scalars().all()
    logs = (await db.execute(
        select(OrderStatusLog).where(OrderStatusLog.order_id == order.id).order_by(OrderStatusLog.changed_at)
    )).scalars().all()
    feed = (await db.execute(
        select(EventFeedItem).where(
            EventFeedItem.entity_type == "order", EventFeedItem.entity_id == order.id,
            EventFeedItem.visibility == "client").order_by(desc(EventFeedItem.created_at))
    )).scalars().all()
    card = _order_card(order)
    from backend.routers.forms import shown_values
    _dev = (await db.execute(select(Device).where(Device.id == order.device_id))).scalar_one_or_none() if order.device_id else None
    card.update({
        "custom_fields": await shown_values(db, client.org_id, "show_in_portal", order=order, device=_dev, client=client),
        "diagnosis": order.diagnosis,
        "lines": [{"description": ln.description, "qty": ln.qty,
                   "unit_price": ln.unit_price, "line_type": ln.line_type} for ln in lines],
        "can_approve": bool(order.is_estimate) and await _approval_enabled(db, client.org_id),
        **(await _message_fields(db, order, await _org(db, client.org_id))),
        "allow_recommend": await _recommend_enabled(db, client.org_id),
        "recommendations": await _public_recommendations(db, order),
        "timeline": [{"to": lg.to_status,
                      "label": STAGE_LABEL.get(lg.to_status, lg.to_status.replace("_", " ").title()),
                      "at": lg.changed_at.isoformat() if lg.changed_at else None} for lg in logs],
        "updates": [{"type": f.item_type, "body": f.body,
                     "media_data": f.media_data if f.item_type in ("photo", "signature", "file") else None,
                     "media_name": f.media_name, "media_mime": getattr(f, "media_mime", None),
                     "at": f.created_at.isoformat() if f.created_at else None} for f in feed],
    })
    return card


@router.post("/portal/{token}/order/{order_id}/respond")
async def portal_respond(token: str, order_id: str, data: AcceptIn, request: Request, db: AsyncSession = Depends(get_db)):
    client = await _client_by_portal(db, token)
    if data.decision not in ("accept", "decline"):
        raise HTTPException(400, "Invalid decision")
    order = (await db.execute(
        select(Order).where(Order.id == order_id, Order.client_id == client.id)
    )).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    if not await _can_sign(db, order):
        raise HTTPException(403, "Online approval is not available for this document right now")
    if data.decision == "accept" and not data.signature and await _signature_required(db, order):
        raise HTTPException(422, "A signature is required to accept this document")
    converted_id = await _decide_estimate(db, order, data.decision, data.signature, data.name or client.name, request)
    await db.commit()
    return {"ok": True, "decision": data.decision, "converted_order_id": converted_id}


# ── Customer → shop messages (land in the work-order feed) — 144ba ──
class MessageIn(BaseModel):
    body: str
    name: Optional[str] = None


async def _approval_enabled(db: AsyncSession, org_id: str) -> bool:
    from backend.models import Organisation as _O
    org = (await db.execute(select(_O).where(_O.id == org_id))).scalar_one_or_none()
    return getattr(org, "oe_allow_approval", True) if org else True


async def _can_sign(db: AsyncSession, order: "Order") -> bool:
    """144ih — may the customer accept/decline+sign this document now? Estimates use
    oe_allow_approval; work orders are opt-in (oe_wo_allow_approval). Each has an
    optional comma-separated status allow-list; empty = any status (Orderry parity)."""
    org = (await db.execute(select(Organisation).where(Organisation.id == order.org_id))).scalar_one_or_none()
    if not org:
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
    return (not allowed) or (order.status in allowed)


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
        "item": (names[r.order_item_id][0] if r.order_item_id in names else None),
        "item_order": (names[r.order_item_id][1] if r.order_item_id in names else None),
    } for r in rows]


class RecDecideIn(BaseModel):
    approve_ids: list[str] = []
    decline_ids: list[str] = []


async def _decide_recommendations(db: AsyncSession, order: "Order", data: "RecDecideIn") -> dict:
    """Customer approves and/or declines recommended items. Approved items become
    order lines (via the shared money seam); every decision bumps the staff dot."""
    if not await _recommend_enabled(db, order.org_id):
        raise HTTPException(403, "Recommendations are turned off for this shop")
    from backend.models import Recommendation as _R
    from backend.routers.recommendations import approve_recommendation
    ids = list(dict.fromkeys((data.approve_ids or []) + (data.decline_ids or [])))
    if not ids:
        raise HTTPException(400, "Nothing selected")
    rows = {r.id: r for r in (await db.execute(select(_R).where(
        _R.order_id == order.id, _R.id.in_(ids), _R.status == "sent"))).scalars().all()}
    approved = declined = 0
    for rid in (data.approve_ids or []):
        r = rows.get(rid)
        if r:
            await approve_recommendation(db, r)
            approved += 1
    for rid in (data.decline_ids or []):
        r = rows.get(rid)
        if r and r.status == "sent":
            from datetime import datetime as _dtnow
            r.status = "declined"
            r.decided_at = _dtnow.utcnow()
            declined += 1
    decided = approved + declined
    if decided:
        order.rec_unread = (order.rec_unread or 0) + decided
    return {"approved": approved, "declined": declined}


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


@router.post("/track/{token}/message")
async def track_message(token: str, data: MessageIn, db: AsyncSession = Depends(get_db)):
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
    from backend.services.ratelimit import enforce as _rate
    _rate("track_new_req", f"{_ip(request)}:{token}", limit=3, window_sec=3600)
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
                inquiry=f"{ref}: {body}"[:2000], sla_due_at=_dt.utcnow() + _td(minutes=60))
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
    fb.submitted_at = _dt.utcnow()
    from backend.routers.feed import log_system_event
    await log_system_event(db, order.org_id, "order", order.id,
                           f"Customer left feedback: {data.rating}{' — ' + data.comment if data.comment else ''}")
    await db.commit()
    from backend.routers.feedback import _band_message
    return {"ok": True,
            "message": _band_message(fset.system, data.rating, (fset.bands or {})),
            "google_review_url": getattr(fset, "google_review_url", None)}


@router.post("/portal/{token}/order/{order_id}/message")
async def portal_message(token: str, order_id: str, data: MessageIn, db: AsyncSession = Depends(get_db)):
    client = await _client_by_portal(db, token)
    order = (await db.execute(select(Order).where(Order.id == order_id, Order.client_id == client.id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    await _post_client_message(db, order, data.body, data.name or client.name)
    await db.commit()
    return {"ok": True}


async def _recompute_order_total(db: AsyncSession, order: "Order"):
    from backend.models import OrderLine as _OL
    lines = (await db.execute(select(_OL).where(_OL.order_id == order.id))).scalars().all()
    order.total = q2(sum(ln.total or 0 for ln in lines))


@router.post("/track/{token}/recommendations/decide")
async def track_decide_recs(token: str, data: RecDecideIn, db: AsyncSession = Depends(get_db)):
    order = await _load(db, token)
    result = await _decide_recommendations(db, order, data)
    await _recompute_order_total(db, order)
    await db.commit()
    return {"ok": True, **result}


@router.post("/portal/{token}/order/{order_id}/recommendations/decide")
async def portal_decide_recs(token: str, order_id: str, data: RecDecideIn,
                             db: AsyncSession = Depends(get_db)):
    client = await _client_by_portal(db, token)
    order = (await db.execute(select(Order).where(
        Order.id == order_id, Order.client_id == client.id))).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    result = await _decide_recommendations(db, order, data)
    await _recompute_order_total(db, order)
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
        "staff": [{"id": e.id, "name": e.name} for e in staff],
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
    from backend.services.ratelimit import enforce as _rate, client_ip as _ip
    _rate("book", f"{_ip(request)}:{slug}", limit=8, window_sec=300)   # Build 144bz: anti-spam
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
    from backend.services.ratelimit import enforce as _rate, client_ip as _ip
    _rate("book_req", f"{_ip(request)}:{slug}", limit=6, window_sec=300)
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
                sla_due_at=_dt.utcnow() + _td(minutes=60))
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
    digits = "".join(ch for ch in (data.phone or "") if ch.isdigit())[-10:]
    if len(digits) < 7:
        return {"known": False}
    # ponytail: full-org scan for format-agnostic matching; add a normalized-phone
    # column + index when a shop's client list is big enough for this to show up.
    rows = (await db.execute(select(Client).where(Client.org_id == org.id))).scalars().all()
    for c in rows:
        on_file = "".join(ch for ch in (c.phone or "") if ch.isdigit())[-10:]
        if on_file and on_file == digits:
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
  var mount = document.getElementById('repairos-status') ||
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
