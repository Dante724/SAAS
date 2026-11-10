"""Nothing a test starts may outlive it, and a test that hangs fails loudly instead of silently.

Why: pytest-asyncio gives every test a fresh event loop and closes it after the test WITHOUT
cancelling the tasks still on it. Such a task (the other request of a race test whose gather()
raised, a webhook outbox delivery, a post-reply email) is frozen mid-transaction for the rest of the
run: its connection stays "idle in transaction" holding its row locks, and the test engine has no
idle-in-transaction timeout. The next test's cleanup (DELETE FROM every table) then waits on those
locks forever, and the CI shard prints nothing more until the job timeout.

- `cancel_leftover_tasks` runs at the end of every test, on the test's own loop: every task still
  running is cancelled and awaited, so its session rolls back and its connection closes.
- `Watchdog` bounds every test (setup + call + teardown). Past the bound it writes a report (the
  test id, every thread's stack, the database sessions and what they wait on), then ends the
  process: xdist reports the test as failed ("worker crashed") and starts a fresh worker, and the
  controller prints the report in its summary. The shard goes red with the culprit named rather
  than hanging until the job is cancelled.

This module imports nothing from the app (conftest imports it before the app is loaded).
"""
from __future__ import annotations

import asyncio
import faulthandler
import glob
import os
import sys
import tempfile
import threading
from typing import Callable

DEFAULT_TIMEOUT_S = 600.0     # the slowest tests (migration runs) take ~75s on a loaded machine
HANG_EXIT = 70                # EX_SOFTWARE


async def cancel_leftover_tasks(timeout: float = 10.0) -> tuple[list[str], list[str]]:
    """Cancel every other task on the running loop and wait for them to finish unwinding.
    Returns (cancelled, still_running): task names, the second list for tasks that ignored it."""
    me = asyncio.current_task()
    left = [t for t in asyncio.all_tasks() if t is not me and not t.done()]
    for t in left:
        t.cancel()
    pending: set = set()
    if left:
        _, pending = await asyncio.wait(left, timeout=timeout)
    return [t.get_name() for t in left], [repr(t) for t in pending]


def hang_timeout_s() -> float:
    """TEST_HANG_TIMEOUT_S (seconds, 0 = off), else DEFAULT_TIMEOUT_S."""
    raw = os.environ.get("TEST_HANG_TIMEOUT_S", "")
    try:
        return float(raw) if raw.strip() else DEFAULT_TIMEOUT_S
    except ValueError:
        return DEFAULT_TIMEOUT_S


def report_path(controller_pid: int, worker: str) -> str:
    """Where a process writes its hang report. An xdist worker's own stderr is captured and lost
    when it exits, so the controller (`controller_pid`) prints these files in its summary."""
    return os.path.join(tempfile.gettempdir(), f"tekly-hang-{controller_pid}-{worker}.txt")


def reports_for(controller_pid: int) -> list[str]:
    return sorted(glob.glob(os.path.join(tempfile.gettempdir(), f"tekly-hang-{controller_pid}-*.txt")))


class Watchdog:
    """One timer per test. Past the bound it writes the report to `path` (and `stream`), then
    calls `exit_process`. `diagnostics` adds lines (e.g. the database sessions)."""

    def __init__(self, timeout_s: float, path: str, diagnostics: Callable[[], list[str]] | None = None,
                 exit_process: Callable[[int], None] = os._exit, stream=None):
        self.timeout_s = timeout_s
        self.path = path
        self.diagnostics = diagnostics
        self.exit_process = exit_process
        self.stream = stream
        self.loop = None          # the running test's event loop, when it has one (set by conftest)
        self._timer: threading.Timer | None = None

    def start(self, nodeid: str) -> None:
        self.cancel()
        self.loop = None
        if self.timeout_s <= 0:
            return
        self._timer = threading.Timer(self.timeout_s, self._fire, args=(nodeid,))
        self._timer.daemon = True
        self._timer.start()

    def cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _fire(self, nodeid: str) -> None:
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        with open(self.path, "w+") as out:
            out.write(f"=== HANG: {nodeid} ran over {self.timeout_s:.0f}s (worker {worker}). "
                      f"The process was stopped so the run fails instead of hanging. ===\n")
            out.flush()
            faulthandler.dump_traceback(file=out, all_threads=True)   # every thread's stack
            loop = self.loop
            if loop is not None:   # an await-hang shows only the loop above: show where each task waits
                try:
                    tasks = list(asyncio.all_tasks(loop))   # best effort from another thread
                    out.write(f"--- {len(tasks)} asyncio task(s) on the test's loop ---\n")
                    for t in tasks:
                        t.print_stack(file=out)
                except Exception as e:  # noqa: BLE001
                    out.write(f"(could not list asyncio tasks: {e})\n")
            if self.diagnostics:
                try:
                    lines = self.diagnostics()
                    out.write("--- database sessions (other than this check) ---\n")
                    out.write("".join(f"{line}\n" for line in lines) or "(none)\n")
                except Exception as e:  # noqa: BLE001
                    out.write(f"(could not list database sessions: {e})\n")
            out.write(f"=== end of HANG report for {nodeid} ===\n")
            out.flush()
            out.seek(0)
            report = out.read()
        try:
            stream = self.stream or sys.__stderr__
            stream.write("\n" + report)
            stream.flush()
        except Exception:  # noqa: BLE001 — the file is the record; the stream is a courtesy
            pass
        self.exit_process(HANG_EXIT)
