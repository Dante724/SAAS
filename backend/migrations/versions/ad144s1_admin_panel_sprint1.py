"""Admin panel sprint 1: locked-down /admin, roles, the action log, shop account IDs.

- platform_admins: role, token version, 2-step secret and recovery codes, last login.
- New tables: admin_sessions, admin_roles, admin_action_log (append-only: triggers refuse
  UPDATE and DELETE), admin_approval_requests, admin_shop_notes, admin_assignment_log.
- organisations: account_id (unique, backfilled here in batches, redrawing on a clash),
  discount end date and reason, suspend message, assigned admin, last contacted;
  discount_pct becomes an exact decimal.

Existing platform admins keep working as owners and must set up 2-step on their next login.
The NOT NULL on account_id comes in ad144s2, after this backfill.

Revision ID: ad144s1
Revises: ls144r4
"""
import secrets

import sqlalchemy as sa
from alembic import op

revision = "ad144s1"
down_revision = "ls144r4"
branch_labels = None
depends_on = None

# Copied from backend/services/account_id.py so later refactors can't change history.
_ALPHA = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def _new_id() -> str:
    body = "".join(secrets.choice(_ALPHA) for _ in range(6))
    chk = _ALPHA[sum((i + 1) * _ALPHA.index(c) for i, c in enumerate(body)) % len(_ALPHA)]
    return f"TK-{body[:4]}-{body[4:]}{chk}"


SQLITE_TRIGGERS = [
    "CREATE TRIGGER IF NOT EXISTS admin_action_log_no_update BEFORE UPDATE ON admin_action_log "
    "BEGIN SELECT RAISE(ABORT, 'admin_action_log is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS admin_action_log_no_delete BEFORE DELETE ON admin_action_log "
    "BEGIN SELECT RAISE(ABORT, 'admin_action_log is append-only'); END",
]
PG_TRIGGERS = [
    "CREATE OR REPLACE FUNCTION admin_action_log_append_only() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION 'admin_action_log is append-only'; END; $$ LANGUAGE plpgsql",
    "DROP TRIGGER IF EXISTS admin_action_log_no_change ON admin_action_log",
    "CREATE TRIGGER admin_action_log_no_change BEFORE UPDATE OR DELETE ON admin_action_log "
    "FOR EACH ROW EXECUTE FUNCTION admin_action_log_append_only()",
]


