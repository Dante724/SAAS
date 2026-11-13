"""Customer tracking links (/track/{token}): stored hashed, expire after the job closes (SEC-12).

A tracking link is a bearer token: whoever has the URL sees the job, approves estimates and can
pay a deposit. Before SEC-12 the token sat in clear in ``orders.public_token`` and worked forever.

- **At rest:** ``orders.public_token_hash`` is the SHA-256 (hex) of the token; every lookup
  compares hashes (``Order.public_token == token`` in a query is rewritten to that by
  :class:`TokenComparator`). Documents, texts and the staff screen still print the link, so the
  token is also kept encrypted (``orders.public_token_enc``, :mod:`backend.services.crypto`,
  TOKEN_ENCRYPTION_KEY, which production requires). A database dump alone opens no link.
- **Expiry:** a link stops working ``TRACK_LINK_DAYS`` (180) days after the job closed, or after
  it was last re-issued (rotate), whichever is later. A job that is open again (``closed_at``
  cleared) has a working link. There is no per-shop setting yet: none of the existing ones fits
  (``retention_days`` means "keep for good" when unset), so this is a constant for now.
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

from sqlalchemy import and_, func, or_
from sqlalchemy.ext.hybrid import Comparator

TRACK_LINK_DAYS = 180


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
    return start + timedelta(days=TRACK_LINK_DAYS)


def is_expired(order, now: datetime | None = None) -> bool:
    from backend.services.dates import now_utc
    end = expires_at(order)
    return end is not None and (now or now_utc()) >= end
