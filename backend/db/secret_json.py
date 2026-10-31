"""A JSON column whose passcode-like values are encrypted at rest.

Custom fields such as "Passcode", "Login password" or "Pattern lock" (industry forms and
the one-click suggestions) are stored in `custom_data`. With TOKEN_ENCRYPTION_KEY set, those
values are Fernet-encrypted when written and decrypted when read, so the rest of the app sees
plain values and a database copy doesn't hold customers' unlock codes. Which keys count is
decided by name (SECRET_KEY_RE); everything else in the dict is stored as before.
"""
import json
import re

from sqlalchemy.types import JSON, TypeDecorator

SECRET_KEY_RE = re.compile(r"passcode|password|pattern_lock|unlock|(^|_)pin($|_)")


def is_secret_key(key) -> bool:
    return isinstance(key, str) and bool(SECRET_KEY_RE.search(key.lower()))


class SecretFieldsJSON(TypeDecorator):
    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if not isinstance(value, dict):
            return value
        from backend.services import crypto
        if not crypto.is_active():
            return value
        out = {}
        for k, v in value.items():
            if is_secret_key(k) and v not in (None, "") and not crypto.is_encrypted(v):
                v = crypto.encrypt(json.dumps(v))
            out[k] = v
        return out

    def process_result_value(self, value, dialect):
        if not isinstance(value, dict):
            return value
        from backend.services import crypto
        out = {}
        for k, v in value.items():
            if crypto.is_encrypted(v):
                plain = crypto.decrypt(v)
                if plain is not v:
                    try:
                        v = json.loads(plain)
                    except ValueError:
                        v = plain
            out[k] = v
        return out
