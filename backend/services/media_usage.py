"""PERF-22 (owner decision 4): the platform admins' private alert on a shop's stored media.

There is no storage cap a shop can see or hit. Once a sweep finds a shop's stored media
(media_blobs.size_bytes) past MEDIA_ALERT_BYTES, it opens one MediaUsageAlert and logs it at
ERROR on tekly.media, so log alerting sees it. It also counts open alerts in /metrics as
repairos_media_shops_over_threshold. A shop that stays over is not alerted again. When it drops
back below, its alert is closed, so a later crossing alerts once more.

The sweep is platform-wide and reads only sizes, never the files.
"""
import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.config import settings
from backend.models import MediaBlob, MediaUsageAlert
from backend.services.dates import now_utc

log = logging.getLogger("tekly.media")

# Set by the last sweep on the worker that ran it (like reconcile.LAST_COUNTS); None = no sweep yet.
LAST_OVER: Optional[int] = None


def _naive(dt):
    return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt


async def usage_by_shop(db) -> dict[str, int]:
    rows = (await db.execute(
        select(MediaBlob.org_id, func.coalesce(func.sum(MediaBlob.size_bytes), 0))
        .group_by(MediaBlob.org_id).execution_options(skip_tenant_filter=True))).all()
    return {org_id: int(total) for org_id, total in rows}


async def shop_bytes(db, org_id: str) -> int:
    return int((await db.execute(
        select(func.coalesce(func.sum(MediaBlob.size_bytes), 0)).where(MediaBlob.org_id == org_id)
        .execution_options(skip_tenant_filter=True))).scalar() or 0)


async def sweep(db) -> int:
    """Open an alert for each shop that crossed the threshold since the last sweep, refresh the
    open ones and close those back below it. Returns how many new alerts were opened."""
    global LAST_OVER
    threshold = int(settings.MEDIA_ALERT_BYTES or 0)
    usage = await usage_by_shop(db)
    open_rows = {a.org_id: a for a in (await db.execute(
        select(MediaUsageAlert).where(MediaUsageAlert.cleared_at.is_(None))
        .execution_options(skip_tenant_filter=True))).scalars().all()}
    now = _naive(now_utc())
    opened = 0
    for org_id, alert in open_rows.items():
        total = usage.get(org_id, 0)
        if threshold <= 0 or total < threshold:
            alert.cleared_at = now
        else:
            alert.bytes = total
    await db.commit()
    if threshold > 0:
        for org_id, total in sorted(usage.items()):
            if total < threshold or org_id in open_rows:
                continue
            db.add(MediaUsageAlert(org_id=org_id, bytes=total, threshold_bytes=threshold, crossed_at=now))
            try:
                await db.commit()
            except IntegrityError:   # another worker opened it in the same instant: that one alerts
                await db.rollback()
                continue
            opened += 1
            log.error("media usage alert: shop %s stores %.1f GB of media, past the %.1f GB alert "
                      "threshold (MEDIA_ALERT_BYTES). Nothing is blocked; the shop is not told.",
                      org_id, total / 1024 ** 3, threshold / 1024 ** 3)
    LAST_OVER = (await db.execute(select(func.count()).select_from(MediaUsageAlert).where(
        MediaUsageAlert.cleared_at.is_(None)).execution_options(skip_tenant_filter=True))).scalar() or 0
    return opened


async def open_alerts(db, org_ids=None) -> list[dict]:
    """The open alerts, largest first; `org_ids` limits them to the shops an admin may see."""
    q = select(MediaUsageAlert).where(MediaUsageAlert.cleared_at.is_(None))
    if org_ids is not None:
        q = q.where(MediaUsageAlert.org_id.in_(list(org_ids)))
    rows = (await db.execute(q.order_by(MediaUsageAlert.bytes.desc())
                             .execution_options(skip_tenant_filter=True))).scalars().all()
    return [{"org_id": a.org_id, "bytes": int(a.bytes), "threshold_bytes": int(a.threshold_bytes),
             "crossed_at": a.crossed_at.isoformat() if a.crossed_at else None} for a in rows]
