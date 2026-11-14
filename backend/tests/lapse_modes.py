"""Two ways a shop is switched off by the platform, for the suspension gate tests.

"suspended": the admin suspended it (sub_status = "suspended").
"lapsed":    its term ended (sub_end) more than SUB_LAPSE_GRACE_DAYS ago (owner decision 7), which
             services/org_live treats exactly like a suspension.

A test written as `await set_off_status(db, org_id, "suspended")` ... `"active"` runs either way
under `with off_mode("lapsed")`; switching back on clears the term end as renewing would.
"""
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import select

OFF_MODES = ["suspended", "lapsed"]
_mode = {"now": "suspended"}


@contextmanager
def off_mode(mode: str):
    assert mode in OFF_MODES
    before, _mode["now"] = _mode["now"], mode
    try:
        yield
    finally:
        _mode["now"] = before


async def set_off_status(db, org_id, status):
    from backend.config import settings
    from backend.models import Organisation
    from backend.services.dates import platform_today
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    if status == "suspended" and _mode["now"] == "lapsed":
        org.sub_status = "active"
        org.sub_end = platform_today() - timedelta(days=settings.SUB_LAPSE_GRACE_DAYS + 1)
    else:
        org.sub_status, org.sub_end = status, None
    await db.commit()
