"""QuickBooks Online endpoints (Phase 2C): OAuth connect/callback, connection
status, push sync, and the mapping + sync-log audit trail."""
import html
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.models import Employee, IntegrationMapping, IntegrationSyncLog
from backend.services.auth import get_current_employee, require_perm
from backend.services import qbo

router = APIRouter(prefix="/api/integrations/qbo", tags=["integrations"])


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    conn = await qbo.get_connection(db, current.org_id)
    return {"integration": "qbo", "configured": qbo.is_configured(),
            "connected": bool(conn and conn.connected),
            "realm_id": conn.realm_id if conn else None,
            "environment": conn.environment if conn else qbo.settings.QBO_ENVIRONMENT}


@router.get("/connect")
async def connect(db: AsyncSession = Depends(get_db),
                  current: Employee = Depends(require_perm("can_manage_settings"))):
    return await qbo.begin_connect(db, current.org_id)


@router.get("/callback")
async def callback(code: str = Query(...), realmId: str = Query(...), state: str = Query(...),
                   db: AsyncSession = Depends(get_db)):
    """OAuth redirect target. Unauthenticated by design — the browser follows
    Intuit's redirect without a Bearer token, so org/user context is recovered
    from the signed ``state`` (see qbo._parse_state)."""
    # Intuit realm (company) ids are short alphanumeric tokens; anything else is a tampered
    # redirect. Never reflect request values into this unauthenticated HTML page unescaped.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", realmId or ""):
        raise HTTPException(400, "Invalid QuickBooks company id")
    conn = await qbo.complete_connect(db, code, realmId, state)
    return HTMLResponse(f"<h3>QuickBooks connected</h3><p>Realm {html.escape(conn.realm_id or '')}. "
                        "You can close this window.</p>")


class _SyncResult(dict):
    pass


@router.post("/sync/{entity_type}/{local_id}")
async def sync(entity_type: str, local_id: str,
               db: AsyncSession = Depends(get_db),
               current: Employee = Depends(require_perm("can_view_finance"))):
    if entity_type not in ("invoice", "payment", "contact", "item"):
        from fastapi import HTTPException
        raise HTTPException(400, "entity_type must be invoice|payment|contact|item")
    return await qbo.sync_entity(db, current.org_id, entity_type, local_id)


@router.post("/sync-items")
async def sync_items(db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_manage_settings"))):
    """Build 144ag: push every active product to QuickBooks as an item, so invoice
    lines can reference real QBO items. Idempotent (mapped items update in place)."""
    from backend.models import Part
    parts = (await db.execute(select(Part).where(
        Part.org_id == current.org_id, Part.active == True))).scalars().all()  # noqa: E712
    ok = 0
    for p in parts:
        res = await qbo.sync_entity(db, current.org_id, "item", p.id)
        ok += 1 if res.get("status") == "ok" else 0
    return {"total": len(parts), "synced": ok}


@router.post("/pull/{entity_type}")
async def pull(entity_type: str,
               db: AsyncSession = Depends(get_db),
               current: Employee = Depends(require_perm("can_manage_settings"))):
    """Build 144ag: two-way — import existing QuickBooks customers/items and link
    them to matching RepairOS records so we update instead of duplicating."""
    from fastapi import HTTPException
    if entity_type not in ("contact", "item"):
        raise HTTPException(400, "entity_type must be contact|item")
    return await qbo.pull(db, current.org_id, entity_type)


@router.get("/mappings")
async def mappings(db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    rows = (await db.execute(select(IntegrationMapping).where(
        IntegrationMapping.org_id == current.org_id, IntegrationMapping.integration == "qbo"))).scalars().all()
    return {"items": [{"entity_type": m.entity_type, "local_id": m.local_id, "remote_id": m.remote_id,
                       "created_at": m.created_at.isoformat() if m.created_at else None} for m in rows]}


@router.get("/logs")
async def logs(db: AsyncSession = Depends(get_db), current: Employee = Depends(get_current_employee)):
    rows = (await db.execute(select(IntegrationSyncLog).where(
        IntegrationSyncLog.org_id == current.org_id, IntegrationSyncLog.integration == "qbo")
        .order_by(desc(IntegrationSyncLog.created_at)).limit(100))).scalars().all()
    return {"items": [{"entity_type": x.entity_type, "local_id": x.local_id, "direction": x.direction,
                       "status": x.status, "remote_id": x.remote_id, "error": x.error,
                       "created_at": x.created_at.isoformat() if x.created_at else None} for x in rows]}


@router.post("/disconnect")
async def disconnect(db: AsyncSession = Depends(get_db),
                     current: Employee = Depends(require_perm("can_manage_settings"))):
    conn = await qbo.get_connection(db, current.org_id)
    if conn:
        conn.connected = False
        conn.access_token = None
        conn.refresh_token = None
        await db.commit()
    return {"ok": True}
