"""A4 — Xero accounting connector (gated OAuth seam over services/xero.py).

Real code; never fires without XERO_CLIENT_ID/SECRET configured. Mirrors the
shipped QBO connector so both accounting integrations share one shape.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee
from backend.services import xero
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(prefix="/api/integrations/xero", tags=["integrations"])


@router.get("/status")
async def xero_status(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    return await xero.status(db, current.org_id)


@router.get("/connect")
async def xero_connect(
    redirect_uri: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if not xero.is_configured():
        raise HTTPException(400, "Xero is not configured (set XERO_CLIENT_ID/SECRET)")
    return await xero.begin_connect(db, current.org_id, redirect_uri)


@router.get("/callback")
async def xero_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    return await xero.handle_callback(db, current.org_id, code, state)


@router.post("/sync/{entity_type}/{local_id}")
async def xero_sync(
    entity_type: str, local_id: str,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    if entity_type not in ("invoice", "contact", "payment"):
        raise HTTPException(422, "entity_type must be invoice|contact|payment")
    return await xero.push_entity(db, current.org_id, entity_type, local_id)


@router.post("/disconnect")
async def xero_disconnect(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    return await xero.disconnect(db, current.org_id)
