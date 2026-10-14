"""API key management (Phase 2B). Lives under the normal employee session;
the keys it mints authenticate the public /api/v1 surface."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import ApiKey, Employee
from backend.services.auth import get_current_employee, require_perm
from backend.services.apikeys import generate_key

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


def _ser(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "scopes": k.scopes,
            "active": k.active, "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "created_at": k.created_at.isoformat() if k.created_at else None}


@router.get("")
async def list_keys(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    rows = (await db.execute(select(ApiKey).where(
        ApiKey.org_id == current.org_id).order_by(desc(ApiKey.created_at)))).scalars().all()
    return {"items": [_ser(k) for k in rows]}


class KeyIn(BaseModel):
    name: str = "API key"
    scopes: str = "read,write"


@router.post("", status_code=201)
async def create_key(
    data: KeyIn,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    full, prefix, key_hash = generate_key()
    k = ApiKey(org_id=current.org_id, name=data.name, prefix=prefix, key_hash=key_hash,
               scopes=data.scopes or "read,write", created_by_id=current.id)
    db.add(k)
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
    await db.commit()
    return {"ok": True, "revoked": key_id}
