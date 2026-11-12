"""B2 — audit trail. A single helper to record who did what to which entity,
with an optional field-level diff. Best-effort: a failure to write the audit row never breaks the
business action, and never hides the caller's own errors either (audit NTF-07)."""
import logging
from typing import Optional

from backend.models import AuditLog

log = logging.getLogger("tekly.audit")


async def record(db, *, org_id: str, actor_id: Optional[str], action: str,
                 entity_type: str, entity_id: Optional[str] = None,
                 diff: Optional[dict] = None) -> None:
    """Append an audit entry. Caller commits (we only add+flush) so the audit row shares the
    business transaction and is rolled back if that fails.

    The caller's pending rows are flushed first, outside any guard: their errors are the caller's
    and propagate (a constraint error swallowed here used to resurface later as a confusing
    PendingRollbackError). The audit row itself is written in a savepoint, so if only it fails
    (e.g. the tenant guard refuses it) it is rolled back alone, logged, and the caller carries on
    with a usable session."""
    await db.flush()
    try:
        async with db.begin_nested():
            db.add(AuditLog(org_id=org_id, actor_id=actor_id, action=action,
                            entity_type=entity_type, entity_id=entity_id, diff=diff))
    except Exception:  # noqa: BLE001 — auditing never breaks the business action
        log.exception("audit row not written (org=%s action=%s %s %s)", org_id, action, entity_type, entity_id)


def diff_fields(before: dict, after: dict, keys: list[str]) -> dict:
    """Return {key: [old, new]} for keys whose value changed."""
    out = {}
    for k in keys:
        b, a = before.get(k), after.get(k)
        if b != a:
            out[k] = [b, a]
    return out
