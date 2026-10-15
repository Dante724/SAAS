"""B4 metrics/readiness · B5 white-label branding · B6 SIEM export + bulk API."""
import hmac
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.observability import render_metrics
from backend.db.database import get_db
from backend.models import AuditLog, Client, Employee, Organisation
from backend.services.auth import get_current_employee, require_perm

router = APIRouter(tags=["platform"])
log = logging.getLogger("repairos.platform")


# ── B4: metrics + readiness ──────────────────────────────────────────────────
@router.get("/metrics", include_in_schema=False)
async def metrics(authorization: str | None = Header(None)):
    """Prometheus scrape. Request counts and latency per route reveal traffic and endpoints,
    so a deployed instance serves them only to a scraper holding METRICS_TOKEN (and hides
    the endpoint entirely when none is configured)."""
    want = settings.METRICS_TOKEN
    if want:
        got = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(got, want):
            raise HTTPException(401, "Metrics token required", headers={"WWW-Authenticate": "Bearer"})
    elif settings.is_deployed:
        raise HTTPException(404)
    return Response(content=render_metrics(), media_type="text/plain; version=0.0.4")


@router.get("/api/readyz", tags=["meta"])
async def readyz(db: AsyncSession = Depends(get_db)):
    """Readiness: verifies the DB is reachable (for k8s/LB probes)."""
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ready", "db": "ok"}
    except Exception:  # pragma: no cover
        # The driver error names hosts, users and database names — log it, don't publish it.
        log.exception("readiness check: database unreachable")
        return Response(status_code=503, content=json.dumps({"status": "unready", "db": "unreachable"}),
                        media_type="application/json")


# ── B5: white-label branding ─────────────────────────────────────────────────
class _NoBody:
    pass


@router.get("/api/branding", tags=["branding"])
async def get_branding(
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(get_current_employee),
):
    org = (await db.execute(select(Organisation).where(
        Organisation.id == current.org_id))).scalar_one()
    return {
        "name": org.name,
        "primary_color": org.brand_primary_color,
        "custom_domain": org.brand_custom_domain,
        "email_from": org.brand_email_from,
        "hide_powered_by": org.brand_hide_powered_by,
        "logo": org.logo_data,
    }


@router.put("/api/branding", tags=["branding"])
async def set_branding(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    org = (await db.execute(select(Organisation).where(
        Organisation.id == current.org_id))).scalar_one()
    for field, col in (("primary_color", "brand_primary_color"),
                       ("custom_domain", "brand_custom_domain"),
                       ("email_from", "brand_email_from")):
        if field in payload:
            setattr(org, col, payload[field])
    if "hide_powered_by" in payload:
        org.brand_hide_powered_by = bool(payload["hide_powered_by"])
    await db.commit()
    return {"ok": True}


# Public branding resolver (unauth) — for white-label portal/login theming by domain.
@router.get("/api/public/branding", tags=["branding"])
async def public_branding(
    domain: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    org = (await db.execute(select(Organisation).where(
        Organisation.brand_custom_domain == domain))).scalar_one_or_none()
    if not org:
        return {"found": False}
    return {"found": True, "name": org.name,
            "primary_color": org.brand_primary_color,
            "hide_powered_by": org.brand_hide_powered_by,
            "logo": org.logo_data}


# ── B6: SIEM export + bulk API ───────────────────────────────────────────────
@router.get("/api/siem/audit.ndjson", tags=["siem"])
async def siem_audit_export(
    limit: int = Query(1000, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Audit log as newline-delimited JSON (NDJSON) — the lingua franca for SIEM
    ingestion (Splunk/Elastic/Datadog). One event per line."""
    rows = (await db.execute(select(AuditLog).where(
        AuditLog.org_id == current.org_id).order_by(AuditLog.created_at.desc()).limit(limit))).scalars().all()
    lines = []
    for r in rows:
        lines.append(json.dumps({
            "ts": r.created_at.isoformat() if r.created_at else None,
            "org_id": r.org_id, "actor_id": r.actor_id, "action": r.action,
            "entity_type": r.entity_type, "entity_id": r.entity_id, "diff": r.diff,
        }, separators=(",", ":")))
    return Response(content="\n".join(lines) + ("\n" if lines else ""),
                    media_type="application/x-ndjson")


@router.get("/api/bulk/{entity}", tags=["bulk"])
async def bulk_export(
    entity: str,
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current: Employee = Depends(require_perm("can_manage_settings")),
):
    """Paginated bulk export for integration/back-office ETL. Supported:
    clients, employees."""
    models = {"clients": (Client, ("id", "name", "client_type", "phone", "email")),
              "employees": (Employee, ("id", "name", "email", "active"))}
    if entity not in models:
        from fastapi import HTTPException
        raise HTTPException(404, f"Unsupported entity. Try: {sorted(models)}")
    model, fields = models[entity]
    rows = (await db.execute(select(model).where(model.org_id == current.org_id)
                             .limit(limit).offset(offset))).scalars().all()
    return {"entity": entity, "count": len(rows), "offset": offset,
            "items": [{f: getattr(r, f, None) for f in fields} for r in rows]}
