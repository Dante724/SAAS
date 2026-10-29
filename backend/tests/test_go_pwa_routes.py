"""Tekly Go is served at /go/: every Go route returns the Go shell, the service worker is scoped to
/go/ (never the whole site) and is never cached, and the manifest has its own type."""
import pytest


@pytest.fixture
def go_build(tmp_path, monkeypatch):
    # CI runs without a frontend build; point the app at a stand-in Go folder.
    import main
    go = tmp_path / "go"
    go.mkdir()
    (go / "index.html").write_text('<!doctype html><div id="root" data-shell="go"></div>')
    (go / "sw.js").write_text("self.addEventListener('fetch', () => {});")
    (go / "manifest.webmanifest").write_text('{"name": "Tekly Go", "scope": "/go/"}')
    monkeypatch.setattr(main, "_go_dir", str(go))
    monkeypatch.setattr(main, "_go_index", str(go / "index.html"))
    return go


@pytest.mark.asyncio
async def test_go_routes_serve_the_go_shell(client, go_build):
    for path in ("/go/", "/go/today", "/go/job/abc-123", "/go/scan"):
        r = await client.get(path)
        assert r.status_code == 200, path
        assert 'data-shell="go"' in r.text
        assert r.headers["cache-control"] == "no-cache"
    r = await client.get("/go", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/go/"


@pytest.mark.asyncio
async def test_go_service_worker_is_scoped_and_uncached(client, go_build):
    r = await client.get("/go/sw.js")
    assert r.status_code == 200
    assert r.headers["service-worker-allowed"] == "/go/"
    assert r.headers["cache-control"] == "no-cache"
    assert "javascript" in r.headers["content-type"]
    m = await client.get("/go/manifest.webmanifest")
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")


@pytest.mark.asyncio
async def test_go_without_a_build_says_so(client, tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(main, "_go_dir", str(tmp_path / "missing"))
    monkeypatch.setattr(main, "_go_index", str(tmp_path / "missing" / "index.html"))
    assert (await client.get("/go/today")).status_code == 503
    assert (await client.get("/go/sw.js")).status_code == 404
