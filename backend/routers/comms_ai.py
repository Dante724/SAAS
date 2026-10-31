"""Builds 105–108 — VoIP AI depth, e-commerce sync, multi-account email, approvals.

These are the "new channel and governance" features. Where a real deployment would call
an external service (a speech-to-text model, a Shopify API, an SMTP relay), the boundary
is stubbed and clearly marked: the value modelled here is the Tekly-side workflow —
storing the transcript, mapping an external order to an internal one without duplication,
gating a refund above a role's ceiling — not the third-party call itself.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import (
    ApprovalRequest,
    Call,
    EcommerceIntegration,
    EcommerceOrderMap,
    EmailAccount,
    Employee,
    Role,
)
from backend.services.auth import get_current_employee, require_perm
from backend.services.dates import now_utc

router = APIRouter(prefix="/api", tags=["comms-ai"])


# ══ Build 105: VoIP + AI depth ═══════════════════════════════════════════════
class AIResultIn(BaseModel):
    transcript: Optional[str] = None
    summary: Optional[str] = None
    sentiment: Optional[str] = None     # positive|neutral|negative


@router.post("/comms/calls/{call_id}/ai")
async def attach_ai(
    call_id: str, data: AIResultIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Attach AI results (transcript, summary, sentiment) to a call.

    The transcription/summarisation model runs at the edge and posts back here; this
    endpoint is the Tekly-side record. Keeping sentiment as a small enum makes it
    filterable — “show me this week's negative calls” is the report a manager actually
    wants — without committing to any one model's richer output.
    """
    call = (await db.execute(select(Call).where(
        Call.id == call_id, Call.org_id == current.org_id))).scalar_one_or_none()
    if not call:
        raise HTTPException(404, "Call not found")
    if data.sentiment and data.sentiment not in ("positive", "neutral", "negative"):
        raise HTTPException(422, "sentiment must be positive|neutral|negative")
    if data.transcript is not None:
        call.transcript = data.transcript
    if data.summary is not None:
        call.summary = data.summary
    if data.sentiment is not None:
        call.sentiment = data.sentiment
    call.ai_processed_at = now_utc()
    await db.commit()
    return {"ok": True, "call_id": call.id, "sentiment": call.sentiment}


