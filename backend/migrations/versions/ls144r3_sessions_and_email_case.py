"""Launch audit — sign-out that really signs out, and case-insensitive sign-in emails.

- revoked_sessions: POST /api/auth/logout records the token's session id here so the token,
  and any it was refreshed into, is refused at once (logout used to only write an audit row).
- employees.email: lowercase every address that can be lowercased without colliding, then a
  unique index on lower(email) so A@x.com and a@x.com can't be two accounts. If two accounts
  already differ only by case, the index is skipped (and named in the log) rather than failing
  the deploy; the app's own lowercase checks still stop new ones.

Revision ID: ls144r3
Revises: ls144r2
"""
import sqlalchemy as sa
from alembic import op

revision = "ls144r3"
down_revision = "ls144r2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revoked_sessions",
        sa.Column("sid", sa.String(length=64), nullable=False),
        sa.Column("employee_id", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], name="fk_revoked_sessions_employee_id_employees",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sid", name="pk_revoked_sessions"),
    )
    op.create_index("ix_revoked_sessions_employee_id", "revoked_sessions", ["employee_id"])
    op.create_index("ix_revoked_sessions_expires_at", "revoked_sessions", ["expires_at"])

    conn = op.get_bind()
    clashes = conn.execute(sa.text(
        "SELECT lower(email) FROM employees GROUP BY lower(email) HAVING count(*) > 1")).fetchall()
    clash_set = {r[0] for r in clashes}
    for eid, email in conn.execute(sa.text("SELECT id, email FROM employees WHERE email <> lower(email)")).fetchall():
        if email and email.lower() not in clash_set:
            conn.execute(sa.text("UPDATE employees SET email = :e WHERE id = :i"), {"e": email.lower(), "i": eid})
    if clash_set:
        print("ls144r3: NOT adding uq_employees_email_lower; accounts differ only by email case: "
              + ", ".join(sorted(clash_set)) + ". Merge or rename them, then create the index.")
    else:
        op.create_index("uq_employees_email_lower", "employees", [sa.text("lower(email)")], unique=True)


def downgrade() -> None:
    conn = op.get_bind()
    if "uq_employees_email_lower" in {i["name"] for i in sa.inspect(conn).get_indexes("employees")}:
        op.drop_index("uq_employees_email_lower", table_name="employees")
    op.drop_index("ix_revoked_sessions_expires_at", table_name="revoked_sessions")
    op.drop_index("ix_revoked_sessions_employee_id", table_name="revoked_sessions")
    op.drop_table("revoked_sessions")
