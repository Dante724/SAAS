"""A JSON column whose passcode-like values are encrypted at rest.

Custom fields such as "Passcode", "Login password" or "Pattern lock" (industry forms and
the one-click suggestions) are stored in `custom_data`. With TOKEN_ENCRYPTION_KEY set, those
values are Fernet-encrypted when written and decrypted when read, so the rest of the app sees
plain values and a database copy doesn't hold customers' unlock codes. Which keys count is
decided by name (SECRET_KEY_RE: the industry "password" fields are `passcode` / `password`, and
custom fields are keyed by their label, e.g. "Screen lock" → screen_lock). Everything else in the
dict is stored as before.
"""
import json
import re

import sqlalchemy as sa
from sqlalchemy.types import JSON, TypeDecorator

SECRET_KEY_RE = re.compile(r"passcode|password|lock|(security|access|unlock)_code|(^|_)pin($|_)")


SECRET_TABLES = ("orders", "devices", "clients", "leads")


def is_secret_key(key) -> bool:
    return isinstance(key, str) and bool(SECRET_KEY_RE.search(key.lower()))


def encrypt_plain_rows(conn) -> int:
    """Encrypt passcode-like values still stored in plain text; returns the rows changed.

    Runs on a sync connection: the aul2 migration, and the daily sweep (through run_sync) for rows
    saved before TOKEN_ENCRYPTION_KEY was set. Without a key it does nothing."""
    from backend.services import crypto
    if not crypto.is_active():
        return 0
    changed_rows = 0
    for table in SECRET_TABLES:
        rows = conn.execute(sa.text(
            f"SELECT id, custom_data FROM {table} WHERE custom_data IS NOT NULL AND ("  # noqa: S608 — fixed names
            # a cheap pre-filter; is_secret_key() decides per key
            "LOWER(CAST(custom_data AS TEXT)) LIKE '%pass%' OR LOWER(CAST(custom_data AS TEXT)) LIKE '%pin%' "
            "OR LOWER(CAST(custom_data AS TEXT)) LIKE '%lock%' OR LOWER(CAST(custom_data AS TEXT)) LIKE '%code%')"
        )).fetchall()
        for rid, data in rows:
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    continue
            if not isinstance(data, dict):
                continue
            changed = False
            for k, v in list(data.items()):
                if is_secret_key(k) and v not in (None, "") and not crypto.is_encrypted(v):
                    data[k] = crypto.encrypt(json.dumps(v))
                    changed = True
            if changed:
                conn.execute(sa.text(f"UPDATE {table} SET custom_data = :d WHERE id = :i"),  # noqa: S608
                             {"d": json.dumps(data), "i": rid})
                changed_rows += 1
    return changed_rows


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
