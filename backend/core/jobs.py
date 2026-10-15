"""Background job foundation.

A small in-process async runner so work can leave the request path: one-shot
jobs with retry/backoff (`enqueue`) and periodic sweeps (`every`). It is
deliberately minimal and dependency-free.

PRODUCTION SEAM: this runs in-process and is therefore best-effort across a
single worker. For durable, multi-process execution swap `enqueue` to push onto
Celery/Arq/RQ and run `JobRunner` only as the periodic scheduler — the call
sites (`job_runner.enqueue(...)`) do not change.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger("repairos.jobs")


@dataclass
class _Periodic:
    name: str
    interval_s: float
    fn: Callable[[], Awaitable[None]]


@dataclass
class JobRunner:
    _tasks: set = field(default_factory=set)
    _periodics: list[_Periodic] = field(default_factory=list)
    _running: bool = False
    _periodic_handles: list = field(default_factory=list)

    def every(self, interval_s: float, name: str):
        """Decorator to register a periodic coroutine: @job_runner.every(60, 'sweep')."""
        def deco(fn: Callable[[], Awaitable[None]]):
            self._periodics.append(_Periodic(name=name, interval_s=interval_s, fn=fn))
            return fn
        return deco

    def enqueue(self, coro_fn: Callable[..., Awaitable], *args,
                retries: int = 3, backoff_s: float = 2.0, name: str = "") -> None:
        """Fire-and-forget a coroutine with bounded exponential-backoff retry."""
        label = name or getattr(coro_fn, "__name__", "job")
        task = asyncio.create_task(self._run_with_retry(label, coro_fn, args, retries, backoff_s))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_with_retry(self, label, coro_fn, args, retries, backoff_s):
        attempt = 0
        while True:
            try:
                await coro_fn(*args)
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                attempt += 1
                if attempt > retries:
                    log.exception("job '%s' failed after %d attempts", label, retries)
                    return
                delay = backoff_s * (2 ** (attempt - 1))
                log.warning("job '%s' failed (attempt %d/%d), retrying in %.1fs",
                            label, attempt, retries, delay)
                await asyncio.sleep(delay)

    async def _periodic_loop(self, p: _Periodic):
        while self._running:
            try:
                await run_exclusive(p.name, p.fn)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("periodic job '%s' raised", p.name)
            await asyncio.sleep(p.interval_s)

    async def start(self):
        if self._running:
            return
        self._running = True
        for p in self._periodics:
            self._periodic_handles.append(asyncio.create_task(self._periodic_loop(p)))
        log.info("job runner started (%d periodic job(s))", len(self._periodics))

    async def stop(self):
        self._running = False
        for h in self._periodic_handles:
            h.cancel()
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._periodic_handles, *self._tasks, return_exceptions=True)
        self._periodic_handles.clear()
        self._tasks.clear()


def _lock_key(name: str) -> int:
    """Stable signed 64-bit key for pg advisory locks (Python's hash() is per-process)."""
    import hashlib
    return int.from_bytes(hashlib.sha256(f"repairos:{name}".encode()).digest()[:8], "big", signed=True)


async def run_exclusive(name: str, fn: Callable[[], Awaitable[None]]) -> bool:
    """Run `fn` only if no other process is running the job of the same name.

    Every uvicorn worker / replica starts its own JobRunner, so without this each periodic sweep
    ran N times concurrently — sending scheduled SMS/e-mails and retrying webhooks once per
    worker. On Postgres a session-level advisory lock (held on a dedicated connection for the
    duration of the run) elects one runner; the others skip this tick. SQLite deployments are
    single-process, so the job just runs. Returns whether the job ran."""
    from sqlalchemy import text

    from backend.db.database import engine
    if engine.dialect.name != "postgresql":
        await fn()
        return True
    key = _lock_key(name)
    async with engine.connect() as conn:
        got = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})).scalar()
        if not got:
            log.debug("periodic job '%s' already running elsewhere; skipping this tick", name)
            return False
        try:
            await fn()
            return True
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})


# Module-level singleton imported by main.py and call sites.
job_runner = JobRunner()
