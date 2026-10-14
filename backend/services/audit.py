"""B2 — audit trail. A single helper to record who did what to which entity,
with an optional field-level diff. Best-effort: auditing never breaks the
business action (failures are swallowed)."""
from typing import Optional

from backend.models import AuditLog


async def record(db, *, org_id: str, actor_id: Optional[str], action: str,
                 entity_type: str, entity_id: Optional[str] = None,
                 diff: Optional[dict] = None) -> None:
    """Append an audit entry. Caller commits (we only add+flush) so the audit
    row shares the business transaction and is rolled back if that fails."""
    try:
        db.add(AuditLog(org_id=org_id, actor_id=actor_id, action=action,
                        entity_type=entity_type, entity_id=entity_id, diff=diff))
        await db.flush()
    except Exception:
        pass


def diff_fields(before: dict, after: dict, keys: list[str]) -> dict:
    """Return {key: [old, new]} for keys whose value changed."""
    out = {}
    for k in keys:
        b, a = before.get(k), after.get(k)
        if b != a:
            out[k] = [b, a]
    return out
