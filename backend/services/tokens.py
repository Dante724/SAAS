"""Build 144cb — issue and redeem single-use, expiring account tokens
(password reset, email verification).

Design: we generate a high-entropy raw token, hand the raw value to the user
(email link), and persist only its SHA-256 hash. Redemption re-hashes the
presented token and looks it up, so a leaked database can't be replayed. Tokens
are single-use (``used_at``) and time-boxed (``expires_at``).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AuthToken, Employee

RESET_TTL_MIN = 60          # password-reset links live 1 hour
VERIFY_TTL_MIN = 60 * 48    # email-verification links live 48 hours


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def issue(db: AsyncSession, *, employee_id: str, kind: str, ttl_min: int | None = None) -> str:
    """Create a token row and return the RAW token (show once, never stored)."""
    raw = secrets.token_urlsafe(32)
    ttl = ttl_min or (RESET_TTL_MIN if kind == "reset" else VERIFY_TTL_MIN)
    db.add(AuthToken(
        kind=kind, employee_id=employee_id, token_hash=_hash(raw),
        expires_at=datetime.utcnow() + timedelta(minutes=ttl)))
    await db.flush()
    return raw


async def redeem(db: AsyncSession, *, raw: str, kind: str) -> Employee | None:
    """Validate a raw token for ``kind``. On success mark it used and return the
    Employee; on any failure (unknown, wrong kind, expired, already used) return
    None. Callers must treat None as a generic failure."""
    if not raw:
        return None
    row = (await db.execute(select(AuthToken).where(
        AuthToken.token_hash == _hash(raw), AuthToken.kind == kind))).scalar_one_or_none()
    if not row or row.used_at is not None or row.expires_at < datetime.utcnow():
        return None
    row.used_at = datetime.utcnow()
    emp = (await db.execute(select(Employee).where(Employee.id == row.employee_id))).scalar_one_or_none()
    return emp


async def invalidate_all(db: AsyncSession, *, employee_id: str, kind: str) -> None:
    """Burn any outstanding unused tokens of a kind for a user (e.g. when a new
    reset is requested, older links stop working)."""
    rows = (await db.execute(select(AuthToken).where(
        AuthToken.employee_id == employee_id, AuthToken.kind == kind,
        AuthToken.used_at.is_(None)))).scalars().all()
    for r in rows:
        r.used_at = datetime.utcnow()
