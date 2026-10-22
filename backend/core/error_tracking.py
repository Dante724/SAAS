"""Error tracking (phase 4 observability).

Unhandled exceptions were only written to stdout logs. With SENTRY_DSN set they are reported to
Sentry (or any Sentry-protocol service such as GlitchTip) with request context, scrubbed of
credentials. Off when the DSN is empty, so dev and tests send nothing.
"""
import logging

from backend.config import settings

log = logging.getLogger("tekly.errors")

_SENSITIVE_HEADERS = {"authorization", "cookie", "x-api-key", "stripe-signature",
                      "x-square-hmacsha256-signature", "x-hub-signature-256", "x-twilio-signature"}


def _scrub(event, _hint):
    req = event.get("request") or {}
    headers = req.get("headers")
    if isinstance(headers, dict):
        for k in list(headers):
            if k.lower() in _SENSITIVE_HEADERS:
                headers[k] = "[Filtered]"
    req.pop("cookies", None)
    req.pop("data", None)          # request bodies carry passwords, card tokens and customer PII
    if "query_string" in req:
        req["query_string"] = "[Filtered]"   # reset/verify/pay tokens travel in query strings
    return event


def init_error_tracking() -> bool:
    dsn = getattr(settings, "SENTRY_DSN", "")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:   # pragma: no cover — it's in requirements.lock
        log.warning("SENTRY_DSN is set but sentry-sdk isn't installed; error tracking disabled")
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=settings.ENVIRONMENT,
        release=getattr(settings, "RELEASE", "") or None,
        traces_sample_rate=float(getattr(settings, "SENTRY_TRACES_SAMPLE_RATE", 0.0) or 0.0),
        send_default_pii=False,
        before_send=_scrub,
    )
    return True