def upgrade() -> None:
    with op.batch_alter_table("platform_admins") as b:
        b.add_column(sa.Column("role_id", sa.String(), nullable=True))
        b.add_column(sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("mfa_secret", sa.Text(), nullable=True))
        b.add_column(sa.Column("mfa_recovery", sa.Text(), nullable=True))
        b.add_column(sa.Column("mfa_enrolled_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("mfa_last_step", sa.Integer(), nullable=True))
        b.add_column(sa.Column("last_login_at", sa.DateTime(), nullable=True))
    op.create_index("ix_platform_admins_role_id", "platform_admins", ["role_id"])

    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("admin_id", sa.String(), sa.ForeignKey("platform_admins.id"), nullable=False),
        sa.Column("created_at", sa.DateTime()), sa.Column("last_seen_at", sa.DateTime()),
        sa.Column("revoked_at", sa.DateTime()), sa.Column("ip", sa.String(64)),
    )
    op.create_index("ix_admin_sessions_admin_id", "admin_sessions", ["admin_id"])
    op.create_table(
        "admin_roles",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("builtin", sa.String(30)), sa.Column("permissions", sa.JSON()),
        sa.Column("field_levels", sa.JSON()), sa.Column("scope", sa.JSON()), sa.Column("limits", sa.JSON()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_table(
        "admin_action_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime()), sa.Column("admin_id", sa.String()), sa.Column("admin_email", sa.String(200)),
        sa.Column("action", sa.String(60), nullable=False), sa.Column("outcome", sa.String(20)),
        sa.Column("org_id", sa.String()), sa.Column("target", sa.String(120)),
        sa.Column("before", sa.JSON()), sa.Column("after", sa.JSON()), sa.Column("reason", sa.String(500)),
        sa.Column("ip", sa.String(64)), sa.Column("prev_hash", sa.String(64)), sa.Column("row_hash", sa.String(64)),
    )
    for col in ("at", "admin_id", "action", "org_id"):
        op.create_index(f"ix_admin_action_log_{col}", "admin_action_log", [col])
    bind = op.get_bind()
    for sql in (PG_TRIGGERS if bind.dialect.name == "postgresql" else SQLITE_TRIGGERS):
        op.execute(sql)
    op.create_table(
        "admin_approval_requests",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("requester_id", sa.String(), nullable=False), sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(40), nullable=False), sa.Column("payload", sa.JSON()),
        sa.Column("reason", sa.String(500)), sa.Column("status", sa.String(20)),
        sa.Column("decided_by", sa.String()), sa.Column("decided_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()), sa.Column("expires_at", sa.DateTime()),
    )
    op.create_index("ix_admin_approval_requests_requester_id", "admin_approval_requests", ["requester_id"])
    op.create_index("ix_admin_approval_requests_org_id", "admin_approval_requests", ["org_id"])
    op.create_table(
        "admin_shop_notes",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("admin_id", sa.String()), sa.Column("kind", sa.String(20)), sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_admin_shop_notes_org_id", "admin_shop_notes", ["org_id"])
    op.create_table(
        "admin_assignment_log",
        sa.Column("id", sa.String(), primary_key=True), sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("from_admin_id", sa.String()), sa.Column("to_admin_id", sa.String()),
        sa.Column("by_admin_id", sa.String()), sa.Column("reason", sa.String(300)), sa.Column("at", sa.DateTime()),
    )
    op.create_index("ix_admin_assignment_log_org_id", "admin_assignment_log", ["org_id"])

    with op.batch_alter_table("organisations") as b:
        b.add_column(sa.Column("account_id", sa.String(12), nullable=True))
        b.add_column(sa.Column("discount_until", sa.Date(), nullable=True))
        b.add_column(sa.Column("discount_reason", sa.String(300), nullable=True))
        b.add_column(sa.Column("suspend_message", sa.String(500), nullable=True))
        b.add_column(sa.Column("assigned_admin_id", sa.String(), nullable=True))
        b.add_column(sa.Column("last_contacted_at", sa.DateTime(), nullable=True))
        b.alter_column("discount_pct", type_=sa.Numeric(5, 2), existing_type=sa.Float())
    # Unique index first (NULLs don't clash in SQLite or Postgres), then backfill.
    op.create_index("ix_organisations_account_id", "organisations", ["account_id"], unique=True)
    op.create_index("ix_organisations_assigned_admin_id", "organisations", ["assigned_admin_id"])

    taken = {r[0] for r in bind.execute(sa.text(
        "SELECT account_id FROM organisations WHERE account_id IS NOT NULL")).fetchall()}
    while True:
        ids = [r[0] for r in bind.execute(sa.text(
            "SELECT id FROM organisations WHERE account_id IS NULL LIMIT 500")).fetchall()]
        if not ids:
            break
        for oid in ids:
            aid = _new_id()
            while aid in taken:
                aid = _new_id()
            taken.add(aid)
            bind.execute(sa.text("UPDATE organisations SET account_id = :a WHERE id = :i"), {"a": aid, "i": oid})


def downgrade() -> None:
    op.drop_index("ix_organisations_assigned_admin_id", table_name="organisations")
    op.drop_index("ix_organisations_account_id", table_name="organisations")
    with op.batch_alter_table("organisations") as b:
        b.alter_column("discount_pct", type_=sa.Float(), existing_type=sa.Numeric(5, 2))
        for col in ("last_contacted_at", "assigned_admin_id", "suspend_message", "discount_reason",
                    "discount_until", "account_id"):
            b.drop_column(col)
    for t in ("admin_assignment_log", "admin_shop_notes", "admin_approval_requests"):
        op.drop_table(t)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS admin_action_log_no_change ON admin_action_log")
        op.execute("DROP FUNCTION IF EXISTS admin_action_log_append_only()")
    op.drop_table("admin_action_log")
    op.drop_table("admin_roles")
    op.drop_table("admin_sessions")
    op.drop_index("ix_platform_admins_role_id", table_name="platform_admins")
    with op.batch_alter_table("platform_admins") as b:
        for col in ("last_login_at", "mfa_last_step", "mfa_enrolled_at", "mfa_recovery", "mfa_secret",
                    "token_version", "role_id"):
            b.drop_column(col)
