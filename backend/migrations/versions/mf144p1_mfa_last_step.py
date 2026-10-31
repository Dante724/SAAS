"""Security phase 1 — remember the last accepted TOTP step per employee.

A TOTP code stays valid for its whole 30-second window (±1 step for clock drift), so without
this an intercepted code could be replayed. Login now rejects a step at or before the last
one accepted. Nullable, no backfill: NULL means "nothing accepted yet".

Revision ID: mf144p1
Revises: fk144ep01
"""
import sqlalchemy as sa
from alembic import op

revision = "mf144p1"
down_revision = "fk144ep01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("employees") as b:
        b.add_column(sa.Column("mfa_last_step", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("employees") as b:
        b.drop_column("mfa_last_step")
