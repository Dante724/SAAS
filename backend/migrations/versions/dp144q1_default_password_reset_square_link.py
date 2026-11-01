"""Staff still on the old default password must reset it; Square pay links can be closed.

- employees.must_reset_password: set for every active account whose password is still the old
  default ("changeme123", given to staff created from the React screen before that was fixed).
  Those accounts are also signed out everywhere (token_version bump). Sign-in then refuses them
  and emails a reset link instead (H1).
- payment_intents.provider_link_id: the provider's id for the link itself. Square's API needs
  the payment-link id to delete a link, but only its order id was stored (B3).

Revision ID: dp144q1
Revises: pj144p9
"""
import sqlalchemy as sa
from alembic import op

revision = "dp144q1"
down_revision = "pj144p9"
branch_labels = None
depends_on = None

OLD_DEFAULTS = ("changeme123",)


def upgrade() -> None:
    with op.batch_alter_table("employees") as b:
        b.add_column(sa.Column("must_reset_password", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    with op.batch_alter_table("payment_intents") as b:
        b.add_column(sa.Column("provider_link_id", sa.String(120), nullable=True))

    from backend.services.auth import verify_password
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, password_hash FROM employees WHERE active = :t"),
                        {"t": True}).fetchall()
    flagged = [eid for eid, h in rows if h and any(_safe_verify(verify_password, p, h) for p in OLD_DEFAULTS)]
    for eid in flagged:
        conn.execute(sa.text("UPDATE employees SET must_reset_password = :t, "
                             "token_version = COALESCE(token_version, 0) + 1 WHERE id = :id"),
                     {"t": True, "id": eid})


def _safe_verify(verify, password, hashed) -> bool:
    try:
        return bool(verify(password, hashed))
    except Exception:
        return False


def downgrade() -> None:
    with op.batch_alter_table("payment_intents") as b:
        b.drop_column("provider_link_id")
    with op.batch_alter_table("employees") as b:
        b.drop_column("must_reset_password")
