"""Security: the SPA catch-all must never serve files outside the frontend folders.

httpx normalises `..` and `//` out of URLs before sending, which would hide the bug, so these
tests drive the ASGI app with raw scopes — exactly what uvicorn delivers (uvicorn
percent-decodes `%2F` into `scope["path"]`).
"""
import asyncio
import os

import pytest

from main import app

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _react_shell(tmp_path, monkeypatch):
    """CI runs the backend suite without building frontend-react, and without a build the app
    answers 503 'not built' (main._NOT_BUILT). Point it at a stand-in shell so these tests check
    path handling, not whether the frontend happened to be built on this machine."""
    import main
    shell = tmp_path / "index.html"
    shell.write_text('<!doctype html><html><body><div id="root"></div></body></html>')
    monkeypatch.setattr(main, "_have_react", True)
    monkeypatch.setattr(main, "_react_index", str(shell))
MAIN_PY = os.path.join(ROOT, "main.py")
SECRET_MARKER = b"async def serve_spa"  # a line that only exists in main.py


async def _get(path: str):
    """Send a raw GET to the app and return (status, body)."""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
        "root_path": "", "query_string": b"", "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
    }
    sent = []
    delivered = asyncio.Event()

    async def receive():
        # Deliver the (empty) body once; afterwards behave like an idle connection.
        if not delivered.is_set():
            delivered.set()
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()

    async def send(msg):
        sent.append(msg)

    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/../main.py",                 # plain dot-dot
    "/../../main.py",
    "/static/../../main.py",       # dot-dot after a mounted prefix
    "/tests/../../main.py",        # through a real frontend subfolder
    "/" + MAIN_PY,                 # "//abs/path": os.path.join drops the base for absolute paths
    "/" + MAIN_PY.replace("/", "\\"),
    "/..\\main.py",                # backslash separators
    "/../main.py\x00.html",        # NUL byte
])
async def test_traversal_never_leaks_files(path):
    status, body = await _get(path)
    assert SECRET_MARKER not in body, f"{path!r} leaked main.py"
    assert status in (200, 404)  # unknown paths fall through to the SPA shell


@pytest.mark.asyncio
async def test_traversal_cannot_reach_system_files():
    status, body = await _get("//etc/hosts")
    assert b"localhost" not in body or b"<html" in body.lower()


@pytest.mark.asyncio
async def test_real_frontend_files_still_served():
    status, body = await _get("/admin.html")
    assert status == 200 and b"<html" in body.lower()


@pytest.mark.asyncio
async def test_client_routes_still_get_spa_shell():
    status, body = await _get("/orders/123")
    assert status == 200 and b"<html" in body.lower()
