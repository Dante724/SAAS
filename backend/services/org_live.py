"""Is a shop live? The one answer every background job and public page uses.

A shop is live when it is switched on (``Organisation.active``) and the platform admin has not
suspended it (``sub_status == "suspended"``, set by routers/admin.suspend). Suspension is the
platform's kill switch (unpaid accounts, SMS pumping on the operator's Twilio), so a suspended
shop sends nothing, bills nothing and takes no new work from its public pages, exactly like a
switched-off one. Nothing is deleted or marked done meanwhile: queued messages and webhooks wait,
and unsuspending (which only puts ``sub_status`` back) picks everything up again.

Lapsed shops (owner decision 7). A shop whose term end (``sub_end``, a platform-set UTC date: the
term's last day) has passed and that isn't suspended is *lapsed*. For ``SUB_LAPSE_GRACE_DAYS``
(default 7) days after sub_end it keeps normal service and its owner sees a "renew" banner
(``lapse_info``, on /api/auth/me). After that it is held exactly like a suspended shop by every gate
here and by staff sign-in (services/auth.assert_org_active). Renewing (moving sub_end forward)
reopens it. This module is the only place that rule, or the suspension test, is written; a test
(test_d7_lapsed_grace) fails on a stray ``sub_status == "suspended"`` or ``sub_end <`` elsewhere.

Only sub_end counts. A custom-billed shop's ``billing_period_end`` and a trial's ``trial_ends_at``
make a shop read-only (services/billing), not switched off, and are unchanged by this rule.
"""
from datetime import date, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

SUSPENDED = "suspended"

PUBLIC_OFF_MESSAGE = ("This shop isn't taking requests online right now. "
                      "Please contact the shop directly.")
# The public pay page's wording: the customer only learns that online payment is off, never why.
PAY_OFF_MESSAGE = ("This shop isn't taking payments online right now. "
                   "Please contact the shop directly.")


def grace_days() -> int:
    from backend.config import settings
    return max(0, int(settings.SUB_LAPSE_GRACE_DAYS))


def _today(today: Optional[date]) -> date:
    from backend.services.dates import platform_today
    return today or platform_today()


def _lapse_cutoff(today: Optional[date]) -> date:
    """A term that ended before this day is past its grace."""
    return _today(today) - timedelta(days=grace_days())


def is_suspended(org) -> bool:
    return getattr(org, "sub_status", None) == SUSPENDED


def org_lapsed(org, today: Optional[date] = None) -> bool:
    """The term has ended (sub_end is behind us) and the shop isn't suspended: grace or past it."""
    end = getattr(org, "sub_end", None)
    return bool(org is not None and end is not None and not is_suspended(org) and end < _today(today))


def grace_ends_at(org) -> Optional[date]:
    """The last day of a lapsed shop's normal service."""
    end = getattr(org, "sub_end", None)
    return end + timedelta(days=grace_days()) if end is not None else None


def off_reason(org, today: Optional[date] = None) -> Optional[str]:
    """Why the platform holds this shop: "suspended", "lapsed" (past its grace), or None."""
    if org is None:
        return None
    if is_suspended(org):
        return "suspended"
    end = getattr(org, "sub_end", None)
    if end is not None and end < _lapse_cutoff(today):
        return "lapsed"
    return None


def lapse_info(org, today: Optional[date] = None) -> dict:
    """What the owner's "renew" banner needs (on /api/auth/me)."""
    lapsed = org_lapsed(org, today)
    end = getattr(org, "sub_end", None)
    return {"lapsed": lapsed, "sub_end": end.isoformat() if end else None,
            "grace_ends_at": grace_ends_at(org).isoformat() if lapsed else None}


def live_org_clause(today: Optional[date] = None):
    """SQL condition on ``Organisation``: the shop is live (join or select Organisation with it).
    The same rule as ``off_reason``; the grace cutoff is a date computed here, so the comparison is
    a plain date compare on SQLite and Postgres alike."""
    from backend.models import Organisation
    return and_(Organisation.active == True,  # noqa: E712
                or_(Organisation.sub_status.is_(None), Organisation.sub_status != SUSPENDED),
                or_(Organisation.sub_end.is_(None), Organisation.sub_end >= _lapse_cutoff(today)))


def org_is_live(org, today: Optional[date] = None) -> bool:
    """The same rule for an Organisation already loaded."""
    return bool(org is not None and getattr(org, "active", True) and off_reason(org, today) is None)


async def org_id_is_live(db: AsyncSession, org_id: Optional[str]) -> bool:
    if not org_id:
        return False
    from backend.models import Organisation
    return (await db.execute(select(Organisation.id).where(
        Organisation.id == org_id, live_org_clause()))).first() is not None


async def assert_public_live(db: AsyncSession, org_id: Optional[str]) -> None:
    """Public pages that take new work, messages or money refuse a shop that isn't live."""
    if not await org_id_is_live(db, org_id):
        raise HTTPException(403, PUBLIC_OFF_MESSAGE)
