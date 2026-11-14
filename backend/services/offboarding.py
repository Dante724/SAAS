"""Offboarding a shop: export everything it owns, then switch it off (audit 2026-11-12, DB-12).

Nothing in the schema deletes a shop's data: every org_id foreign key is NO ACTION, and money
rows never cascade (DB-12). This is the one supported way to close a shop:

1. **Export** every row of every table that carries the shop's ``org_id`` (since corg01 that is
   every tenant table, money children included), plus the shop's own ``organisations`` row, as
   JSON-safe values. Secrets are left out: password and token hashes, MFA secrets, encrypted
   credentials and tokens, session rows (``EXCLUDED_TABLES`` / ``_secret``). Media bytes are
   base64. The caller hands the export to the shop (or to its accountant) before step 2.
2. **Soft-delete:** ``organisations.active = False`` and ``offboarded_at = now``; every staff
   account is deactivated and its tokens revoked (``token_version`` + 1), which also refuses the
   shop's API keys (an inactive creator's key is refused). Public pages, sends and background
   jobs already skip a shop that isn't active (services/org_live). One audit_log row records it.

**No hard purge here.** Offboarding keeps the rows, invisible to everyone (no staff can sign in, no
public link answers). Deleting them for good is a separate, manual step (owner decision 9):
services/shop_purge, run from /admin by an owner at least SHOP_PURGE_AFTER_DAYS (default 30, clamped to 30..90)
after offboarding, never on a schedule.

Run it from a shell with the platform console's database access, e.g.::

    from backend.services.offboarding import offboard_org
    result = await offboard_org(db, org_id, actor="admin@tekly.app", reason="Closed account, ticket 123")
    json.dump(result["export"], open(f"{org_id}.json", "w"))
    await db.commit()
"""
from __future__ import annotations

import base64
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

# Session and credential tables: nothing in them is the shop's business data.
EXCLUDED_TABLES = {"auth_sessions", "revoked_sessions", "auth_tokens", "api_keys", "client_portal_links",
                   "org_integration_credentials", "role_permission_backups", "idempotency_records",
                   "api_idempotency_keys"}
_SECRET_WORDS = ("password", "secret", "token", "credential", "mfa_", "_enc", "api_key", "key_hash")


def _secret(column: str) -> bool:
    return any(w in column for w in _SECRET_WORDS)


def _json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode()
    return value


async def export_org(db: AsyncSession, org_id: str) -> dict:
    """{table: [row dicts]} of everything the shop owns, secrets left out. Reads plain Table
    objects (the tenant read guard filters ORM entities only), always WHERE org_id = the shop."""
    from backend.db.database import Base
    from backend.models import all_models  # noqa: F401 — every table registered
    out: dict[str, list] = {}
    org_t = Base.metadata.tables["organisations"]
    cols = [c for c in org_t.columns if not _secret(c.name)]
    out["organisations"] = [{c.name: _json(v) for c, v in zip(cols, row)}
                            for row in (await db.execute(select(*cols).where(org_t.c.id == org_id))).all()]
    for t in Base.metadata.sorted_tables:
        if "org_id" not in t.c or t.name in EXCLUDED_TABLES:
            continue
        cols = [c for c in t.columns if not _secret(c.name)]
        rows = (await db.execute(select(*cols).where(t.c.org_id == org_id))).all()
        if rows:
            out[t.name] = [{c.name: _json(v) for c, v in zip(cols, row)} for row in rows]
    return out


async def offboard_org(db: AsyncSession, org_id: str, *, actor: str, reason: str,
                       now: datetime | None = None) -> dict:
    """Export the shop, then switch it off. The caller commits. Returns
    {"export": {...}, "counts": {table: rows}, "already": bool}. Running it again on an offboarded
    shop exports again and changes nothing."""
    from backend.models import Employee, Organisation
    from backend.services import audit
    from backend.services.dates import now_utc
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id)
                            .execution_options(skip_tenant_filter=True))).scalar_one_or_none()
    if org is None:
        raise LookupError("No such shop")
    if not (reason or "").strip():
        raise ValueError("Say why the shop is offboarded")
    export = await export_org(db, org_id)
    counts = {t: len(rows) for t, rows in export.items()}
    already = org.offboarded_at is not None
    if not already:
        org.active = False
        org.offboarded_at = now or now_utc()
        await db.execute(update(Employee).where(Employee.org_id == org_id).values(
            active=False, token_version=Employee.token_version + 1)
            .execution_options(skip_tenant_filter=True, synchronize_session=False))
        await audit.record(db, org_id=org_id, actor_id=None, action="organisation.offboarded",
                           entity_type="organisation", entity_id=org_id,
                           diff={"by": actor[:200], "reason": reason.strip()[:500], "rows_exported": counts})
    return {"export": export, "counts": counts, "already": already}
