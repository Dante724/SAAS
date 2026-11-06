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

Pass `body=` (what was asked: path ids and the request body) and the key is bound to it: the same
key sent with a different request is a 422, not the first request's answer. `run_once` wraps the
whole begin / work / finish-or-release dance for a handler.

Money safety:
- The work's first commit also stamps the key's `committed_at`, in the same transaction. From then
  on the key is never released: if building the answer fails after the money committed, a retry
  gets a 409 ("already carried out") instead of posting again. An error that says nothing was
  done (`idem_retry_safe = True` on the exception) still releases it.
- A key whose request died mid-flight (cancelled, process killed) would block retries for 48h. A
  key still in flight after STALE_AFTER, whose work never committed, can be taken over by a retry.
  The takeover moves the key's `created_at`; the original's commit checks it hasn't moved (row
  lock + compare), so a slow original that wakes up afterwards can't commit its posting too.
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import IntegrityError

from backend.models import IdempotencyRecord

HEADER = "Idempotency-Key"
TTL = timedelta(hours=48)
STALE_AFTER = timedelta(minutes=2)

IN_FLIGHT = "The same request is still being processed. Try again in a moment."
DONE_NO_ANSWER = ("This request has already been carried out, but its answer was lost. "
                  "Refresh to see the result instead of sending it again.")
TAKEN_OVER = ("This request took too long and was sent again; the retry was carried out instead. "
              "Refresh to see the result.")


@dataclass
class Claim:
    record_id: Optional[str] = None
    replay: Any = None
    started_at: Optional[datetime] = None
    committed: bool = False
    _unwatch: Optional[Callable[[], None]] = field(default=None, repr=False)

    @property
    def active(self) -> bool:
        return self.record_id is not None

    def _mine(self):
        return (IdempotencyRecord.id == self.record_id) & (IdempotencyRecord.created_at == self.started_at)

    def _watch(self, db) -> None:
        """Stamp committed_at in the same transaction as each commit the work makes, and refuse
        that commit if a retry has taken the key over."""
        sess = getattr(db, "sync_session", db)

        def before_commit(session):
            res = session.execute(update(IdempotencyRecord).where(self._mine()).values(
                committed_at=func.coalesce(IdempotencyRecord.committed_at, datetime.utcnow()))
                .execution_options(synchronize_session=False))
            if res.rowcount != 1:
                raise HTTPException(409, TAKEN_OVER)

        def after_commit(session):
            self.committed = True

        event.listen(sess, "before_commit", before_commit)
        event.listen(sess, "after_commit", after_commit)

        def unwatch():
            event.remove(sess, "before_commit", before_commit)
            event.remove(sess, "after_commit", after_commit)
        self._unwatch = unwatch

    def _stop(self) -> None:
        if self._unwatch is not None:
            self._unwatch()
            self._unwatch = None

    async def finish(self, db, response: Any) -> None:
        if not self.active:
            return
        self._stop()
        await db.execute(update(IdempotencyRecord).where(self._mine()).values(
            response=jsonable_encoder(response)).execution_options(synchronize_session=False))
        await db.commit()

    async def release(self, db, *, retry_safe: bool = False) -> None:
        """The work failed. Free the key for a retry, unless the work already committed (then the
        key stays, answered by DONE_NO_ANSWER) and the error doesn't say nothing was done."""
        if not self.active:
            return
        self._stop()
        try:
            await db.rollback()
            if self.committed and not retry_safe:
                return
            await db.execute(delete(IdempotencyRecord).where(self._mine()))
            await db.commit()
        except Exception:
            pass   # the stale window or the 48h sweep frees it


def request_hash(body: Any) -> str:
    return hashlib.sha256(json.dumps(jsonable_encoder(body), sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


async def idem_begin(db, org_id: str, request: Request, endpoint: str, *, body: Any = None) -> Claim:
    key = (request.headers.get(HEADER) or "").strip() if request is not None else ""
    if not key:
        return Claim()
    if len(key) > 80:
        raise HTTPException(400, "Idempotency-Key is too long (80 characters at most)")
    digest = request_hash(body) if body is not None else None
    existing = (await db.execute(select(IdempotencyRecord).where(
        IdempotencyRecord.org_id == org_id, IdempotencyRecord.key == key)
        .execution_options(populate_existing=True))).scalar_one_or_none()
    if existing is not None:
        if existing.endpoint != endpoint:
            raise HTTPException(422, "This Idempotency-Key was already used for a different action")
        if digest is not None and existing.request_hash is not None and existing.request_hash != digest:
            raise HTTPException(422, "This Idempotency-Key was already used with a different request")
        if existing.response is not None:
            return Claim(replay=existing.response)
        if existing.committed_at is not None:
            raise HTTPException(409, DONE_NO_ANSWER)
        if existing.created_at is not None and existing.created_at < datetime.utcnow() - STALE_AFTER:
            # Its request looks dead and never committed anything: take the key over. Only one
            # retry can win (compare on created_at), and the original can't commit after this.
            now = datetime.utcnow()
            res = await db.execute(update(IdempotencyRecord).where(
                IdempotencyRecord.id == existing.id, IdempotencyRecord.created_at == existing.created_at,
                IdempotencyRecord.response.is_(None), IdempotencyRecord.committed_at.is_(None),
            ).values(created_at=now, request_hash=digest if digest is not None else existing.request_hash)
                .execution_options(synchronize_session=False))
            await db.commit()
            if res.rowcount == 1:
                claim = Claim(record_id=existing.id, started_at=now)
                claim._watch(db)
                return claim
        raise HTTPException(409, IN_FLIGHT)
    now = datetime.utcnow()
    rec = IdempotencyRecord(org_id=org_id, key=key, endpoint=endpoint, request_hash=digest, created_at=now)
    db.add(rec)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, IN_FLIGHT)
    claim = Claim(record_id=rec.id, started_at=now)
    claim._watch(db)
    return claim


async def run_once(db, org_id: str, request: Optional[Request], endpoint: str, body: Any,
                   work: Callable[[], Awaitable[Any]]) -> Any:
    """Run `work()` once per Idempotency-Key (and request body); a retry gets the first answer."""
    claim = await idem_begin(db, org_id, request, endpoint, body=body)
    if claim.replay is not None:
        return claim.replay
    try:
        result = await work()
    except BaseException as e:
        await claim.release(db, retry_safe=bool(getattr(e, "idem_retry_safe", False)))
        raise
    await claim.finish(db, result)
    return result


async def sweep(db) -> int:
    """Delete keys older than 48h. Returns how many were removed."""
    res = await db.execute(delete(IdempotencyRecord).where(
        IdempotencyRecord.created_at < datetime.utcnow() - TTL))
    await db.commit()
    return res.rowcount or 0
