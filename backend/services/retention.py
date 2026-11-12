"""Data retention: delete old notifications, call records, messages and audit entries.

Each shop chooses how many days to keep each kind (Organisation.retention_days, edited at
/api/compliance/retention). Nothing is set by default, so nothing is deleted until the owner
turns it on. A daily job (core/scheduled.py) runs purge_all; expired portal links are always
cleared. Deletes run shop by shop in batches of BATCH rows, committed as they go; a shop that fails
is logged (tekly.retention) and the others still run.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("tekly.retention")

# PERF-18: rows deleted per statement. A shop turning retention on for three years of audit log
# used to send one DELETE for all of it, which the 15 s statement timeout cancelled every night
# (silently). Each batch is its own short statement and is committed as it goes.
BATCH = 5000

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


def _batched(stmt):
    """The same DELETE, limited to BATCH rows: `DELETE … WHERE id IN (SELECT id … LIMIT n)`
    (Postgres has no DELETE … LIMIT). Run until it deletes fewer than BATCH."""
    table = stmt.table
    ids = select(table.c.id).where(stmt.whereclause).limit(BATCH)
    return delete(table).where(table.c.id.in_(ids))


async def purge_org(db: AsyncSession, org, now: datetime | None = None) -> dict:
    """Delete this shop's records older than its retention settings, BATCH rows per statement,
    committing after every batch so a long purge never holds one huge transaction (or loses what
    it already deleted). Returns {kind: rows}."""
    now = now or datetime.utcnow()
    done = {}
    for kind, days in clean(org.retention_days).items():
        n = 0
        for stmt in _statements(kind, org.id, now - timedelta(days=days)):
            while True:
                got = (await db.execute(_batched(stmt).execution_options(
                    synchronize_session=False))).rowcount or 0
                n += got
                await db.commit()
                if got < BATCH:
                    break
        done[kind] = n
    return done


async def purge_all(db: AsyncSession, now: datetime | None = None) -> dict:
    """Every shop that set a retention period, one shop at a time: its batches are committed as
    they go, and a shop that fails is logged and rolled back without touching the others
    (PERF-18: one transaction for every shop, and a failure was swallowed with no log line)."""
    from backend.models import Organisation
    from backend.services import portal_links
    total: dict = {"portal_links": await portal_links.purge_expired(db)}
    await db.commit()
    orgs = [(oid, days) for oid, days in (await db.execute(
        select(Organisation.id, Organisation.retention_days)
        .where(Organisation.retention_days.is_not(None)).order_by(Organisation.id))).all()]
    failed = 0
    for oid, days in orgs:
        org = _Org(oid, days)
        try:
            done = await purge_org(db, org, now)
        except Exception:  # noqa: BLE001 — one shop's bad setting or timeout mustn't stop the rest
            await db.rollback()
            failed += 1
            log.exception("retention purge failed for shop %s", oid)
            continue
        for k, n in done.items():
            total[k] = total.get(k, 0) + n
    if failed:
        total["failed_shops"] = failed
        log.error("retention purge: %d shop(s) failed", failed)
    return total


class _Org:
    """What purge_org reads of a shop (id and settings), without keeping ORM rows across commits."""
    __slots__ = ("id", "retention_days")

    def __init__(self, id, retention_days):
        self.id, self.retention_days = id, retention_days
