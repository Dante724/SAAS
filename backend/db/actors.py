"""Who did it: stamp the acting person on money events, whatever route makes them.

A discount on a job line, a refund and a drawer close are made from many places (job lines,
estimates, bundles, timers, returns, POS, cash). Rather than edit each one, a before-flush hook
fills the "by" column from the person on this request's session (set by get_current_employee).
Nothing is overwritten: a route that already set the column wins, and background jobs (no
person) leave it empty. Tekly Sense Watchdog relies on these to be fair to the right person.
"""
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session


def _changed(obj, key: str) -> bool:
    return inspect(obj).attrs[key].history.has_changes()


@event.listens_for(Session, "before_flush")
def _stamp_actors(session: Session, flush_context, instances) -> None:
    actor = session.info.get("actor_id")
    if not actor:
        return
    from backend.models import CashShift, OrderLine, Refund

    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, OrderLine):
            if (obj.discount or 0) > 0 and (obj in session.new or _changed(obj, "discount")) \
                    and not _changed(obj, "discount_by_id"):
                obj.discount_by_id = actor
        elif isinstance(obj, Refund):
            if obj in session.new and not obj.created_by_id:
                obj.created_by_id = actor
        elif isinstance(obj, CashShift):
            if obj.closed_at is not None and not obj.closed_by_id and _changed(obj, "closed_at"):
                obj.closed_by_id = actor
