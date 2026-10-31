"""Audit Low fixes: hashed, expiring customer-portal links; per-shop data retention.

- client_portal_links: one row per issued link, holding only the SHA-256 of the token and an
  expiry. Existing clients.portal_token values move across hashed (so links already sent keep
  working until they expire) and the plain-text column is dropped.
- organisations.retention_days: how long to keep old notifications, call data, messages and
  audit entries (null = keep, the default).
- notification_logs.is_html: HTML or plain, fixed from the template when the message is filled.

Revision ID: aul1
Revises: aud3r01
"""
import hashlib
import uuid
from datetime import datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision = "aul1"
down_revision = "aud3r01"
branch_labels = None
depends_on = None

_KEEP_DAYS = 90   # = services/portal_links.LINK_DAYS at the time of writing


def upgrade() -> None:
    op.create_table(
        "client_portal_links",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("client_id", sa.String(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_client_portal_links_org_id", "client_portal_links", ["org_id"])
    op.create_index("ix_client_portal_links_client_id", "client_portal_links", ["client_id"])
    op.create_index("ix_client_portal_links_token_hash", "client_portal_links", ["token_hash"], unique=True)
    op.create_index("ix_client_portal_links_expires_at", "client_portal_links", ["expires_at"])

    conn = op.get_bind()
    now = datetime.utcnow()
    links = sa.table("client_portal_links", *(sa.column(c) for c in
                                               ("id", "org_id", "client_id", "token_hash", "expires_at", "created_at")))
    rows = conn.execute(sa.text(
        "SELECT id, org_id, portal_token FROM clients WHERE portal_token IS NOT NULL")).fetchall()
    if rows:
        op.bulk_insert(links, [{
            "id": str(uuid.uuid4()), "org_id": r.org_id, "client_id": r.id,
            "token_hash": hashlib.sha256(r.portal_token.encode()).hexdigest(),
            "expires_at": now + timedelta(days=_KEEP_DAYS), "created_at": now} for r in rows])

    with op.batch_alter_table("clients") as b:
        b.drop_index("ix_clients_portal_token")
        b.drop_column("portal_token")

    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("retention_days", sa.JSON(), nullable=True))
    with op.batch_alter_table("notification_logs") as b:
        b.add_column(sa.Column("is_html", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("notification_logs") as b:
        b.drop_column("is_html")
    with op.batch_alter_table("organisations") as b:
        b.drop_column("retention_days")
    # Hashed links can't be turned back into tokens: customers need a fresh link after a downgrade.
    with op.batch_alter_table("clients") as b:
        b.add_column(sa.Column("portal_token", sa.String(length=64), nullable=True))
        b.create_index("ix_clients_portal_token", ["portal_token"], unique=True)
    op.drop_index("ix_client_portal_links_expires_at", table_name="client_portal_links")
    op.drop_index("ix_client_portal_links_token_hash", table_name="client_portal_links")
    op.drop_index("ix_client_portal_links_client_id", table_name="client_portal_links")
    op.drop_index("ix_client_portal_links_org_id", table_name="client_portal_links")
    op.drop_table("client_portal_links")
