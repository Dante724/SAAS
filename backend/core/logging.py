"""Logging configuration.

Plain, readable logs in development; single-line JSON in production (LOG_JSON)
so log shippers (CloudWatch/Datadog/Loki) can parse them. Call setup_logging()
once at startup before the app handles traffic.
"""
import json
import logging
import re
import sys
from datetime import datetime, timezone

from backend.config import settings


class RedactionFilter(logging.Filter):
    """Build 144cq: scrub secrets/PII from log messages before they're emitted.

    Masks common sensitive key=value / "key": "value" shapes (password, token,
    secret, authorization, api_key, card, cvv) and bare Bearer tokens. Applied
    to every handler so a stray f-string can't leak a credential to the log sink.
    """
    _KV = re.compile(r'(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization|auth|cvv|card[_-]?number)'
                     r'(["\']?\s*[:=]\s*["\']?)([^\s"\',;}]+)')
    _BEARER = re.compile(r'(?i)(bearer\s+)([A-Za-z0-9._\-]+)')

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = self._KV.sub(r'\1\2***REDACTED***', msg)
        red = self._BEARER.sub(r'\1***REDACTED***', red)
        if red != msg:
            record.msg = red
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "request_id"):
            payload["request_id"] = record.request_id
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactionFilter())   # Build 144cq: scrub secrets/PII from logs
    if settings.LOG_JSON:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s :: %(message)s", "%H:%M:%S"))
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL.upper())
    # Tame noisy libraries; let our namespace flow at the configured level.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("repairos").info("logging configured (env=%s json=%s level=%s)",
                                        settings.ENVIRONMENT, settings.LOG_JSON, settings.LOG_LEVEL)
