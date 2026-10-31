"""The /admin platform portal API: Tekly's own back office.

Sign-in (password, then 2-step) lives on a small public router. Everything else
sits on a router whose dependency requires a live 2-step session, so a new route
can't forget it (a test walks every route). On top of that, each route asks for a
permission, shops are filtered by the admin's scope, fields by their role's field
levels, money actions by their limits, and every action lands in the append-only
action log. See docs/plans/2026-10-24-admin-panel.md.
"""
import asyncio
import math
import re
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.database import get_db
from backend.models import (
    AdminActionLog,
    AdminApprovalRequest,
    AdminAssignmentLog,
    AdminRole,
    AdminSession,
    AdminShopNote,
    Employee,
    Order,
    Organisation,
    PlatformAdmin,
    PlatformCoupon,
)
from backend.services import admin_log, crypto, mfa
from backend.services.account_id import normalize as normalize_account_id
from backend.services.admin_access import (
    ALL_PERMS,
    BUILTIN_ROLES,
    FIELD_GROUPS,
    PERMISSIONS,
    PLANS,
    AdminCtx,
    admin_ctx,
    can_see,
    effective_discount,
    ensure_builtin_roles,
    get_shop,
    mask,
    over_discount_limit,
    over_trial_limit,
    require_admin_perm,
    scope_shops,
    wider_than,
)
from backend.services.auth import hash_password, verify_password
from backend.services.platform_auth import (
    COOKIE,
    SESSION_SECONDS,
    admin_from_ticket,
    create_ticket,
    end_session,
    get_current_platform_admin,
    password_problem,
    start_session,
)
from backend.services.ratelimit import aenforce as _rate
from backend.services.ratelimit import client_ip as _ip

public = APIRouter(prefix="/api/admin", tags=["platform-admin"])
private = APIRouter(prefix="/api/admin", tags=["platform-admin"],
                    dependencies=[Depends(get_current_platform_admin)])
router = APIRouter()

NEW_TRIAL_DAYS = 14
REQUEST_DAYS = 7
_DUMMY_HASH = hash_password("tekly-admin-timing-equaliser")
_CODE_RE = re.compile(r"^[A-Z0-9]{3,32}$")   # Paddle discount codes: letters and digits, up to 32


def _parse_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        raise HTTPException(422, "Use a date like 2026-12-31")


def _segment(org: Organisation) -> str:
    st = org.sub_status or "trial"
    if st in ("active", "past_due"):
        return "paid"
    if st in ("churned", "unpaid"):
        return "lost_paid"
    if st == "trial_lost" or (st not in ("suspended",) and org.trial_ends_at and org.trial_ends_at <= date.today()):
        return "lost_trial"
    if st == "suspended":
        return "suspended"
    if org.sub_start and (date.today() - org.sub_start).days <= NEW_TRIAL_DAYS:
        return "new"
    return "trial"


def _segment_filter(q, segment: str):
    new_since = date.today() - timedelta(days=NEW_TRIAL_DAYS)
    # Anything that isn't a known non-trial stage counts as a trial, as in _segment().
    on_trial = or_(Organisation.sub_status.is_(None), Organisation.sub_status.notin_(
        ("active", "past_due", "churned", "unpaid", "trial_lost", "suspended")))
    # A trial whose clock ran out without paying counts as a lost trial, as in _segment().
    trial_over = Organisation.trial_ends_at.is_not(None) & (Organisation.trial_ends_at <= date.today())
    trial = on_trial & ~trial_over
    by = {
        "paid": Organisation.sub_status.in_(("active", "past_due")),
        "lost_paid": Organisation.sub_status.in_(("churned", "unpaid")),
        "lost_trial": (Organisation.sub_status == "trial_lost") | (on_trial & trial_over),
        "suspended": Organisation.sub_status == "suspended",
        "new": trial & (Organisation.sub_start >= new_since),
        "trial": trial & or_(Organisation.sub_start.is_(None), Organisation.sub_start < new_since),
    }
    if segment not in by:
        raise HTTPException(422, "Unknown segment")
    return q.where(by[segment])


def _iso(d):
    return d.isoformat() if d else None


async def _set_cookie(response: Response, token: str):
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict",
                        secure=settings.is_deployed, path="/api/admin")


# ── Sign-in ──────────────────────────────────────────────────────────────────
class AdminLogin(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


@public.post("/login")
async def admin_login(data: AdminLogin, request: Request, db: AsyncSession = Depends(get_db)):
    email = (data.email or "").strip().lower()
    await _rate("admin_login", f"{_ip(request)}:{email}", limit=10, window_sec=300)
    await _rate("admin_login_acct", email, limit=20, window_sec=900)
    await _rate("admin_login_ip", _ip(request) or "?", limit=60, window_sec=900)   # spraying many emails
    admin = (await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == email))).scalar_one_or_none()
    # Always run the hash check so an unknown email takes as long as a wrong password.
    ok = await asyncio.to_thread(verify_password, data.password, admin.password_hash if admin else _DUMMY_HASH)
    if not admin or not admin.active or not ok:
        # Name the account only when it exists (a mistyped field can hold a pasted password).
        await admin_log.write(db, admin=admin if admin and admin.active else None, action="login",
                              outcome="refused", target=email[:120] if admin else "unknown email", ip=_ip(request))
        await db.commit()
        raise HTTPException(401, "Email or password is wrong")
    stage = "mfa" if admin.mfa_enrolled_at else "mfa_setup"
    return {"next": stage, "ticket": create_ticket(admin, stage), "name": admin.name}


class TicketIn(BaseModel):
    ticket: str = Field(max_length=2000)


class ConfirmIn(TicketIn):
    code: str = Field(max_length=12)


class MfaIn(TicketIn):
    code: Optional[str] = Field(None, max_length=12)
    recovery_code: Optional[str] = Field(None, max_length=40)


@public.post("/mfa/setup")
async def mfa_setup(data: TicketIn, db: AsyncSession = Depends(get_db)):
    admin = await admin_from_ticket(db, data.ticket, "mfa_setup")
    secret = mfa.new_secret()
    admin.mfa_secret = crypto.encrypt(secret)
    await db.commit()
    return {"secret": secret, "uri": mfa.provisioning_uri(secret, account=admin.email, issuer="Tekly admin")}


@public.post("/mfa/setup/confirm")
async def mfa_setup_confirm(data: ConfirmIn, request: Request, response: Response,
                            db: AsyncSession = Depends(get_db)):
    admin = await admin_from_ticket(db, data.ticket, "mfa_setup")
    await _rate("admin_mfa", admin.id, limit=10, window_sec=300)
    secret = crypto.decrypt(admin.mfa_secret) if admin.mfa_secret else None
    step = mfa.verify_step(secret, data.code) if secret else None
    if step is None:
        raise HTTPException(401, "That code didn't match. Check the time on your phone and try again.")
    plain, hashed = await asyncio.to_thread(mfa.make_recovery_codes, 8)   # 8 slow hashes: off the event loop
    admin.mfa_recovery, admin.mfa_enrolled_at, admin.mfa_last_step = hashed, datetime.utcnow(), step
    token = await start_session(db, admin, _ip(request))
    await admin_log.write(db, admin=admin, action="mfa_enrolled", ip=_ip(request))
    await db.commit()
    await _set_cookie(response, token)
    return {"access_token": token, "token_type": "bearer", "expires_in": SESSION_SECONDS, "recovery_codes": plain}


