"""B1 — TOTP-based MFA. Self-contained RFC-6238 implementation (no pyotp
dependency, which isn't installable in the locked sandbox network)."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time

from backend.services.auth import hash_password, verify_password

_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def new_secret() -> str:
    """A random base32 secret (160 bits)."""
    return "".join(secrets.choice(_B32) for _ in range(32))


def _b32decode(secret: str) -> bytes:
    pad = "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode(secret.upper() + pad)


def totp(secret: str, *, at: int | None = None, step: int = 30, digits: int = 6) -> str:
    """Compute the TOTP code for a secret at a given unix time."""
    counter = int((at if at is not None else time.time()) // step)
    key = _b32decode(secret)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def verify(secret: str, code: str, *, window: int = 1) -> bool:
    """Verify a code, allowing +/- `window` steps for clock drift."""
    return verify_step(secret, code, window=window) is not None


def verify_step(secret: str, code: str, *, window: int = 1) -> int | None:
    """Like verify(), but return the 30-second time step the code belongs to (or None), so a
    caller can refuse to accept the same code twice."""
    if not code or not secret:
        return None
    code = code.strip().replace(" ", "")
    now = int(time.time())
    for w in range(-window, window + 1):
        if hmac.compare_digest(totp(secret, at=now + w * 30), code):
            return (now + w * 30) // 30
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str = "Tekly") -> str:
    """otpauth:// URI for authenticator-app QR enrollment."""
    from urllib.parse import quote
    label = quote(f"{issuer}:{account}")
    return (f"otpauth://totp/{label}?secret={secret}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30")


def make_recovery_codes(n: int = 8) -> tuple[list[str], str]:
    """Return (plaintext_codes, json_of_hashed_codes). Show plaintext once."""
    plain = ["-".join(secrets.token_hex(2) for _ in range(2)) for _ in range(n)]
    hashed = json.dumps([hash_password(c) for c in plain])
    return plain, hashed


def consume_recovery_code(hashed_json: str | None, code: str) -> tuple[bool, str | None]:
    """If `code` matches an unused recovery code, return (True, remaining_json)."""
    if not hashed_json:
        return False, hashed_json
    try:
        hashes = json.loads(hashed_json)
    except Exception:
        return False, hashed_json
    for i, h in enumerate(hashes):
        if verify_password(code.strip(), h):
            hashes.pop(i)
            return True, json.dumps(hashes)
    return False, hashed_json


# unused import guard (os kept for parity with other services)
_ = os
