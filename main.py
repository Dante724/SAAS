import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import backend.core.scheduled  # noqa: F401 — registers periodic jobs
from backend.config import settings
from backend.core.jobs import job_runner
from backend.core.logging import setup_logging
from backend.core.middleware import RequestContextMiddleware
from backend.db.database import init_db
from backend.routers import (
    admin,
    ai,
    analytics,
    apikeys,
    appointments,
    assets,
    auth,
    automations,
    bins,
    bundles,
    cash,
    catalog,
    catalog_products,
    chats,
    checklists,
    clients,
    comms,
    comms_ai,
    compliance,
    conversions,
    currencies,
    custom_reports,
    directories,
    documents,
    engage,
    feed,
    feedback,
    finance,
    forms,
    inbox,
    industry,
    insights,
    integrations_credentials,
    integrations_email,
    integrations_qbo,
    integrations_xero,
    inventory,
    leads,
    lookups,
    media,
    mfa,
    notifications,
    orders,
    payments,
    payroll,
    platform,
    prefs,
    pricing,
    printing,
    public,
    push,
    rbac,
    recommendations,
    regions,
    reports,
    resources,
    returns,
    sales,
    scan,
    schedules,
    settings_finance,
    sso,
    staff,
    statuses,
    stock_docs,
    suppliers,
    tags,
    tasks,
    tech,
    telephony,
    timeclock,
    transfers,
    v1,
    warehouses,
    webhooks,
)

setup_logging()
from backend.core.error_tracking import init_error_tracking  # noqa: E402

init_error_tracking()
log = logging.getLogger("repairos")

ROUTERS = [
    auth, orders, clients, inventory, finance, staff, reports, feed, forms,
    notifications, public, catalog, assets, cash, statuses, feedback, documents,
    bins, payroll, leads, pricing, sales, returns, transfers, bundles, appointments,
    chats, resources, timeclock, webhooks, payments, apikeys, v1,
    integrations_qbo, ai, catalog_products, tech, push, checklists, lookups, engage, settings_finance, rbac, directories, analytics, media, compliance, platform, mfa, sso, comms, integrations_xero, integrations_email, integrations_credentials, industry, schedules, conversions, warehouses, tasks, stock_docs, suppliers, printing, inbox, comms_ai, automations, insights, recommendations, prefs, admin, currencies, tags, telephony, custom_reports, regions, scan,
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting RepairOS (env=%s)", settings.ENVIRONMENT)
    if settings.is_deployed and not _have_react:
        raise RuntimeError(f"React build missing ({_react_index}). Build frontend-react before starting "
                           "in production/staging (the Dockerfile does this).")
    await init_db()
    # Build 144ce: ensure a bootstrap platform admin exists for the /admin portal.
    try:
        from backend.db.database import AsyncSessionLocal
        from backend.services.platform_auth import ensure_seed_admin
        async with AsyncSessionLocal() as _s:
            await ensure_seed_admin(_s)
    except Exception:  # noqa: BLE001 — never block startup on this
        log.exception("platform admin seed skipped")
    if settings.SEED_DEMO:
        if settings.is_deployed:
            log.warning("SEED_DEMO is enabled in production — refusing to seed demo data.")
        else:
            from scripts.seed import seed_demo_data
            await seed_demo_data()
    await job_runner.start()
    try:
        yield
    finally:
        await job_runner.stop()
        log.info("RepairOS shut down cleanly")


# Build 144cq: hide the interactive API docs / schema in production.
_docs_kwargs = {} if not settings.is_deployed else {
    "docs_url": None, "redoc_url": None, "openapi_url": None,
}
app = FastAPI(
    title="RepairOS API",
    description="Orderry-style repair shop management",
    version="0.1.0",
    lifespan=lifespan,
    **_docs_kwargs,
)

app.add_middleware(RequestContextMiddleware)
from backend.core.observability import MetricsMiddleware, RateLimitMiddleware, SecurityHeadersMiddleware

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RateLimitMiddleware, limit=600, window_s=60)
from backend.core.body_limit import BodySizeLimitMiddleware

# Inside CORS so a 413 still carries CORS headers and the browser can show the message.
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.MAX_REQUEST_BYTES)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # Credentials cannot be combined with a wildcard origin (CORS spec); only
    # enable them for an explicit allowlist.
    allow_credentials="*" not in settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in ROUTERS:
    app.include_router(r.router)
app.include_router(sales.st_router)
app.include_router(industry.scan_router)


@app.get("/health", include_in_schema=False)
@app.get("/api/health", tags=["meta"])
async def health():
    """Liveness/readiness probe — checks DB connectivity."""
    from sqlalchemy import text

    from backend.db.database import AsyncSessionLocal
    db_ok = True
    try:
        async with AsyncSessionLocal() as s:
            await s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
        log.exception("health check: database unreachable")
    status = "ok" if db_ok else "degraded"
    return JSONResponse(
        {"status": status, "environment": settings.ENVIRONMENT, "database": "up" if db_ok else "down"},
        status_code=200 if db_ok else 503,
    )


# ── Frontend (SPA) ──
frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
app.mount("/static", StaticFiles(directory=os.path.join(frontend_dir, "static")), name="static")

