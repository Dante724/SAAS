"""Retire OrderStatus.triggers_notification: the notification rules are the one source of truth.

Before this, a status change ran the rules engine and, only when NO rule matched, fell back
to the legacy dispatch: every active status-change NotificationTemplate went out if the
status had ``triggers_notification`` on. New shops are seeded with an "email on every status
change" rule, so the status editor's "Text the customer" switch was nearly always ignored and
shop owners couldn't tell which setting was real.

Now the legacy fallback is gone (backend/routers/orders.py). To keep sending behaviour
identical for a shop that relied on it, each flagged status is converted into the per-status
rules the fallback would have produced, then the flag is cleared:

* event = ``closed`` for a terminal status, else ``status_changed`` (what the status change
  evaluates), ``trigger_status_key`` = the status key, ``record_type`` = the status's.
* one rule per template the fallback would have sent (generic status-change templates plus
  ones scoped to this status), with that template's channel and the template attached.
* nothing when a rule already fires for that status: the fallback never ran then, so adding
  a rule would double-send. A location-scoped rule counts as covering (conservative: it
  can't double-send; orders at other locations lose a send they only got by accident).
* nothing when no template matches: the fallback sent nothing either.

``plan`` is pure (plain dicts in, rule dicts out) so the startup self-heal and the Alembic
data migration share it without importing the app.
"""
from collections.abc import Iterable

_STATUS_EVENTS = ("status_change", "order_status_changed", "status_changed")


def plan(statuses: Iterable[dict], rules: Iterable[dict], templates: Iterable[dict]) -> list[dict]:
    """statuses: flagged OrderStatus rows {key, record_type, is_terminal}.
    rules: the org's NotificationRule rows {module, scope, event, trigger_status_key,
    record_type, active}. templates: NotificationTemplate rows {id, channel, trigger_event,
    trigger_status, active}. Returns NotificationRule field dicts to insert."""
    rules = [r for r in rules if r.get("active") and (r.get("module") or "order") == "order"
             and (r.get("scope") or "client") == "client"]
    tpls = [t for t in templates if t.get("active")
            and (t.get("trigger_event") or "").lower() in _STATUS_EVENTS]
    out: list[dict] = []
    for s in statuses:
        key, rt = s["key"], s.get("record_type")
        event = "closed" if s.get("is_terminal") else "status_changed"
        covered = any(r["event"] == event and r.get("trigger_status_key") in (None, "", key)
                      and (not r.get("record_type") or r["record_type"] == rt) for r in rules)
        if covered:
            continue
        for t in tpls:
            if t.get("trigger_status") and t["trigger_status"] != key:
                continue
            out.append({"module": "order", "scope": "client", "record_type": rt, "event": event,
                        "trigger_status_key": key, "channel": t["channel"], "template_id": t["id"],
                        "delay_min": 0, "working_hours_only": False, "chat_first": False,
                        "reminder_offset_min": 0, "active": True})
    return out


async def convert(db, org_id: str) -> int:
    """Convert this org's flagged statuses into rules and clear the flags. Idempotent (a
    second run finds no flagged statuses). Caller commits. Returns rules created."""
    from sqlalchemy import select, update

    from backend.models import NotificationRule, NotificationTemplate, OrderStatus

    flagged = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == org_id, OrderStatus.triggers_notification == True))).scalars().all()  # noqa: E712
    if not flagged:
        return 0
    rules = (await db.execute(select(NotificationRule).where(
        NotificationRule.org_id == org_id))).scalars().all()
    tpls = (await db.execute(select(NotificationTemplate).where(
        NotificationTemplate.org_id == org_id))).scalars().all()
    cols = lambda o, names: {n: getattr(o, n) for n in names}  # noqa: E731
    new = plan(
        [cols(s, ("key", "record_type", "is_terminal")) for s in flagged if s.active],
        [cols(r, ("module", "scope", "event", "trigger_status_key", "record_type", "active")) for r in rules],
        [cols(t, ("id", "channel", "trigger_event", "trigger_status", "active")) for t in tpls])
    for spec in new:
        db.add(NotificationRule(org_id=org_id, **spec))
    await db.execute(update(OrderStatus).where(OrderStatus.org_id == org_id)
                     .values(triggers_notification=False))
    return len(new)
