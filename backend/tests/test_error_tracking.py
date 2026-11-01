"""Error tracking is opt-in and never ships credentials (phase 4 observability)."""
from backend.config import settings
from backend.core import error_tracking


def test_off_without_a_dsn(monkeypatch):
    monkeypatch.setattr(settings, "SENTRY_DSN", "")
    assert error_tracking.init_error_tracking() is False


def test_on_with_a_dsn(monkeypatch):
    import sentry_sdk
    seen = {}
    monkeypatch.setattr(settings, "SENTRY_DSN", "https://public@o0.ingest.example/1")
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: seen.update(kw))
    assert error_tracking.init_error_tracking() is True
    assert seen["send_default_pii"] is False and seen["before_send"] is error_tracking._scrub


def test_events_are_scrubbed():
    ev = {"request": {"headers": {"Authorization": "Bearer abc", "Cookie": "s=1", "Accept": "x"},
                      "cookies": {"s": "1"}, "data": {"password": "pw"},
                      "query_string": "token=secret"}}
    out = error_tracking._scrub(ev, None)["request"]
    assert out["headers"]["Authorization"] == "[Filtered]" and out["headers"]["Cookie"] == "[Filtered]"
    assert out["headers"]["Accept"] == "x"
    assert "cookies" not in out and "data" not in out and out["query_string"] == "[Filtered]"
