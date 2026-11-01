"""Idempotency keys: a client sends `Idempotency-Key: <uuid>` on a create or a payment, and a
retry with the same key gets the first answer back instead of running again. Phones retry a lot
(double taps, a request that timed out but did reach the server), and a second job or a second
payment is far worse than a slow one.

Usage in a handler:

    claim = await idem_begin(db, current.org_id, request, "orders.create")
    if claim.replay is not None:
        return claim.replay
    try:
        ...do the work, commit...
    except BaseException:
        await claim.release(db)      # the work failed: let the client try again with the same key
        raise
    await claim.finish(db, result)   # remember the answer
    return result

Without the header nothing changes (claim.active is False and finish/release do nothing).
The key is reserved (committed) before the work starts, so two concurrent requests with one key
can't both run: the second sees the reservation and gets a 409 telling it to retry shortly.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from backend.models import IdempotencyRecord

HEADER = "Idempotency-Key"
TTL = timedelta(hours=48)


@dataclass
class Claim:
    record_id: Optional[str] = None
    replay: Any = None

    @property
    def active(self) -> bool:
        return self.record_id is not None

    async def finish(self, db, response: Any) -> None:
        if not self.active:
            return
        rec = await db.get(IdempotencyRecord, self.record_id)
        if rec is not None:
            rec.response = jsonable_encoder(response)
            await db.commit()

    async def release(self, db) -> None:
        if not self.active:
            return
        try:
            await db.rollback()
            await db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.id == self.record_id))
            await db.commit()
        except Exception:
            pass   # the 48h sweep removes it; the client then gets "still in progress" until then


async def idem_begin(db, org_id: str, request: Request, endpoint: str) -> Claim:
    key = (request.headers.get(HEADER) or "").strip()
    if not key:
        return Claim()
    if len(key) > 80:
        raise HTTPException(400, "Idempotency-Key is too long (80 characters at most)")
    existing = (await db.execute(select(IdempotencyRecord).where(
        IdempotencyRecord.org_id == org_id, IdempotencyRecord.key == key))).scalar_one_or_none()
    if existing is not None:
        if existing.endpoint != endpoint:
            raise HTTPException(422, "This Idempotency-Key was already used for a different action")
        if existing.response is None:
            raise HTTPException(409, "The same request is still being processed. Try again in a moment.")
        return Claim(replay=existing.response)
    rec = IdempotencyRecord(org_id=org_id, key=key, endpoint=endpoint, created_at=datetime.utcnow())
    db.add(rec)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "The same request is still being processed. Try again in a moment.")
    return Claim(record_id=rec.id)


async def sweep(db) -> int:
    """Delete keys older than 48h. Returns how many were removed."""
    res = await db.execute(delete(IdempotencyRecord).where(
        IdempotencyRecord.created_at < datetime.utcnow() - TTL))
    await db.commit()
    return res.rowcount or 0
