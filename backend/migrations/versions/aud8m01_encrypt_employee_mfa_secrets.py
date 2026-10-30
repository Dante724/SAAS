"""Encrypt staff 2-step (TOTP) secrets at rest (audit 29 Oct 2026, M8).

They were stored as plain base32, so a leaked database backup was enough to generate valid
codes. The column grows to fit Fernet ciphertext and existing rows are encrypted with
TOKEN_ENCRYPTION_KEY (services/crypto). Without a key (local dev) values stay as they are and
are encrypted on next enrolment once a key is set; reads accept both.

Revision ID: aud8m01
Revises: aud6m01
"""
import sqlalchemy as sa
from alembic import op

revision = "aud8m01"
down_revision = "aud6m01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("employees") as b:
        b.alter_column("mfa_secret", existing_type=sa.String(64), type_=sa.String(255),
                       existing_nullable=True)
    from backend.services import crypto
    if not crypto.is_active():
        return
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, mfa_secret FROM employees WHERE mfa_secret IS NOT NULL")).fetchall()
    for eid, secret in rows:
        enc = crypto.encrypt(secret) if not crypto.is_encrypted(secret) else secret
        if enc != secret:
            conn.execute(sa.text("UPDATE employees SET mfa_secret = :s WHERE id = :id"), {"s": enc, "id": eid})


def downgrade() -> None:
    from backend.services import crypto
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, mfa_secret FROM employees WHERE mfa_secret IS NOT NULL")).fetchall()
    for eid, secret in rows:
        plain = crypto.decrypt(secret)
        if plain != secret:
            conn.execute(sa.text("UPDATE employees SET mfa_secret = :s WHERE id = :id"), {"s": plain, "id": eid})
    with op.batch_alter_table("employees") as b:
        b.alter_column("mfa_secret", existing_type=sa.String(255), type_=sa.String(64),
                       existing_nullable=True)
