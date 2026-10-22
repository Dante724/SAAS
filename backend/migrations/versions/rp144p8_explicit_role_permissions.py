"""Roles become explicit allow-lists, so new permissions start off.

Adds roles.perms_explicit and snapshots every existing role: each current permission
is written out as granted unless the role switched it off (opt-in codes stay off unless
granted). Behaviour for existing roles is unchanged; codes added to the catalogue later
are denied for every role until someone grants them. The owner (no role) keeps everything.

Revision ID: rp144p8
Revises: rc144p7
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "rp144p8"
down_revision = "rc144p7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("roles") as b:
        b.add_column(sa.Column("perms_explicit", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    from backend.services.permissions import ALL_CODES, OPT_IN
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, permissions FROM roles")).fetchall()
    for rid, raw in rows:
        perms = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
        perms = perms or {}
        if perms.get("all") == "write":          # the shop creator's admin role: everything
            snap = {c: True for c in ALL_CODES}
        else:
            snap = {c: (perms.get(c) is True) if c in OPT_IN else (perms.get(c) is not False) for c in ALL_CODES}
        stmt = sa.text("UPDATE roles SET permissions = :p, perms_explicit = :t WHERE id = :i").bindparams(
            sa.bindparam("p", type_=sa.JSON()))
        conn.execute(stmt, {"p": snap, "t": True, "i": rid})


def downgrade() -> None:
    # Explicit allow-lists read the same under the old deny-wins rule for every code they list.
    with op.batch_alter_table("roles") as b:
        b.drop_column("perms_explicit")
