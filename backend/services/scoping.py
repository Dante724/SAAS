"""Build 83 — record-level access scoping (Orderry "access conditions").

Roles already gate *features* (can_x). This adds gating by *record*: a specialist
may be allowed to open work orders in general, yet only see the ones assigned to
them; a salesperson may only open the contacts they manage.

Design notes:
  * **Default-allow.** No role, or scope left at "all", means no filtering. This
    matches `require_perm`'s semantics and keeps existing installs unchanged.
  * **Filters are applied at the query**, not after fetching, so pagination and
    counts stay correct.
  * Scoping restricts *visibility*, not *association*: an employee scoped to "own
    contacts" can still attach any client to an order (Orderry behaves the same) —
    they just can't browse the full contact list.
"""
import json
from typing import Optional

from sqlalchemy import or_

from backend.models import Client, Employee, Order, Role


async def role_for(db, current: Employee) -> Optional[Role]:
    if not current.role_id:
        return None
    from sqlalchemy import select
    return (await db.execute(select(Role).where(Role.id == current.role_id))).scalar_one_or_none()


def _ids(value) -> list:
    """Role list fields are JSON, but location_ids is a legacy Text column that may
    hold a JSON string. Accept both."""
    if not value:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def scope_clients(q, role: Optional[Role], current: Employee):
    """Restrict a Client query to the contacts this employee may see."""
    if not role or (role.contact_scope or "all") == "all":
        return q
    # "own": clients I manage. Unmanaged clients stay visible to nobody but full-access
    # roles, so a scoped user can't browse the whole book by accident.
    return q.where(Client.manager_id == current.id)


def scope_orders(q, role: Optional[Role], current: Employee):
    """Restrict an Order query to the work orders this employee may see."""
    if not role:
        return q
    if (role.order_scope or "all") == "own":
        # orders assigned to me, or that I created and nobody owns yet
        q = q.where(or_(Order.assigned_to_id == current.id,
                        Order.assigned_to_id.is_(None)))
    if (role.location_scope or "all") == "selected":
        allowed = _ids(role.location_ids)
        if allowed:
            q = q.where(Order.location_id.in_(allowed))
    # A5: per-status VIEW ACL — hide orders whose status this role may not see.
    hidden = _ids(getattr(role, "hidden_status_keys", None))
    if hidden:
        q = q.where(Order.status.not_in(hidden))
    return q


def allowed_warehouses(role: Optional[Role]) -> Optional[list]:
    """Location ids this role may act on in inventory. None = all."""
    if not role or (role.warehouse_scope or "all") == "all":
        return None
    return _ids(role.warehouse_ids)


def may_use_warehouse(role: Optional[Role], location_id: Optional[str]) -> bool:
    allowed = allowed_warehouses(role)
    if allowed is None or location_id is None:
        return True
    return location_id in allowed


def allowed_accounts(role: Optional[Role]) -> Optional[list]:
    """Cash accounts this role may see. None = all."""
    if not role or not role.account_ids:
        return None
    ids = _ids(role.account_ids)
    return ids or None


def may_use_account(role: Optional[Role], account_id: Optional[str]) -> bool:
    allowed = allowed_accounts(role)
    if allowed is None or account_id is None:
        return True
    return account_id in allowed


def payroll_cutoff(role: Optional[Role]):
    """Earliest date an employee may see their own compensation, or None for no limit."""
    if not role or not role.payroll_visibility_days:
        return None
    from datetime import date, timedelta
    return date.today() - timedelta(days=int(role.payroll_visibility_days))


async def can_view_client(db, current: Employee, client: Client) -> bool:
    role = await role_for(db, current)
    if not role or (role.contact_scope or "all") == "all":
        return True
    return client.manager_id == current.id


async def can_view_order(db, current: Employee, order: Order) -> bool:
    role = await role_for(db, current)
    if not role:
        return True
    if (role.order_scope or "all") == "own":
        if order.assigned_to_id not in (current.id, None):
            return False
    if (role.location_scope or "all") == "selected":
        allowed = _ids(role.location_ids)
        if allowed and order.location_id not in allowed:
            return False
    # same per-status VIEW ACL scope_orders applies to lists
    if order.status in _ids(getattr(role, "hidden_status_keys", None)):
        return False
    return True


async def guard_record_scope(request, db, current: Employee) -> None:
    """Router-level guard: on any route whose path names an `{order_id}` or `{client_id}`,
    refuse (403) when the caller's role scope ("own orders/contacts only", allowed
    locations, hidden statuses) excludes that record. Before this, only a few GETs checked
    scope, so a restricted technician could assign, edit, message, bill or delete any order
    or client whose id they knew. Records of another org (or missing) are left to the
    endpoint, which 404s."""
    from sqlalchemy import select
    pp = request.path_params
    if pp.get("order_id"):
        order = (await db.execute(select(Order).where(
            Order.id == pp["order_id"], Order.org_id == current.org_id))).scalar_one_or_none()
        if order is not None and not await can_view_order(db, current, order):
            from fastapi import HTTPException
            raise HTTPException(403, "You can only act on work orders within your access")
    if pp.get("client_id"):
        client = (await db.execute(select(Client).where(
            Client.id == pp["client_id"], Client.org_id == current.org_id))).scalar_one_or_none()
        if client is not None and not await can_view_client(db, current, client):
            from fastapi import HTTPException
            raise HTTPException(403, "You can only act on clients within your access")
