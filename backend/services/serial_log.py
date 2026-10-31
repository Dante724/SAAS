"""The unit passport's history (ii144c, council 2026-10-25 item 10).

Every place a serialized unit is received, moved, used on a job, returned, sold or written off calls
`record` with the unit, so the passport can show where the unit has been without reconstructing it
from documents. The unit's own row says where it is now; these rows say how it got there.
Callers commit.
"""
from typing import Optional

from backend.models import SerialEvent, SerializedItem

KINDS = ("received", "moved", "used", "returned", "sold", "written_off", "restored", "converted", "counted")


def record(db, unit: SerializedItem, kind: str, *, ref_type: Optional[str] = None, ref_id: Optional[str] = None,
           label: Optional[str] = None, actor_id: Optional[str] = None) -> SerialEvent:
    """Add one history line for `unit`, stamped with where the unit is after the change."""
    assert kind in KINDS, kind
    ev = SerialEvent(org_id=unit.org_id, item=unit, kind=kind, ref_type=ref_type, ref_id=ref_id,
                     label=(label or "")[:200] or None, location_id=unit.location_id, bin_id=unit.bin_id,
                     created_by_id=actor_id)
    db.add(ev)
    return ev


def record_many(db, units, kind: str, **kw) -> None:
    for u in units:
        record(db, u, kind, **kw)
