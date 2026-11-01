"""Audit Low fixes: API keys with no recorded creator go to the shop owner.

A key now stops working when its creator can't sign in, and one with no creator is refused. Keys
made before creators were recorded are given to the shop's owner (the earliest active employee
with no role), so they keep working for as long as the owner's account does. A shop with no
active owner keeps none, and its old keys stop. Data only; no schema change.

Revision ID: aul3
Revises: aul2
"""
import sqlalchemy as sa
from alembic import op

revision = "aul3"
down_revision = "aul2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    orgs = conn.execute(sa.text(
        "SELECT DISTINCT org_id FROM api_keys WHERE created_by_id IS NULL")).scalars().all()
    for org_id in orgs:
        owner = conn.execute(sa.text(
            "SELECT id FROM employees WHERE org_id = :o AND role_id IS NULL AND active = :t "
            "ORDER BY created_at LIMIT 1"), {"o": org_id, "t": True}).scalar()
        if owner:
            conn.execute(sa.text(
                "UPDATE api_keys SET created_by_id = :e WHERE org_id = :o AND created_by_id IS NULL"),
                {"e": owner, "o": org_id})


def downgrade() -> None:
    pass   # which keys had no creator isn't kept; they stay with the owner
