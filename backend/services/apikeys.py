"""Public REST API authentication (Phase 2B).

API keys look like `rok_<prefix>_<secret>`. Only the sha256 hash is stored. The
public-API dependency resolves a bearer token to an org context (an ApiPrincipal),
checks scopes, and stamps last_used. Management of keys (create/list/revoke) lives
in routers/apikeys.py under the normal employee session.
"""
import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import ApiKey
from backend.services.dates import now_utc


def generate_key() -> tuple[str, str, str]:
    """Return (full_plaintext_key, prefix, sha256_hash). Shown once at creation."""
    prefix = secrets.token_hex(4)            # 8 hex chars
    secret = secrets.token_urlsafe(24)
    full = f"rok_{prefix}_{secret}"
    return full, f"rok_{prefix}", hashlib.sha256(full.encode()).hexdigest()


def hash_key(full: str) -> str:
    return hashlib.sha256(full.encode()).hexdigest()


# WS-C: scoped keys. Legacy broad scopes ("read", "write", "*") keep working everywhere;
# "<resource>:read" / "<resource>:write" narrow a key to one resource (v2 only — v1 needs a
# broad scope). "write" implies "read".
RESOURCES = ("contacts", "orders", "estimates", "products", "appointments", "invoices", "credit_notes",
             "time_entries", "inquiries", "tags", "services", "bundles", "webhooks")
BROAD = ("read", "write", "*")
# Never implied by a broad scope: managing webhooks would let any old "read,write" key subscribe
# a URL of its choosing to every event, so it needs an explicit webhooks:read / webhooks:write.
EXPLICIT_ONLY = ("webhooks",)
DEFAULT_RATE_PER_MIN = 120
MAX_RATE_PER_MIN = 6000


def parse_scopes(raw: str) -> list[str]:
    """Validate a csv of scopes; raises 422 on anything unknown."""
    out = []
    for s in (x.strip() for x in (raw or "").split(",")):
        if not s:
            continue
        res, _, act = s.partition(":")
        ok = s in BROAD or (res in RESOURCES and act in ("read", "write"))
        if not ok:
            raise HTTPException(422, f"Unknown scope “{s}”. Use read, write, * or <resource>:read|write "
                                     f"with a resource from: {', '.join(RESOURCES)}")
        if s not in out:
            out.append(s)
    if not out:
        raise HTTPException(422, "Pick at least one scope")
    return out


@dataclass
class ApiPrincipal:
    org_id: str
    key_id: str
    scopes: set[str]

    def allows(self, action: str, resource: Optional[str] = None) -> bool:
        s = self.scopes
        if resource not in EXPLICIT_ONLY and ("*" in s or "write" in s or action in s):
            return True
        if resource and (f"{resource}:{action}" in s or (action == "read" and f"{resource}:write" in s)):
            return True
        return False

    def require(self, scope: str, resource: Optional[str] = None) -> None:
        if self.allows(scope, resource):
            return
        need = f"{resource}:{scope}" if resource else scope
        raise HTTPException(403, f"API key lacks '{need}' scope")


async def get_api_principal(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> ApiPrincipal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing API key (Authorization: Bearer rok_...)",
                            headers={"WWW-Authenticate": "Bearer"})
    token = authorization.split(" ", 1)[1].strip()
    if not token.startswith("rok_"):
        raise HTTPException(401, "Not a public API key")
    row = (await db.execute(select(ApiKey).where(
        ApiKey.key_hash == hash_key(token), ApiKey.active == True))).scalar_one_or_none()  # noqa: E712
    if not row:
        raise HTTPException(401, "Invalid or revoked API key")
    # A suspended or lapsed shop's integrations stop with it (keys used to keep working).
    from backend.models import Organisation
    from backend.services.auth import assert_org_active
    assert_org_active((await db.execute(select(Organisation).where(
        Organisation.id == row.org_id))).scalar_one_or_none())
    from backend.services.ratelimit import aenforce
    limit = min(max(row.rate_limit_per_min or DEFAULT_RATE_PER_MIN, 1), MAX_RATE_PER_MIN)
    await aenforce("api-key-min", row.id, limit, 60)
    row.last_used_at = now_utc()
    await db.commit()
    scopes = {s.strip() for s in (row.scopes or "").split(",") if s.strip()}
    return ApiPrincipal(org_id=row.org_id, key_id=row.id, scopes=scopes)


def require_scope(scope: str, resource: Optional[str] = None):
    """Dependency factory: enforce a scope (optionally for one resource) on a public-API route."""
    async def _dep(principal: ApiPrincipal = Depends(get_api_principal)) -> ApiPrincipal:
        principal.require(scope, resource)
        return principal
    return _dep
