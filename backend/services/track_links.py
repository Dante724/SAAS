"""Customer tracking links (/track/{token}): stored hashed, expire after the job closes (SEC-12).

A tracking link is a bearer token: whoever has the URL sees the job, approves estimates and can
pay a deposit. Before SEC-12 the token sat in clear in ``orders.public_token`` and worked forever.

- **At rest:** ``orders.public_token_hash`` is the SHA-256 (hex) of the token; every lookup
  compares hashes (``Order.public_token == token`` in a query is rewritten to that by
  :class:`TokenComparator`). Documents, texts and the staff screen still print the link, so the
  token is also kept encrypted (``orders.public_token_enc``, :mod:`backend.services.crypto`,
  TOKEN_ENCRYPTION_KEY, which production requires). A database dump alone opens no link.
- **Expiry (owner decision 8):** a link stops working ``settings.TRACK_LINK_DAYS`` (90) days
  after the job closed, or after it was last re-issued (rotate), whichever is later. One platform
  default, no per-shop setting. An open job never expires, however old. A reopened job is open
  again: reopening by a status change keeps ``closed_at`` (orders.update_status only writes it on
  a close), so :func:`link_expired` also checks the job still sits in a closed status, the same
  test as orders._is_closed. Closing it again restarts the clock (``closed_at`` = now).
  An expired link answers :class:`TrackLinkExpired` (410 Gone, ``expired: true`` and the shop's
  public contact, never job data) on every route keyed by the token: the page, its actions, its
  PDFs and its photos (they all load the job through routers/public._load).
- **Rotate / revoke** keep working: setting ``order.public_token`` to a new token or to None
  replaces the hash and the ciphertext, and always clears the legacy plaintext column.
- **Legacy column:** migration dbi02 hashed and encrypted every existing token and keeps the old
  ``public_token`` column for the previous image (DEPLOY.md, expand -> contract). New code never
  writes it; a link the previous image issued during a deploy or after a rollback (plaintext,
  no hash yet) is still found through it until the contract release clears that column.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.hybrid import Comparator

EXPIRED_MESSAGE = "This link has expired. Please contact the shop if you need anything about this repair."


def link_days() -> int:
    """The platform window (days after the close) a tracking link keeps working."""
    from backend.config import settings
    return int(settings.TRACK_LINK_DAYS)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """~128 bits, url-safe, fits the old 40-char column."""
    return secrets.token_urlsafe(16)


def reveal(order) -> str | None:
    """The token in clear, for printing the link (None: the job has no link)."""
    from backend.services import crypto
    if order.public_token_enc:
        return crypto.decrypt(order.public_token_enc)
    return order.legacy_public_token


def assign(order, token: str | None, now: datetime | None = None) -> None:
    """Issue (or with None, revoke) the job's link. The plaintext column is always cleared."""
    from backend.services import crypto
    from backend.services.dates import now_utc
    order.legacy_public_token = None
    if not token:
        order.public_token_hash = order.public_token_enc = order.public_token_issued_at = None
        return
    order.public_token_hash = hash_token(token)
    order.public_token_enc = crypto.encrypt(token)
    order.public_token_issued_at = now or now_utc()


class TokenComparator(Comparator):
    """``Order.public_token`` in SQL. Selecting it yields the stored form (the ciphertext when a key
    is set); comparing it to a token compares SHA-256 hashes, plus the legacy plaintext column
    for links the previous image issued (rows with no hash yet)."""

    def __init__(self, cls):
        self._cls = cls
        super().__init__(func.coalesce(cls.public_token_enc, cls.legacy_public_token))

    def __eq__(self, other):  # noqa: D105
        if other is None:
            return and_(self._cls.public_token_hash.is_(None), self._cls.legacy_public_token.is_(None))
        return or_(self._cls.public_token_hash == hash_token(other),
                   and_(self._cls.public_token_hash.is_(None), self._cls.legacy_public_token == other))

    def __ne__(self, other):  # noqa: D105
        from sqlalchemy import not_
        return not_(self.__eq__(other))

    def is_(self, other):
        return self.__eq__(other)

    def is_not(self, other):
        return self.__ne__(other)


def expires_at(order) -> datetime | None:
    """When this job's link stops working (None: it doesn't, the job is open)."""
    closed = getattr(order, "closed_at", None)
    if not closed:
        return None
    issued = getattr(order, "public_token_issued_at", None)
    start = max(closed, issued) if issued else closed
    return start + timedelta(days=link_days())


def is_expired(order, now: datetime | None = None) -> bool:
    """Past the window by the dates alone. :func:`link_expired` also checks the job is closed."""
    from backend.services.dates import now_utc
    end = expires_at(order)
    return end is not None and (now or now_utc()) >= end


async def _sits_closed(db, order) -> bool:
    """Is the job in a closed (terminal) status right now? Mirrors orders._is_closed: a job moved
    back to an open status keeps its old closed_at but is open."""
    from backend.models import OrderStatus
    st = (await db.execute(select(OrderStatus).where(
        OrderStatus.org_id == order.org_id, OrderStatus.key == order.status,
        OrderStatus.is_estimate == bool(order.is_estimate)))).scalars().first()
    if st:
        return st.status_group == "closed" or bool(st.is_terminal)
    return bool(order.closed_at)


async def link_expired(db, order, now: datetime | None = None) -> bool:
    """Has this job's tracking link stopped working? Only reads the status when the dates say so."""
    return is_expired(order, now) and await _sits_closed(db, order)


class TrackLinkExpired(HTTPException):
    """410 Gone for an expired tracking link. main.py renders it as
    ``{"detail", "expired": true, "shop": {name, phone, email}}``, which the track page shows as
    its polite "this link has expired, contact the shop" state."""

    def __init__(self, shop: dict):
        super().__init__(410, EXPIRED_MESSAGE, headers={"Cache-Control": "no-store"})
        self.shop = shop


async def shop_contact(db, order) -> dict:
    """The shop's public contact for the expired page: the same name and phone the track page
    shows (the job's location phone, else the shop's) and the shop's public email (printed on its
    documents). Nothing about the job itself."""
    from backend.models import Location, Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == order.org_id))).scalar_one_or_none()
    loc = (await db.execute(select(Location).where(Location.id == order.location_id))).scalar_one_or_none() \
        if order.location_id else None
    phone = (loc.phone if (loc and getattr(loc, "phone", None)) else getattr(org, "company_phone", None))
    return {"name": org.name if org else "Repair shop", "phone": phone or None,
            "email": (getattr(org, "company_email", None) or None) if org else None}
