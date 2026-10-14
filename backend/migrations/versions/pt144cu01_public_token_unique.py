"""Build 144cu — orders.public_token is unique (the customer track link can never repeat).

Dedupes any pre-existing collisions (and fills NULLs are left as-is: multiple NULLs are allowed
by a unique index, so revoked links stay revoked), then adds a DB-wide unique index.

Revision ID: pt144cu01
Revises: tx144bh01
"""
import secrets

import sqlalchemy as sa
from alembic import op

revision = "pt144cu01"
down_revision = "tx144bh01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # 1) Regenerate any duplicate non-null tokens so the unique index can be built.
    rows = conn.execute(sa.text(
        "SELECT id, public_token FROM orders WHERE public_token IS NOT NULL ORDER BY id"
    )).fetchall()
    seen = set()
    for oid, tok in rows:
        if tok in seen:
            new = secrets.token_urlsafe(16)
            while new in seen:
                new = secrets.token_urlsafe(16)
            conn.execute(sa.text("UPDATE orders SET public_token = :t WHERE id = :i"),
                         {"t": new, "i": oid})
            seen.add(new)
        else:
            seen.add(tok)

    # 2) Replace the plain index with a unique one.
    try:
        op.drop_index("ix_orders_public_token", table_name="orders")
    except Exception:  # noqa: BLE001 — index name may differ / not exist on some DBs
        pass
    op.create_index("ix_orders_public_token", "orders", ["public_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_orders_public_token", table_name="orders")
    op.create_index("ix_orders_public_token", "orders", ["public_token"])
