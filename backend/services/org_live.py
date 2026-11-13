"""Is a shop live? The one answer every background job and public page uses.

A shop is live when it is switched on (``Organisation.active``) and the platform admin has not
suspended it (``sub_status == "suspended"``, set by routers/admin.suspend). Suspension is the
platform's kill switch (unpaid accounts, SMS pumping on the operator's Twilio), so a suspended
shop sends nothing, bills nothing and takes no new work from its public pages, exactly like a
switched-off one. Nothing is deleted or marked done meanwhile: queued messages and webhooks wait,
and unsuspending (which only puts ``sub_status`` back) picks everything up again.

Staff sign-in has its own, stricter gate (services/auth.assert_org_active), which also locks a
shop whose subscription term has ended; that stricter rule is not applied to customers here.
"""
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


def live_org_clause():
    """SQL condition on ``Organisation``: the shop is live (join or select Organisation with it)."""
    from backend.models import Organisation
    return and_(Organisation.active == True,  # noqa: E712
                or_(Organisation.sub_status.is_(None), Organisation.sub_status != SUSPENDED))


def org_is_live(org) -> bool:
    """The same rule for an Organisation already loaded."""
    return bool(org is not None and org.active and getattr(org, "sub_status", None) != SUSPENDED)


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
