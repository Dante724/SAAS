"""API key management (Phase 2B, hardened in apih1). Lives under the normal employee session;
the keys it mints authenticate the public /api/v1 and /api/v2 surfaces.

- Listing, creating, rotating and revoking all need can_manage_settings.
- Scopes are a fixed set: broad read / write / costs:read, plus <resource>:read|write for v2
  (services/apikeys.py). A new key defaults to read only.
- Each key has its own per-minute rate limit (default 120, at most 6000).
- costs:read can only be granted by someone whose role may see costs.
- Keys expire (default one year), and an org holds at most MAX_ACTIVE_KEYS active keys.
- Rotation issues a new secret; the old one keeps working for ROTATION_GRACE.
"""
from datetime import datetime, timedelta
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import ApiKey, Employee
from backend.services import audit
from backend.services.apikeys import (
    ALL_SCOPES,
    DEFAULT_RATE_PER_MIN,
    DEFAULT_SCOPES,
    DEFAULT_TTL,
    EXPLICIT_ONLY,
    MAX_ACTIVE_KEYS,
    MAX_RATE_PER_MIN,
    RESOURCES,
    ROTATION_GRACE,
    SCOPES,
    generate_key,
    key_rate_limit,
    normalize_scopes,
    parse_scopes,
)
from backend.services.auth import has_perm, require_perm

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])

_MANAGE = require_perm("can_manage_settings")


def _status(k: ApiKey, now: datetime) -> str:
    if not k.active:
        return "revoked"
    if k.expires_at and k.expires_at <= now:
        return "expired"
    return "active"


def _ser(k: ApiKey, creators: Optional[dict] = None) -> dict:
    now = datetime.utcnow()
    creator = (creators or {}).get(k.created_by_id)
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "scopes": k.scopes,
            "scope_list": sorted(parse_scopes(k.scopes)),
            "rate_limit_per_min": getattr(k, "rate_limit_per_min", None), "effective_rate_limit_per_min": key_rate_limit(k),
            "active": k.active, "status": _status(k, now),
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "rotated_at": k.rotated_at.isoformat() if k.rotated_at else None,
            "previous_expires_at": (k.previous_expires_at.isoformat()
                                    if k.previous_expires_at and k.previous_expires_at > now else None),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "created_by_id": k.created_by_id,
            "created_by_name": creator.name if creator else None,
            # A4: a key whose creator has left keeps working; the list says so, so it gets reviewed.
            "creator_inactive": bool(creator is not None and creator.active is False)}


async def _creators(db: AsyncSession, keys) -> dict:
    ids = {k.created_by_id for k in keys if k.created_by_id}
    if not ids:
        return {}
    rows = (await db.execute(select(Employee).where(Employee.id.in_(ids)))).scalars().all()
    return {e.id: e for e in rows}


