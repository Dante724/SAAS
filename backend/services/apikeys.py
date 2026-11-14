"""Public REST API authentication (Phase 2B, hardened in apih1).

API keys look like `tk_<prefix>_<secret>` (keys made before the Tekly rename start with `rok_` and
keep working). Only the sha256 hash is stored. The
public-API dependency resolves a bearer token to an org context (an ApiPrincipal),
checks scopes, and stamps last_used. Management of keys (create/list/rotate/revoke) lives
in routers/apikeys.py under the normal employee session.

Scopes (apih1):
- ``read``: every GET on /api/v1.
- ``write``: the POST routes. ``write`` implies ``read``.
- ``costs:read``: cost fields (product cost_price, time-entry cost_rate / billable_rate) appear
  in responses. Without it they are left out. Only someone who may see costs can mint it.
- ``<resource>:read`` / ``<resource>:write`` (WS-C): narrow a key to one resource on /api/v2.
  ``<resource>:write`` implies ``<resource>:read``. A broad scope covers every v2 resource except
  ``webhooks``, which always needs an explicit ``webhooks:read`` / ``webhooks:write``. v1 needs a
  broad scope.
Keys made before apih1 keep the scopes they were stored with. The legacy ``*`` means read and
write, never costs:read.
"""
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import ApiKey

SCOPES = ("read", "write", "costs:read")
DEFAULT_SCOPES = ("read",)
DEFAULT_TTL = timedelta(days=365)
ROTATION_GRACE = timedelta(hours=24)
MAX_ACTIVE_KEYS = 25
LAST_USED_EVERY = timedelta(minutes=1)

# auto_error=False: we raise our own 401s (same messages as before), and the scheme still lands
# in the OpenAPI document as the bearer security requirement.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="ApiKey",
                           description="A Tekly API key: `Authorization: Bearer tk_…`. "
                                       "Create one in Settings → Integrations → API keys.")


# New keys start with tk_ (Tekly). Keys issued under the old RepairOS name start with rok_ and are
# still accepted until they expire or are rotated.
KEY_PREFIX = "tk_"
KEY_PREFIXES = ("tk_", "rok_")


def is_api_key(token: str) -> bool:
    return token.startswith(KEY_PREFIXES)


def generate_key() -> tuple[str, str, str]:
    """Return (full_plaintext_key, prefix, sha256_hash). Shown once at creation."""
    prefix = secrets.token_hex(4)            # 8 hex chars
    secret = secrets.token_urlsafe(24)
    full = f"{KEY_PREFIX}{prefix}_{secret}"
    return full, f"{KEY_PREFIX}{prefix}", hashlib.sha256(full.encode()).hexdigest()


def hash_key(full: str) -> str:
    return hashlib.sha256(full.encode()).hexdigest()


# WS-C: resource scopes narrow a key to one resource ("inquiries:read", "products:write"). They
# work on /api/v2 only; v1 needs a broad read / write.
RESOURCES = ("contacts", "orders", "estimates", "products", "appointments", "invoices", "credit_notes",
             "time_entries", "inquiries", "tags", "services", "bundles", "webhooks", "locations", "employees")
BROAD = ("read", "write", "*")
# Never implied by a broad scope: managing webhooks would let any old "read,write" key subscribe
# a URL of its choosing to every event, so it needs an explicit webhooks:read / webhooks:write.
EXPLICIT_ONLY = ("webhooks",)
RESOURCE_SCOPES = tuple(f"{r}:{a}" for r in RESOURCES for a in ("read", "write"))
ALL_SCOPES = SCOPES + RESOURCE_SCOPES

# WS-C: per-key request budget, enforced when the key is checked. The shop-wide middleware
# limit (RATE_LIMIT_USER_PER_MIN, bucketed on the key id) still applies on top; the headers
# report whichever of the two is closer to running out.
DEFAULT_RATE_PER_MIN = 120
MAX_RATE_PER_MIN = 6000


def parse_scopes(raw: Optional[str]) -> set[str]:
    """The scopes stored on a key row (lenient: unknown names are kept but grant nothing)."""
    return {s.strip() for s in (raw or "").split(",") if s.strip()}


def normalize_scopes(value) -> list[str]:
    """Scopes asked for when a key is minted: a list or a csv. The legacy '*' is stored as
    read + write. Raises ValueError naming anything unknown, or when nothing is picked."""
    if value is None:
        return list(DEFAULT_SCOPES)
    items = [x.strip() for x in value.split(",")] if isinstance(value, str) else [str(x).strip() for x in value]
    out: list[str] = []
    for s in (x for x in items if x):
        if s not in BROAD and s not in ALL_SCOPES:
            raise ValueError(f"Unknown scope “{s[:60]}”. Use read, write, costs:read or "
                             f"<resource>:read|write with a resource from: {', '.join(RESOURCES)}")
        for t in (("read", "write") if s == "*" else (s,)):
            if t not in out:
                out.append(t)
    if not out:
        raise ValueError("Pick at least one scope")
    return out


