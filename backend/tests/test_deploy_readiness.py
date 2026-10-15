"""Platform: the app can be deployed reproducibly (security phase 4).

Regressions for audit findings P1, P3, P5, B22:
- without a React build, "/" fell back to a legacy shell whose bundle no longer exists (blank page),
  and a production deploy happily started that way;
- MEDIA_BACKEND=s3 imported boto3, which was never in requirements (crashed on first upload);
- dependencies were unpinned ">=" floors, so every install could differ.
"""
from pathlib import Path

import pytest

import main
from backend.config import settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_deployed_app_refuses_to_start_without_the_frontend_build(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(main, "_have_react", False)
    with pytest.raises(RuntimeError, match="React build missing"):
        async with main.lifespan(main.app):
            pass


@pytest.mark.asyncio
async def test_missing_build_shows_an_explanation_not_a_blank_page(client, monkeypatch):
    monkeypatch.setattr(main, "_have_react", False)
    r = await client.get("/orders/123")
    assert r.status_code == 503 and "npm run build" in r.text


def test_s3_media_backend_dependency_is_installed():
    import boto3  # noqa: F401  (services/media.py imports it lazily for MEDIA_BACKEND=s3)


@pytest.mark.parametrize("lock", ["requirements.lock", "requirements-dev.lock"])
def test_lockfiles_pin_every_dependency_with_hashes(lock):
    text = (ROOT / lock).read_text()
    pins = [ln for ln in text.splitlines() if ln and not ln.startswith((" ", "#", "-"))]
    assert pins and all("==" in p for p in pins), [p for p in pins if "==" not in p][:5]
    assert text.count("--hash=sha256:") >= len(pins)
    for must in ("fastapi==", "sqlalchemy==", "boto3=="):
        assert must in text


@pytest.mark.asyncio
async def test_app_shell_revalidates_and_hashed_assets_are_immutable(client, monkeypatch):
    """index.html had no Cache-Control, so browsers kept serving the previous release's shell
    (and its old bundles) after a deploy."""
    if not main._have_react:
        pytest.skip("React build not present")
    r = await client.get("/settings")
    assert r.headers.get("cache-control") == "no-cache"
    import re
    asset = re.search(r'/assets/[^"]+\.js', r.text).group(0)
    a = await client.get(asset)
    assert a.status_code == 200 and "immutable" in a.headers.get("cache-control", "")
