"""RBAC v2 endpoints: permission catalog, role templates, role matrix + scope.

Backward compatible with the existing flat Role.permissions dict; adds structured
catalog/templates and per-location scoping.
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, Role
from backend.services.auth import get_current_employee, require_perm
from backend.services.permissions import CATALOG, TEMPLATES, permission_errors

router = APIRouter(prefix="/api/settings/rbac", tags=["settings"])


@router.get("/catalog")
async def permission_catalog(current: Employee = Depends(get_current_employee)):
    """Grouped permission catalog for the role-editor UI (searchable matrix)."""
    return {"catalog": {group: [{"code": code, "label": label} for code, label in perms]
                        for group, perms in CATALOG.items()},
            "templates": list(TEMPLATES.keys())}


def _ser_role(r: Role) -> dict:
    return {"id": r.id, "name": r.name, "permissions": r.permissions or {},
            "location_scope": getattr(r, "location_scope", "all"),
            "location_ids": json.loads(getattr(r, "location_ids", None) or "[]"),
            "contact_scope": getattr(r, "contact_scope", "all"),
            "order_scope": getattr(r, "order_scope", "all"),
            "warehouse_scope": getattr(r, "warehouse_scope", "all"),
            "warehouse_ids": getattr(r, "warehouse_ids", None) or [],
            "account_ids": getattr(r, "account_ids", None) or [],
            "payroll_visibility_days": getattr(r, "payroll_visibility_days", None),
            "hidden_status_keys": getattr(r, "hidden_status_keys", None) or [],
            "home_feed_sources": getattr(r, "home_feed_sources", None) or [],
            "home_route": getattr(r, "home_route", None) or ""}


@router.get("/roles")
async def list_roles(db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(get_current_employee)):
    rows = (await db.execute(select(Role).where(Role.org_id == current.org_id))).scalars().all()
    return {"items": [_ser_role(r) for r in rows]}


class RoleIn(BaseModel):
    name: str
    permissions: dict = {}          # {code: bool}; omitted => allowed (deny-wins)
    template: Optional[str] = None  # seed from a template
    location_scope: str = "all"
    location_ids: list[str] = []
    # Build 83 — record-level access conditions
    contact_scope: str = "all"      # all|own
    order_scope: str = "all"        # all|own
    warehouse_scope: str = "all"    # all|selected
    warehouse_ids: list[str] = []
    account_ids: list[str] = []     # empty = all accounts
    payroll_visibility_days: Optional[int] = None
    hidden_status_keys: list[str] = []   # A5: order/estimate status keys this role can't view
    home_feed_sources: list[str] = []    # Home-feed signals this role sees ([] = all)
    home_route: Optional[str] = None      # landing page for this role ("" / None = Home)


_SCOPE_FIELDS = ("location_scope", "location_ids", "contact_scope", "order_scope", "warehouse_scope",
                 "warehouse_ids", "account_ids", "payroll_visibility_days", "hidden_status_keys",
                 "home_feed_sources", "home_route")


def _apply_scope(r: Role, data: "RoleIn") -> None:
    for field, value in (("contact_scope", data.contact_scope),
                         ("order_scope", data.order_scope),
                         ("warehouse_scope", data.warehouse_scope)):
        if value not in ("all", "own", "selected"):
            raise HTTPException(422, f"{field} must be all|own|selected")
        setattr(r, field, value)
    r.warehouse_ids = data.warehouse_ids or None
    r.account_ids = data.account_ids or None
    r.payroll_visibility_days = data.payroll_visibility_days
    r.hidden_status_keys = data.hidden_status_keys or None
    r.home_feed_sources = data.home_feed_sources or None
    r.home_route = (data.home_route or "").strip() or None


def _validate(perms: dict):
    errors = permission_errors(perms)
    if errors:
        raise HTTPException(422, "; ".join(errors))


@router.post("/roles", status_code=201)
async def create_role(data: RoleIn, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_roles"))):
    perms = dict(TEMPLATES.get(data.template, {})) if data.template else {}
    perms.update(data.permissions)
    _validate(perms)
    r = Role(org_id=current.org_id, name=data.name, permissions=perms,
             location_scope=data.location_scope,
             location_ids=json.dumps(data.location_ids) if data.location_ids else None)
    _apply_scope(r, data)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return _ser_role(r)


@router.put("/roles/{role_id}")
async def update_role(role_id: str, data: RoleIn, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_roles"))):
    r = (await db.execute(select(Role).where(
        Role.id == role_id, Role.org_id == current.org_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Role not found")
    _validate(data.permissions)
    # A field the client didn't send keeps its current value. Defaulting it to "all" would quietly
    # widen a restricted role (the old role editor never sent location or account limits).
    current_vals = _ser_role(r)
    missing = {k: current_vals[k] for k in _SCOPE_FIELDS if k not in data.model_fields_set}
    if missing:
        data = RoleIn(**{**data.model_dump(), **missing})
    if data.location_scope not in ("all", "selected"):
        raise HTTPException(422, "location_scope must be all|selected")
    r.name = data.name
    r.permissions = data.permissions
    r.location_scope = data.location_scope
    r.location_ids = json.dumps(data.location_ids) if data.location_ids else None
    _apply_scope(r, data)
    await db.commit()
    return _ser_role(r)


@router.delete("/roles/{role_id}")
async def delete_role(role_id: str, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_roles"))):
    r = (await db.execute(select(Role).where(
        Role.id == role_id, Role.org_id == current.org_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Role not found")
    # People without a role have full access, so never orphan anyone: move them first.
    from backend.models import Employee as _E
    from backend.models import Invite as _I
    used = (await db.execute(select(func.count()).select_from(_E).where(_E.role_id == r.id))).scalar() or 0
    invited = (await db.execute(select(func.count()).select_from(_I).where(_I.role_id == r.id, _I.accepted_at.is_(None)))).scalar() or 0
    if used or invited:
        raise HTTPException(409, f"{used + invited} {'person uses' if used + invited == 1 else 'people use'} this role. Give them another role first.")
    await db.delete(r)
    await db.commit()
    return {"ok": True}


# ── Build 83: employee groups (separate from roles) ──────────────────────────
from backend.models import EmployeeGroup  # noqa: E402


def _ser_group(g: EmployeeGroup) -> dict:
    return {"id": g.id, "name": g.name, "member_ids": g.member_ids or []}


@router.get("/groups")
async def list_groups(db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(get_current_employee)):
    rows = (await db.execute(select(EmployeeGroup).where(
        EmployeeGroup.org_id == current.org_id))).scalars().all()
    return {"items": [_ser_group(g) for g in rows]}


class GroupIn(BaseModel):
    name: str
    member_ids: list[str] = []


@router.post("/groups", status_code=201)
async def create_group(data: GroupIn, db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(require_perm("can_manage_roles"))):
    g = EmployeeGroup(org_id=current.org_id, name=data.name, member_ids=data.member_ids)
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return _ser_group(g)


@router.put("/groups/{group_id}")
async def update_group(group_id: str, data: GroupIn, db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(require_perm("can_manage_roles"))):
    g = (await db.execute(select(EmployeeGroup).where(
        EmployeeGroup.id == group_id, EmployeeGroup.org_id == current.org_id))).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Group not found")
    g.name = data.name
    g.member_ids = data.member_ids
    await db.commit()
    return _ser_group(g)


@router.delete("/groups/{group_id}")
async def delete_group(group_id: str, db: AsyncSession = Depends(get_db),
                       current: Employee = Depends(require_perm("can_manage_roles"))):
    g = (await db.execute(select(EmployeeGroup).where(
        EmployeeGroup.id == group_id, EmployeeGroup.org_id == current.org_id))).scalar_one_or_none()
    if g:
        await db.delete(g)
        await db.commit()
    return {"ok": True}