def validate_scopes(value) -> list[str]:
    """normalize_scopes for callers outside a request model: a 422 instead of ValueError."""
    try:
        return normalize_scopes(value)
    except ValueError as e:
        raise HTTPException(422, str(e))


def effective_scopes(stored: set[str]) -> set[str]:
    """What a stored scope set allows. Legacy '*' is read + write (never costs:read); write
    implies read, both broad and per resource ("orders:write" ⇒ "orders:read")."""
    out = {s for s in stored if s in ALL_SCOPES}
    if "*" in stored:
        out |= {"read", "write"}
    if "write" in out:
        out.add("read")
    out |= {s[:-len(":write")] + ":read" for s in out if s.endswith(":write")}
    return out


@dataclass
class ApiPrincipal:
    org_id: str
    key_id: str
    scopes: set[str]

    @property
    def can_see_costs(self) -> bool:
        return "costs:read" in self.scopes

    def allows(self, action: str, resource: Optional[str] = None) -> bool:
        """Without a resource (v1) only a broad scope counts. With one (v2), a broad scope
        covers every resource except the explicit-only ones (webhooks), and
        "<resource>:<action>" covers that resource."""
        eff = effective_scopes(self.scopes)
        if resource is None:
            return action in eff
        if resource not in EXPLICIT_ONLY and action in eff:
            return True
        return f"{resource}:{action}" in eff

    def require(self, scope: str, resource: Optional[str] = None) -> None:
        if self.allows(scope, resource):
            return
        need = f"{resource}:{scope}" if resource else scope
        raise HTTPException(403, f"API key lacks '{need}' scope")


def key_lookup_clause(token_hash: str, now: datetime):
    """SQL condition: an active, unexpired key whose current secret (or, during a rotation's
    grace period, previous secret) hashes to token_hash."""
    return and_(
        ApiKey.active == True,  # noqa: E712
        or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
        or_(ApiKey.key_hash == token_hash,
            and_(ApiKey.previous_key_hash == token_hash, ApiKey.previous_expires_at > now)),
    )


async def _authenticate(creds: Optional[HTTPAuthorizationCredentials], db: AsyncSession,
                        method: str = "GET") -> tuple[ApiKey, ApiPrincipal]:
    if creds is None or not creds.credentials:
        raise HTTPException(401, "Missing API key (Authorization: Bearer tk_...)",
                            headers={"WWW-Authenticate": "Bearer"})
    token = creds.credentials.strip()
    if not is_api_key(token):
        raise HTTPException(401, "Not a public API key", headers={"WWW-Authenticate": "Bearer"})
    now = datetime.utcnow()
    row = (await db.execute(select(ApiKey).where(key_lookup_clause(hash_key(token), now)))).scalars().first()
    if not row:
        raise HTTPException(401, "Invalid, expired or revoked API key", headers={"WWW-Authenticate": "Bearer"})
    # A key stops with the person who made it: deactivating a teammate also cuts off the
    # integrations they set up (a new key can be made by someone still on the team). A key with no
    # recorded creator has nobody to check, so it is refused too (aul3 gave old ones to the owner).
    # SEC-09 (final review): and it never does more than its creator may do NOW. A key acts
    # shop-wide, so it works only while its creator could still make it (key managers whose role
    # sees the whole shop); narrowing their role pauses it, restoring the role resumes it.
    from backend.models import Employee
    creator = (await db.execute(select(Employee).where(
        Employee.id == row.created_by_id))).scalar_one_or_none() if row.created_by_id else None
    if creator is None or not creator.active or await creator_key_block(db, creator):
        raise HTTPException(401, "This API key's creator no longer has access. Create a new key.",
                            headers={"WWW-Authenticate": "Bearer"})
    # A suspended or lapsed shop's integrations stop with it (keys used to keep working).
    from backend.models import Organisation
    from backend.services.auth import assert_org_active
    from backend.services.billing import enforce_api_key
    org = (await db.execute(select(Organisation).where(
        Organisation.id == row.org_id))).scalar_one_or_none()
    assert_org_active(org)
    enforce_api_key(org, method)   # API access is Pro; a read-only shop's keys can only read
    # Same flush-time shop guard as signed-in staff (db/tenancy.py): an API write can't touch
    # or point at another shop's rows.
    db.sync_session.info["tenant_org"] = row.org_id
    scopes = await creator_key_scopes(db, creator, parse_scopes(row.scopes))
    return row, ApiPrincipal(org_id=row.org_id, key_id=row.id, scopes=scopes)


