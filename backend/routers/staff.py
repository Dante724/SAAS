from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, Role, Task
from backend.services.auth import get_current_employee, hash_password, require_perm

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

class RoleIn(BaseModel):
    name: str
    permissions: dict = {}
    # Build 83 — record-level access conditions
    contact_scope: str = "all"       # all|own
    order_scope: str = "all"         # all|own
    warehouse_scope: str = "all"     # all|selected
    warehouse_ids: list[str] = []
    payroll_visibility_days: Optional[int] = None


def _apply_role_scope(r: "Role", data: "RoleIn") -> None:
    for field, value in (("contact_scope", data.contact_scope),
                         ("order_scope", data.order_scope),
                         ("warehouse_scope", data.warehouse_scope)):
        if value not in ("all", "own", "selected"):
            raise HTTPException(422, f"{field} must be all|own|selected")
        setattr(r, field, value)
    r.warehouse_ids = data.warehouse_ids or None
    r.payroll_visibility_days = data.payroll_visibility_days

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
    return [{"id": e.id, "name": e.name, "email": e.email,
             "phone": e.phone, "wage_type": e.wage_type, "wage_value": e.wage_value,
             "commission_labour": e.commission_labour, "commission_parts": e.commission_parts,
             "weekly_hours": e.weekly_hours, "hourly_basis": e.hourly_basis or "labour",
             "track_job_hours": e.track_job_hours, "track_attendance": e.track_attendance,
             "position": e.position, "hired_at": e.hired_at.isoformat() if e.hired_at else None,
             "tax_id": e.tax_id, "public_name": e.public_name, "note": e.note,
             "location_id": e.location_id, "role_id": e.role_id, "active": e.active} for e in employees]

@router.post("/employees", status_code=201)
async def create_employee(
    data: EmployeeIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_employees")),
):
    import secrets

    existing = await db.execute(select(Employee).where(Employee.email == data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already in use")
    if data.password is not None and len(data.password) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LEN} characters")
    emp = Employee(
        org_id=current.org_id,
        name=data.name, email=data.email,
        # Without a password, store a random one nobody knows; the welcome email lets
        # the teammate choose their own.
        password_hash=hash_password(data.password or secrets.token_urlsafe(32)),
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
        emp.email, f"You've been added to {org.name if org else 'your team'} on RepairOS",
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
    payload = data.model_dump(exclude_unset=True)
    if "email" in payload and payload["email"] != emp.email:
        dupe = (await db.execute(select(Employee).where(Employee.email == payload["email"]))).scalar_one_or_none()
        if dupe:
            raise HTTPException(400, "Email already in use")
    if "hourly_basis" in payload and payload["hourly_basis"] not in ("labour", "attendance"):
        payload["hourly_basis"] = "labour"
    if "hired_at" in payload:
        payload["hired_at"] = _parse_date(payload["hired_at"])
    for k, v in payload.items():
        setattr(emp, k, v)
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
    if len(data.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    emp.password_hash = hash_password(data.password)
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
    from backend.routers.auth import _link
    from backend.services import tokens as _tok
    from backend.services.notifications import send_direct_email
    await _tok.invalidate_all(db, employee_id=emp.id, kind="reset")
    raw = await _tok.issue(db, employee_id=emp.id, kind="reset")
    await db.commit()
    url = _link(f"/reset-password?token={raw}")
    await send_direct_email(
        emp.email, "Set your RepairOS password",
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
    emp.mfa_enabled = False
    emp.mfa_secret = None
    emp.mfa_recovery_codes = None
    emp.token_version = (emp.token_version or 0) + 1   # force re-login
    await db.commit()
    return {"ok": True}


# ── Build 144cc: teammate invitations ─────────────────────────────
class InviteIn(BaseModel):
    email: str
    name: Optional[str] = None
    role_id: Optional[str] = None
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
    dupe = (await db.execute(select(Employee).where(
        Employee.email == email, Employee.org_id == current.org_id))).scalar_one_or_none()
    if dupe:
        raise HTTPException(400, "Someone with that email is already on the team")
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
    await db.commit()
    from backend.models import Organisation
    from backend.routers.auth import _link
    from backend.services.notifications import send_direct_email
    org = (await db.execute(select(Organisation).where(
        Organisation.id == current.org_id))).scalar_one_or_none()
    org_name = org.name if org else "your team"
    url = _link(f"/accept-invite?token={raw}")
    await send_direct_email(
        email, f"You're invited to {org_name} on RepairOS",
        f"{current.name} invited you to join {org_name} on RepairOS.\n\n"
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


@router.post("/roles", status_code=201)
async def create_role(
    data: RoleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_roles")),
):
    r = Role(org_id=current.org_id, name=data.name, permissions=data.permissions)
    _apply_role_scope(r, data)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return {"id": r.id, "name": r.name}


@router.put("/roles/{role_id}")
async def update_role(
    role_id: str, data: RoleIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_roles")),
):
    r = (await db.execute(select(Role).where(Role.id == role_id, Role.org_id == current.org_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Role not found")
    r.name = data.name
    r.permissions = data.permissions
    _apply_role_scope(r, data)
    await db.commit()
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
    emp.active = False
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
        "warranty_count_from": org.warranty_count_from,
        "asset_one_open_ticket": org.asset_one_open_ticket,
        "rounding_enabled": org.rounding_enabled,
        "currency_display_style": org.currency_display_style,
        "currency_position": org.currency_position,
        "payment_provider": org.payment_provider or "auto",
        "clock_wo_enabled": org.clock_wo_enabled if org.clock_wo_enabled is not None else True,
        "clock_attendance_enabled": org.clock_attendance_enabled if org.clock_attendance_enabled is not None else True,
        "clock_labour_billing": bool(getattr(org, "clock_labour_billing", False)),
        "require_mfa": bool(getattr(org, "require_mfa", True)),
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
    warranty_count_from: Optional[str] = None
    asset_one_open_ticket: Optional[bool] = None
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
    if "payment_provider" in payload:
        valid = ("auto", "stripe", "square", "sumup", "simulated")
        if payload["payment_provider"] not in valid:
            raise HTTPException(422, f"Unknown payment_provider. Valid: {list(valid)}")
    # Build 121: the API field is 'currencies' but the column is 'currencies_json'
    if "currencies" in payload:
        org.currencies_json = payload.pop("currencies")
    for k, v in payload.items():
        setattr(org, k, v)
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
        "default_product_type": org.inv_default_product_type or "default",
        "default_vehicle_type": org.inv_default_vehicle_type or "Car",
        "po_approval_threshold": org.po_approval_threshold or 0,
    }


class InventorySettingsIn(BaseModel):
    enable_bins: Optional[bool] = None
    unique_identifier: Optional[str] = None
    enable_reservations: Optional[bool] = None
    reservation_max_days: Optional[int] = None
    product_serial_template: Optional[str] = None
    asset_serial_template: Optional[str] = None
    default_product_type: Optional[str] = None
    default_vehicle_type: Optional[str] = None
    po_approval_threshold: Optional[float] = None


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
    mapping = {
        "enable_bins": "inv_enable_bins", "unique_identifier": "inv_unique_identifier",
        "enable_reservations": "inv_enable_reservations", "reservation_max_days": "inv_reservation_max_days",
        "product_serial_template": "inv_product_serial_template", "asset_serial_template": "inv_asset_serial_template",
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
        "oe_allow_messages": getattr(org, "oe_allow_messages", True),
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
    oe_allow_messages: Optional[bool] = None
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

