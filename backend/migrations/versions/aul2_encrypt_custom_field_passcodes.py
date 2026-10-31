"""Audit Low fixes: encrypt passcode-like custom-field values already stored in plain text.

New writes are encrypted by db/secret_json.SecretFieldsJSON (orders, devices, clients, leads).
This re-saves existing rows so their "Passcode", "Login password", "Pattern lock"… values are
encrypted too. Without TOKEN_ENCRYPTION_KEY (dev, tests) there is no key and nothing changes.
Data only; no schema change.

Revision ID: aul2
Revises: aul1
"""
from alembic import op

revision = "aul2"
down_revision = "aul1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from backend.db.secret_json import encrypt_plain_rows
    encrypt_plain_rows(op.get_bind())


def downgrade() -> None:
    pass   # values stay encrypted; the model still reads them