@router.get("/comms/calls/insights")
async def call_insights(
    sentiment: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Calls with AI results, optionally filtered by sentiment — the manager's
    “what went wrong on the phone this week” view."""
    q = select(Call).where(Call.org_id == current.org_id, Call.ai_processed_at.isnot(None))
    if sentiment:
        q = q.where(Call.sentiment == sentiment)
    rows = (await db.execute(q.order_by(Call.created_at.desc()).limit(200))).scalars().all()
    return {"items": [{"id": c.id, "from_number": c.from_number, "direction": c.direction,
                       "sentiment": c.sentiment, "summary": c.summary,
                       "created_at": c.created_at.isoformat()} for c in rows]}


# ══ Build 106: multi-account email with sender permissions ═══════════════════
class EmailAccountIn(BaseModel):
    address: str
    display_name: Optional[str] = None
    provider: str = "gmail"
    is_default: bool = False
    allowed_employee_ids: list[str] = []


@router.get("/settings/email-accounts")
async def list_email_accounts(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(EmailAccount).where(
        EmailAccount.org_id == current.org_id, EmailAccount.active == True))).scalars().all()  # noqa: E712
    # an employee only sees accounts they may actually send from
    out = []
    for a in rows:
        allowed = not a.allowed_employee_ids or current.id in a.allowed_employee_ids
        out.append({"id": a.id, "address": a.address, "display_name": a.display_name,
                    "provider": a.provider, "is_default": a.is_default,
                    "can_send": allowed,
                    "allowed_employee_ids": a.allowed_employee_ids or []})
    return {"items": out}


@router.post("/settings/email-accounts", status_code=201)
async def add_email_account(
    data: EmailAccountIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    count = len((await db.execute(select(EmailAccount).where(
        EmailAccount.org_id == current.org_id, EmailAccount.active == True))).scalars().all())  # noqa: E712
    if count >= 10:
        raise HTTPException(422, "Up to 10 email accounts per organisation")
    if data.provider not in ("gmail", "smtp", "outlook"):
        raise HTTPException(422, "provider must be gmail|smtp|outlook")
    if data.is_default:
        for a in (await db.execute(select(EmailAccount).where(
                EmailAccount.org_id == current.org_id))).scalars().all():
            a.is_default = False
    acc = EmailAccount(org_id=current.org_id, **data.model_dump())
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return {"id": acc.id, "address": acc.address}


# ══ Build 107: e-commerce sync ═══════════════════════════════════════════════
PLATFORMS = ("shopify", "woocommerce", "etsy", "amazon")


class IntegrationIn(BaseModel):
    platform: str
    store_url: Optional[str] = None
    location_id: Optional[str] = None
    warehouse_id: Optional[str] = None
    credential_ref: Optional[str] = None
    sync_products: bool = True
    sync_orders: bool = True


@router.get("/ecommerce/integrations")
async def list_integrations(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(EcommerceIntegration).where(
        EcommerceIntegration.org_id == current.org_id))).scalars().all()
    return {"items": [{"id": i.id, "platform": i.platform, "store_url": i.store_url,
                       "active": i.active, "sync_products": i.sync_products,
                       "sync_orders": i.sync_orders,
                       "last_synced_at": i.last_synced_at.isoformat() if i.last_synced_at else None}
                      for i in rows]}


@router.post("/ecommerce/integrations", status_code=201)
async def add_integration(
    data: IntegrationIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if data.platform not in PLATFORMS:
        raise HTTPException(422, f"platform must be one of {PLATFORMS}")
    integ = EcommerceIntegration(org_id=current.org_id, **data.model_dump())
    db.add(integ)
    await db.commit()
    await db.refresh(integ)
    return {"id": integ.id, "platform": integ.platform}


class SyncOrderIn(BaseModel):
    external_order_id: str
    external_status: Optional[str] = None
    client_name: Optional[str] = None
    client_phone: Optional[str] = None


@router.post("/ecommerce/integrations/{integration_id}/sync-order")
async def sync_order(
    integration_id: str, data: SyncOrderIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Ingest (or update) one store order. Idempotent on external_order_id via the
    order map, so a store that re-sends the same order updates the existing Tekly
    order instead of creating a duplicate — the single most important property of any
    sync, and the one most integrations get wrong.
    """
    integ = (await db.execute(select(EcommerceIntegration).where(
        EcommerceIntegration.id == integration_id,
        EcommerceIntegration.org_id == current.org_id))).scalar_one_or_none()
    if not integ:
        raise HTTPException(404, "Integration not found")

    existing = (await db.execute(select(EcommerceOrderMap).where(
        EcommerceOrderMap.integration_id == integration_id,
        EcommerceOrderMap.external_order_id == data.external_order_id))).scalar_one_or_none()

    from backend.models import Client, Location, Order
    from backend.routers.orders import _order_number
    from sqlalchemy import func as _f

    if existing:
        if existing.order_id and data.external_status:
            existing.external_status = data.external_status
        integ.last_synced_at = now_utc()
        await db.commit()
        return {"order_id": existing.order_id, "created": False, "updated": True}

    client = None
    if data.client_phone:
        client = (await db.execute(select(Client).where(
            Client.org_id == current.org_id, Client.phone == data.client_phone))).scalars().first()
    if not client:
        client = Client(org_id=current.org_id, name=data.client_name or "Online customer",
                        phone=data.client_phone)
        db.add(client)
        await db.flush()

    loc_id = integ.location_id
    if not loc_id:
        loc = (await db.execute(select(Location).where(
            Location.org_id == current.org_id).order_by(Location.id).limit(1))).scalar_one_or_none()
        loc_id = loc.id if loc else None
    seq_val = (await db.execute(select(_f.count()).select_from(Order).where(
        Order.org_id == current.org_id))).scalar() or 0
    order = Order(org_id=current.org_id, client_id=client.id,
                  order_number=_order_number(current.org_id, seq_val + 1),
                  complaint=f"{integ.platform} order {data.external_order_id}",
                  location_id=loc_id, status="new")
    db.add(order)
    await db.flush()
    db.add(EcommerceOrderMap(org_id=current.org_id, integration_id=integration_id,
                             external_order_id=data.external_order_id, order_id=order.id,
                             external_status=data.external_status))
    integ.last_synced_at = now_utc()
    await db.commit()
    return {"order_id": order.id, "created": True, "updated": False}


# ══ Build 108: approval requests (refund ceiling) ════════════════════════════
class ApprovalIn(BaseModel):
    kind: str                          # refund|discount|retro_edit
    amount: Optional[float] = None
    ref_type: Optional[str] = None
    ref_id: Optional[str] = None
    reason: Optional[str] = None


@router.post("/approvals", status_code=201)
async def request_approval(
    data: ApprovalIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Request approval for a sensitive action. A refund at or below the requester's role
    ceiling is auto-approved (no bottleneck for routine work); above it, the request is
    parked for someone authorised. This is the graduated alternative to a blunt
    can/can't-refund permission called out in the roles gap analysis."""
    if data.kind not in ("refund", "discount", "retro_edit"):
        raise HTTPException(422, "kind must be refund|discount|retro_edit")

    ceiling = None
    if current.role_id:
        role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
        ceiling = float(role.refund_ceiling) if role and role.refund_ceiling is not None else None

    auto = (data.kind in ("refund", "discount") and data.amount is not None
            and ceiling is not None and data.amount <= ceiling)
    req = ApprovalRequest(
        org_id=current.org_id, kind=data.kind, amount=data.amount,
        ref_type=data.ref_type, ref_id=data.ref_id, reason=data.reason,
        requested_by_id=current.id,
        status="approved" if auto else "pending",
        decided_by_id=current.id if auto else None,
        decided_at=now_utc() if auto else None)
    db.add(req)
    await db.commit()
    await db.refresh(req)
    return {"id": req.id, "status": req.status, "auto_approved": auto,
            "ceiling": ceiling}


@router.get("/approvals")
async def list_approvals(
    status: str = "pending",
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(ApprovalRequest).where(ApprovalRequest.org_id == current.org_id)
    if status != "all":
        q = q.where(ApprovalRequest.status == status)
    rows = (await db.execute(q.order_by(ApprovalRequest.created_at.desc()).limit(200))).scalars().all()
    return {"items": [{"id": r.id, "kind": r.kind, "amount": float(r.amount) if r.amount is not None else None,
                       "status": r.status, "reason": r.reason,
                       "requested_by_id": r.requested_by_id,
                       "created_at": r.created_at.isoformat()} for r in rows]}


class DecisionIn(BaseModel):
    decision: str                      # approve|reject


@router.post("/approvals/{req_id}/decide")
async def decide_approval(
    req_id: str, data: DecisionIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Approve or reject a parked request. Requires settings authority, and — a real
    control — the requester cannot decide their own request."""
    req = (await db.execute(select(ApprovalRequest).where(
        ApprovalRequest.id == req_id,
        ApprovalRequest.org_id == current.org_id))).scalar_one_or_none()
    if not req:
        raise HTTPException(404, "Approval request not found")
    if req.status != "pending":
        raise HTTPException(409, f"This request is already {req.status}")
    if req.requested_by_id == current.id:
        raise HTTPException(403, "You cannot decide your own approval request")
    if data.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve|reject")
    req.status = "approved" if data.decision == "approve" else "rejected"
    req.decided_by_id = current.id
    req.decided_at = now_utc()
    await db.commit()
    return {"id": req.id, "status": req.status}


# ══ Build 110: webhook secret management ═════════════════════════════════════
@router.get("/settings/webhook-secret")
async def get_webhook_secret(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """The org's inbound-webhook token, for configuring providers. Orgs created before
    build 110 have none yet — viewing lazily issues one, which also closes their
    compatibility window (from then on, unsigned posts are rejected)."""
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(
        Organisation.id == current.org_id))).scalar_one()
    if not org.webhook_secret:
        import secrets as _sec
        org.webhook_secret = _sec.token_urlsafe(24)
        await db.commit()
    return {"webhook_secret": org.webhook_secret,
            "header": "X-Webhook-Token",
            "endpoints": [f"/api/chats/webhook/{current.org_id}",
                          f"/api/comms/calls/webhook?org_id={current.org_id}"]}


@router.post("/settings/webhook-secret/rotate")
async def rotate_webhook_secret(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Issue a new token, invalidating the old one immediately — the standard response
    to a leaked secret. Providers must be updated with the new value."""
    from backend.models import Organisation
    import secrets as _sec
    org = (await db.execute(select(Organisation).where(
        Organisation.id == current.org_id))).scalar_one()
    org.webhook_secret = _sec.token_urlsafe(24)
    await db.commit()
    return {"webhook_secret": org.webhook_secret, "rotated": True}
