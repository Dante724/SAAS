"""144ax — shared labour-actuals helpers.

One definition of "actual clocked time" so the inline WO booked-vs-actual widget and the
Labour-efficiency report can never drift apart. Actual = JOB time entries (not break/admin),
live-aware for a still-running timer, excluding voided entries.
"""
from datetime import datetime

from sqlalchemy import select

from backend.models import TimeEntry


def entry_seconds(e) -> int:
    """Clocked seconds for one entry; counts a still-running timer up to now."""
    if e.ended_at:
        return int(e.duration_sec or 0)
    return max(0, int((datetime.utcnow() - e.started_at).total_seconds())) if e.started_at else 0


def is_job_actual(e) -> bool:
    return e.category == "job" and e.state != "void"


async def line_actual_seconds(db, order_id) -> dict:
    """{order_line_id: seconds} of JOB clocked time per line for one order (live-aware)."""
    rows = (await db.execute(select(TimeEntry).where(
        TimeEntry.order_id == order_id))).scalars().all()
    out: dict = {}
    for e in rows:
        lid = getattr(e, "order_line_id", None)
        if not lid or not is_job_actual(e):
            continue
        out[lid] = out.get(lid, 0) + entry_seconds(e)
    return out
