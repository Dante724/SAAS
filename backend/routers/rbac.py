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
from backend.services import audit
from backend.services.auth import get_current_employee, require_perm
from backend.services.permissions import (
    ALL_CODES,
    CATALOG,
    NOT_YET_ENFORCED,
    TEMPLATES,
    make_explicit,
    new_role_permissions,
    role_allows,
)

router = APIRouter(prefix="/api/settings/rbac", tags=["settings"])


@router.get("/catalog")
async def permission_catalog(current: Employee = Depends(get_current_employee)):
    """Grouped permission catalog for the role-editor UI (searchable matrix)."""
    # `enforced: false` = listed for completeness but not wired to any screen yet; the role
    # editor shows it as "coming soon" so switching it off isn't mistaken for protection.
    return {"catalog": {group: [{"code": code, "label": label, "enforced": code not in NOT_YET_ENFORCED}
                                for code, label in perms]
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
    unknown = set(perms) - ALL_CODES
    if unknown:
        raise HTTPException(422, f"Unknown permission codes: {sorted(unknown)}")


# ── No self-widening ──────────────────────────────────────────────────────────
# Whoever edits roles (anyone but the owner) can only hand out what they have themselves,
# can't loosen record/location limits they're under, and can't edit their own role.
_NARROW = {"own", "selected"}


async def _editor_role(db: AsyncSession, current: Employee) -> Optional[Role]:
    from backend.services.auth import is_shop_owner
    if not current.role_id or await is_shop_owner(db, current):
        return None                 # the owner: no limits to stay within
    role = (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()
    if role is None:
        raise HTTPException(403, "Your role is missing; ask the owner")
    return role


def _assert_scope_not_wider(editor: Role, data: "RoleIn") -> None:
    for field in ("order_scope", "contact_scope", "warehouse_scope", "location_scope"):
        if getattr(editor, field, "all") in _NARROW and getattr(data, field, "all") not in _NARROW:
            raise HTTPException(403, f"You can't give a role wider access than your own ({field.replace('_', ' ')})")
    if getattr(editor, "location_scope", "all") == "selected":
        mine = set(json.loads(editor.location_ids or "[]"))
        if not set(data.location_ids or []) <= mine:
            raise HTTPException(403, "You can't give a role locations you don't have")


async def guard_role_change(db: AsyncSession, current: Employee, role: Optional[Role],
                            new_perms: dict, data: Optional["RoleIn"] = None) -> dict:
    """Check (and for a new role, cap) a role change. Returns the permissions to save."""
    editor = await _editor_role(db, current)
    if editor is None:
        return new_perms
    if role is not None and role.id == editor.id:
        raise HTTPException(403, "You can't change your own role. Ask the owner or another manager.")
    held = {c for c in ALL_CODES if role_allows(editor, c)}
    if role is None:
        # A new role: anything the editor doesn't have is simply left off.
        new_perms = {c: (v and c in held) for c, v in new_perms.items()}
    else:
        before = {c for c in ALL_CODES if role_allows(role, c)}
        added = {c for c, v in new_perms.items() if v and c not in before}
        beyond = sorted(added - held)
        if beyond:
            raise HTTPException(403, f"You can't grant permissions you don't have: {', '.join(beyond)}")
    if data is not None:
        _assert_scope_not_wider(editor, data)
    return new_perms


@router.post("/roles", status_code=201)
async def create_role(data: RoleIn, db: AsyncSession = Depends(get_db),
                      current: Employee = Depends(require_perm("can_manage_roles"))):
    _validate(data.permissions)
    if data.template and data.template not in TEMPLATES:
        raise HTTPException(422, f"Unknown template. Valid: {list(TEMPLATES)}")
    perms = new_role_permissions(data.template, data.permissions)
    perms = await guard_role_change(db, current, None, perms, data)
    r = Role(org_id=current.org_id, name=data.name, permissions=perms, perms_explicit=True,
             location_scope=data.location_scope,
             location_ids=json.dumps(data.location_ids) if data.location_ids else None)
    _apply_scope(r, data)
    db.add(r)
    await db.flush()
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="role.create",
                       entity_type="role", entity_id=r.id, diff={"name": r.name, "permissions": perms})
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
    # Codes the editor didn't send keep their current value (the role is an explicit allow-list).
    make_explicit(r)
    merged = {**(r.permissions or {}), **{k: bool(v) for k, v in data.permissions.items()}}
    merged = await guard_role_change(db, current, r, merged, data)
    r.name = data.name
    r.permissions = merged
    r.perms_explicit = True
    r.location_scope = data.location_scope
    r.location_ids = json.dumps(data.location_ids) if data.location_ids else None
    _apply_scope(r, data)
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="role.update",
                       entity_type="role", entity_id=r.id, diff={"name": r.name, "permissions": merged})
    await db.commit()
    from backend.core.cost_redaction import invalidate_role
    invalidate_role(r.id)
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
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="role.delete",
                       entity_type="role", entity_id=r.id, diff={"name": r.name})
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
