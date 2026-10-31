import asyncio
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, Role, Task
from backend.services import audit
from backend.services import multi_asset as _ma
from backend.services.auth import (get_current_employee, hash_password, is_old_default_password, norm_email,
                                   require_perm, verify_password)

router = APIRouter(prefix="/api/staff", tags=["staff"])


def _parse_date(s: Optional[str]):
    """Parse a 'YYYY-MM-DD' string into a date; empty/invalid -> None."""
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


class EmployeeIn(BaseModel):
    name: str
    email: str
    # No default: an omitted password means "email them a link to set their own".
    password: Optional[str] = None
    phone: Optional[str] = None
    role_id: Optional[str] = None
    location_id: Optional[str] = None
    wage_type: str = "fixed"
    wage_value: float = 0.0
    commission_labour: float = 0.0
    commission_parts: float = 0.0
    weekly_hours: Optional[float] = None
    hourly_basis: Optional[str] = None    # labour|attendance (null = labour)
    track_job_hours: bool = True          # show the work-order labour timer
    track_attendance: bool = True         # show the shop clock-in / clock-out
    # build 144k: general-info fields
    position: Optional[str] = None        # job title
    hired_at: Optional[str] = None        # YYYY-MM-DD
    tax_id: Optional[str] = None          # Employee ITN
    public_name: Optional[str] = None     # name on public documents
    note: Optional[str] = None

class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    current_password: Optional[str] = None   # required to change your OWN email
    phone: Optional[str] = None
    role_id: Optional[str] = None
    location_id: Optional[str] = None
    wage_type: Optional[str] = None
    wage_value: Optional[float] = None
    commission_labour: Optional[float] = None
    commission_parts: Optional[float] = None
    weekly_hours: Optional[float] = None
    hourly_basis: Optional[str] = None
    track_job_hours: Optional[bool] = None
    track_attendance: Optional[bool] = None
    position: Optional[str] = None
    hired_at: Optional[str] = None
    tax_id: Optional[str] = None
    public_name: Optional[str] = None
    note: Optional[str] = None
    active: Optional[bool] = None

class PasswordReset(BaseModel):
    password: str
    current_password: Optional[str] = None   # required to change your OWN password

class RoleIn(BaseModel):
    name: str
    permissions: dict = {}
    # Build 83 — record-level access conditions
    contact_scope: str = "all"       # all|own
    order_scope: str = "all"         # all|own
    warehouse_scope: str = "all"     # all|selected
    warehouse_ids: list[str] = []
    payroll_visibility_days: Optional[int] = None


class TaskIn(BaseModel):
    title: str
    description: Optional[str] = None
    assigned_to_id: Optional[str] = None
    order_id: Optional[str] = None
    priority: str = "normal"
    due_at: Optional[str] = None

class TaskUpdate(BaseModel):
    status: str  # todo|in_progress|done

