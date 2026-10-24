"""The admin action log: every admin action, allowed, refused or sent for approval.

Rows are written in the same transaction as the change they record, so a rolled-back
change leaves no row. Refusals are committed on their own (there is no change).
Append-only twice over: a database trigger refuses UPDATE and DELETE, and each row
stores a SHA-256 over its content plus the previous row's hash, so a gap or an edit
shows up in ``verify_chain``.
"""
import hashlib
import hmac
import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select, text

from backend.models import AdminActionLog, PlatformAdmin

_FIELDS = ("at", "admin_id", "admin_email", "action", "outcome", "org_id", "target", "before", "after",
           "reason", "ip")
_LOCK_KEY = 7_315_144   # Postgres advisory lock: one writer extends the chain at a time
VERIFY_ROWS = 2000      # /log checks the most recent rows; a full check is a script away


def _key() -> bytes:
    # Keyed, so someone with database access can't rewrite rows and recompute the chain.
    from backend.services.platform_auth import _key as signing_key
    return hashlib.sha256(("admin-log:" + signing_key()).encode()).digest()


def _digest(prev: str | None, row: AdminActionLog) -> str:
    body = {f: getattr(row, f) for f in _FIELDS}
    body["at"] = row.at.isoformat() if row.at else None
    raw = (prev or "") + json.dumps(body, sort_keys=True, default=str, separators=(",", ":"))
    return hmac.new(_key(), raw.encode(), hashlib.sha256).hexdigest()


async def write(db, *, admin: PlatformAdmin | None, action: str, org_id=None, target=None,
                before=None, after=None, reason=None, outcome="allowed", ip=None) -> AdminActionLog:
    if db.bind.dialect.name == "postgresql":   # SQLite already writes one at a time
        await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    prev = (await db.execute(select(AdminActionLog.row_hash).order_by(AdminActionLog.id.desc()).limit(1))).scalar()
    row = AdminActionLog(at=datetime.utcnow(), admin_id=admin.id if admin else None,
                         admin_email=admin.email if admin else None, action=action, outcome=outcome,
                         org_id=org_id, target=(str(target)[:120] if target else None),
                         before=before, after=after, reason=(reason or None) and str(reason)[:500],
                         ip=(ip or "")[:64] or None, prev_hash=prev)
    row.row_hash = _digest(prev, row)
    db.add(row)
    await db.flush()
    return row


async def refuse(db, *, admin, action, detail="You don't have permission to do that", status=403, **kw):
    """Log a refused action (committed on its own) and raise."""
    from types import SimpleNamespace
    who = SimpleNamespace(id=admin.id, email=admin.email) if admin else None   # read before rollback expires it
    await db.rollback()
    await write(db, admin=who, action=action, outcome="refused", **kw)
    await db.commit()
    raise HTTPException(status, detail)


async def verify_chain(db, last: int | None = VERIFY_ROWS) -> bool:
    """True when every row (or the most recent `last`) links to the one before and
    still matches its hash. A removed row breaks the link; an edited row breaks its hash."""
    q = select(AdminActionLog).order_by(AdminActionLog.id.desc())
    if last:
        q = q.limit(last)
    rows = list(reversed((await db.execute(q)).scalars().all()))
    if not rows:
        return True
    prev = rows[0].prev_hash if last else None
    if not last and rows[0].prev_hash is not None:
        return False
    for row in rows:
        if row.prev_hash != prev or row.row_hash != _digest(prev, row):
            return False
        prev = row.row_hash
    return True
