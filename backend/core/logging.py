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
    secret, authorization, api_key, card, cvv), bare Bearer tokens, email addresses and the
    bound values a database error quotes — in the message and in the traceback. Applied
    to every handler so a stray f-string can't leak a credential to the log sink.
    """
    _KV = re.compile(r'(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization|auth|cvv|card[_-]?number)'
                     r'(["\']?\s*[:=]\s*["\']?)([^\s"\',;}]+)')
    _BEARER = re.compile(r'(?i)(bearer\s+)([A-Za-z0-9._\-]+)')
    # Capability tokens live in URL paths (/pay/<token>, /api/portal/<token>, invite and
    # reset links), and the access log writes every path. Mask long random segments; UUIDs
    # are record ids, not secrets, and stay readable for debugging.
    _PATH_TOKEN = re.compile(r'(?<=/)[A-Za-z0-9_\-]{20,}(?=[/?#\s]|$)')
    # Kept readable: UUIDs (record ids) and route names like "asset-identifier-rules" (lowercase
    # words) — random tokens always mix in digits or capitals.
    _KEEP = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[a-z_-]+')

    # Database errors quote the bound values on their own "[parameters: …]" line; deployed engines
    # hide them (hide_parameters), and this catches dev engines and any other library that does it.
    _SQL_PARAMS = re.compile(r'\[parameters: .*?\](?=\n|$)', re.DOTALL)
    _EMAIL = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')

    @classmethod
    def redact(cls, text: str) -> str:
        """Mask secrets and personal data in any log/exception text."""
        red = cls._SQL_PARAMS.sub('[parameters: hidden]', text)
        red = cls._KV.sub(r'\1\2***REDACTED***', red)
        red = cls._BEARER.sub(r'\1***REDACTED***', red)
        red = cls._EMAIL.sub('***@***', red)
        return cls._PATH_TOKEN.sub(lambda m: m.group(0) if cls._KEEP.fullmatch(m.group(0)) else "***", red)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = self.redact(msg)
        if red != msg:
            record.msg = red
            record.args = ()
        # Tracebacks carry the exception's own text (a database error quotes the row it failed on).
        # Formatters reuse exc_text when it is set, so redacting it here covers every handler.
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
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
        if record.exc_text:
            payload["exc"] = record.exc_text            # already redacted by RedactionFilter
        elif record.exc_info:
            payload["exc"] = RedactionFilter.redact(self.formatException(record.exc_info))
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
    logging.getLogger("tekly").info("logging configured (env=%s json=%s level=%s)",
                                        settings.ENVIRONMENT, settings.LOG_JSON, settings.LOG_LEVEL)
