"""Staff notifications + Web Push.

`notify()` is the single entry point: it persists a per-employee
StaffNotification (which powers the in-app unread count and the technician
dashboard) and then best-effort delivers a Web Push message to that employee's
subscribed devices.

Delivery is gated like every other provider in Tekly: without VAPID keys it
is logged/simulated, never failing the caller. With keys + the `pywebpush`
package installed it sends a real encrypted push.
"""
import asyncio
import json
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import PushSubscription, StaffNotification
from backend.services.dates import now_utc

log = logging.getLogger("tekly.push")

VALID_KINDS = {
    "job.assigned", "schedule.changed", "chat.message",
    "estimate.approved", "appointment.cancelled", "payment.received",
    "inventory.reorder", "membership.alert",
    "task.assigned", "task.changed", "task.completed",   # tasks phase 0: tasks reach the bell
}


def push_enabled() -> bool:
    return bool(getattr(settings, "VAPID_PRIVATE_KEY", "") and getattr(settings, "VAPID_PUBLIC_KEY", ""))


async def notify(db: AsyncSession, *, org_id: str, employee_id: str, kind: str,
                 title: str, body: str = "", deep_link: Optional[str] = None,
                 commit: bool = True, respect_prefs: bool = True,
                 deferred: Optional[list] = None) -> Optional[StaffNotification]:
    """Create a staff notification and dispatch push. Safe to call inline.

    If the employee has muted this `kind` (NotificationPreference.enabled=False)
    and `respect_prefs` is True, nothing is created or sent and None is returned.

    `deferred`: a list to collect the push into instead of sending it now; the caller sends it
    with dispatch_deferred() after its own commit. Use it inside a transaction or savepoint:
    sending commits (pruned or touched subscriptions), which would close the caller's transaction.
    """
    if respect_prefs and not await _kind_enabled(db, employee_id, kind):
        return None
    n = StaffNotification(org_id=org_id, employee_id=employee_id, kind=kind,
                          title=title, body=body, deep_link=deep_link)
    db.add(n)
    if commit:
        await db.commit()
        await db.refresh(n)
    elif deferred is not None:
        await db.flush()                       # gives the notification its id, no commit
    payload = {
        "title": title, "body": body, "kind": kind,
        "deep_link": deep_link or "/tech", "id": n.id,
    }
    if deferred is not None:
        deferred.append((employee_id, payload))
    else:
        await _dispatch_push(db, employee_id, payload)
    return n


async def dispatch_deferred(db: AsyncSession, items: list) -> None:
    """Send pushes collected with notify(deferred=...), once the caller has committed."""
    for employee_id, payload in items:
        try:
            await _dispatch_push(db, employee_id, payload)
        except Exception:  # noqa: BLE001 — a push is best-effort, never the caller's failure
            log.exception("deferred push failed (employee=%s)", employee_id)


async def _kind_enabled(db: AsyncSession, employee_id: str, kind: str) -> bool:
    """Notifications are on by default; a row with enabled=False mutes a kind."""
    from backend.models import NotificationPreference
    pref = (await db.execute(select(NotificationPreference).where(
        NotificationPreference.employee_id == employee_id,
        NotificationPreference.kind == kind))).scalar_one_or_none()
    return True if pref is None else bool(pref.enabled)


async def _dispatch_push(db: AsyncSession, employee_id: str, payload: dict) -> None:
    subs = (await db.execute(
        select(PushSubscription).where(PushSubscription.employee_id == employee_id)
    )).scalars().all()
    if not subs:
        return
    if not push_enabled():
        log.info("[push:simulated] -> employee=%s devices=%d payload=%s",
                 employee_id, len(subs), payload.get("title"))
        return
    try:
        from pywebpush import webpush  # type: ignore
    except Exception:
        log.warning("pywebpush not installed; push not sent (employee=%s)", employee_id)
        return
    claims = {"sub": f"mailto:{settings.VAPID_CLAIMS_EMAIL}"}
    for s in subs:
        try:
            # pywebpush is blocking HTTP: run it off the event loop.
            await asyncio.to_thread(
                webpush,
                subscription_info={"endpoint": s.endpoint,
                                   "keys": {"p256dh": s.p256dh, "auth": s.auth}},
                data=json.dumps(payload),
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims=dict(claims),
            )
            s.last_used_at = now_utc()
        except Exception as e:  # pragma: no cover - needs live endpoint
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):  # gone -> prune dead subscription
                await db.delete(s)
            else:
                log.warning("push failed (employee=%s): %s", employee_id, e)
    await db.commit()