@router.get("")
async def list_keys(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(_MANAGE),
):
    rows = (await db.execute(select(ApiKey).where(
        ApiKey.org_id == current.org_id).order_by(desc(ApiKey.created_at)))).scalars().all()
    creators = await _creators(db, rows)
    return {"items": [_ser(k, creators) for k in rows], "available_scopes": list(SCOPES),
            "resources": list(RESOURCES), "explicit_only": list(EXPLICIT_ONLY),
            "resource_scopes": [s for s in ALL_SCOPES if s not in SCOPES],
            "default_rate_limit_per_min": DEFAULT_RATE_PER_MIN, "max_rate_limit_per_min": MAX_RATE_PER_MIN,
            "max_active": MAX_ACTIVE_KEYS}


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field("API key", min_length=1, max_length=120)
    # Broad read / write / costs:read, or <resource>:read|write (API v2). A list or the old csv.
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES), min_length=1)
    # Days until the key stops working. Default one year; null = never expires.
    expires_in_days: Optional[int] = Field(int(DEFAULT_TTL.days), ge=1, le=3650)
    # WS-C: requests per minute for this key (default DEFAULT_RATE_PER_MIN).
    rate_limit_per_min: Optional[int] = Field(None, ge=1, le=MAX_RATE_PER_MIN)

    @field_validator("scopes", mode="before")
    @classmethod
    def _scopes(cls, v: Union[str, list, None]):
        return normalize_scopes(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


@router.post("", status_code=201)
async def create_key(
    data: KeyIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(_MANAGE),
):
    scopes = sorted(set(data.scopes))
    if "costs:read" in scopes:
        from backend.services.redact import COST_PERM
        if not await has_perm(db, current, COST_PERM):
            raise HTTPException(403, "Only someone who can see purchase prices can grant costs:read")
    now = datetime.utcnow()
    active = (await db.execute(select(func.count()).select_from(ApiKey).where(
        ApiKey.org_id == current.org_id, ApiKey.active == True,  # noqa: E712
        or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now)))).scalar() or 0
    if active >= MAX_ACTIVE_KEYS:
        raise HTTPException(409, f"This shop already has {MAX_ACTIVE_KEYS} active API keys. Revoke one first.")
    full, prefix, key_hash = generate_key()
    expires = now + timedelta(days=data.expires_in_days) if data.expires_in_days else None
    k = ApiKey(org_id=current.org_id, name=data.name, prefix=prefix, key_hash=key_hash,
               scopes=",".join(scopes), rate_limit_per_min=data.rate_limit_per_min,
               created_by_id=current.id, expires_at=expires)
    db.add(k)
    await db.flush()
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="apikey.create",
                       entity_type="api_key", entity_id=k.id,
                       diff={"name": k.name, "scopes": k.scopes, "rate_limit_per_min": k.rate_limit_per_min,
                             "expires_at": expires.isoformat() if expires else None})
    await db.commit()
    await db.refresh(k)
    # the plaintext is returned exactly once
    return {**_ser(k, {current.id: current}), "key": full,
            "note": "Store this key now — it will not be shown again."}


async def _own_key(db: AsyncSession, current: Employee, key_id: str) -> ApiKey:
    k = (await db.execute(select(ApiKey).where(
        ApiKey.id == key_id, ApiKey.org_id == current.org_id))).scalar_one_or_none()
    if not k:
        raise HTTPException(404, "Key not found")
    return k


@router.post("/{key_id}/rotate")
async def rotate_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(_MANAGE),
):
    """Issue a new secret for the key. The old secret keeps working for 24 hours so the
    integration can be updated without downtime. Scopes, name and expiry stay the same."""
    k = await _own_key(db, current, key_id)
    now = datetime.utcnow()
    if _status(k, now) != "active":
        raise HTTPException(409, "Only an active key can be rotated")
    full, prefix, new_hash = generate_key()
    k.previous_key_hash = k.key_hash
    k.previous_expires_at = now + ROTATION_GRACE
    k.key_hash, k.prefix, k.rotated_at = new_hash, prefix, now
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="apikey.rotate",
                       entity_type="api_key", entity_id=k.id,
                       diff={"name": k.name, "old_secret_valid_until": k.previous_expires_at.isoformat()})
    await db.commit()
    await db.refresh(k)
    from backend.core.observability import forget_key
    forget_key(k.previous_key_hash)
    return {**_ser(k, await _creators(db, [k])), "key": full,
            "note": "Store this key now — it will not be shown again. The old key works for 24 more hours."}


@router.delete("/{key_id}")
async def revoke_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(_MANAGE),
):
    k = await _own_key(db, current, key_id)
    k.active = False
    # A revoke also ends a rotation's grace period at once.
    k.previous_expires_at = None
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="apikey.revoke",
                       entity_type="api_key", entity_id=k.id, diff={"name": k.name})
    await db.commit()
    from backend.core.observability import forget_key
    forget_key(k.key_hash)
    if k.previous_key_hash:
        forget_key(k.previous_key_hash)
    return {"ok": True, "revoked": key_id}

