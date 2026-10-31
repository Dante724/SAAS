"""Audit Low fixes: encrypt passcode-like custom-field values already stored in plain text.

New writes are encrypted by db/secret_json.SecretFieldsJSON (orders, devices, clients, leads).
This re-saves existing rows so their "Passcode", "Login password", "Pattern lock"… values are
encrypted too. Without TOKEN_ENCRYPTION_KEY (dev, tests) there is no key and nothing changes.
Data only; no schema change.

Revision ID: aul2
Revises: aul1
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "aul2"
down_revision = "aul1"
branch_labels = None
depends_on = None

_TABLES = ("orders", "devices", "clients", "leads")


def upgrade() -> None:
    from backend.db.secret_json import is_secret_key
    from backend.services import crypto
    if not crypto.is_active():
        return
    conn = op.get_bind()
    for table in _TABLES:
        rows = conn.execute(sa.text(
            f"SELECT id, custom_data FROM {table} WHERE custom_data IS NOT NULL AND ("  # noqa: S608 — fixed names
            "LOWER(CAST(custom_data AS TEXT)) LIKE '%pass%' OR LOWER(CAST(custom_data AS TEXT)) LIKE '%pin%' "
            "OR LOWER(CAST(custom_data AS TEXT)) LIKE '%unlock%' OR LOWER(CAST(custom_data AS TEXT)) LIKE '%pattern%')"
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


def downgrade() -> None:
    pass   # values stay encrypted; the model still reads them
