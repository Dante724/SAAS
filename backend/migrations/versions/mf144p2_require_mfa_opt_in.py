"""Security phase 1 — make org-wide mandatory 2FA opt-in.

`organisations.require_mfa` defaulted to TRUE, but it was only ever a client-side nudge.
2FA is now enforced on the server for orgs that require it, so — per the owner's decision —
every existing org is switched to FALSE (nothing changes for anyone until an owner turns it on
in Settings) and new orgs default to FALSE.

Revision ID: mf144p2
Revises: mf144p1
"""
import sqlalchemy as sa
from alembic import op

revision = "mf144p2"
down_revision = "mf144p1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE organisations SET require_mfa = :off").bindparams(off=False))
    with op.batch_alter_table("organisations") as b:
        b.alter_column("require_mfa", existing_type=sa.Boolean(), existing_nullable=False,
                       server_default=sa.false())


def downgrade() -> None:
    # Restores the old default only; per-org values chosen after the upgrade are kept.
    with op.batch_alter_table("organisations") as b:
        b.alter_column("require_mfa", existing_type=sa.Boolean(), existing_nullable=False,
                       server_default=sa.true())