@public.post("/login/mfa")
async def login_mfa(data: MfaIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    admin = await admin_from_ticket(db, data.ticket, "mfa")
    await _rate("admin_mfa", admin.id, limit=10, window_sec=300)
    ok = False
    if data.code:
        secret = crypto.decrypt(admin.mfa_secret) if admin.mfa_secret else None
        step = mfa.verify_step(secret, data.code) if secret else None
        if step is not None and step > (admin.mfa_last_step or 0):   # a code works once
            admin.mfa_last_step, ok = step, True
    elif data.recovery_code:
        used, remaining = await asyncio.to_thread(mfa.consume_recovery_code, admin.mfa_recovery, data.recovery_code)
        if used:
            admin.mfa_recovery, ok = remaining, True
    if not ok:
        await admin_log.write(db, admin=admin, action="login_2step", outcome="refused", ip=_ip(request))
        await db.commit()
        raise HTTPException(401, "That code didn't work")
    token = await start_session(db, admin, _ip(request))
    await admin_log.write(db, admin=admin, action="login", ip=_ip(request),
                          after={"with": "recovery_code" if data.recovery_code else "code"})
    await db.commit()
    await _set_cookie(response, token)
    return {"access_token": token, "token_type": "bearer", "expires_in": SESSION_SECONDS}


@private.post("/logout")
async def logout(request: Request, response: Response, ctx: AdminCtx = Depends(admin_ctx),
                 db: AsyncSession = Depends(get_db)):
    await end_session(db, ctx.session_id)
    await admin_log.write(db, admin=ctx.admin, action="logout", ip=ctx.ip)
    await db.commit()
    response.delete_cookie(COOKIE, path="/api/admin")
    return {"ok": True}


@private.get("/me")
async def admin_me(ctx: AdminCtx = Depends(admin_ctx), db: AsyncSession = Depends(get_db)):
    role = (await db.execute(select(AdminRole).where(AdminRole.id == ctx.admin.role_id))).scalar_one_or_none() \
        if ctx.admin.role_id else None
    return {"id": ctx.admin.id, "name": ctx.admin.name, "email": ctx.admin.email,
            "role": {"key": ctx.role_key, "name": role.name if role else "Owner"},
            "permissions": {c: ctx.can(c) for c in ALL_PERMS}, "field_levels": ctx.fields,
            "scope": ctx.scope, "limits": ctx.limits}


# ── Shops ────────────────────────────────────────────────────────────────────
async def _admin_names(db) -> dict:
    return {a.id: a.name for a in (await db.execute(select(PlatformAdmin))).scalars()}


async def _owner_info(db, orgs) -> dict:
    owner_ids = [o.owner_id for o in orgs if o.owner_id]
    if not owner_ids:
        return {}
    rows = (await db.execute(select(Employee.id, Employee.name, Employee.email).where(Employee.id.in_(owner_ids)))).all()
    return {r.id: (r.name, r.email) for r in rows}


def _shop_row(org: Organisation, *, employees: int, owner, names: dict) -> dict:
    return {
        "id": org.id, "account_id": org.account_id, "name": org.name, "plan": org.plan,
        "sub_status": org.sub_status, "segment": _segment(org),
        "sub_start": _iso(org.sub_start), "sub_end": _iso(org.sub_end), "country": org.country,
        "employees": employees, "created_at": _iso(getattr(org, "created_at", None)),
        "assigned_admin_id": org.assigned_admin_id, "assigned_to": names.get(org.assigned_admin_id),
        "last_contacted_at": _iso(org.last_contacted_at),
        "discount_pct": effective_discount(org), "discount_until": _iso(org.discount_until),
        "discount_reason": org.discount_reason,
        "company_email": org.company_email, "company_phone": org.company_phone,
        "owner_name": owner[0] if owner else None, "owner_email": owner[1] if owner else None,
        "suspend_message": org.suspend_message,
        # Subscription billing (services/billing.py): trial clock, Stripe or custom pricing.
        "trial_ends_at": _iso(org.trial_ends_at), "billing_period": org.billing_period,
        "billing_mode": org.billing_mode or "stripe", "billing_period_end": _iso(org.billing_period_end),
        "billing_note": org.billing_note, "price_locked_until": _iso(org.price_locked_until),
        "stripe_customer_id": org.stripe_customer_id,
    }


@private.get("/stats")
async def admin_stats(ctx: AdminCtx = Depends(require_admin_perm("view_shops")), db: AsyncSession = Depends(get_db)):
    orgs = (await db.execute(scope_shops(select(Organisation), ctx))).scalars().all()
    counts = {"new": 0, "trial": 0, "paid": 0, "lost_trial": 0, "lost_paid": 0, "suspended": 0}
    for o in orgs:
        counts[_segment(o)] += 1
    out = {"total": len(orgs), "segments": counts,
           "my_book": sum(1 for o in orgs if o.assigned_admin_id == ctx.admin.id)}
    if ctx.can("approve_requests"):
        out["pending_requests"] = (await db.execute(select(func.count()).select_from(AdminApprovalRequest).where(
            AdminApprovalRequest.status == "pending", AdminApprovalRequest.expires_at > datetime.utcnow()))).scalar()
    return out


@private.get("/tenants")
async def list_tenants(segment: Optional[str] = None, q: Optional[str] = Query(None, max_length=120),
                       mine: bool = False, ending_days: Optional[int] = Query(None, ge=0, le=90),
                       limit: int = 50, offset: int = 0,
                       ctx: AdminCtx = Depends(require_admin_perm("view_shops")),
                       db: AsyncSession = Depends(get_db)):
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    query = scope_shops(select(Organisation), ctx)
    if segment:
        query = _segment_filter(query, segment)
    if mine:
        query = query.where(Organisation.assigned_admin_id == ctx.admin.id)
    if ending_days is not None:   # trials whose end date falls in the next N days
        today = date.today()
        until = today + timedelta(days=ending_days)
        # The billing trial clock (trial_ends_at) or a hand-set term end (sub_end).
        query = query.where(or_(and_(Organisation.sub_end >= today, Organisation.sub_end <= until),
                                and_(Organisation.trial_ends_at >= today, Organisation.trial_ends_at <= until)),
                            or_(Organisation.sub_status.is_(None),
                                Organisation.sub_status.notin_(("active", "churned", "trial_lost", "suspended"))))
    if q and q.strip():
        term = q.strip()[:120]
        acct = normalize_account_id(term)
        like = f"%{term.lower()}%"
        conds = [func.lower(Organisation.name).like(like)]
        # Search contact details only where this person may see them, so a search can't
        # confirm a hidden email or phone.
        contact = or_(func.lower(Organisation.company_email).like(like), Organisation.company_phone.like(f"%{term}%"))
        if ctx.level("owner_contact") == "shown":
            conds.append(contact)
        elif ctx.level("owner_contact") == "own":
            conds.append(and_(Organisation.assigned_admin_id == ctx.admin.id, contact))
        if acct:
            conds.append(Organisation.account_id == acct)
        query = query.where(or_(*conds))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar()
    orgs = (await db.execute(query.order_by(Organisation.name).limit(limit).offset(offset))).scalars().all()
    ids = [o.id for o in orgs]
    emp = dict((await db.execute(select(Employee.org_id, func.count()).where(Employee.org_id.in_(ids))
                                 .group_by(Employee.org_id))).all()) if ids else {}
    owners, names = await _owner_info(db, orgs), await _admin_names(db)
    items = [mask(_shop_row(o, employees=emp.get(o.id, 0), owner=owners.get(o.owner_id), names=names), o, ctx)
             for o in orgs]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@private.get("/tenants/{org_id}")
async def tenant_detail(org_id: str, ctx: AdminCtx = Depends(require_admin_perm("view_shops")),
                        db: AsyncSession = Depends(get_db)):
    org = await get_shop(db, ctx, org_id)
    emps = (await db.execute(select(Employee).where(Employee.org_id == org_id).order_by(Employee.name))).scalars().all()
    owners, names = await _owner_info(db, [org]), await _admin_names(db)
    row = _shop_row(org, employees=len(emps), owner=owners.get(org.owner_id), names=names)
    row["admin_notes"] = org.admin_notes
    row["employees_detail"] = [{
        "id": e.id, "name": e.name, "email": e.email, "active": e.active,
        "access_start": _iso(e.access_start), "access_end": _iso(e.access_end)} for e in emps]
    since = datetime.utcnow() - timedelta(days=7)
    row["jobs_7d"] = (await db.execute(select(func.count()).select_from(Order).where(
        Order.org_id == org_id, Order.created_at >= since))).scalar()
    row["last_activity_at"] = _iso((await db.execute(select(func.max(Order.created_at)).where(
        Order.org_id == org_id))).scalar())
    notes = (await db.execute(select(AdminShopNote).where(AdminShopNote.org_id == org_id)
                              .order_by(AdminShopNote.created_at.desc()).limit(100))).scalars().all()
    row["notes"] = [{"id": n.id, "kind": n.kind, "body": n.body, "by": names.get(n.admin_id),
                     "at": _iso(n.created_at)} for n in notes]
    hist = (await db.execute(select(AdminActionLog).where(AdminActionLog.org_id == org_id,
                                                          AdminActionLog.outcome != "refused")
                             .order_by(AdminActionLog.id.desc()).limit(100))).scalars().all()
    # History says who did what and why; before/after values only for owners (they may hold money).
    money = {"discount", "discount_removed", "apply_code", "approve_discount", "decline_discount"}
    see_money = can_see(ctx, org, "billing")
    row["history"] = [{"action": h.action, "by": h.admin_email, "at": _iso(h.at), "reason": h.reason,
                       **({"before": h.before, "after": h.after} if ctx.is_owner else {})}
                      for h in hist if see_money or h.action not in money]
    return mask(row, org, ctx)


class PatchTenant(BaseModel):
    sub_start: Optional[str] = None
    sub_end: Optional[str] = None
    sub_status: Optional[str] = None
    trial_ends_at: Optional[str] = None
    billing_mode: Optional[str] = None          # stripe | custom
    billing_period_end: Optional[str] = None    # a custom shop's term end
    billing_note: Optional[str] = Field(None, max_length=500)   # the agreed custom price (internal)
    price_locked_until: Optional[str] = None    # founding-customer price lock
    reason: str = Field(min_length=3, max_length=500)


_BILLING_KEYS = ("trial_ends_at", "billing_mode", "billing_period_end", "billing_note", "price_locked_until")


def _sub_snapshot(org: Organisation) -> dict:
    return {"sub_start": _iso(org.sub_start), "sub_end": _iso(org.sub_end), "sub_status": org.sub_status,
            "trial_ends_at": _iso(org.trial_ends_at), "billing_mode": org.billing_mode or "stripe",
            "billing_period_end": _iso(org.billing_period_end), "billing_note": org.billing_note,
            "price_locked_until": _iso(org.price_locked_until)}


@private.patch("/tenants/{org_id}")
async def patch_tenant(org_id: str, data: PatchTenant, ctx: AdminCtx = Depends(require_admin_perm("change_plan")),
                       db: AsyncSession = Depends(get_db)):
    """Subscription dates, stage and billing terms (owner-level). Suspending has its own action."""
    org = await get_shop(db, ctx, org_id)
    p = data.model_dump(exclude_unset=True)
    if any(k in p for k in _BILLING_KEYS) and not can_see(ctx, org, "billing"):
        await admin_log.refuse(db, admin=ctx.admin, action="edit_subscription", org_id=org_id, ip=ctx.ip,
                               detail="You can't see this shop's billing, so ask its account owner")
    before = _sub_snapshot(org)
    if "sub_status" in p:
        if p["sub_status"] not in {"trial", "active", "past_due", "unpaid", "trial_lost", "churned"}:
            raise HTTPException(422, "Use suspend or unsuspend for that")
        if org.sub_status == "suspended":
            raise HTTPException(409, "This shop is suspended. Unsuspend it first.")
        org.sub_status = p["sub_status"]
    if "sub_start" in p:
        org.sub_start = _parse_date(p["sub_start"])
    if "sub_end" in p:
        org.sub_end = _parse_date(p["sub_end"])
    if "trial_ends_at" in p:   # extend (or end) a trial by hand
        org.trial_ends_at = _parse_date(p["trial_ends_at"])
    if "billing_mode" in p:
        if p["billing_mode"] not in ("stripe", "custom"):
            raise HTTPException(422, "billing_mode must be stripe or custom")
        if p["billing_mode"] == "custom" and org.stripe_subscription_id and org.sub_status in ("active", "past_due"):
            raise HTTPException(409, "This shop pays through Stripe. Cancel that subscription first.")
        org.billing_mode = p["billing_mode"]
    if "billing_period_end" in p:
        org.billing_period_end = _parse_date(p["billing_period_end"])
    if "billing_note" in p:
        org.billing_note = (p["billing_note"] or "").strip() or None
    if "price_locked_until" in p:
        org.price_locked_until = _parse_date(p["price_locked_until"])
    after = _sub_snapshot(org)
    if org.sub_end and org.sub_end < date.today():
        await _boot_staff(db, org.id)
    await admin_log.write(db, admin=ctx.admin, action="edit_subscription", org_id=org.id, before=before, after=after,
                          reason=data.reason, ip=ctx.ip)
    await db.commit()
    return {"ok": True}


async def _boot_staff(db, org_id):
    await db.execute(update(Employee).where(Employee.org_id == org_id)
                     .values(token_version=func.coalesce(Employee.token_version, 0) + 1))


# ── Money actions (limits + approvals) ──────────────────────────────────────
async def _request(db, ctx: AdminCtx, org: Organisation, action: str, payload: dict, reason: str, why: str):
    req = AdminApprovalRequest(requester_id=ctx.admin.id, org_id=org.id, action=action, payload=payload,
                               reason=reason, expires_at=datetime.utcnow() + timedelta(days=REQUEST_DAYS))
    db.add(req)
    await db.flush()
    await admin_log.write(db, admin=ctx.admin, action=action, outcome="requested", org_id=org.id,
                          after=payload, reason=f"{reason} ({why})", ip=ctx.ip, target=req.id)
    await db.commit()
    return {"status": "requested", "request_id": req.id, "why": why}


async def _apply(db, org: Organisation, action: str, payload: dict) -> tuple[dict, dict]:
    """Apply a money action; returns (before, after)."""
    if action == "discount":
        before = {"pct": float(org.discount_pct or 0), "until": _iso(org.discount_until), "reason": org.discount_reason}
        org.discount_pct = payload["pct"]
        org.discount_until = _parse_date(payload.get("until"))
        org.discount_reason = payload.get("reason")
        return before, {"pct": payload["pct"], "until": payload.get("until"), "reason": org.discount_reason}
    if action == "extend_trial":
        # The trial clock services/billing reads is trial_ends_at; a hand-set term end (sub_end,
        # which blocks sign-in) moves with it so it doesn't cut the longer trial short.
        days = timedelta(days=int(payload["days"]))
        before = {"sub_end": _iso(org.sub_end), "trial_ends_at": _iso(org.trial_ends_at)}
        org.trial_ends_at = max(org.trial_ends_at or date.today(), date.today()) + days
        if org.sub_end is not None:
            org.sub_end = max(org.sub_end, date.today()) + days
        return before, {"sub_end": _iso(org.sub_end), "trial_ends_at": _iso(org.trial_ends_at),
                        "days": payload["days"]}
    if action == "plan_change":
        before = {"plan": org.plan}
        org.plan = payload["plan"]
        return before, {"plan": org.plan}
    raise HTTPException(422, "Unknown action")


class DiscountIn(BaseModel):
    pct: float
    until: Optional[str] = None
    reason: str = Field(min_length=3, max_length=300)


@private.post("/tenants/{org_id}/discount")
async def set_discount(org_id: str, data: DiscountIn, ctx: AdminCtx = Depends(require_admin_perm("give_discount")),
                       db: AsyncSession = Depends(get_db)):
    if not math.isfinite(data.pct) or not (0 < data.pct <= 100):
        raise HTTPException(422, "A discount is between 0 and 100%")
    until = _parse_date(data.until)
    if until and until < date.today():
        raise HTTPException(422, "The end date has already passed")
    org = await get_shop(db, ctx, org_id)
    if not can_see(ctx, org, "billing"):   # don't let someone overwrite a discount they can't see
        await admin_log.refuse(db, admin=ctx.admin, action="discount", org_id=org_id, ip=ctx.ip,
                               detail="You can't see this shop's billing, so ask its account owner")
    payload = {"pct": round(data.pct, 2), "until": _iso(until), "reason": data.reason}
    why = over_discount_limit(ctx, payload["pct"], until)
    if why:
        return await _request(db, ctx, org, "discount", payload, data.reason, why)
    before, after = await _apply(db, org, "discount", payload)
    await admin_log.write(db, admin=ctx.admin, action="discount", org_id=org.id, before=before, after=after,
                          reason=data.reason, ip=ctx.ip)
    await db.commit()
    return {"status": "applied"}


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@private.post("/tenants/{org_id}/discount/remove")
async def remove_discount(org_id: str, data: ReasonIn, ctx: AdminCtx = Depends(require_admin_perm("give_discount")),
                          db: AsyncSession = Depends(get_db)):
    org = await get_shop(db, ctx, org_id)
    if not can_see(ctx, org, "billing"):
        await admin_log.refuse(db, admin=ctx.admin, action="discount_removed", org_id=org_id, ip=ctx.ip,
                               detail="You can't see this shop's billing, so ask its account owner")
    before = {"pct": float(org.discount_pct or 0), "until": _iso(org.discount_until)}
    org.discount_pct, org.discount_until, org.discount_reason = 0, None, None
    await admin_log.write(db, admin=ctx.admin, action="discount_removed", org_id=org.id, before=before,
                          after={"pct": 0}, reason=data.reason, ip=ctx.ip)
    await db.commit()
    return {"ok": True}


class TrialIn(BaseModel):
    days: int = Field(ge=1, le=90)
    reason: str = Field(min_length=3, max_length=300)


@private.post("/tenants/{org_id}/extend-trial")
async def extend_trial(org_id: str, data: TrialIn, ctx: AdminCtx = Depends(require_admin_perm("extend_trial")),
                       db: AsyncSession = Depends(get_db)):
    org = await get_shop(db, ctx, org_id)
    payload = {"days": data.days}
    # The cap counts this person's extensions on this shop over 90 days, not one call at a time.
    recent = (await db.execute(select(AdminActionLog.after).where(
        AdminActionLog.org_id == org.id, AdminActionLog.admin_id == ctx.admin.id,
        AdminActionLog.action == "extend_trial", AdminActionLog.outcome == "allowed",
        AdminActionLog.at >= datetime.utcnow() - timedelta(days=90)))).scalars().all()
    already = sum(int((a or {}).get("days") or 0) for a in recent)
    why = over_trial_limit(ctx, already + data.days)
    if why:
        return await _request(db, ctx, org, "extend_trial", payload, data.reason, why)
    before, after = await _apply(db, org, "extend_trial", payload)
    await admin_log.write(db, admin=ctx.admin, action="extend_trial", org_id=org.id, before=before, after=after,
                          reason=data.reason, ip=ctx.ip)
    await db.commit()
    return {"status": "applied", "sub_end": after["sub_end"], "trial_ends_at": after["trial_ends_at"]}


class PlanIn(BaseModel):
    plan: str
    reason: str = Field(min_length=3, max_length=300)


@private.post("/tenants/{org_id}/plan")
async def change_plan(org_id: str, data: PlanIn, ctx: AdminCtx = Depends(admin_ctx),
                      db: AsyncSession = Depends(get_db)):
    if not (ctx.can("change_plan") or ctx.can("request_plan_change")):
        await admin_log.refuse(db, admin=ctx.admin, action="plan_change", org_id=org_id, ip=ctx.ip)
    if data.plan not in PLANS:
        raise HTTPException(422, f"Plan must be one of {', '.join(PLANS)}")
    org = await get_shop(db, ctx, org_id)
    payload = {"plan": data.plan}
    if not ctx.can("change_plan"):
        return await _request(db, ctx, org, "plan_change", payload, data.reason, "Plan changes need the owner's OK")
    before, after = await _apply(db, org, "plan_change", payload)
    await admin_log.write(db, admin=ctx.admin, action="plan_change", org_id=org.id, before=before, after=after,
                          reason=data.reason, ip=ctx.ip)
    await db.commit()
    return {"status": "applied"}


# ── Account actions ─────────────────────────────────────────────────────────
class SuspendIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    message: Optional[str] = Field(None, max_length=500)


@private.post("/tenants/{org_id}/suspend")
async def suspend(org_id: str, data: SuspendIn, ctx: AdminCtx = Depends(require_admin_perm("suspend")),
                  db: AsyncSession = Depends(get_db)):
    org = await get_shop(db, ctx, org_id)
    before = {"sub_status": org.sub_status}
    org.sub_status, org.suspend_message = "suspended", (data.message or "").strip() or None
    await _boot_staff(db, org.id)
    await admin_log.write(db, admin=ctx.admin, action="suspend", org_id=org.id, before=before,
                          after={"sub_status": "suspended", "message": org.suspend_message}, reason=data.reason, ip=ctx.ip)
    await db.commit()
    return {"ok": True}


class UnsuspendIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    status: Optional[str] = None     # default: what it was before the suspension


@private.post("/tenants/{org_id}/unsuspend")
async def unsuspend(org_id: str, data: UnsuspendIn, ctx: AdminCtx = Depends(require_admin_perm("suspend")),
                    db: AsyncSession = Depends(get_db)):
    org = await get_shop(db, ctx, org_id)
    if org.sub_status != "suspended":
        raise HTTPException(409, "This shop isn't suspended")
    status = data.status
    if status is None:
        last = (await db.execute(select(AdminActionLog.before).where(
            AdminActionLog.org_id == org.id, AdminActionLog.action == "suspend", AdminActionLog.outcome == "allowed")
            .order_by(AdminActionLog.id.desc()).limit(1))).scalar()
        status = (last or {}).get("sub_status") or "trial"
    if status not in ("active", "trial"):
        status = "trial"
    data.status = status
    org.sub_status, org.suspend_message = status, None
    await admin_log.write(db, admin=ctx.admin, action="unsuspend", org_id=org.id, before={"sub_status": "suspended"},
                          after={"sub_status": data.status}, reason=data.reason, ip=ctx.ip)
    await db.commit()
    return {"ok": True}


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    kind: str = "note"


@private.post("/tenants/{org_id}/notes", status_code=201)
async def add_note(org_id: str, data: NoteIn, ctx: AdminCtx = Depends(require_admin_perm("add_notes")),
                   db: AsyncSession = Depends(get_db)):
    if data.kind not in ("note", "call", "email"):
        raise HTTPException(422, "A note is a note, call or email")
    org = await get_shop(db, ctx, org_id)
    note = AdminShopNote(org_id=org.id, admin_id=ctx.admin.id, kind=data.kind, body=data.body.strip())
    db.add(note)
    if data.kind in ("call", "email"):
        org.last_contacted_at = datetime.utcnow()
    await admin_log.write(db, admin=ctx.admin, action="note", org_id=org.id, after={"kind": data.kind}, ip=ctx.ip)
    await db.commit()
    return {"id": note.id}


class AssignIn(BaseModel):
    admin_id: str
    reason: str = Field(min_length=3, max_length=300)


@private.post("/tenants/{org_id}/assign")
async def assign(org_id: str, data: AssignIn, ctx: AdminCtx = Depends(require_admin_perm("assign_shops")),
                 db: AsyncSession = Depends(get_db)):
    org = await get_shop(db, ctx, org_id)
    to = (await db.execute(select(PlatformAdmin).where(PlatformAdmin.id == data.admin_id,
                                                       PlatformAdmin.active.is_(True)))).scalar_one_or_none()
    if not to:
        raise HTTPException(404, "That person isn't an active admin")
    if not ctx.is_owner and org.assigned_admin_id not in (None, ctx.admin.id):
        await admin_log.refuse(db, admin=ctx.admin, action="assign", org_id=org.id, ip=ctx.ip,
                               detail="Only the shop's current owner can hand it on")
    db.add(AdminAssignmentLog(org_id=org.id, from_admin_id=org.assigned_admin_id, to_admin_id=to.id,
                              by_admin_id=ctx.admin.id, reason=data.reason))
    before = {"assigned_admin_id": org.assigned_admin_id}
    org.assigned_admin_id = to.id
    await admin_log.write(db, admin=ctx.admin, action="assign", org_id=org.id, before=before,
                          after={"assigned_admin_id": to.id}, reason=data.reason, ip=ctx.ip)
    await db.commit()
    return {"ok": True}


class EmployeeWindow(BaseModel):
    access_start: Optional[str] = None
    access_end: Optional[str] = None
    active: Optional[bool] = None
    reason: str = Field(min_length=3, max_length=300)


@private.patch("/tenants/{org_id}/employees/{emp_id}")
async def patch_employee_window(org_id: str, emp_id: str, data: EmployeeWindow,
                                ctx: AdminCtx = Depends(require_admin_perm("edit_staff_access")),
                                db: AsyncSession = Depends(get_db)):
    org = await get_shop(db, ctx, org_id)
    emp = (await db.execute(select(Employee).where(Employee.id == emp_id, Employee.org_id == org.id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    before = {"access_start": _iso(emp.access_start), "access_end": _iso(emp.access_end), "active": emp.active}
    p = data.model_dump(exclude_unset=True)
    if "access_start" in p:
        emp.access_start = _parse_date(p["access_start"])
    if "access_end" in p:
        emp.access_end = _parse_date(p["access_end"])
    if p.get("active") is not None:
        emp.active = bool(p["active"])
    emp.token_version = (emp.token_version or 0) + 1
    await admin_log.write(db, admin=ctx.admin, action="staff_access", org_id=org.id, target=emp.id, before=before,
                          after={"access_start": _iso(emp.access_start), "access_end": _iso(emp.access_end),
                                 "active": emp.active}, reason=data.reason, ip=ctx.ip)
    await db.commit()
    return {"ok": True}


# ── Coupons ──────────────────────────────────────────────────────────────────
class CouponIn(BaseModel):
    code: Optional[str] = Field(None, max_length=32)
    percent: float
    plan: Optional[str] = None
    expires_at: Optional[str] = None
    note: Optional[str] = Field(None, max_length=300)


@private.get("/coupons")
async def list_coupons(ctx: AdminCtx = Depends(require_admin_perm("create_codes")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(PlatformCoupon).order_by(PlatformCoupon.created_at.desc()).limit(500))).scalars()
    return {"items": [{"id": c.id, "code": c.code, "percent": c.percent, "plan": c.plan, "active": c.active,
                       "expires_at": _iso(c.expires_at), "note": c.note} for c in rows]}


@private.post("/coupons", status_code=201)
async def create_coupon(data: CouponIn, ctx: AdminCtx = Depends(require_admin_perm("create_codes")),
                        db: AsyncSession = Depends(get_db)):
    import secrets
    from backend.services.account_id import ALPHABET
    code = (data.code or "").strip().upper() or ("SAVE" + "".join(secrets.choice(ALPHABET) for _ in range(6)))
    if not _CODE_RE.match(code):
        raise HTTPException(422, "Codes are 3 to 32 letters and numbers, no spaces or dashes")
    if not math.isfinite(data.percent) or not (0 < data.percent <= 100):
        raise HTTPException(422, "A discount is between 0 and 100%")
    if data.plan and data.plan not in PLANS:
        raise HTTPException(422, f"Plan must be one of {', '.join(PLANS)}")
    expires = _parse_date(data.expires_at)
    why = over_discount_limit(ctx, data.percent, expires)
    if why:
        await admin_log.refuse(db, admin=ctx.admin, action="create_code", ip=ctx.ip, after={"code": code},
                               detail=why)
    c = PlatformCoupon(code=code, percent=round(data.percent, 2), plan=data.plan or None, expires_at=expires,
                       note=data.note)
    db.add(c)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "That code already exists")
    await admin_log.write(db, admin=ctx.admin, action="create_code", target=code,
                          after={"percent": c.percent, "plan": c.plan, "expires_at": _iso(expires)}, ip=ctx.ip)
    await db.commit()
    return {"ok": True, "code": code}


class ApplyCouponIn(BaseModel):
    code: str = Field(max_length=40)


@private.post("/tenants/{org_id}/apply-coupon")
async def apply_coupon(org_id: str, data: ApplyCouponIn, ctx: AdminCtx = Depends(require_admin_perm("give_discount")),
                       db: AsyncSession = Depends(get_db)):
    org = await get_shop(db, ctx, org_id)
    code = (data.code or "").strip().upper()
    c = (await db.execute(select(PlatformCoupon).where(PlatformCoupon.code == code))).scalar_one_or_none()
    if not c or not c.active:
        raise HTTPException(404, "Coupon not found")
    if c.expires_at and c.expires_at < date.today():
        raise HTTPException(400, "Coupon has expired")
    if c.plan and c.plan != org.plan:
        raise HTTPException(400, f"This coupon only applies to the {c.plan} plan")
    if not can_see(ctx, org, "billing"):
        await admin_log.refuse(db, admin=ctx.admin, action="apply_code", org_id=org_id, ip=ctx.ip,
                               detail="You can't see this shop's billing, so ask its account owner")
    payload = {"pct": float(c.percent), "until": _iso(c.expires_at), "reason": f"Coupon {c.code}"}
    why = over_discount_limit(ctx, payload["pct"], c.expires_at)
    if why:   # a coupon doesn't get round someone's discount limit
        return await _request(db, ctx, org, "discount", payload, f"Coupon {c.code}", why)
    before, after = await _apply(db, org, "discount", payload)
    await admin_log.write(db, admin=ctx.admin, action="apply_code", org_id=org.id, before=before, after=after,
                          reason=f"Coupon {c.code}", ip=ctx.ip)
    await db.commit()
    return {"ok": True, "status": "applied", "discount_pct": float(org.discount_pct or 0)}


# ── Approvals ────────────────────────────────────────────────────────────────
def _req_row(r: AdminApprovalRequest, names, shops) -> dict:
    status = r.status
    if status == "pending" and r.expires_at and r.expires_at < datetime.utcnow():
        status = "expired"
    return {"id": r.id, "action": r.action, "payload": r.payload, "reason": r.reason, "status": status,
            "requested_by": names.get(r.requester_id), "org_id": r.org_id, "shop": shops.get(r.org_id),
            "created_at": _iso(r.created_at), "decided_by": names.get(r.decided_by), "decided_at": _iso(r.decided_at)}


@private.get("/approvals")
async def list_approvals(status: str = "pending", ctx: AdminCtx = Depends(admin_ctx), db: AsyncSession = Depends(get_db)):
    q = select(AdminApprovalRequest).order_by(AdminApprovalRequest.created_at.desc()).limit(200)
    if status == "pending":
        q = q.where(AdminApprovalRequest.status == "pending", AdminApprovalRequest.expires_at > datetime.utcnow())
    elif status != "all":
        q = q.where(AdminApprovalRequest.status == status)
    if not ctx.can("approve_requests"):
        q = q.where(AdminApprovalRequest.requester_id == ctx.admin.id)
    rows = (await db.execute(q)).scalars().all()
    shops = dict((await db.execute(select(Organisation.id, Organisation.name).where(
        Organisation.id.in_([r.org_id for r in rows])))).all()) if rows else {}
    names = await _admin_names(db)
    return {"items": [_req_row(r, names, shops) for r in rows]}


class DecideIn(BaseModel):
    decision: str
    note: Optional[str] = Field(None, max_length=300)


@private.post("/approvals/{req_id}/decide")
async def decide(req_id: str, data: DecideIn, ctx: AdminCtx = Depends(require_admin_perm("approve_requests")),
                 db: AsyncSession = Depends(get_db)):
    if data.decision not in ("approve", "decline"):
        raise HTTPException(422, "Approve or decline")
    r = (await db.execute(select(AdminApprovalRequest).where(AdminApprovalRequest.id == req_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Request not found")
    if r.requester_id == ctx.admin.id and not ctx.is_owner:
        raise HTTPException(403, "Someone else has to approve your own request")
    org = await get_shop(db, ctx, r.org_id)          # the approver's own scope applies too
    payload = r.payload or {}
    if data.decision == "approve" and r.action == "discount" and payload.get("until") \
            and date.fromisoformat(payload["until"]) < date.today():
        raise HTTPException(409, "That discount's end date has passed. Ask for a new one.")
    # Claim the request atomically, so two clicks can't apply it twice.
    now = datetime.utcnow()
    claimed = await db.execute(update(AdminApprovalRequest).where(
        AdminApprovalRequest.id == r.id, AdminApprovalRequest.status == "pending",
        AdminApprovalRequest.expires_at > now).values(
        status="approved" if data.decision == "approve" else "declined", decided_by=ctx.admin.id, decided_at=now))
    if claimed.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "This request is no longer waiting")
    await db.refresh(r)
    before = after = None
    if data.decision == "approve":
        before, after = await _apply(db, org, r.action, payload)
    await admin_log.write(db, admin=ctx.admin, action=f"{data.decision}_{r.action}", org_id=r.org_id, target=r.id,
                          before=before, after=after, reason=data.note or r.reason, ip=ctx.ip)
    await db.commit()
    return {"status": r.status}


@private.post("/approvals/{req_id}/cancel")
async def cancel_request(req_id: str, ctx: AdminCtx = Depends(admin_ctx), db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(AdminApprovalRequest).where(AdminApprovalRequest.id == req_id,
                                                            AdminApprovalRequest.requester_id == ctx.admin.id))).scalar_one_or_none()
    if not r or r.status != "pending":
        raise HTTPException(404, "Request not found")
    r.status = "cancelled"
    await admin_log.write(db, admin=ctx.admin, action=f"cancel_{r.action}", org_id=r.org_id, target=r.id, ip=ctx.ip)
    await db.commit()
    return {"ok": True}


# ── Admins and roles ─────────────────────────────────────────────────────────
def _role_row(r: AdminRole) -> dict:
    return {"id": r.id, "name": r.name, "builtin": r.builtin, "permissions": r.permissions or {},
            "field_levels": r.field_levels or {}, "scope": r.scope or {}, "limits": r.limits or {}}


async def _owner_count(db) -> int:
    owner_role = (await db.execute(select(AdminRole.id).where(AdminRole.builtin == "owner"))).scalar()
    return (await db.execute(select(func.count()).select_from(PlatformAdmin).where(
        PlatformAdmin.active.is_(True),
        or_(PlatformAdmin.role_id.is_(None), PlatformAdmin.role_id == owner_role)))).scalar()


async def _role_for(db, key_or_id: str) -> AdminRole:
    await ensure_builtin_roles(db)
    r = (await db.execute(select(AdminRole).where(or_(AdminRole.id == key_or_id, AdminRole.builtin == key_or_id)))).scalar_one_or_none()
    if not r:
        raise HTTPException(422, "Unknown role")
    return r


def _grants_more(ctx: AdminCtx, role: AdminRole) -> bool:
    """True when `role` gives anything the acting admin doesn't have (self-escalation)."""
    return wider_than(ctx, _role_row(role))


@private.get("/admins")
async def list_admins(ctx: AdminCtx = Depends(require_admin_perm("manage_admins")), db: AsyncSession = Depends(get_db)):
    await ensure_builtin_roles(db)
    roles = {r.id: r for r in (await db.execute(select(AdminRole))).scalars()}
    book = dict((await db.execute(select(Organisation.assigned_admin_id, func.count())
                                  .where(Organisation.assigned_admin_id.isnot(None))
                                  .group_by(Organisation.assigned_admin_id))).all())
    admins = (await db.execute(select(PlatformAdmin).order_by(PlatformAdmin.name))).scalars().all()
    await db.commit()
    return {"items": [{"id": a.id, "name": a.name, "email": a.email, "active": a.active,
                       "role": roles[a.role_id].name if a.role_id in roles else "Owner",
                       "role_id": a.role_id, "two_step": bool(a.mfa_enrolled_at),
                       "last_login_at": _iso(a.last_login_at), "shops": book.get(a.id, 0)} for a in admins]}


@private.get("/team")
async def team(ctx: AdminCtx = Depends(require_admin_perm("assign_shops")), db: AsyncSession = Depends(get_db)):
    """Names only, for handing a shop to a colleague."""
    rows = (await db.execute(select(PlatformAdmin.id, PlatformAdmin.name).where(PlatformAdmin.active.is_(True))
                             .order_by(PlatformAdmin.name))).all()
    return {"items": [{"id": r.id, "name": r.name} for r in rows]}


class NewAdminIn(BaseModel):
    email: str = Field(max_length=200)
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(max_length=200)
    role: str


@private.post("/admins", status_code=201)
async def create_admin(data: NewAdminIn, ctx: AdminCtx = Depends(require_admin_perm("manage_admins")),
                       db: AsyncSession = Depends(get_db)):
    if password_problem(data.password):
        raise HTTPException(422, password_problem(data.password))
    email = data.email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "Enter an email address")
    role = await _role_for(db, data.role)
    if _grants_more(ctx, role):
        await admin_log.refuse(db, admin=ctx.admin, action="create_admin", target=email, ip=ctx.ip,
                               detail="You can't give someone more access than you have")
    if (await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == email))).scalar_one_or_none():
        raise HTTPException(400, "That email already has an admin account")
    pw_hash = await asyncio.to_thread(hash_password, data.password)
    a = PlatformAdmin(email=email, name=data.name.strip(), password_hash=pw_hash, role_id=role.id)
    db.add(a)
    await db.flush()
    await admin_log.write(db, admin=ctx.admin, action="create_admin", target=email, after={"role": role.name}, ip=ctx.ip)
    await db.commit()
    return {"id": a.id}


class AdminPatch(BaseModel):
    role: Optional[str] = None
    name: Optional[str] = Field(None, max_length=200)
    reason: str = Field(min_length=3, max_length=300)


@private.patch("/admins/{admin_id}")
async def patch_admin(admin_id: str, data: AdminPatch, ctx: AdminCtx = Depends(require_admin_perm("manage_admins")),
                      db: AsyncSession = Depends(get_db)):
    a = (await db.execute(select(PlatformAdmin).where(PlatformAdmin.id == admin_id))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Admin not found")
    before = {"role_id": a.role_id, "name": a.name}
    if not ctx.is_owner and await _is_owner_admin(db, a):
        await admin_log.refuse(db, admin=ctx.admin, action="edit_admin", target=a.email, ip=ctx.ip,
                               detail="Only an owner can change an owner")
    if data.role:
        role = await _role_for(db, data.role)
        if _grants_more(ctx, role):
            await admin_log.refuse(db, admin=ctx.admin, action="change_role", target=a.email, ip=ctx.ip,
                                   detail="You can't give someone more access than you have")
        was_owner = await _is_owner_admin(db, a)
        if was_owner and role.builtin != "owner" and await _owner_count(db) <= 1:
            raise HTTPException(409, "Keep at least one owner")
        a.role_id = role.id          # read fresh on every request: the new rules apply at once
    if data.name:
        a.name = data.name.strip()
    await admin_log.write(db, admin=ctx.admin, action="edit_admin", target=a.email, before=before,
                          after={"role_id": a.role_id, "name": a.name}, reason=data.reason, ip=ctx.ip)
    await db.commit()
    return {"ok": True}


async def _is_owner_admin(db, a: PlatformAdmin) -> bool:
    if not a.role_id:
        return True
    return (await db.execute(select(AdminRole.builtin).where(AdminRole.id == a.role_id))).scalar() == "owner"


class DeactivateIn(BaseModel):
    reassign_to: Optional[str] = None
    reason: str = Field(min_length=3, max_length=300)


@private.post("/admins/{admin_id}/deactivate")
async def deactivate_admin(admin_id: str, data: DeactivateIn,
                           ctx: AdminCtx = Depends(require_admin_perm("manage_admins")),
                           db: AsyncSession = Depends(get_db)):
    a = (await db.execute(select(PlatformAdmin).where(PlatformAdmin.id == admin_id))).scalar_one_or_none()
    if not a or not a.active:
        raise HTTPException(404, "Admin not found")
    if await _is_owner_admin(db, a):
        if not ctx.is_owner:
            await admin_log.refuse(db, admin=ctx.admin, action="deactivate_admin", target=a.email, ip=ctx.ip,
                                   detail="Only an owner can remove an owner")
        if await _owner_count(db) <= 1:
            raise HTTPException(409, "Keep at least one owner")
    shops = (await db.execute(select(Organisation).where(Organisation.assigned_admin_id == a.id))).scalars().all()
    if shops:
        to = (await db.execute(select(PlatformAdmin).where(PlatformAdmin.id == (data.reassign_to or ""),
                                                           PlatformAdmin.active.is_(True),
                                                           PlatformAdmin.id != a.id))).scalar_one_or_none()
        if not to:
            raise HTTPException(409, f"{a.name} still has {len(shops)} shops. Choose who takes them over.")
        for org in shops:
            db.add(AdminAssignmentLog(org_id=org.id, from_admin_id=a.id, to_admin_id=to.id, by_admin_id=ctx.admin.id,
                                      reason=f"{a.name} left: {data.reason}"))
            org.assigned_admin_id = to.id
    a.active = False
    a.token_version = (a.token_version or 0) + 1
    await db.execute(update(AdminSession).where(AdminSession.admin_id == a.id, AdminSession.revoked_at.is_(None))
                     .values(revoked_at=datetime.utcnow()))
    await db.execute(update(AdminApprovalRequest).where(AdminApprovalRequest.requester_id == a.id,
                                                        AdminApprovalRequest.status == "pending")
                     .values(status="cancelled"))
    await admin_log.write(db, admin=ctx.admin, action="deactivate_admin", target=a.email,
                          after={"shops_moved": len(shops), "to": data.reassign_to}, reason=data.reason, ip=ctx.ip)
    await db.commit()
    return {"ok": True, "shops_moved": len(shops)}


@private.get("/roles")
async def list_roles(ctx: AdminCtx = Depends(require_admin_perm("manage_admins")), db: AsyncSession = Depends(get_db)):
    await ensure_builtin_roles(db)
    await db.commit()
    rows = (await db.execute(select(AdminRole).order_by(AdminRole.name))).scalars().all()
    return {"items": [_role_row(r) for r in rows],
            "catalog": {"permissions": {g: [{"code": c, "label": lbl} for c, lbl in items]
                                        for g, items in PERMISSIONS.items()},
                        "field_groups": list(FIELD_GROUPS), "levels": ["hidden", "own", "shown"],
                        "stages": ["trial", "paying", "lost", "suspended"], "builtin": list(BUILTIN_ROLES)}}


class RoleIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    permissions: dict = {}
    field_levels: dict = {}
    scope: dict = {}
    limits: dict = {}
    reason: str = Field(min_length=3, max_length=300)


def _clean_role(data: RoleIn) -> dict:
    perms = {k: True for k, v in (data.permissions or {}).items() if v and k in ALL_PERMS}
    levels = {g: lv for g, lv in (data.field_levels or {}).items() if g in FIELD_GROUPS and lv in ("hidden", "own", "shown")}
    sc = data.scope or {}
    scope = {}
    if sc.get("countries"):
        scope["countries"] = [str(c).upper()[:2] for c in sc["countries"] if str(c).strip()][:50]
    if sc.get("own_book"):
        scope["own_book"] = True
    if sc.get("stages") is not None:
        if not isinstance(sc["stages"], list):
            raise HTTPException(422, "Stages must be a list")
        scope["stages"] = [s for s in sc["stages"] if s in ("trial", "paying", "lost", "suspended")]
    if sc.get("countries") is not None and not isinstance(sc["countries"], list):
        raise HTTPException(422, "Countries must be a list")
    limits = {}
    for k in ("max_discount_pct", "max_discount_months", "max_trial_days"):
        v = (data.limits or {}).get(k)
        if v is not None:
            v = float(v)
            if not math.isfinite(v) or v < 0 or v > (100 if k == "max_discount_pct" else 36 if k == "max_discount_months" else 365):
                raise HTTPException(422, f"{k} is out of range")
            limits[k] = v
    return {"name": data.name.strip(), "permissions": perms, "field_levels": levels, "scope": scope, "limits": limits}


@private.post("/roles", status_code=201)
async def create_role(data: RoleIn, ctx: AdminCtx = Depends(require_admin_perm("edit_roles")),
                      db: AsyncSession = Depends(get_db)):
    spec = _clean_role(data)
    if wider_than(ctx, spec):
        await admin_log.refuse(db, admin=ctx.admin, action="create_role", target=spec["name"], ip=ctx.ip,
                               detail="You can't create a role with more access than you have")
    role = AdminRole(**spec)
    db.add(role)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "A role with that name exists")
    await admin_log.write(db, admin=ctx.admin, action="create_role", target=role.name, after=spec,
                          reason=data.reason, ip=ctx.ip)
    await db.commit()
    return {"id": role.id}


@private.patch("/roles/{role_id}")
async def edit_role(role_id: str, data: RoleIn, ctx: AdminCtx = Depends(require_admin_perm("edit_roles")),
                    db: AsyncSession = Depends(get_db)):
    role = (await db.execute(select(AdminRole).where(AdminRole.id == role_id))).scalar_one_or_none()
    if not role:
        raise HTTPException(404, "Role not found")
    if role.builtin == "owner":
        raise HTTPException(409, "The owner role can't be edited")
    spec = _clean_role(data)
    if wider_than(ctx, spec):
        await admin_log.refuse(db, admin=ctx.admin, action="edit_role", target=role.name, ip=ctx.ip,
                               detail="You can't give a role more access than you have")
    before = _role_row(role)
    for k, v in spec.items():
        setattr(role, k, v)
    # Roles are read fresh on every request, so everyone with it gets the new rules at once.
    await admin_log.write(db, admin=ctx.admin, action="edit_role", target=role.name, before=before, after=spec,
                          reason=data.reason, ip=ctx.ip)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "A role with that name exists")
    return {"ok": True}


@private.get("/log")
async def read_log(org_id: Optional[str] = None, limit: int = 100, before_id: Optional[int] = None,
                   ctx: AdminCtx = Depends(require_admin_perm("read_log")), db: AsyncSession = Depends(get_db)):
    q = select(AdminActionLog).order_by(AdminActionLog.id.desc()).limit(max(1, min(int(limit), 500)))
    if org_id:
        q = q.where(AdminActionLog.org_id == org_id)
    if before_id:
        q = q.where(AdminActionLog.id < before_id)
    rows = (await db.execute(q)).scalars().all()
    return {"items": [{"id": r.id, "at": _iso(r.at), "by": r.admin_email, "action": r.action, "outcome": r.outcome,
                       "org_id": r.org_id, "target": r.target, "reason": r.reason, "ip": r.ip,
                       # Old and new values can hold money: owners only.
                       **({"before": r.before, "after": r.after} if ctx.is_owner else {})} for r in rows],
            "chain_ok": await admin_log.verify_chain(db)}


router.include_router(public)
router.include_router(private)
