"""API key management (Phase 2B). Lives under the normal employee session;
the keys it mints authenticate the public /api/v1 surface."""

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import ApiKey, Employee
from backend.services import audit
from backend.services.auth import require_perm
from backend.services.apikeys import MAX_RATE_PER_MIN, generate_key, parse_scopes

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


def _ser(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "scopes": k.scopes,
            "rate_limit_per_min": k.rate_limit_per_min, "active": k.active, "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "created_at": k.created_at.isoformat() if k.created_at else None}


@router.get("")
async def list_keys(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    rows = (await db.execute(select(ApiKey).where(
        ApiKey.org_id == current.org_id).order_by(desc(ApiKey.created_at)))).scalars().all()
    return {"items": [_ser(k) for k in rows]}


class KeyIn(BaseModel):
    name: str = Field("API key", max_length=120)
    scopes: str = Field("read,write", max_length=300)   # csv: read, write, * or <resource>:read|write
    rate_limit_per_min: Optional[int] = Field(None, ge=1, le=MAX_RATE_PER_MIN)


@router.post("", status_code=201)
async def create_key(
    data: KeyIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    scopes = ",".join(parse_scopes(data.scopes or "read,write"))
    full, prefix, key_hash = generate_key()
    k = ApiKey(org_id=current.org_id, name=data.name, prefix=prefix, key_hash=key_hash,
               scopes=scopes, rate_limit_per_min=data.rate_limit_per_min, created_by_id=current.id)
    db.add(k)
    await db.flush()
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="apikey.create",
                       entity_type="api_key", entity_id=k.id, diff={"name": k.name, "scopes": k.scopes})
    await db.commit()
    await db.refresh(k)
    # the plaintext is returned exactly once
    return {**_ser(k), "key": full,
            "note": "Store this key now — it will not be shown again."}


@router.delete("/{key_id}")
async def revoke_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    k = (await db.execute(select(ApiKey).where(
        ApiKey.id == key_id, ApiKey.org_id == current.org_id))).scalar_one_or_none()
    if not k:
        raise HTTPException(404, "Key not found")
    k.active = False
    await audit.record(db, org_id=current.org_id, actor_id=current.id, action="apikey.revoke",
                       entity_type="api_key", entity_id=k.id, diff={"name": k.name})
    await db.commit()
    return {"ok": True, "revoked": key_id}
