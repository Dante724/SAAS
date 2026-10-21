"""D1 — Twilio voice: calls.provider_sid (the provider's call id, e.g. Twilio CallSid),
unique per org so a retried webhook can never log the same call twice.

`calls.direction` already exists (A7), so only provider_sid is added.

Also (integration review S3) two new permissions on existing roles, so nobody gains or loses
anything they couldn't do before except what the review asked for:
- can_make_calls: granted to a role exactly when it can reply to customers (can_reply_customer).
- can_create_clients: POST /clients was open to every role, so every existing role keeps it.
New roles get both from the templates (Partner: no calls).

Revision ID: opd1
Revises: opa1
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "opd1"
down_revision = "opa1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("calls") as b:
        b.add_column(sa.Column("provider_sid", sa.String(length=64), nullable=True))
    op.create_index("uq_calls_org_provider_sid", "calls", ["org_id", "provider_sid"], unique=True)
    _grant_role_perms()


NEW_CODES = ("can_make_calls", "can_create_clients")


def _rows():
    conn = op.get_bind()
    return conn, conn.execute(sa.text("SELECT id, permissions, perms_explicit FROM roles")).fetchall()


def _write(conn, rid, perms):
    stmt = sa.text("UPDATE roles SET permissions = :p WHERE id = :i").bindparams(
        sa.bindparam("p", type_=sa.JSON()))
    conn.execute(stmt, {"p": perms, "i": rid})


def _grant_role_perms() -> None:
    conn, rows = _rows()
    for rid, raw, explicit in rows:
        perms = dict(raw if isinstance(raw, dict) else (json.loads(raw) if raw else {}) or {})
        if not explicit and perms.get("all") == "write":
            continue                                   # legacy admin: already everything
        if explicit:                                   # default-deny allow-list
            perms["can_make_calls"] = perms.get("can_reply_customer") is True
            perms["can_create_clients"] = True
        else:                                          # legacy deny-list: absent = allowed
            if perms.get("can_reply_customer") is False:
                perms["can_make_calls"] = False
        _write(conn, rid, perms)


def downgrade() -> None:
    conn, rows = _rows()
    for rid, raw, _explicit in rows:
        perms = dict(raw if isinstance(raw, dict) else (json.loads(raw) if raw else {}) or {})
        if any(k in perms for k in NEW_CODES):
            _write(conn, rid, {k: v for k, v in perms.items() if k not in NEW_CODES})
    op.drop_index("uq_calls_org_provider_sid", table_name="calls")
    with op.batch_alter_table("calls") as b:
        b.drop_column("provider_sid")
