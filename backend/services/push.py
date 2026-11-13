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

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import PushSubscription, StaffNotification
from backend.services.after_commit import current_tx, is_outermost, undone
from backend.services.dates import now_utc
from backend.services.money import json_default

log = logging.getLogger("tekly.push")

# A Web Push service that doesn't answer must not hold a request (or a sweep) open: pywebpush passes
# this on to requests (audit NTF-06; its default is to wait forever).
PUSH_TIMEOUT_S = 10

VALID_KINDS = {
    "job.assigned", "schedule.changed", "chat.message",
    "estimate.approved", "appointment.cancelled", "payment.received",
    "inventory.reorder", "membership.alert",
    "task.assigned", "task.changed", "task.completed",   # tasks phase 0: tasks reach the bell
    "task.overdue",                                      # tk02: the overdue sweep
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
    with dispatch_deferred() after its own commit. Prefer it inside a transaction or savepoint:
    the push then goes out only for work that was actually saved.

    `commit=False` never commits the caller's session, and the push waits for the caller's
    commit: it is sent once that commit succeeds and dropped if the caller rolls back, so a push
    never announces work that wasn't saved (audit NTF-01 follow-up).
    """
    if not await _still_works_here(db, employee_id):
        return None                            # re-review N3: nobody who has left gets a bell or push
    if respect_prefs and not await _kind_enabled(db, employee_id, kind):
        return None
    n = StaffNotification(org_id=org_id, employee_id=employee_id, kind=kind,
                          title=title, body=body, deep_link=deep_link)
    db.add(n)
    if commit:
        await db.commit()
        await db.refresh(n)
    else:
        await db.flush()                       # gives the notification its id (the push's "id"), no commit
    payload = {
        "title": title, "body": body, "kind": kind,
        # "url" mirrors deep_link for clients that look for the conventional key
        "deep_link": deep_link or "/tech", "url": deep_link or "/tech", "id": n.id,
    }
    if deferred is not None:
        deferred.append((employee_id, payload))
    elif commit:
        await _dispatch_push(db, employee_id, payload, commit=True)
    else:
        # The caller owns the transaction and may still fail and roll back: send after its commit.
        _after_commit(db, org_id, employee_id, payload)
    return n


# ── commit=False: send once the caller's transaction commits ────────────────────────────────────
_AFTER_COMMIT = "tekly_push_after_commit"
_SENDING: set = set()          # tasks sending pushes after a commit, kept referenced until done


def _after_commit(db: AsyncSession, org_id: str, employee_id: str, payload: dict) -> None:
    sync = getattr(db, "sync_session", db)
    # Tagged with the transaction (or savepoint) it was queued in: see services/after_commit.
    sync.info.setdefault(_AFTER_COMMIT, []).append((current_tx(sync), db.bind, org_id, employee_id, payload))


@event.listens_for(Session, "after_commit")
def _send_committed(session) -> None:
    if not is_outermost(session):
        return                      # a SAVEPOINT release: the caller's work isn't saved yet (N4)
    items = [i[1:] for i in session.info.pop(_AFTER_COMMIT, None) or ()]
    if not items:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:            # a sync session outside the app's loop: nothing can send
        log.warning("%d push(es) not sent: no event loop after commit", len(items))
        return
    task = loop.create_task(_send_items(items))
    _SENDING.add(task)
    task.add_done_callback(_SENDING.discard)


@event.listens_for(Session, "after_soft_rollback")
def _drop_rolled_back(session, rolled_back) -> None:
    """Drop the pushes whose bell items were rolled back. A SAVEPOINT rollback (a failed audit row,
    a rule, an automation) undoes only what was queued inside it; earlier pushes stay (N4)."""
    items = session.info.get(_AFTER_COMMIT)
    if not items:
        return
    kept = [i for i in items if not undone(i[0], rolled_back)]
    if kept:
        session.info[_AFTER_COMMIT] = kept
    else:
        session.info.pop(_AFTER_COMMIT, None)


async def _send_items(items: list) -> None:
    """Send pushes queued in a committed transaction, in a session of their own (the caller's
    session is still in use by the request that committed)."""
    from sqlalchemy.ext.asyncio import AsyncSession as _AS

    from backend.db.tenancy import set_tenant
    for bind, org_id, employee_id, payload in items:
        try:
            async with _AS(bind=bind, expire_on_commit=False) as s:
                set_tenant(s, org_id)
                await _dispatch_push(s, employee_id, payload, commit=True)
        except Exception:  # noqa: BLE001 — a push is best-effort, never anyone's failure
            log.exception("push after commit failed (employee=%s)", employee_id)


async def wait_pending() -> None:
    """Wait for pushes being sent after a commit (tests, and a clean shutdown)."""
    while _SENDING:
        await asyncio.gather(*list(_SENDING), return_exceptions=True)


async def dispatch_deferred(db: AsyncSession, items: list) -> None:
    """Send pushes collected with notify(deferred=...), once the caller has committed."""
    for employee_id, payload in items:
        try:
            await _dispatch_push(db, employee_id, payload, commit=True)
        except Exception:  # noqa: BLE001 — a push is best-effort, never the caller's failure
            log.exception("deferred push failed (employee=%s)", employee_id)


async def _still_works_here(db: AsyncSession, employee_id: Optional[str]) -> bool:
    """False for a deactivated employee (left the shop); unknown ids are left to the caller."""
    from backend.models import Employee
    active = (await db.execute(select(Employee.active).where(Employee.id == employee_id))).scalar_one_or_none()
    return active is not False


async def _kind_enabled(db: AsyncSession, employee_id: str, kind: str) -> bool:
    """Notifications are on by default; a row with enabled=False mutes a kind."""
    from backend.models import NotificationPreference
    pref = (await db.execute(select(NotificationPreference).where(
        NotificationPreference.employee_id == employee_id,
        NotificationPreference.kind == kind))).scalar_one_or_none()
    return True if pref is None else bool(pref.enabled)


async def _dispatch_push(db: AsyncSession, employee_id: str, payload: dict, *, commit: bool) -> None:
    """Send to every device of the employee. Sending touches (last_used_at) or prunes (gone
    endpoints) subscriptions. ``commit`` saves that bookkeeping straight away, and only callers
    that own the transaction pass True: notify(commit=True), dispatch_deferred() and the
    after-commit sender. Committing a caller's session here used to save its half-done
    transaction (audit NTF-01)."""
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
                data=json.dumps(payload, default=json_default),   # DB-08: Decimal amounts as numbers
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims=dict(claims),
                timeout=PUSH_TIMEOUT_S,
            )
            s.last_used_at = now_utc()
        except Exception as e:  # pragma: no cover - needs live endpoint
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):  # gone -> prune dead subscription
                await db.delete(s)
            else:
                log.warning("push failed (employee=%s): %s", employee_id, e)
    if commit:
        await db.commit()