@router.get("/employees")
async def list_employees(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(
        select(Employee).where(Employee.org_id == current.org_id, Employee.active == True)  # noqa: E712
    )
    employees = result.scalars().all()
    from backend.services.auth import has_perm
    # Pay and tax ids are payroll data: only roles allowed to view payroll see colleagues' values.
    see_pay = await has_perm(db, current, "can_view_payroll")
    see_own = see_pay or await has_perm(db, current, "can_view_own_compensation")
    from backend.services.payroll_views import in_scope, payroll_scope_ids
    scope = await payroll_scope_ids(db, current)   # E3: colleagues' pay only inside the payroll scope
    listed = await _picker_lists(db, current.org_id, employees)

    def pay(e, v):
        if e.id == current.id:
            return v if see_own else None
        return v if (see_pay and in_scope(scope, e.id)) else None
    return [{"id": e.id, "name": e.name, "email": e.email,
             "phone": e.phone, "wage_type": pay(e, e.wage_type), "wage_value": pay(e, e.wage_value),
             "commission_labour": pay(e, e.commission_labour), "commission_parts": pay(e, e.commission_parts),
             "weekly_hours": e.weekly_hours, "hourly_basis": e.hourly_basis or "labour",
             "track_job_hours": e.track_job_hours, "track_attendance": e.track_attendance,
             "position": e.position, "hired_at": e.hired_at.isoformat() if e.hired_at else None,
             "tax_id": pay(e, e.tax_id), "public_name": e.public_name, "note": e.note,
             "location_id": e.location_id, "role_id": e.role_id, "active": e.active,
             # whether their role shows them in the work order's Managers / Assignee pickers
             "in_managers": listed[e.id][0], "in_assignees": listed[e.id][1]} for e in employees]


async def _picker_lists(db, org_id, employees) -> dict:
    """{employee_id: (in Managers list, in Assignee list)} from each person's role."""
    from backend.models import Organisation, Role
    from backend.services.permissions import role_allows
    owner_id = (await db.execute(select(Organisation.owner_id).where(Organisation.id == org_id))).scalar_one_or_none()
    role_ids = {e.role_id for e in employees if e.role_id}
    roles = {r.id: r for r in (await db.execute(select(Role).where(Role.id.in_(role_ids)))).scalars().all()} if role_ids else {}
    out = {}
    for e in employees:
        if not e.role_id or e.id == owner_id:
            out[e.id] = (True, True)
        else:
            r = roles.get(e.role_id)
            out[e.id] = (bool(r) and role_allows(r, "display_in_managers"),
                         bool(r) and role_allows(r, "display_in_assignees"))
    return out

PAY_FIELDS = ("wage_type", "wage_value", "commission_labour", "commission_parts", "tax_id")
# Compensation edits are audited with their old and new values.
WAGE_AUDIT_FIELDS = ("wage_type", "wage_value", "hourly_basis", "weekly_hours",
                     "commission_labour", "commission_parts")


# ── Privilege guards ──────────────────────────────────────────────
# require_perm is default-allow, so a role's access is "everything except what it denies"
# (permissions set to False). No role, and the owner's {"all": "write"} role, deny nothing.
# An actor may only assign a role, or act on a teammate, whose denied set includes
# everything the actor is denied — i.e. never hand out or take over more access than they have.

async def _denied_for_role(db: AsyncSession, org_id: str, role_id: Optional[str]) -> set:
    if not role_id:
        return set()
    role = (await db.execute(select(Role).where(Role.id == role_id, Role.org_id == org_id))).scalar_one_or_none()
    if not role:
        raise HTTPException(400, "Unknown role")
    from backend.services.permissions import ALL_CODES, role_allows
    return {c for c in ALL_CODES if not role_allows(role, c)}


async def _assert_can_assign_role(db: AsyncSession, current: Employee, role_id: Optional[str]) -> None:
    owner = await _is_owner(db, current)
    # "No role" means no limits at all: every permission and no location, record or payroll scope.
    # Only the owner can hand that out — a manager whose role has every permission but is scoped
    # to some locations or their own records used to pass the permission comparison below and
    # widen themselves (audit 2026-10-29, H4).
    if not role_id and not owner:
        raise HTTPException(403, "Only the shop owner can give someone unlimited access. Pick a role instead.")
    mine = set() if owner else await _denied_for_role(db, current.org_id, current.role_id)
    theirs = await _denied_for_role(db, current.org_id, role_id)
    if not mine <= theirs:
        raise HTTPException(403, "You can't assign a role with more access than your own")
    # Permissions alone aren't the whole story: a role also limits which locations, orders,
    # contacts and warehouses its holders see. Never hand out wider data scope than you have.
    if current.role_id and role_id and not await _is_owner(db, current):
        from backend.routers.rbac import _assert_scope_not_wider
        editor = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
        target = (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
        if editor and target:
            from backend.routers.rbac import PAYROLL_ACCESS
            from backend.services.permissions import role_allows
            _assert_scope_not_wider(editor, _ScopeView(target),
                                    grants_payroll=any(role_allows(target, c) for c in PAYROLL_ACCESS))


class _ScopeView:
    """A role's data-scope fields in the shape rbac._assert_scope_not_wider reads."""
    def __init__(self, role: Role):
        import json
        for f in ("order_scope", "contact_scope", "warehouse_scope", "location_scope", "payroll_scope"):
            setattr(self, f, getattr(role, f, None) or "all")
        self.payroll_employee_ids = getattr(role, "payroll_employee_ids", None) or []
        self.location_ids = json.loads(role.location_ids or "[]") if isinstance(role.location_ids, str) else (role.location_ids or [])


async def _is_owner(db: AsyncSession, emp: Employee) -> bool:
    from backend.services.auth import is_shop_owner
    return await is_shop_owner(db, emp)


async def _assert_current_password(current: Employee, password: Optional[str]) -> None:
    if not password or not await asyncio.to_thread(verify_password, password, current.password_hash):
        raise HTTPException(403, "Enter your current password to change this")


async def _assert_can_manage(db: AsyncSession, current: Employee, emp: Employee) -> None:
    if emp.id == current.id:
        return
    # The shop owner's account (password, 2-step, email, role, access) is theirs alone:
    # an equally-privileged teammate must not be able to take it over.
    if await _is_owner(db, emp):
        raise HTTPException(403, "Only the shop owner can change the owner's account")
    mine = set() if await _is_owner(db, current) else await _denied_for_role(db, current.org_id, current.role_id)
    try:
        theirs = await _denied_for_role(db, current.org_id, emp.role_id)
    except HTTPException:
        theirs = set()   # dangling role → treat as full access (the safe direction)
    if not mine <= theirs:
        raise HTTPException(403, "You can't change an account with more access than your own")


@router.post("/employees", status_code=201)
async def create_employee(
    data: EmployeeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_employees")),
):
    import secrets

    data.email = norm_email(data.email)
    existing = await db.execute(select(Employee).where(func.lower(Employee.email) == data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already in use")
    if data.password is not None and len(data.password) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if is_old_default_password(data.password):
        raise HTTPException(400, "Choose a different password")
    await _assert_can_assign_role(db, current, data.role_id)
    from backend.services.auth import has_perm
    from backend.services.payroll_views import payroll_scope_ids
    # A role limited to some people's payroll (E3) can't set a newcomer's pay: they're outside it.
    if not await has_perm(db, current, "can_view_payroll") or await payroll_scope_ids(db, current) is not None:
        defaults = EmployeeIn.model_fields
        for k in PAY_FIELDS:
            if k in defaults:
                setattr(data, k, defaults[k].default)
    emp = Employee(
        org_id=current.org_id,
        name=data.name, email=data.email,
        # Without a password, store a random one nobody knows; the welcome email lets
        # the teammate choose their own.
        password_hash=await asyncio.to_thread(hash_password, data.password or secrets.token_urlsafe(32)),
        phone=data.phone, role_id=data.role_id,
        location_id=data.location_id,
        wage_type=data.wage_type, wage_value=data.wage_value,
        commission_labour=data.commission_labour, commission_parts=data.commission_parts,
        weekly_hours=data.weekly_hours,
        hourly_basis=data.hourly_basis if data.hourly_basis in ("labour", "attendance") else "labour",
        track_job_hours=data.track_job_hours, track_attendance=data.track_attendance,
        position=data.position, tax_id=data.tax_id, public_name=data.public_name,
        note=data.note, hired_at=_parse_date(data.hired_at),
    )
    db.add(emp)
    await db.flush()
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="employee.create",
                       entity_type="employee", entity_id=emp.id,
                       diff={"email": emp.email, "role_id": emp.role_id,
                             **{k: getattr(emp, k) for k in WAGE_AUDIT_FIELDS}})
    await db.commit()
    await db.refresh(emp)
    invite_sent = False
    if not data.password:
        await _send_set_password_email(db, emp, current.org_id)
        invite_sent = True
    return {"id": emp.id, "name": emp.name, "invite_sent": invite_sent}


MIN_PASSWORD_LEN = 8
SET_PASSWORD_TTL_MIN = 60 * 24 * 7   # same 7 days as teammate invites


async def _send_set_password_email(db: AsyncSession, emp: Employee, org_id: str) -> None:
    """Email a new teammate a single-use link to choose their password (reuses the
    password-reset token + /reset-password page, with an invite-length expiry)."""
    from backend.models import Organisation
    from backend.routers.auth import _link
    from backend.services import tokens as _tok
    from backend.services.notifications import send_direct_email
    raw = await _tok.issue(db, employee_id=emp.id, kind="reset", ttl_min=SET_PASSWORD_TTL_MIN)
    await db.commit()
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one_or_none()
    url = _link(f"/reset-password?token={raw}")
    await send_direct_email(
        emp.email, f"You've been added to {org.name if org else 'your team'} on Tekly",
        f"Hi {emp.name},\n\nAn account has been created for you. Choose your password here:\n\n{url}\n\n"
        f"The link expires in 7 days and can be used once.")


@router.patch("/employees/{emp_id}")
async def update_employee(
    emp_id: str, data: EmployeeUpdate,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_employees")),
):
    emp = (await db.execute(select(Employee).where(Employee.id == emp_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    await _assert_can_manage(db, current, emp)
    payload = data.model_dump(exclude_unset=True)
    from backend.services.auth import has_perm
    from backend.services.payroll_views import in_scope, payroll_scope_ids
    from backend.services.auth import is_shop_owner
    own_pay = emp.id == current.id and current.role_id and not await is_shop_owner(db, current)
    if own_pay or not await has_perm(db, current, "can_view_payroll") \
            or not in_scope(await payroll_scope_ids(db, current), emp.id):
        # Pay is hidden from this role in list_employees (no payroll view, or outside its payroll
        # scope), so a full-form save would send blanks: never let it change compensation it can't see.
        # Nobody but the owner sets their own pay either (same rule as payroll's assert_not_self).
        for k in PAY_FIELDS:
            payload.pop(k, None)
    if "role_id" in payload and payload["role_id"] != emp.role_id:
        if emp.id == current.id and not await _is_owner(db, current):
            raise HTTPException(403, "You can't change your own role. Ask the owner.")
        await _assert_can_assign_role(db, current, payload["role_id"])
    current_password = payload.pop("current_password", None)
    if "email" in payload:
        payload["email"] = norm_email(payload["email"])
        if payload["email"] == norm_email(emp.email):
            payload.pop("email")
    if "email" in payload:
        if emp.id == current.id:
            # Changing your own sign-in email then resetting the password is an account takeover
            # for anyone holding a session token, so it needs the password too.
            await _assert_current_password(current, current_password)
        dupe = (await db.execute(select(Employee).where(
            func.lower(Employee.email) == payload["email"], Employee.id != emp.id))).scalar_one_or_none()
        if dupe:
            raise HTTPException(400, "Email already in use")
        emp.email_verified = False   # the new address hasn't been proven yet
    if "hourly_basis" in payload and payload["hourly_basis"] not in ("labour", "attendance"):
        payload["hourly_basis"] = "labour"
    if "hired_at" in payload:
        payload["hired_at"] = _parse_date(payload["hired_at"])
    changes = {k: {"from": getattr(emp, k), "to": v} for k, v in payload.items()
               if k in ("role_id", "email", "active", "location_id", *WAGE_AUDIT_FIELDS)
               and getattr(emp, k) != v}
    if "tax_id" in payload and payload["tax_id"] != emp.tax_id:
        changes["tax_id"] = {"changed": True}   # an identifier: record the change, not the value
    for k, v in payload.items():
        setattr(emp, k, v)
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="employee.update",
                       entity_type="employee", entity_id=emp.id,
                       diff={"fields": sorted(payload), **changes})
    await db.commit()
    return {"ok": True}


@router.post("/employees/{emp_id}/password")
async def reset_password(
    emp_id: str, data: PasswordReset,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_employees")),
):
    emp = (await db.execute(select(Employee).where(Employee.id == emp_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    await _assert_can_manage(db, current, emp)
    if emp.id == current.id:
        await _assert_current_password(current, data.current_password)
    if len(data.password) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if is_old_default_password(data.password):
        raise HTTPException(400, "Choose a different password")
    emp.password_hash = await asyncio.to_thread(hash_password, data.password)
    emp.token_version = (emp.token_version or 0) + 1   # sign out existing sessions
    emp.must_reset_password = False
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="employee.password_set",
                       entity_type="employee", entity_id=emp.id)
    await db.commit()
    return {"ok": True}


@router.post("/employees/{emp_id}/send-reset")
async def send_password_reset(
    emp_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_employees")),
):
    """Build 144cb — email a self-service reset link to a teammate instead of
    typing a password for them. Same token machinery as the logged-out flow."""
    emp = (await db.execute(select(Employee).where(
        Employee.id == emp_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    await _assert_can_manage(db, current, emp)
    from backend.routers.auth import _link
    from backend.services import tokens as _tok
    from backend.services.notifications import send_direct_email
    await _tok.invalidate_all(db, employee_id=emp.id, kind="reset")
    raw = await _tok.issue(db, employee_id=emp.id, kind="reset")
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="employee.password_reset_sent",
                       entity_type="employee", entity_id=emp.id)
    await db.commit()
    url = _link(f"/reset-password?token={raw}")
    await send_direct_email(
        emp.email, "Set your Tekly password",
        f"An administrator asked us to help you set your password. Open this link:\n\n{url}\n\n"
        f"It expires in 1 hour and can be used once.")
    return {"ok": True}


@router.post("/employees/{emp_id}/reset-mfa")
async def reset_employee_mfa(emp_id: str, db: AsyncSession = Depends(get_db),
                            current: Employee = Depends(require_perm("can_manage_employees"))):
    """Build 144cd — admin unlock: clear a teammate's 2FA so they can re-enroll
    (lost authenticator device). If the org mandates 2FA they'll be prompted to
    set it up again on next sign-in."""
    emp = (await db.execute(select(Employee).where(
        Employee.id == emp_id, Employee.org_id == current.org_id))).scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    await _assert_can_manage(db, current, emp)
    emp.mfa_enabled = False
    emp.mfa_secret = None
    emp.mfa_recovery_codes = None
    emp.token_version = (emp.token_version or 0) + 1   # force re-login
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="employee.mfa_reset",
                       entity_type="employee", entity_id=emp.id)
    await db.commit()
    return {"ok": True}


# ── Build 144cc: teammate invitations ─────────────────────────────
class InviteIn(BaseModel):
    email: str
    name: Optional[str] = None
    role_id: Optional[str] = None

    @field_validator("role_id")
    @classmethod
    def _blank_is_none(cls, v):
        return v or None   # the invite form sends "" for "no role"
    location_id: Optional[str] = None


@router.get("/invites")
async def list_invites(db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(get_current_employee)):
    """Pending (unaccepted) invitations for this org."""
    from backend.models import Invite
    rows = (await db.execute(select(Invite).where(
        Invite.org_id == current.org_id, Invite.accepted_at.is_(None)
    ).order_by(Invite.created_at.desc()))).scalars().all()
    now = datetime.utcnow()
    return {"items": [{
        "id": i.id, "email": i.email, "name": i.name, "role_id": i.role_id,
        "location_id": i.location_id, "expired": i.expires_at < now,
        "created_at": i.created_at.isoformat() if i.created_at else None,
    } for i in rows]}


@router.post("/invites", status_code=201)
async def create_invite(data: InviteIn, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(require_perm("can_manage_employees"))):
    """Invite a teammate. Emails a single-use link; the invitee sets their own
    password on accept. Blocks if the email already has an account or open invite."""
    import hashlib
    import secrets
    from datetime import timedelta

    from backend.models import Invite
    email = (data.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email is required")
    await _assert_can_assign_role(db, current, data.role_id)
    dupe = (await db.execute(select(Employee).where(func.lower(Employee.email) == email))).scalar_one_or_none()
    if dupe and dupe.org_id == current.org_id:
        raise HTTPException(400, "Someone with that email is already on the team")
    if dupe:
        # One email = one Tekly login today; accepting would fail on the unique email.
        raise HTTPException(409, "That email already has a Tekly account at another shop. "
                                 "Invite them with a different email address.")
    open_inv = (await db.execute(select(Invite).where(
        Invite.org_id == current.org_id, Invite.email == email,
        Invite.accepted_at.is_(None)))).scalar_one_or_none()
    raw = secrets.token_urlsafe(32)
    if open_inv:
        open_inv.token_hash = hashlib.sha256(raw.encode()).hexdigest()
        open_inv.expires_at = datetime.utcnow() + timedelta(days=7)
        open_inv.name, open_inv.role_id, open_inv.location_id = data.name, data.role_id, data.location_id
        inv = open_inv
    else:
        inv = Invite(
            org_id=current.org_id, email=email, name=data.name, role_id=data.role_id,
            location_id=data.location_id, invited_by=current.id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=datetime.utcnow() + timedelta(days=7))
        db.add(inv)
    await db.flush()
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="invite.create",
                       entity_type="invite", entity_id=inv.id, diff={"email": email, "role_id": data.role_id})
    await db.commit()
    from backend.models import Organisation
    from backend.routers.auth import _link
    from backend.services.notifications import send_direct_email
    org = (await db.execute(select(Organisation).where(
        Organisation.id == current.org_id))).scalar_one_or_none()
    org_name = org.name if org else "your team"
    url = _link(f"/accept-invite?token={raw}")
    await send_direct_email(
        email, f"You're invited to {org_name} on Tekly",
        f"{current.name} invited you to join {org_name} on Tekly.\n\n"
        f"Set your password and get started:\n\n{url}\n\n"
        f"This invitation expires in 7 days.")
    return {"ok": True, "invite_id": inv.id}


@router.delete("/invites/{invite_id}")
async def revoke_invite(invite_id: str, db: AsyncSession = Depends(get_db),
                        current: Employee = Depends(require_perm("can_manage_employees"))):
    from backend.models import Invite
    inv = (await db.execute(select(Invite).where(
        Invite.id == invite_id, Invite.org_id == current.org_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invite not found")
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="invite.revoke",
                       entity_type="invite", entity_id=inv.id, diff={"email": inv.email})
    await db.delete(inv)
    await db.commit()
    return {"ok": True}


# ── Roles ─────────────────────────────────────

@router.get("/roles")
async def list_roles(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    roles = (await db.execute(select(Role).where(Role.org_id == current.org_id))).scalars().all()
    counts = {}
    for e in (await db.execute(select(Employee).where(Employee.org_id == current.org_id, Employee.active == True))).scalars().all():  # noqa: E712
        if e.role_id:
            counts[e.role_id] = counts.get(e.role_id, 0) + 1
    return {"items": [{"id": r.id, "name": r.name, "permissions": r.permissions or {},
                       "contact_scope": getattr(r, "contact_scope", "all"),
                       "order_scope": getattr(r, "order_scope", "all"),
                       "warehouse_scope": getattr(r, "warehouse_scope", "all"),
                       "warehouse_ids": getattr(r, "warehouse_ids", None) or [],
                       "payroll_visibility_days": getattr(r, "payroll_visibility_days", None),
                       "employee_count": counts.get(r.id, 0)} for r in roles]}


# Role create/update share the role editor's rules (routers/rbac.py): explicit allow-lists,
# no granting what you don't have, no loosening your own limits, no editing your own role.
@router.post("/roles", status_code=201)
async def create_role(
    data: RoleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_roles")),
):
    from backend.routers import rbac as _rbac
    r = await _rbac.create_role(_rbac.RoleIn(**data.model_dump(exclude_unset=True)), db, current)
    return {"id": r["id"], "name": r["name"]}


@router.put("/roles/{role_id}")
async def update_role(
    role_id: str, data: RoleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_roles")),
):
    from backend.routers import rbac as _rbac
    await _rbac.update_role(role_id, _rbac.RoleIn(**data.model_dump(exclude_unset=True)), db, current)
    return {"ok": True}


@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_roles")),
):
    r = (await db.execute(select(Role).where(Role.id == role_id, Role.org_id == current.org_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Role not found")
    in_use = (await db.execute(select(func.count()).select_from(Employee).where(Employee.role_id == role_id))).scalar() or 0
    if in_use:
        raise HTTPException(400, f"{in_use} employee(s) still use this role")
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="role.delete",
                       entity_type="role", entity_id=r.id, diff={"name": r.name})
    await db.delete(r)
    await db.commit()
    return {"ok": True}

@router.delete("/employees/{emp_id}")
async def deactivate_employee(
    emp_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_employees")),
):
    result = await db.execute(select(Employee).where(Employee.id == emp_id, Employee.org_id == current.org_id))
    emp = result.scalar_one_or_none()
    if not emp:
        raise HTTPException(404, "Employee not found")
    await _assert_can_manage(db, current, emp)
    emp.active = False
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="employee.deactivate",
                       entity_type="employee", entity_id=emp.id)
    await db.commit()
    return {"ok": True}

# ── Tasks ─────────────────────────────────────

@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = None,
    assigned_to_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    q = select(Task).where(Task.org_id == current.org_id).order_by(Task.due_at.asc())
    if status:
        q = q.where(Task.status == status)
    if assigned_to_id:
        q = q.where(Task.assigned_to_id == assigned_to_id)
    result = await db.execute(q)
    tasks = result.scalars().all()
    return [{"id": t.id, "title": t.title, "status": t.status,
             "priority": t.priority, "assigned_to_id": t.assigned_to_id,
             "order_id": t.order_id,
             "due_at": t.due_at.isoformat() if t.due_at else None,
             "completed_at": t.completed_at.isoformat() if t.completed_at else None} for t in tasks]

@router.post("/tasks", status_code=201)
async def create_task(
    data: TaskIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    task = Task(
        org_id=current.org_id,
        title=data.title, description=data.description,
        assigned_to_id=data.assigned_to_id, created_by_id=current.id,
        order_id=data.order_id, priority=data.priority,
        due_at=datetime.fromisoformat(data.due_at) if data.due_at else None,
        status="todo",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return {"id": task.id, "title": task.title}

@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: str, data: TaskUpdate,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    result = await db.execute(select(Task).where(Task.id == task_id, Task.org_id == current.org_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(404, "Task not found")
    task.status = data.status
    if data.status == "done":
        task.completed_at = datetime.utcnow()
    await db.commit()
    return {"ok": True}


def _ser_location(ln) -> dict:
    return {"id": ln.id, "name": ln.name, "address": ln.address, "phone": ln.phone,
            "timezone": ln.timezone, "active": ln.active, "receipt_header": ln.receipt_header,
            "working_hours": ln.working_hours or None,
            "legal_entity_id": ln.legal_entity_id, "price_type_id": ln.price_type_id}


@router.get("/locations")
async def list_locations(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import Location
    rows = (await db.execute(select(Location).where(Location.org_id == current.org_id)
            .order_by(Location.created_at, Location.id))).scalars().all()
    return {"items": [_ser_location(ln) for ln in rows]}


class LocationIn(BaseModel):
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None
    timezone: Optional[str] = None
    active: Optional[bool] = None
    receipt_header: Optional[str] = None
    working_hours: Optional[dict] = None       # {mon:{open,close,closed}, ...}
    legal_entity_id: Optional[str] = None      # Orderry: location→legal entity (drives invoices)
    price_type_id: Optional[str] = None        # per-location price level


@router.post("/locations", status_code=201)
async def create_location(
    data: LocationIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    from backend.models import Location
    if not data.name.strip():
        raise HTTPException(422, "Location name is required")
    ln = Location(org_id=current.org_id, name=data.name.strip(),
                  address=data.address, phone=data.phone,
                  timezone=data.timezone or "UTC",
                  active=True if data.active is None else data.active,
                  receipt_header=data.receipt_header, working_hours=data.working_hours,
                  legal_entity_id=data.legal_entity_id, price_type_id=data.price_type_id)
    db.add(ln)
    await db.commit()
    await db.refresh(ln)
    return _ser_location(ln)


@router.put("/locations/{location_id}")
async def update_location(
    location_id: str, data: LocationIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    from backend.models import Location
    ln = (await db.execute(select(Location).where(
        Location.id == location_id, Location.org_id == current.org_id))).scalar_one_or_none()
    if not ln:
        raise HTTPException(404, "Location not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(ln, k, v)
    await db.commit()
    await db.refresh(ln)
    return _ser_location(ln)


@router.delete("/locations/{location_id}")
async def deactivate_location(
    location_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Deactivate rather than hard-delete — a location is referenced by orders, cash and
    warehouses, so it's archived (hidden from pickers) and can be reactivated."""
    from backend.models import Location
    ln = (await db.execute(select(Location).where(
        Location.id == location_id, Location.org_id == current.org_id))).scalar_one_or_none()
    if not ln:
        raise HTTPException(404, "Location not found")
    active_count = (await db.execute(select(func.count()).select_from(Location)
                    .where(Location.org_id == current.org_id, Location.active == True))).scalar() or 0  # noqa: E712
    if ln.active and active_count <= 1:
        raise HTTPException(409, "You must keep at least one active location")
    ln.active = False
    await db.commit()
    return {"ok": True}


# ── Company / General settings (Settings > General) ──

@router.get("/company")
async def get_company(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Company not found")
    return {
        "name": org.name, "company_email": org.company_email, "company_phone": org.company_phone,
        "company_address": org.company_address, "company_details": org.company_details,
        "company_note": org.company_note, "website": org.website, "logo_data": org.logo_data,
        "country": org.country,
        "currency": org.currency, "currency_code": org.currency_code, "timezone": org.timezone,
        "document_language": org.document_language or "en",
        "currencies": org.currencies_json or [],
        "industry_template": org.industry_template or "generic",
        "prohibit_dup_phone": org.prohibit_dup_phone,
        "prohibit_dup_email": org.prohibit_dup_email,
        "contact_consent_phone_default": org.contact_consent_phone_default,
        "contact_consent_email_default": org.contact_consent_email_default,
        "prevent_backdating_invoices": org.prevent_backdating_invoices,
        "default_due_days": org.default_due_days,
        "default_comment_visibility": org.default_comment_visibility,
        "default_file_visibility": org.default_file_visibility,
        "public_employee_name": getattr(org, "public_employee_name", None) or "full",
        "warranty_count_from": org.warranty_count_from,
        "asset_one_open_ticket": org.asset_one_open_ticket,
        "multi_asset_jobs": _ma.enabled(org),
        "multi_asset_available": _ma.available(org),
        "rounding_enabled": org.rounding_enabled,
        "currency_display_style": org.currency_display_style,
        "currency_position": org.currency_position,
        "payment_provider": org.payment_provider or "auto",
        "clock_wo_enabled": org.clock_wo_enabled if org.clock_wo_enabled is not None else True,
        "clock_attendance_enabled": org.clock_attendance_enabled if org.clock_attendance_enabled is not None else True,
        "clock_labour_billing": bool(getattr(org, "clock_labour_billing", False)),
        "require_mfa": bool(getattr(org, "require_mfa", False)),
    }


class CompanyIn(BaseModel):
    name: Optional[str] = None
    company_email: Optional[str] = None
    company_phone: Optional[str] = None
    company_address: Optional[str] = None
    company_details: Optional[str] = None
    company_note: Optional[str] = None
    website: Optional[str] = None
    logo_data: Optional[str] = None
    country: Optional[str] = None
    currency: Optional[str] = None
    currency_code: Optional[str] = None
    timezone: Optional[str] = None
    document_language: Optional[str] = None
    currencies: Optional[list] = None       # [{code, symbol, rate, primary}]
    industry_template: Optional[str] = None
    payment_provider: Optional[str] = None
    prohibit_dup_phone: Optional[bool] = None
    prohibit_dup_email: Optional[bool] = None
    contact_consent_phone_default: Optional[bool] = None
    contact_consent_email_default: Optional[bool] = None
    prevent_backdating_invoices: Optional[bool] = None
    default_due_days: Optional[int] = None
    default_comment_visibility: Optional[str] = None
    default_file_visibility: Optional[str] = None
    public_employee_name: Optional[str] = None          # WS-C #18: full | first | first_initial
    warranty_count_from: Optional[str] = None
    asset_one_open_ticket: Optional[bool] = None
    multi_asset_jobs: Optional[bool] = None            # several items per job (eligible industries only)
    rounding_enabled: Optional[bool] = None
    currency_display_style: Optional[str] = None
    currency_position: Optional[str] = None
    clock_wo_enabled: Optional[bool] = None            # build 144x: WO/job/task timer master switch
    clock_attendance_enabled: Optional[bool] = None    # build 144x: shop attendance master switch
    clock_labour_billing: Optional[bool] = None        # build 144y: bill labour from tracked time
    require_mfa: Optional[bool] = None                  # build 144cd: org-wide mandatory 2FA
    oe_show_scheduled: Optional[bool] = None            # 144dr: show scheduled date on the track page
    book_show_employee_step: Optional[bool] = None      # 144dr: online appointment employee-pick step
    auto_draft_invoice_on_close: Optional[bool] = None  # 144dr: auto-draft an invoice when a WO closes
    default_invoice_display: Optional[str] = None       # 144dr: detailed|simplified|one_line


@router.put("/company")
async def update_company(
    data: CompanyIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Company not found")
    payload = data.model_dump(exclude_unset=True)
    if "industry_template" in payload:
        from backend.services import industry as _ind
        valid = set(_ind.TEMPLATE_KEYS) | set(_ind.LEGACY_ALIASES)
        if payload["industry_template"] not in valid:
            raise HTTPException(422, f"Unknown industry_template. Valid: {sorted(valid)}")
    for k in ("default_comment_visibility", "default_file_visibility"):
        if k in payload and payload[k] not in ("private", "public"):
            raise HTTPException(422, "Visibility must be private or public")
    if "public_employee_name" in payload and payload["public_employee_name"] not in ("full", "first", "first_initial"):
        raise HTTPException(422, "Name display must be full, first or first_initial")
    if "payment_provider" in payload:
        valid = ("auto", "stripe", "square", "sumup", "simulated")
        if payload["payment_provider"] not in valid:
            raise HTTPException(422, f"Unknown payment_provider. Valid: {list(valid)}")
    if payload.get("multi_asset_jobs"):
        from backend.services import industry as _ind
        tmpl = payload.get("industry_template", org.industry_template)
        if _ind.normalize_template_key(tmpl) not in _ma.ELIGIBLE:
            raise HTTPException(422, "Several items per job is only for tools, equipment, phone and computer shops")
    # Build 121: the API field is 'currencies' but the column is 'currencies_json'
    if "currencies" in payload:
        org.currencies_json = payload.pop("currencies")
    for k, v in payload.items():
        setattr(org, k, v)
    # Several items per job is strictly for tools / equipment / phone / computer shops:
    # moving to any other industry switches it off (moving back needs a fresh opt-in).
    if "industry_template" in payload and not _ma.available(org):
        org.multi_asset_jobs = False
    await db.commit()
    return {"ok": True}


@router.get("/inventory-settings")
async def get_inventory_settings(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """Build 133 — Settings > Inventory. Coalesces to sensible defaults for orgs that
    haven't set anything yet."""
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Company not found")
    return {
        "enable_bins": bool(org.inv_enable_bins) if org.inv_enable_bins is not None else False,
        "unique_identifier": org.inv_unique_identifier or "name",
        "enable_reservations": bool(org.inv_enable_reservations) if org.inv_enable_reservations is not None else True,
        "reservation_max_days": org.inv_reservation_max_days if org.inv_reservation_max_days is not None else 14,
        "product_serial_template": org.inv_product_serial_template or "",
        "asset_serial_template": org.inv_asset_serial_template or "",
        "sku_template": org.inv_sku_template or "",
        "bin_template": org.inv_bin_template or "",
        "barcode_prefix": org.inv_barcode_prefix or "20",
        "default_product_type": org.inv_default_product_type or "default",
        "default_vehicle_type": org.inv_default_vehicle_type or "Car",
        "po_approval_threshold": org.po_approval_threshold or 0,
        "po_to_job": org.inv_po_to_job or "auto",
    }


class InventorySettingsIn(BaseModel):
    enable_bins: Optional[bool] = None
    unique_identifier: Optional[str] = None
    enable_reservations: Optional[bool] = None
    reservation_max_days: Optional[int] = None
    product_serial_template: Optional[str] = None
    asset_serial_template: Optional[str] = None
    sku_template: Optional[str] = None       # ii144a: empty = SKUs stay as typed
    bin_template: Optional[str] = None
    barcode_prefix: Optional[str] = None     # GS1 in-store 20–29
    default_product_type: Optional[str] = None
    default_vehicle_type: Optional[str] = None
    po_approval_threshold: Optional[float] = None
    po_to_job: Optional[str] = None      # auto | manual — parts arriving on a job's PO


@router.put("/inventory-settings")
async def update_inventory_settings(
    data: InventorySettingsIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_inventory")),
):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Company not found")
    p = data.model_dump(exclude_unset=True)
    if "unique_identifier" in p and p["unique_identifier"] not in ("name", "sku", "barcode", "code"):
        raise HTTPException(422, "unique_identifier must be name|sku|barcode|code")
    if "po_to_job" in p and p["po_to_job"] not in ("auto", "manual"):
        raise HTTPException(422, "po_to_job must be auto or manual")
    from backend.services import numbering
    for k in ("product_serial_template", "asset_serial_template", "sku_template", "bin_template"):
        if k in p:
            p[k] = (p[k] or "").strip() or None
            why = numbering.template_problem(p[k]) if p[k] else None
            if why:
                raise HTTPException(422, why)
    if "barcode_prefix" in p:
        p["barcode_prefix"] = (p["barcode_prefix"] or "").strip() or None
        if p["barcode_prefix"] and p["barcode_prefix"] not in numbering.IN_STORE_PREFIXES:
            raise HTTPException(422, "Generated barcodes start with 20 to 29 — the range set aside for "
                                     "a shop's own codes, so they never clash with a manufacturer's.")
    mapping = {
        "po_to_job": "inv_po_to_job",
        "enable_bins": "inv_enable_bins", "unique_identifier": "inv_unique_identifier",
        "enable_reservations": "inv_enable_reservations", "reservation_max_days": "inv_reservation_max_days",
        "product_serial_template": "inv_product_serial_template", "asset_serial_template": "inv_asset_serial_template",
        "sku_template": "inv_sku_template", "bin_template": "inv_bin_template", "barcode_prefix": "inv_barcode_prefix",
        "default_product_type": "inv_default_product_type", "default_vehicle_type": "inv_default_vehicle_type",
        "po_approval_threshold": "po_approval_threshold",
    }
    for k, v in p.items():
        setattr(org, mapping[k], v)
    await db.commit()
    return {"ok": True}


@router.post("/ledger/rebuild")
async def rebuild_ledger(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_view_finance")),
):
    """Build 125 — reconstruct the unified contact ledger from existing orders, payments,
    postings and returns so historical balances aren't blank. Idempotent; run once when
    adopting the ledger. Returns a summary of what was (re)built."""
    from backend.services.ledger import rebuild_org_ledger
    summary = await rebuild_org_ledger(db, current.org_id, created_by_id=current.id)
    return {"ok": True, **summary}


@router.get("/public-pages")
async def get_public_pages(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Company not found")
    return {
        "slug": org.subdomain or org.id,
        "appointment_enabled": org.appointment_enabled,
        "page_color": org.page_color, "page_radius": org.page_radius,
        "page_show_language": org.page_show_language, "page_show_logo": org.page_show_logo,
        "appointment_time_slot": org.appointment_time_slot,
        "appointment_allow_multiple": org.appointment_allow_multiple,
        "appointment_consider_orders": org.appointment_consider_orders,
        "appointment_lead_min": org.appointment_lead_min,
        "appointment_window_days": org.appointment_window_days,
        "appointment_theme": org.appointment_theme,
        "page_background_pattern": org.page_background_pattern,
        "oe_show_logo": org.oe_show_logo, "oe_logo_size": org.oe_logo_size,
        "oe_show_name": org.oe_show_name, "oe_show_address": org.oe_show_address,
        "oe_show_email": org.oe_show_email, "oe_show_description": org.oe_show_description,
        "oe_separate_tables": org.oe_separate_tables,
        "oe_show_zero_price_products": org.oe_show_zero_price_products,
        "oe_show_zero_price_services": org.oe_show_zero_price_services,
        "oe_allow_approval": getattr(org, "oe_allow_approval", True),
        "oe_estimate_line_choice": getattr(org, "oe_estimate_line_choice", True),
        "oe_allow_messages": getattr(org, "oe_allow_messages", True),
        "oe_msg_days_after_close": getattr(org, "oe_msg_days_after_close", None) if getattr(org, "oe_msg_days_after_close", None) is not None else 14,
        "oe_allow_recommend": getattr(org, "oe_allow_recommend", True),
        "oe_show_scheduled": getattr(org, "oe_show_scheduled", False),
        "book_show_employee_step": getattr(org, "book_show_employee_step", True),
        "auto_draft_invoice_on_close": getattr(org, "auto_draft_invoice_on_close", False),
        "default_invoice_display": getattr(org, "default_invoice_display", "detailed"),
        "oe_allow_staff_reply": getattr(org, "oe_allow_staff_reply", True),
        "oe_wo_allow_approval": getattr(org, "oe_wo_allow_approval", False),
        "oe_approval_statuses": getattr(org, "oe_approval_statuses", None) or "",
        "oe_wo_approval_statuses": getattr(org, "oe_wo_approval_statuses", None) or "",
        "oe_estimate_require_signature": getattr(org, "oe_estimate_require_signature", False),
        "oe_wo_require_signature": getattr(org, "oe_wo_require_signature", False),
        "oe_estimate_accept_status": getattr(org, "oe_estimate_accept_status", None) or "",
        "oe_estimate_decline_status": getattr(org, "oe_estimate_decline_status", None) or "",
        "oe_wo_accept_status": getattr(org, "oe_wo_accept_status", None) or "",
        "oe_wo_decline_status": getattr(org, "oe_wo_decline_status", None) or "",
    }


class PublicPagesIn(BaseModel):
    appointment_enabled: Optional[bool] = None
    page_color: Optional[str] = None
    page_radius: Optional[int] = None
    page_show_language: Optional[bool] = None
    page_show_logo: Optional[bool] = None
    page_background_pattern: Optional[str] = None
    oe_show_logo: Optional[bool] = None
    oe_logo_size: Optional[str] = None
    oe_show_name: Optional[bool] = None
    oe_show_address: Optional[bool] = None
    oe_show_email: Optional[bool] = None
    oe_show_description: Optional[bool] = None
    oe_separate_tables: Optional[bool] = None
    oe_show_zero_price_products: Optional[bool] = None
    oe_show_zero_price_services: Optional[bool] = None
    oe_allow_approval: Optional[bool] = None
    oe_estimate_line_choice: Optional[bool] = None      # E5: customers may untick estimate lines
    oe_allow_messages: Optional[bool] = None
    oe_msg_days_after_close: Optional[int] = Field(default=None, ge=0, le=365)
    oe_allow_recommend: Optional[bool] = None
    oe_allow_staff_reply: Optional[bool] = None
    oe_show_scheduled: Optional[bool] = None            # 144dr
    book_show_employee_step: Optional[bool] = None      # 144dr
    oe_wo_allow_approval: Optional[bool] = None         # 144ih
    oe_approval_statuses: Optional[str] = None          # 144ih comma-separated status keys
    oe_wo_approval_statuses: Optional[str] = None       # 144ih
    oe_estimate_require_signature: Optional[bool] = None  # 144ii
    oe_wo_require_signature: Optional[bool] = None
    oe_estimate_accept_status: Optional[str] = None
    oe_estimate_decline_status: Optional[str] = None
    oe_wo_accept_status: Optional[str] = None
    oe_wo_decline_status: Optional[str] = None
    appointment_time_slot: Optional[int] = None
    appointment_allow_multiple: Optional[bool] = None
    appointment_consider_orders: Optional[bool] = None
    appointment_lead_min: Optional[int] = None
    appointment_window_days: Optional[int] = None
    appointment_theme: Optional[int] = None


@router.put("/public-pages")
async def update_public_pages(
    data: PublicPagesIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Company not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(org, k, v)
    await db.commit()
    return {"ok": True}


class SubdomainIn(BaseModel):
    subdomain: str


@router.put("/public-pages/subdomain")
async def change_subdomain(
    data: SubdomainIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """1a: change the public-page subdomain slug (must be unique, url-safe)."""
    import re as _re
    slug = data.subdomain.strip().lower()
    if not _re.fullmatch(r"[a-z0-9][a-z0-9-]{2,40}", slug):
        raise HTTPException(422, "Subdomain must be 3-40 chars: a-z, 0-9, hyphens")
    from backend.models import Organisation
    taken = (await db.execute(select(Organisation).where(
        Organisation.subdomain == slug, Organisation.id != current.org_id))).scalar_one_or_none()
    if taken:
        raise HTTPException(409, "That subdomain is already taken")
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one()
    org.subdomain = slug
    await db.commit()
    return {"ok": True, "subdomain": slug}


@router.get("/cash-categories")
async def get_cash_categories(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    """2b: list cash-flow categories + the org's per-doc-type defaults."""
    from backend.models import CashFlowItem, Organisation
    rows = (await db.execute(select(CashFlowItem).where(
        CashFlowItem.org_id == current.org_id, CashFlowItem.active == True))).scalars().all()  # noqa: E712
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one()
    return {"items": [{"id": c.id, "name": c.name, "direction": c.direction} for c in rows],
            "default_tickets_id": org.cash_category_tickets_id,
            "default_sales_id": org.cash_category_sales_id}


class CashDefaultsIn(BaseModel):
    default_tickets_id: Optional[str] = None
    default_sales_id: Optional[str] = None


@router.put("/cash-categories/defaults")
async def set_cash_defaults(
    data: CashDefaultsIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == current.org_id))).scalar_one()
    org.cash_category_tickets_id = data.default_tickets_id
    org.cash_category_sales_id = data.default_sales_id
    await db.commit()
    return {"ok": True}

