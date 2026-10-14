"""Symmetric encryption for secrets at rest (OAuth access/refresh tokens).

If ``settings.TOKEN_ENCRYPTION_KEY`` (a urlsafe-base64 Fernet key) is set, values
are Fernet-encrypted with a version prefix before they hit the database.
Otherwise — dev and the test suite — values pass through unchanged.

Decryption is deliberately tolerant: anything that isn't our prefixed ciphertext
is returned as-is. That means plaintext rows written before encryption was turned
on, and rows written under a rotated/absent key, never crash the send path — they
just come back verbatim. Generate a key with ``Fernet.generate_key()``.
"""
from backend.config import settings

_PREFIX = "enc:v1:"


def _fernet():
    key = getattr(settings, "TOKEN_ENCRYPTION_KEY", "") or ""
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        return None


def is_active() -> bool:
    """True when a usable key is configured (encryption will actually happen)."""
    return _fernet() is not None


def encrypt(value):
    """Encrypt a string for storage. None passes through. No key → returns the
    value unchanged (dev/test)."""
    if value is None:
        return None
    f = _fernet()
    if not f:
        return value
    return _PREFIX + f.encrypt(value.encode()).decode()


def decrypt(value):
    """Reverse of :func:`encrypt`. Tolerates plaintext and undecryptable values
    by returning them unchanged, so the send path never raises on bad data."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        return value
    f = _fernet()
    if not f:
        return value
    try:
        return f.decrypt(value[len(_PREFIX):].encode()).decode()
    except Exception:
        return value
