"""Build 144ce — the separate /admin platform portal API.

Every route here requires a platform-admin token (scope="platform"), which a
tenant login can never produce. This is where you (the operator) see all
organisations by lifecycle segment, control each account's term and per-employee
access windows, set discounts, and generate coupon codes.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, Organisation, PlatformAdmin, PlatformCoupon
from backend.services.auth import verify_password
from backend.services.platform_auth import (
    create_platform_token,
    get_current_platform_admin,
)
from backend.services.ratelimit import client_ip as _ip
from backend.services.ratelimit import enforce as _rate

router = APIRouter(prefix="/api/admin", tags=["platform-admin"])

NEW_TRIAL_DAYS = 14


def _parse_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _segment(org: Organisation) -> str:
    """Map raw sub_status → the portal's lifecycle buckets."""
    st = org.sub_status or "trial"
    if st == "active":
        return "paid"
    if st == "churned":
        return "lost_paid"
    if st == "trial_lost":
        return "lost_trial"
    if st == "suspended":
        return "suspended"
    # trial: "new" if it started within the first two weeks
    if org.sub_start and (date.today() - org.sub_start).days <= NEW_TRIAL_DAYS:
        return "new"
    return "trial"


# ── Auth ─────────────────────────────────────────────────────────
class AdminLogin(BaseModel):
    email: str
    password: str


@router.post("/login")
async def admin_login(data: AdminLogin, request: Request, db: AsyncSession = Depends(get_db)):
    _rate("admin_login", f"{_ip(request)}:{(data.email or '').lower()}", limit=10, window_sec=300)
    _rate("admin_login_acct", (data.email or "").strip().lower(), limit=20, window_sec=900)
    admin = (await db.execute(select(PlatformAdmin).where(
        PlatformAdmin.email == (data.email or "").lower()))).scalar_one_or_none()
    if not admin or not admin.active or not verify_password(data.password, admin.password_hash):
        raise HTTPException(401, "Invalid platform credentials")
    return {"access_token": create_platform_token(admin.id), "token_type": "bearer", "name": admin.name}


@router.get("/me")
async def admin_me(admin: PlatformAdmin = Depends(get_current_platform_admin)):
    return {"id": admin.id, "name": admin.name, "email": admin.email}


# ── Tenants ──────────────────────────────────────────────────────
def _tenant_row(org: Organisation, emp_count: int) -> dict:
    return {
        "id": org.id, "name": org.name, "plan": org.plan,
        "sub_status": org.sub_status, "segment": _segment(org),
        "sub_start": org.sub_start.isoformat() if org.sub_start else None,
        "sub_end": org.sub_end.isoformat() if org.sub_end else None,
        "discount_pct": org.discount_pct or 0.0,
        "country": org.country, "employees": emp_count,
        "created_at": org.created_at.isoformat() if getattr(org, "created_at", None) else None,
    }


@router.get("/stats")
async def admin_stats(db: AsyncSession = Depends(get_db),
                      admin: PlatformAdmin = Depends(get_current_platform_admin)):
    orgs = (await db.execute(select(Organisation))).scalars().all()
    counts = {"new": 0, "trial": 0, "paid": 0, "lost_trial": 0, "lost_paid": 0, "suspended": 0}
    for o in orgs:
        counts[_segment(o)] = counts.get(_segment(o), 0) + 1
    return {"total": len(orgs), "segments": counts}


@router.get("/tenants")
async def list_tenants(segment: Optional[str] = None, q: Optional[str] = None,
                       db: AsyncSession = Depends(get_db),
                       admin: PlatformAdmin = Depends(get_current_platform_admin)):
    orgs = (await db.execute(select(Organisation).order_by(Organisation.name))).scalars().all()
    # employee counts in one grouped query
    rows = (await db.execute(select(Employee.org_id, func.count()).group_by(Employee.org_id))).all()
    emp_counts = {oid: n for oid, n in rows}
    out = []
    for o in orgs:
        if segment and _segment(o) != segment:
            continue
        if q and q.lower() not in (o.name or "").lower():
            continue
        out.append(_tenant_row(o, emp_counts.get(o.id, 0)))
    return {"items": out}


@router.get("/tenants/{org_id}")
async def tenant_detail(org_id: str, db: AsyncSession = Depends(get_db),
                        admin: PlatformAdmin = Depends(get_current_platform_admin)):
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Tenant not found")
    emps = (await db.execute(select(Employee).where(
        Employee.org_id == org_id).order_by(Employee.name))).scalars().all()
    row = _tenant_row(org, len(emps))
    row["admin_notes"] = org.admin_notes
    row["employees_detail"] = [{
        "id": e.id, "name": e.name, "email": e.email, "active": e.active,
        "access_start": e.access_start.isoformat() if e.access_start else None,
        "access_end": e.access_end.isoformat() if e.access_end else None,
    } for e in emps]
    return row


class TenantPatch(BaseModel):
    plan: Optional[str] = None
    sub_status: Optional[str] = None
    sub_start: Optional[str] = None
    sub_end: Optional[str] = None
    discount_pct: Optional[float] = None
    admin_notes: Optional[str] = None


