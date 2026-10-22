"""Public REST API authentication (Phase 2B).

API keys look like `rok_<prefix>_<secret>`. Only the sha256 hash is stored. The
public-API dependency resolves a bearer token to an org context (an ApiPrincipal),
checks scopes, and stamps last_used. Management of keys (create/list/revoke) lives
in routers/apikeys.py under the normal employee session.
"""
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import ApiKey


def generate_key() -> tuple[str, str, str]:
    """Return (full_plaintext_key, prefix, sha256_hash). Shown once at creation."""
    prefix = secrets.token_hex(4)            # 8 hex chars
    secret = secrets.token_urlsafe(24)
    full = f"rok_{prefix}_{secret}"
    return full, f"rok_{prefix}", hashlib.sha256(full.encode()).hexdigest()


def hash_key(full: str) -> str:
    return hashlib.sha256(full.encode()).hexdigest()


@dataclass
class ApiPrincipal:
    org_id: str
    key_id: str
    scopes: set[str]

    def require(self, scope: str) -> None:
        if "*" in self.scopes or scope in self.scopes:
            return
        raise HTTPException(403, f"API key lacks '{scope}' scope")


async def get_api_principal(
    request: Request,
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
    from backend.services.billing import enforce_api_key
    org = (await db.execute(select(Organisation).where(
        Organisation.id == row.org_id))).scalar_one_or_none()
    assert_org_active(org)
    enforce_api_key(org, request.method)   # API access is Pro; read-only shops' keys only read
    row.last_used_at = datetime.utcnow()
    await db.commit()
    scopes = {s.strip() for s in (row.scopes or "").split(",") if s.strip()}
    return ApiPrincipal(org_id=row.org_id, key_id=row.id, scopes=scopes)


def require_scope(scope: str):
    """Dependency factory: enforce a scope on a public-API route."""
    async def _dep(principal: ApiPrincipal = Depends(get_api_principal)) -> ApiPrincipal:
        principal.require(scope)
        return principal
    return _dep