# ── React redesign (guarded root cutover) ──
# The Liquid Clay redesign IS the app now and is served at "/". It has no login
# screen of its own, so the (unchanged) legacy login lives at "/login" and the
# other auth deep-links (reset / verify / invite). The React bundle shares this
# origin and reuses the existing bearer session — same data, same endpoints, same
# flow, new UI. Assets are served from the built dist folder at /assets/*.
react_dir = os.path.join(os.path.dirname(__file__), "frontend-react", "dist")
_react_index = os.path.join(react_dir, "index.html")
_have_react = os.path.isfile(_react_index)
class _HashedAssets(StaticFiles):
    """Vite file names carry a content hash, so they can be cached for a year without ever going
    stale; a new build references new names."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code in (200, 304):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


if os.path.isdir(os.path.join(react_dir, "assets")):
    app.mount("/assets", _HashedAssets(directory=os.path.join(react_dir, "assets")), name="react-assets")


_NOT_BUILT = ("<!doctype html><meta charset=utf-8><title>RepairOS</title>"
              "<body style='font-family:system-ui;padding:40px'><h2>The web app isn't built</h2>"
              "<p>Run <code>cd frontend-react &amp;&amp; npm ci &amp;&amp; npm run build</code>, then restart. "
              "Sign-in is still available at <a href='/login'>/login</a>.</p></body>")


def _serve_react():
    # The old fallback served the legacy shell, whose hashed bundle no longer exists — a blank
    # page. Say what's wrong instead (deployed environments refuse to boot without the build).
    if _have_react:
        # The shell names the current hashed bundles. Without a cache policy browsers reused a
        # stale copy heuristically, so after a deploy people kept running the previous release.
        return FileResponse(_react_index, headers={"Cache-Control": "no-cache"})
    return HTMLResponse(_NOT_BUILT, status_code=503)


@app.get("/", include_in_schema=False)
async def serve_frontend():
    return _serve_react()


# All auth pages are now React (served from the redesign bundle; main.tsx detects
# the path and renders the matching <Login/> / <Signup/> / <ResetPassword/> /
# <VerifyEmail/> / <AcceptInvite/>). The legacy vanilla-JS auth shell is retired.
@app.get("/login", include_in_schema=False)
@app.get("/signup", include_in_schema=False)
@app.get("/reset-password", include_in_schema=False)
@app.get("/verify-email", include_in_schema=False)
@app.get("/accept-invite", include_in_schema=False)
async def serve_auth_pages():
    return _serve_react()


# Back-compat: the old preview path now just points at the redesign at root.
@app.get("/app2", include_in_schema=False)
@app.get("/app2/{path:path}", include_in_schema=False)
async def serve_react_legacy_path(path: str = ""):
    return _serve_react()


@app.get("/track", include_in_schema=False)
async def serve_track_lookup():
    # Bare /track → React lookup page (order number + phone → the tracking link).
    return _serve_react()


@app.get("/track/{token}", include_in_schema=False)
async def serve_track(token: str):
    # Public tracking / estimate / e-sign is now a React page (served from the
    # redesign bundle; it detects "/track/" and renders <PublicTrack/>).
    return _serve_react()


@app.get("/admin", include_in_schema=False)
async def serve_admin():
    # Build 144cf: the separate platform-operator portal (own login, outside any tenant).
    return FileResponse(os.path.join(frontend_dir, "admin.html"))


@app.get("/book/{slug}", include_in_schema=False)
async def serve_book(slug: str):
    # Appointment 2.0 — React page (main.tsx detects "/book/" and renders <PublicAppointment/>).
    return _serve_react()


# ── Technician PWA ──
@app.get("/tech", include_in_schema=False)
async def serve_tech():
    return FileResponse(os.path.join(frontend_dir, "tech.html"))


@app.get("/sw.js", include_in_schema=False)
async def serve_sw():
    # Root-scoped service worker (controls "/"). No-cache so updates ship promptly.
    return FileResponse(
        os.path.join(frontend_dir, "sw.js"),
        media_type="text/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/manifest.webmanifest", include_in_schema=False)
async def serve_manifest():
    return FileResponse(
        os.path.join(frontend_dir, "manifest.webmanifest"),
        media_type="application/manifest+json",
    )


@app.get("/pay/{intent_id}", include_in_schema=False)
async def serve_pay(intent_id: str):
    return FileResponse(os.path.join(frontend_dir, "pay.html"))


@app.get("/offline.html", include_in_schema=False)
async def serve_offline():
    return FileResponse(os.path.join(frontend_dir, "offline.html"))


@app.get("/portal/{token}", include_in_schema=False)
async def serve_portal(token: str):
    return FileResponse(os.path.join(frontend_dir, "portal.html"))


def _contained_file(base_dir: str, rel: str):
    """Return the real path of `rel` under `base_dir` if it is an existing file *inside*
    base_dir, else None. Guards the SPA fallback against path traversal: `..` segments,
    percent-encoded slashes (decoded by the server before routing), absolute paths (which
    os.path.join would otherwise let replace the base) and symlinks pointing outside."""
    if not rel or "\x00" in rel or "\\" in rel:
        return None
    try:
        base = os.path.realpath(base_dir)
        cand = os.path.realpath(os.path.join(base, rel))
        if os.path.commonpath([base, cand]) != base or not os.path.isfile(cand):
            return None
    except (ValueError, OSError):
        return None
    return cand


@app.get("/{path:path}", include_in_schema=False)
async def serve_spa(path: str):
    # Never let the SPA fallback answer for API/asset namespaces — return a real 404.
    if path.startswith(("api/", "static/", "assets/")):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    # Serve a real built React file (favicon, vite.svg, etc.) if present.
    if path and _have_react:
        rcand = _contained_file(react_dir, path)
        if rcand:
            return FileResponse(rcand)
    # Then any real legacy file (public assets), else the React SPA shell so
    # client-side routes resolve on hard refresh. Traversal attempts fall through to
    # the shell, exactly like any other unknown path.
    candidate = _contained_file(frontend_dir, path)
    if candidate:
        return FileResponse(candidate)
    return _serve_react()
