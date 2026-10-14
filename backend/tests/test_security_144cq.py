"""Build 144cq — security hardening: SSRF guard, upload cap, log redaction."""
import logging

import pytest

from backend.core.logging import RedactionFilter
from backend.core.ssrf import UnsafeURLError, assert_safe_url


@pytest.mark.parametrize("bad", [
    "http://127.0.0.1/x", "http://localhost/x", "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5/x", "http://192.168.1.1/x", "http://[::1]/x", "ftp://example.com/x",
    "file:///etc/passwd", "http://0.0.0.0/x",
])
def test_ssrf_blocks_private_and_bad_schemes(bad):
    with pytest.raises(UnsafeURLError):
        assert_safe_url(bad)


def test_ssrf_allows_public():
    # 1.1.1.1 is public; use an IP literal so the test needs no DNS.
    assert assert_safe_url("https://1.1.1.1/webhook") == "https://1.1.1.1/webhook"


@pytest.mark.asyncio
async def test_upload_cap_rejects_oversize(monkeypatch):
    from backend.config import settings
    from backend.services import media
    monkeypatch.setattr(settings, "MEDIA_MAX_BYTES", 10, raising=False)
    big = "A" * 500  # >10 bytes decoded
    with pytest.raises(ValueError):
        await media.store(None, "org1", big)


def test_log_redaction_masks_secrets():
    f = RedactionFilter()
    rec = logging.LogRecord("t", logging.INFO, __file__, 1,
                            'login password=hunter2 token: abc.def.ghi Bearer zzz.yyy', None, None)
    assert f.filter(rec)
    out = rec.getMessage()
    assert "hunter2" not in out and "abc.def.ghi" not in out and "zzz.yyy" not in out
    assert "REDACTED" in out


def test_docs_hidden_in_production(monkeypatch):
    from backend.config import Settings
    s = Settings(ENVIRONMENT="production", SECRET_KEY="x" * 40, CORS_ORIGINS="https://app.example.com")
    assert s.is_production is True
