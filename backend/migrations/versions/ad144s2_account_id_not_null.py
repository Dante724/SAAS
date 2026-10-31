"""Every shop has an account ID now (backfilled in ad144s1): make it required.

Fails loudly, rather than silently, if any shop is still missing one.

Revision ID: ad144s2
Revises: ad144s1
"""
import sqlalchemy as sa
from alembic import op

revision = "ad144s2"
down_revision = "ad144s1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    missing = op.get_bind().execute(sa.text("SELECT count(*) FROM organisations WHERE account_id IS NULL")).scalar()
    if missing:
        raise RuntimeError(f"ad144s2: {missing} shops have no account_id; re-run ad144s1's backfill first")
    with op.batch_alter_table("organisations") as b:
        b.alter_column("account_id", existing_type=sa.String(12), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("organisations") as b:
        b.alter_column("account_id", existing_type=sa.String(12), nullable=True)
