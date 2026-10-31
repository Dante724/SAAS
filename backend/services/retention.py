"""Data retention: delete old notifications, call records, messages and audit entries.

Each shop chooses how many days to keep each kind (Organisation.retention_days, edited at
/api/compliance/retention). Nothing is set by default, so nothing is deleted until the owner
turns it on. A daily job (core/scheduled.py) runs purge_all; expired portal links are always
cleared.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

# kind -> label shown in Settings
KINDS = {
    "notifications": "Sent notifications (customer and staff)",
    "calls": "Call records, recordings and transcripts",
    "messages": "Chat and inbox messages",
    "audit": "Activity log (audit trail)",
}
MIN_DAYS = 30          # never shorter: a month of history is needed to answer a customer
MAX_DAYS = 3650


def clean(raw: dict | None) -> dict:
    """Validated {kind: days}; a missing or empty kind means keep for good."""
    from fastapi import HTTPException
    out = {}
    for k, v in (raw or {}).items():
        if k not in KINDS:
            raise HTTPException(422, f"Unknown kind of record: {k}")
        if v in (None, "", 0):
            continue
        try:
            days = int(v)
        except (TypeError, ValueError):
            raise HTTPException(422, "Days must be a whole number")
        if not MIN_DAYS <= days <= MAX_DAYS:
            raise HTTPException(422, f"Keep records for between {MIN_DAYS} and {MAX_DAYS} days")
        out[k] = days
    return out


def _statements(kind: str, org_id: str, cutoff: datetime) -> list:
    from backend.models import (AuditLog, Call, ChatMessage, ChatThread, InboxMessage,
                                NotificationLog, StaffNotification)
    if kind == "notifications":
        return [
            # queued and scheduled sends haven't gone out yet
            delete(NotificationLog).where(NotificationLog.org_id == org_id, NotificationLog.created_at < cutoff,
                                          NotificationLog.status.notin_(("queued", "scheduled"))),
            delete(StaffNotification).where(StaffNotification.org_id == org_id,
                                            StaffNotification.created_at < cutoff),
        ]
    if kind == "calls":
        return [delete(Call).where(Call.org_id == org_id, Call.created_at < cutoff)]
    if kind == "messages":
        return [
            delete(ChatMessage).where(ChatMessage.created_at < cutoff, ChatMessage.thread_id.in_(
                select(ChatThread.id).where(ChatThread.org_id == org_id))),
            delete(InboxMessage).where(InboxMessage.org_id == org_id, InboxMessage.created_at < cutoff),
        ]
    if kind == "audit":
        return [delete(AuditLog).where(AuditLog.org_id == org_id, AuditLog.created_at < cutoff)]
    return []


async def purge_org(db: AsyncSession, org, now: datetime | None = None) -> dict:
    """Delete this shop's records older than its retention settings. Returns {kind: rows}."""
    now = now or datetime.utcnow()
    done = {}
    for kind, days in clean(org.retention_days).items():
        n = 0
        for stmt in _statements(kind, org.id, now - timedelta(days=days)):
            n += (await db.execute(stmt.execution_options(synchronize_session=False))).rowcount or 0
        done[kind] = n
    return done


async def purge_all(db: AsyncSession, now: datetime | None = None) -> dict:
    from backend.models import Organisation
    from backend.services import portal_links
    total: dict = {"portal_links": await portal_links.purge_expired(db)}
    orgs = (await db.execute(select(Organisation).where(Organisation.retention_days.is_not(None)))).scalars().all()
    for org in orgs:
        try:
            for k, n in (await purge_org(db, org, now)).items():
                total[k] = total.get(k, 0) + n
        except Exception:  # noqa: BLE001 — one shop's bad setting mustn't stop the rest
            continue
    await db.commit()
    return total
