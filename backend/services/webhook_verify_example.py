"""How a receiver verifies a Tekly webhook (reference for the public docs; tested).

Every delivery is an HTTPS POST with a JSON body and these headers:

    Tekly-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256>[,v1=<hex>...]
    Tekly-Webhook-Id: <delivery id>     (the same on every retry of this delivery: dedupe on it)
    Tekly-Event: <event name>

The signed message is f"{t}.{raw_body}" (the exact bytes received, before any JSON parsing),
keyed with the endpoint's signing secret (it starts with ``whsec_``; use the whole string).
During a secret rotation the header carries one ``v1`` per active secret, so a receiver that
holds either the old or the new secret verifies. Reject a signature whose ``t`` is more than
five minutes from your clock, which stops a captured request being replayed later.

Test vector (also asserted in backend/tests/test_webhooks_hardening.py):

    secret    = "whsec_test_5WbX3nC1p9QeK2tLmZ8rYvA4"
    timestamp = 1767225600
    body      = '{"id":"evt_test","event":"order.created","data":{"id":"ord_1"}}'
    v1        = "d83670797014725e0bb393ba9d8969615956b6cb14b92d18bcdb6d926654023b"
    header    = "t=1767225600,v1=d83670797014725e0bb393ba9d8969615956b6cb14b92d18bcdb6d926654023b"

Only the standard library is used, so this file can be copied into a receiver as is.
"""
import hashlib
import hmac
import time
from collections.abc import Iterable
from typing import Optional, Union

TOLERANCE_SECONDS = 300


def compute_signature(secret: str, timestamp: int, body: Union[str, bytes]) -> str:
    """Hex HMAC-SHA256 of f"{timestamp}.{body}" keyed with the secret."""
    raw = body.encode() if isinstance(body, str) else body
    return hmac.new(secret.encode(), str(int(timestamp)).encode() + b"." + raw, hashlib.sha256).hexdigest()


def parse_signature_header(header: str) -> tuple[Optional[int], list[str]]:
    """Split ``t=...,v1=...,v1=...`` into (timestamp, [v1 signatures]). Unknown keys are ignored."""
    ts, sigs = None, []
    for part in (header or "").split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                ts = int(value)
            except ValueError:
                return None, []
        elif key == "v1" and value:
            sigs.append(value)
    return ts, sigs


def verify(secret: Union[str, Iterable[str]], signature_header: str, body: Union[str, bytes],
           *, tolerance: int = TOLERANCE_SECONDS, now: Optional[float] = None) -> bool:
    """True when the Tekly-Signature header is valid for this body and fresh enough.

    ``secret`` may be one secret or several (the old and new one while you rotate)."""
    ts, sigs = parse_signature_header(signature_header)
    if ts is None or not sigs:
        return False
    if abs((time.time() if now is None else now) - ts) > tolerance:
        return False
    secrets = [secret] if isinstance(secret, str) else list(secret)
    for s in secrets:
        expected = compute_signature(s, ts, body)
        if any(hmac.compare_digest(expected, sig) for sig in sigs):
            return True
    return False


TEST_VECTOR = {
    "secret": "whsec_test_5WbX3nC1p9QeK2tLmZ8rYvA4",
    "timestamp": 1767225600,
    "body": '{"id":"evt_test","event":"order.created","data":{"id":"ord_1"}}',
    "signature": "d83670797014725e0bb393ba9d8969615956b6cb14b92d18bcdb6d926654023b",
}