KEY_MANAGER_PERM = "can_manage_settings"


def role_is_shop_wide(role) -> bool:
    """No data limits at all: every location, every record, every warehouse and cashbox, no
    hidden statuses. ("selected" with no locations picked counts as limited.) Payroll scope is
    not compared: no API key surface reads payroll."""
    if role is None:
        return False
    if any((getattr(role, f, None) or "all") != "all"
           for f in ("location_scope", "order_scope", "contact_scope", "warehouse_scope")):
        return False
    return not (getattr(role, "account_ids", None) or getattr(role, "hidden_status_keys", None))


async def creator_key_block(db: AsyncSession, emp) -> Optional[str]:
    """Why this person may not hold (make, or keep using) an API key, or None if they may.
    SEC-09: a key acts on the whole shop, so only the owner or a key manager whose role sees the
    whole shop can stand behind one."""
    from backend.models import Role
    from backend.services.auth import is_shop_owner
    from backend.services.permissions import role_allows
    if await is_shop_owner(db, emp):
        return None
    role = (await db.execute(select(Role).where(Role.id == emp.role_id))).scalar_one_or_none() \
        if emp.role_id else None
    if role is None or not role_allows(role, KEY_MANAGER_PERM):
        return "Only someone who manages settings can make API keys"
    if not role_is_shop_wide(role):
        return ("An API key works on the whole shop, so it can only be made by the owner or someone "
                "whose role isn't limited to some locations, records, warehouses, cashboxes or statuses")
    return None


async def creator_key_scopes(db: AsyncSession, emp, scopes: set) -> set:
    """The key's scopes, capped by what its creator may see now: costs:read needs the creator to
    still see purchase prices."""
    if "costs:read" in scopes:
        from backend.services.auth import has_perm
        from backend.services.redact import COST_PERM
        if not await has_perm(db, emp, COST_PERM):
            scopes = set(scopes) - {"costs:read"}
    return scopes


def key_rate_limit(row: ApiKey) -> int:
    return min(max(getattr(row, "rate_limit_per_min", None) or DEFAULT_RATE_PER_MIN, 1), MAX_RATE_PER_MIN)


async def _enforce_key_limit(row: ApiKey, request: Optional[Request]) -> None:
    """WS-C per-key limit. The result goes on request.state so RateLimitMiddleware can report
    the binding limit in X-RateLimit-*; a 429 here carries those headers itself."""
    from backend.services.ratelimit import aenforce
    limit = key_rate_limit(row)
    try:
        remaining = await aenforce("api-key-min", row.id, limit, 60)
    except HTTPException as e:
        raise HTTPException(429, "Rate limit exceeded for this API key",
                            headers={**(e.headers or {}), "X-RateLimit-Limit": str(limit),
                                     "X-RateLimit-Remaining": "0"})
    if request is not None:
        request.state.api_key_rate = (limit, remaining)


async def _touch(db: AsyncSession, row: ApiKey) -> None:
    """Stamp last_used_at at most once a minute per key (it used to be a write + commit on
    every request). Runs only after the scope check passed."""
    now = datetime.utcnow()
    if row.last_used_at and now - row.last_used_at < LAST_USED_EVERY:
        return
    await db.execute(update(ApiKey).where(
        ApiKey.id == row.id,
        or_(ApiKey.last_used_at.is_(None), ApiKey.last_used_at < now - LAST_USED_EVERY),
    ).values(last_used_at=now).execution_options(synchronize_session=False))
    await db.commit()


def require_scope(scope: Optional[str], resource: Optional[str] = None):
    """Dependency factory: authenticate the key, apply its own rate limit, enforce a scope
    (optionally for one resource; None = any valid key), then stamp last use."""
    async def _dep(
        request: Request,
        creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
        db: AsyncSession = Depends(get_db),
    ) -> ApiPrincipal:
        row, principal = await _authenticate(creds, db, request.method)
        await _enforce_key_limit(row, request)
        if scope is not None:
            principal.require(scope, resource)
        await _touch(db, row)
        return principal
    return _dep


# Kept for callers that only need "a valid key" (it means the broad read scope).
get_api_principal = require_scope("read")
# Any valid key, whatever its scopes (v2 discovery shows a key its own shop and scopes).
any_api_key = require_scope(None)
