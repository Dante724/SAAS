"""Public REST API authentication (Phase 2B, hardened in apih1).

API keys look like `rok_<prefix>_<secret>`. Only the sha256 hash is stored. The
public-API dependency resolves a bearer token to an org context (an ApiPrincipal),
checks scopes, and stamps last_used. Management of keys (create/list/rotate/revoke) lives
in routers/apikeys.py under the normal employee session.

Scopes (apih1):
- ``read``: every GET on /api/v1.
- ``write``: the POST routes. ``write`` implies ``read``.
- ``costs:read``: cost fields (product cost_price, time-entry cost_rate / billable_rate) appear
  in responses. Without it they are left out. Only someone who may see costs can mint it.
Keys made before apih1 keep the scopes they were stored with. The legacy ``*`` means read and
write, never costs:read.
"""
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException
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
                           description="A Tekly API key: `Authorization: Bearer rok_…`. "
                                       "Create one in Settings → Integrations → API keys.")


def generate_key() -> tuple[str, str, str]:
    """Return (full_plaintext_key, prefix, sha256_hash). Shown once at creation."""
    prefix = secrets.token_hex(4)            # 8 hex chars
    secret = secrets.token_urlsafe(24)
    full = f"rok_{prefix}_{secret}"
    return full, f"rok_{prefix}", hashlib.sha256(full.encode()).hexdigest()


def hash_key(full: str) -> str:
    return hashlib.sha256(full.encode()).hexdigest()


def parse_scopes(raw: Optional[str]) -> set[str]:
    return {s.strip() for s in (raw or "").split(",") if s.strip()}


def effective_scopes(stored: set[str]) -> set[str]:
    """What a stored scope set allows: write implies read; legacy '*' is read + write."""
    out = {s for s in stored if s in SCOPES}
    if "*" in stored:
        out |= {"read", "write"}
    if "write" in out:
        out.add("read")
    return out


@dataclass
class ApiPrincipal:
    org_id: str
    key_id: str
    scopes: set[str]

    @property
    def can_see_costs(self) -> bool:
        return "costs:read" in self.scopes

    def require(self, scope: str) -> None:
        if scope in effective_scopes(self.scopes):
            return
        raise HTTPException(403, f"API key lacks '{scope}' scope")


def key_lookup_clause(token_hash: str, now: datetime):
    """SQL condition: an active, unexpired key whose current secret (or, during a rotation's
    grace period, previous secret) hashes to token_hash."""
    return and_(
        ApiKey.active == True,  # noqa: E712
        or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
        or_(ApiKey.key_hash == token_hash,
            and_(ApiKey.previous_key_hash == token_hash, ApiKey.previous_expires_at > now)),
    )


async def _authenticate(creds: Optional[HTTPAuthorizationCredentials], db: AsyncSession) -> tuple[ApiKey, ApiPrincipal]:
    if creds is None or not creds.credentials:
        raise HTTPException(401, "Missing API key (Authorization: Bearer rok_...)",
                            headers={"WWW-Authenticate": "Bearer"})
    token = creds.credentials.strip()
    if not token.startswith("rok_"):
        raise HTTPException(401, "Not a public API key", headers={"WWW-Authenticate": "Bearer"})
    now = datetime.utcnow()
    row = (await db.execute(select(ApiKey).where(key_lookup_clause(hash_key(token), now)))).scalars().first()
    if not row:
        raise HTTPException(401, "Invalid, expired or revoked API key", headers={"WWW-Authenticate": "Bearer"})
    # A suspended or lapsed shop's integrations stop with it (keys used to keep working).
    from backend.models import Organisation
    from backend.services.auth import assert_org_active
    assert_org_active((await db.execute(select(Organisation).where(
        Organisation.id == row.org_id))).scalar_one_or_none())
    return row, ApiPrincipal(org_id=row.org_id, key_id=row.id, scopes=parse_scopes(row.scopes))


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


def require_scope(scope: str):
    """Dependency factory: authenticate the key, enforce a scope, then stamp last use."""
    async def _dep(
        creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
        db: AsyncSession = Depends(get_db),
    ) -> ApiPrincipal:
        row, principal = await _authenticate(creds, db)
        principal.require(scope)
        await _touch(db, row)
        return principal
    return _dep


# Kept for callers that only need "a valid key" (it now means the read scope).
get_api_principal = require_scope("read")
