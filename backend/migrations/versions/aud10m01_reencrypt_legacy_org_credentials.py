"""Move shop integration keys off the legacy SECRET_KEY-derived key (audit 29 Oct 2026, M10).

Credentials saved before TOKEN_ENCRYPTION_KEY existed were encrypted with a key derived from
SECRET_KEY (the sign-in key), which was kept in the ring for reading. They are re-encrypted with
TOKEN_ENCRYPTION_KEY here and the legacy key is no longer used once a dedicated key is set. Rows
neither key can read are left alone: the shop sees "Enter them again" and nothing falls back to
the operator's keys. No-op without TOKEN_ENCRYPTION_KEY (local dev keeps the legacy key).

Revision ID: aud10m01
Revises: aud8m01
"""
import sqlalchemy as sa
from alembic import op

revision = "aud10m01"
down_revision = "aud8m01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from backend.services import crypto
    from backend.services.org_creds import reencrypt_legacy
    _require_key_when_deployed(crypto)
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, data_enc FROM org_integration_credentials "
                                "WHERE data_enc IS NOT NULL")).fetchall()
    for cid, token in rows:
        new = reencrypt_legacy(token)
        if new:
            conn.execute(sa.text("UPDATE org_integration_credentials SET data_enc = :t WHERE id = :id"),
                         {"t": new, "id": cid})


def _require_key_when_deployed(crypto) -> None:
    """A deployed migrate job without the app's TOKEN_ENCRYPTION_KEY would record this revision
    as done while leaving the rows untouched; refuse instead so it is rerun with the key."""
    from backend.config import settings
    if settings.is_deployed and not crypto.is_active():
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be set for this migration when deployed.")


def downgrade() -> None:
    # Rows stay readable with TOKEN_ENCRYPTION_KEY; the previous code also read that key first.
    pass
