"""Who in Tekly's admin team may do what, on which shops, seeing which fields.

Every admin request passes the same gates, all server side:
1. signed in (platform_auth.get_current_platform_admin: live session, 2-step done);
2. permission: the role's allow-list (unticked = no; new codes start off);
3. scope: which shops (countries, own book only, stages); out of scope looks missing;
4. field levels: each field group is hidden, own book only, or shown, and hidden
   fields are removed from responses (never just greyed out on screen);
5. limits: e.g. discount up to 15% for 3 months; over the cap becomes a request to
   the owner;
6. the action log (admin_log), for allowed and refused actions alike.

An admin with no role is an owner, so installs from before roles keep working.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import or_, select

from backend.db.database import get_db
from backend.models import AdminRole, Organisation, PlatformAdmin
from backend.services.platform_auth import get_current_platform_admin

# code -> label, grouped for the role editor.
PERMISSIONS = {
    "Shops": [
        ("view_shops", "View shops"),
        ("add_notes", "Add notes and log calls"),
        ("assign_shops", "Hand shops to someone else"),
        ("email_shops", "Email shops"),
    ],
    "Money": [
        ("give_discount", "Give discounts (within limits)"),
        ("extend_trial", "Extend trials and grace periods (within limits)"),
        ("request_plan_change", "Ask for a plan change"),
        ("change_plan", "Change plans"),
        ("create_codes", "Create coupon codes"),
    ],
    "Account actions": [
        ("suspend", "Suspend and unsuspend shops"),
        ("change_owner", "Change a shop's owner or email"),
        ("edit_staff_access", "Change a shop's staff access dates"),
        ("export_data", "Export a shop's data"),
        ("run_imports", "Run imports"),
        ("support_access", "Ask for support access"),
    ],
    "Admin": [
        ("manage_admins", "Add and remove admins"),
        ("edit_roles", "Edit roles"),
        ("read_log", "Read the full action log"),
        ("approve_requests", "Approve requests"),
    ],
}
ALL_PERMS = [c for group in PERMISSIONS.values() for c, _ in group]

# field group -> the keys it covers in shop responses
FIELD_GROUPS = {
    "billing": ["discount_pct", "discount_until", "discount_reason",
                "billing_note", "price_locked_until", "stripe_customer_id"],
    "owner_contact": ["company_email", "company_phone", "owner_name", "owner_email"],
    "staff": ["employees_detail"],
    "notes": ["notes", "admin_notes"],
    "usage": ["jobs_7d", "last_activity_at"],
    "revenue": ["mrr"],          # company-wide numbers on the stats screen
}
LEVELS = ("hidden", "own", "shown")
STAGES = {"trial": ["trial"], "paying": ["active"], "lost": ["trial_lost", "churned"], "suspended": ["suspended"]}
# The subscription plans (services/billing.py); a trial is a status (sub_status), not a plan.
PLANS = ("solo", "shop", "pro")


def _all(level="shown"):
    return {g: level for g in FIELD_GROUPS}


BUILTIN_ROLES = {
    "owner": {
        "name": "Owner",
        "permissions": {c: True for c in ALL_PERMS},
        "field_levels": _all("shown"),
        "scope": {},
        "limits": {},
    },
    "sales_am": {
        "name": "Sales and account management",
        "permissions": {c: True for c in ("view_shops", "add_notes", "assign_shops", "email_shops", "give_discount",
                                          "extend_trial", "request_plan_change", "create_codes")},
        "field_levels": {**_all("hidden"), "billing": "own", "owner_contact": "own", "notes": "shown", "usage": "shown"},
        # Every trial (selling), plus the paying shops in their own book (account management).
        "scope": {"open_stages": ["trial"], "own_book_otherwise": True},
        "limits": {"max_discount_pct": 15, "max_discount_months": 3, "max_trial_days": 7},
    },
    "sales": {
        "name": "Sales",
        "permissions": {c: True for c in ("view_shops", "add_notes", "email_shops", "give_discount",
                                          "extend_trial", "create_codes", "assign_shops")},
        "field_levels": {**_all("hidden"), "owner_contact": "shown", "notes": "shown", "usage": "shown"},
        "scope": {"stages": ["trial"]},
        "limits": {"max_discount_pct": 10, "max_discount_months": 3, "max_trial_days": 7},
    },
    "account_manager": {
        "name": "Account manager",
        "permissions": {c: True for c in ("view_shops", "add_notes", "assign_shops", "email_shops", "give_discount",
                                          "extend_trial", "request_plan_change")},
        "field_levels": {**_all("hidden"), "billing": "own", "owner_contact": "own", "notes": "shown", "usage": "shown"},
        "scope": {"own_book": True, "stages": ["paying"]},
        "limits": {"max_discount_pct": 15, "max_discount_months": 3, "max_trial_days": 7},
    },
    "support": {
        "name": "Support",
        "permissions": {c: True for c in ("view_shops", "add_notes", "run_imports", "support_access",
                                          "change_owner", "edit_staff_access")},
        "field_levels": {**_all("hidden"), "owner_contact": "shown", "staff": "shown", "notes": "shown", "usage": "shown"},
        "scope": {},
        "limits": {},
    },
}


async def ensure_builtin_roles(db) -> None:
    """Create any missing ready-made role. Runs at startup; safe if two requests race
    (the loser's insert is rolled back inside a savepoint)."""
    from sqlalchemy.exc import IntegrityError
    have = {r.builtin for r in (await db.execute(select(AdminRole).where(AdminRole.builtin.isnot(None)))).scalars()}
    for key, spec in BUILTIN_ROLES.items():
        if key in have:
            continue
        try:
            async with db.begin_nested():
                db.add(AdminRole(builtin=key, **spec))
        except IntegrityError:
            pass


@dataclass
class AdminCtx:
    admin: PlatformAdmin
    role_key: str | None
    perms: dict
    fields: dict
    scope: dict
    limits: dict
    ip: str | None = None
    session_id: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def is_owner(self) -> bool:
        return self.role_key == "owner"

    def can(self, code: str) -> bool:
        return bool(self.perms.get(code))

    def level(self, group: str) -> str:
        lv = self.fields.get(group, "hidden")
        return lv if lv in LEVELS else "hidden"


def ctx_from(admin: PlatformAdmin, role: AdminRole | None, request: Request | None = None) -> AdminCtx:
    from backend.services.ratelimit import client_ip
    ip = client_ip(request) if request is not None else None
    sid = getattr(getattr(request, "state", None), "admin_session_id", None) if request is not None else None
    if role is None:   # legacy admin without a role = owner
        spec = BUILTIN_ROLES["owner"]
        return AdminCtx(admin, "owner", dict(spec["permissions"]), dict(spec["field_levels"]), {}, {}, ip, sid)
    return AdminCtx(admin, role.builtin, dict(role.permissions or {}), dict(role.field_levels or {}),
                    dict(role.scope or {}), dict(role.limits or {}), ip, sid)


async def admin_ctx(request: Request, admin: PlatformAdmin = Depends(get_current_platform_admin),
                    db=Depends(get_db)) -> AdminCtx:
    role = None
    if admin.role_id:
        role = (await db.execute(select(AdminRole).where(AdminRole.id == admin.role_id))).scalar_one_or_none()
        if role is None:   # a role that vanished grants nothing
            return AdminCtx(admin, None, {}, _all("hidden"), {"stages": []}, {}, None,
                            getattr(request.state, "admin_session_id", None))
    return ctx_from(admin, role, request)


def require_admin_perm(code: str):
    """Route dependency: refuse (and log the refusal) unless the role allows `code`."""
    assert code in ALL_PERMS, code

    async def dep(request: Request, ctx: AdminCtx = Depends(admin_ctx), db=Depends(get_db)) -> AdminCtx:
        if not ctx.can(code):
            from backend.services import admin_log
            await admin_log.refuse(db, admin=ctx.admin, action=code, ip=ctx.ip,
                                   target=f"{request.method} {request.url.path}")
        return ctx
    return dep


# ── scope ────────────────────────────────────────────────────────────────────
def scope_shops(q, ctx: AdminCtx):
    """Filter an Organisation query to the shops this admin may reach."""
    sc = ctx.scope or {}
    if sc.get("countries"):
        q = q.where(Organisation.country.in_([c.upper() for c in sc["countries"]]))
    if sc.get("own_book"):
        q = q.where(Organisation.assigned_admin_id == ctx.admin.id)
    if sc.get("own_book_otherwise"):
        # Shops in `open_stages` for everyone in this role; any other shop only if it's theirs.
        open_statuses = [s for st in sc.get("open_stages", []) for s in STAGES.get(st, [])]
        conds = [Organisation.assigned_admin_id == ctx.admin.id]
        if open_statuses:
            conds.append(Organisation.sub_status.in_(open_statuses))
        if "trial" in open_statuses:
            conds.append(Organisation.sub_status.is_(None))
        q = q.where(or_(*conds))
    if "stages" in sc and sc["stages"] is not None:
        statuses = [s for st in sc["stages"] for s in STAGES.get(st, [])]
        if not statuses:
            return q.where(Organisation.id.is_(None))   # nothing
        conds = [Organisation.sub_status.in_(statuses)]
        if "trial" in statuses:
            conds.append(Organisation.sub_status.is_(None))
        q = q.where(or_(*conds))
    return q


async def get_shop(db, ctx: AdminCtx, org_id: str) -> Organisation:
    """The shop if it exists and is in scope; otherwise 404 (out of scope looks missing)."""
    q = scope_shops(select(Organisation).where(Organisation.id == org_id), ctx)
    org = (await db.execute(q)).scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Shop not found")
    return org


def can_see(ctx: AdminCtx, org: Organisation, group: str) -> bool:
    lv = ctx.level(group)
    return lv == "shown" or (lv == "own" and org.assigned_admin_id == ctx.admin.id)


def wider_than(ctx: AdminCtx, spec: dict) -> bool:
    """True when a role spec (permissions, field levels, scope, limits) gives anything the
    acting admin doesn't have. Owners can hand out anything; nobody else can hand out more
    than they hold."""
    if ctx.is_owner:
        return False
    if any(v and not ctx.can(k) for k, v in (spec.get("permissions") or {}).items()):
        return True
    rank = {"hidden": 0, "own": 1, "shown": 2}
    for g, lv in (spec.get("field_levels") or {}).items():
        if rank.get(lv, 0) > rank.get(ctx.level(g), 0):
            return True
    if (spec.get("scope") or {}) != (ctx.scope or {}) and not _narrower_scope(spec.get("scope") or {}, ctx.scope or {}):
        return True
    for k, v in (spec.get("limits") or {}).items():
        mine = (ctx.limits or {}).get(k)
        if mine is None or float(v) > float(mine):
            return True
    for k in (ctx.limits or {}):
        if k not in (spec.get("limits") or {}):
            return True   # no limit at all is wider than any limit
    return False


def _narrower_scope(theirs: dict, mine: dict) -> bool:
    """Conservative: a scope counts as narrower only if it keeps every restriction of mine."""
    for k, v in mine.items():
        if theirs.get(k) != v:
            if k == "countries" and v and theirs.get(k) and set(theirs[k]) <= set(v):
                continue
            if k == "stages" and v is not None and theirs.get(k) is not None and set(theirs[k]) <= set(v):
                continue
            return False
    return True


def mask(row: dict, org: Organisation | None, ctx: AdminCtx) -> dict:
    """Remove field groups this role can't see for this shop."""
    own = org is not None and org.assigned_admin_id == ctx.admin.id
    out = dict(row)
    for group, keys in FIELD_GROUPS.items():
        lv = ctx.level(group)
        if lv == "shown" or (lv == "own" and own):
            continue
        for k in keys:
            out.pop(k, None)
    return out


# ── limits ───────────────────────────────────────────────────────────────────
def over_discount_limit(ctx: AdminCtx, pct: float, until: date | None) -> str | None:
    """None when within this role's discount limit, else why it needs approval."""
    if ctx.is_owner:
        return None
    lim = ctx.limits or {}
    cap = lim.get("max_discount_pct")
    if cap is None or pct > float(cap):
        return f"Above your discount limit of {cap or 0}%"
    months = lim.get("max_discount_months")
    if months is not None:
        if until is None:
            return "Your discounts need an end date"
        if until > date.today() + timedelta(days=31 * int(months)):
            return f"Longer than your {months}-month limit"
    return None


def over_trial_limit(ctx: AdminCtx, days: int) -> str | None:
    if ctx.is_owner:
        return None
    cap = (ctx.limits or {}).get("max_trial_days")
    if cap is None or days > int(cap):
        return f"Above your limit of {cap or 0} days"
    return None


def effective_discount(org: Organisation, today: date | None = None) -> float:
    """The discount in force today: 0 once its end date has passed."""
    pct = float(org.discount_pct or 0)
    if org.discount_until and org.discount_until < (today or date.today()):
        return 0.0
    return pct


__all__ = ["AdminCtx", "admin_ctx", "require_admin_perm", "scope_shops", "get_shop", "mask", "can_see",
           "wider_than", "over_discount_limit", "over_trial_limit", "effective_discount", "ensure_builtin_roles",
           "PERMISSIONS", "ALL_PERMS", "FIELD_GROUPS", "BUILTIN_ROLES", "PLANS"]