@router.patch("/tenants/{org_id}")
async def patch_tenant(org_id: str, data: TenantPatch, db: AsyncSession = Depends(get_db),
                       admin: PlatformAdmin = Depends(get_current_platform_admin)):
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Tenant not found")
    p = data.model_dump(exclude_unset=True)
    if "sub_status" in p:
        valid = {"trial", "active", "trial_lost", "churned", "suspended"}
        if p["sub_status"] not in valid:
            raise HTTPException(422, f"sub_status must be one of {sorted(valid)}")
        org.sub_status = p["sub_status"]
    if "plan" in p:
        org.plan = p["plan"]
    if "sub_start" in p:
        org.sub_start = _parse_date(p["sub_start"])
    if "sub_end" in p:
        org.sub_end = _parse_date(p["sub_end"])
    if "discount_pct" in p:
        org.discount_pct = max(0.0, min(100.0, float(p["discount_pct"] or 0)))
    if "admin_notes" in p:
        org.admin_notes = p["admin_notes"]
    # Build 144ch: if this change blocks the account (suspended, or term ended in the
    # past), boot every live session immediately by bumping each employee's token
    # version — get_current_employee rejects tokens carrying a stale version.
    blocked_now = org.sub_status == "suspended" or (org.sub_end and org.sub_end < date.today())
    if blocked_now and ("sub_status" in p or "sub_end" in p):
        from sqlalchemy import update as _update
        await db.execute(
            _update(Employee).where(Employee.org_id == org_id)
            .values(token_version=func.coalesce(Employee.token_version, 0) + 1))
    await db.commit()
    return {"ok": True}


class EmployeeWindow(BaseModel):
    access_start: Optional[str] = None
    access_end: Optional[str] = None
    active: Optional[bool] = None


@router.patch("/tenants/{org_id}/employees/{emp_id}")
async def patch_employee_window(org_id: str, emp_id: str, data: EmployeeWindow,
                                db: AsyncSession = Depends(get_db),
                                admin: PlatformAdmin = Depends(get_current_platform_admin)):
    emp = (await db.execute(select(Employee).where(
        Employee.id == emp_id, Employee.org_id == org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    p = data.model_dump(exclude_unset=True)
    if "access_start" in p:
        emp.access_start = _parse_date(p["access_start"])
    if "access_end" in p:
        emp.access_end = _parse_date(p["access_end"])
    if "active" in p and p["active"] is not None:
        emp.active = bool(p["active"])
    emp.token_version = (emp.token_version or 0) + 1   # re-check on next request
    await db.commit()
    return {"ok": True}


# ── Coupons ──────────────────────────────────────────────────────
class CouponIn(BaseModel):
    code: Optional[str] = None       # auto-generated if omitted
    percent: float
    plan: Optional[str] = None
    expires_at: Optional[str] = None
    note: Optional[str] = None


@router.get("/coupons")
async def list_coupons(db: AsyncSession = Depends(get_db),
                       admin: PlatformAdmin = Depends(get_current_platform_admin)):
    rows = (await db.execute(select(PlatformCoupon).order_by(PlatformCoupon.created_at.desc()))).scalars().all()
    return {"items": [{
        "id": c.id, "code": c.code, "percent": c.percent, "plan": c.plan,
        "active": c.active, "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        "note": c.note,
    } for c in rows]}


@router.post("/coupons", status_code=201)
async def create_coupon(data: CouponIn, db: AsyncSession = Depends(get_db),
                        admin: PlatformAdmin = Depends(get_current_platform_admin)):
    import secrets
    code = (data.code or "").strip().upper() or ("SAVE" + secrets.token_hex(3).upper())
    if (await db.execute(select(PlatformCoupon).where(PlatformCoupon.code == code))).scalar_one_or_none():
        raise HTTPException(400, "That coupon code already exists")
    c = PlatformCoupon(code=code, percent=max(0.0, min(100.0, float(data.percent or 0))),
                       plan=data.plan or None, expires_at=_parse_date(data.expires_at), note=data.note)
    db.add(c)
    await db.commit()
    return {"ok": True, "code": code}


@router.post("/tenants/{org_id}/apply-coupon")
async def apply_coupon(org_id: str, body: dict, db: AsyncSession = Depends(get_db),
                       admin: PlatformAdmin = Depends(get_current_platform_admin)):
    """Apply a coupon to a tenant → sets that tenant's discount_pct (respecting an
    optional plan scope)."""
    code = (body.get("code") or "").strip().upper()
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Tenant not found")
    c = (await db.execute(select(PlatformCoupon).where(PlatformCoupon.code == code))).scalar_one_or_none()
    if not c or not c.active:
        raise HTTPException(404, "Coupon not found")
    if c.expires_at and c.expires_at < date.today():
        raise HTTPException(400, "Coupon has expired")
    if c.plan and c.plan != org.plan:
        raise HTTPException(400, f"This coupon only applies to the {c.plan} plan")
    org.discount_pct = c.percent
    await db.commit()
    return {"ok": True, "discount_pct": org.discount_pct}
