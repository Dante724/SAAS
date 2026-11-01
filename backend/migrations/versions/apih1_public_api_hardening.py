"""Public API v1 hardening.

- api_keys.expires_at: keys can expire (NULL = never; keys made before this revision keep NULL).
- api_keys.previous_key_hash / previous_expires_at / rotated_at: a rotated key's old secret keeps
  working for a grace period so an integration can swap without downtime.
- api_idempotency_keys: Idempotency-Key replay store for POSTs on /api/v1 (24 hours).

Revision ID: apih1
Revises: ojj1
"""
import sqlalchemy as sa
from alembic import op

revision = "apih1"
down_revision = "ojj1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as b:
        b.add_column(sa.Column("expires_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("previous_key_hash", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("previous_expires_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("rotated_at", sa.DateTime(), nullable=True))
    op.create_index("ix_api_keys_previous_key_hash", "api_keys", ["previous_key_hash"])

    op.create_table(
        "api_idempotency_keys",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("key_id", sa.String(), nullable=False),
        sa.Column("idem_key", sa.String(length=255), nullable=False),
        sa.Column("route", sa.String(length=80), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"], name="fk_api_idempotency_keys_org_id_organisations"),
        sa.ForeignKeyConstraint(["key_id"], ["api_keys.id"], name="fk_api_idempotency_keys_key_id_api_keys"),
        sa.PrimaryKeyConstraint("id", name="pk_api_idempotency_keys"),
        sa.UniqueConstraint("org_id", "key_id", "idem_key", name="uq_api_idempotency_key"),
    )
    op.create_index("ix_api_idempotency_keys_org_id", "api_idempotency_keys", ["org_id"])
    op.create_index("ix_api_idempotency_keys_key_id", "api_idempotency_keys", ["key_id"])
    op.create_index("ix_api_idempotency_keys_created_at", "api_idempotency_keys", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_api_idempotency_keys_created_at", table_name="api_idempotency_keys")
    op.drop_index("ix_api_idempotency_keys_key_id", table_name="api_idempotency_keys")
    op.drop_index("ix_api_idempotency_keys_org_id", table_name="api_idempotency_keys")
    op.drop_table("api_idempotency_keys")
    op.drop_index("ix_api_keys_previous_key_hash", table_name="api_keys")
    with op.batch_alter_table("api_keys") as b:
        b.drop_column("rotated_at")
        b.drop_column("previous_expires_at")
        b.drop_column("previous_key_hash")
        b.drop_column("expires_at")
