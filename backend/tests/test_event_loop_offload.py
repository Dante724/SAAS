"""CPU-heavy work stays off the event loop (phase 4 performance, audit P7).

bcrypt (~250 ms), reportlab sheets and openpyxl saves ran inline in async handlers, so one
login or label sheet stalled every other request on that worker.
"""
import asyncio
import inspect

import pytest


def test_password_and_render_calls_are_offloaded():
    import backend.routers.admin as admin
    import backend.routers.auth as auth
    import backend.routers.mfa as mfa
    import backend.routers.printing as printing
    import backend.routers.staff as staff
    for mod in (auth, mfa, staff, admin):
        src = inspect.getsource(mod)
        for fn in ("verify_password(", "hash_password("):
            for line in src.splitlines():
                if fn in line and "def " not in line and "import" not in line and "_DUMMY_HASH =" not in line:
                    assert "to_thread" in line, (mod.__name__, line.strip())
    assert "pdf = printing.render_" not in inspect.getsource(printing)


@pytest.mark.asyncio
async def test_login_stays_responsive_while_hashing(client, tenant):
    """A ticking coroutine keeps running during a (failed) login — bcrypt isn't blocking."""
    ticks = 0
    stop = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    t = asyncio.create_task(ticker())
    r = await client.post("/api/auth/token", data={"username": "nobody@example.test", "password": "x"})
    stop.set()
    await t
    assert r.status_code == 401
    assert ticks >= 5


@pytest.mark.asyncio
async def test_label_sheet_size_is_capped(client, tenant):
    r = await client.post("/api/print/labels", json={"part_ids": ["p"] * 40, "copies": 100}, headers=tenant)
    assert r.status_code == 422 and "labels" in r.text
