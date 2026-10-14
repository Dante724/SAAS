"""Build 144ca — per-user UI preferences (list column layouts + saved views),
so a user's choices follow them across devices instead of living in one browser.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, UserPref
from backend.services.auth import get_current_employee

router = APIRouter(prefix="/api/prefs", tags=["prefs"])

_ALLOWED_PREFIX = ("listview:",)   # keep the store scoped to known UI prefs
_ALLOWED_EXACT = ("lang", "layout")   # 144ci: language · 144cn: drag-anywhere layout offsets


def _ok_key(key: str) -> bool:
    return (key in _ALLOWED_EXACT or any(key.startswith(p) for p in _ALLOWED_PREFIX)) and len(key) <= 120


class PrefIn(BaseModel):
    value: dict


@router.get("/{pref_key}")
async def get_pref(pref_key: str, db: AsyncSession = Depends(get_db),
                   current: Employee = Depends(get_current_employee)):
    if not _ok_key(pref_key):
        return {"value": {}}
    row = (await db.execute(select(UserPref).where(
        UserPref.employee_id == current.id, UserPref.pref_key == pref_key))).scalar_one_or_none()
    return {"value": (row.value if row else {}) or {}}


@router.put("/{pref_key}")
async def put_pref(pref_key: str, data: PrefIn, db: AsyncSession = Depends(get_db),
                   current: Employee = Depends(get_current_employee)):
    if not _ok_key(pref_key):
        return {"ok": False, "reason": "unsupported key"}
    from datetime import datetime
    row = (await db.execute(select(UserPref).where(
        UserPref.employee_id == current.id, UserPref.pref_key == pref_key))).scalar_one_or_none()
    if row:
        row.value = data.value
        row.updated_at = datetime.utcnow()
    else:
        db.add(UserPref(org_id=current.org_id, employee_id=current.id,
                        pref_key=pref_key, value=data.value, updated_at=datetime.utcnow()))
    await db.commit()
    return {"ok": True}
