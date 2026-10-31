"""Team E — payroll scope per role, own-earnings permission, checklist auto-attach.

- roles.payroll_scope (own | all | selected) + roles.payroll_employee_ids: whose payroll a role
  may see and manage. Existing roles keep "all", so nothing changes until someone narrows it.
- can_view_own_payroll is granted to existing explicit roles that already hold
  can_view_own_compensation (the older "see your own pay" right). A role that was denied its own
  pay stays denied; explicit roles would otherwise start without it either way.
- checklist_templates.auto_status_keys: statuses that attach the template automatically.

Revision ID: ope1
Revises: ls144r4
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "ope1"
down_revision = "ls144r4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("roles") as b:
        b.add_column(sa.Column("payroll_scope", sa.String(length=10), nullable=False, server_default="all"))
        b.add_column(sa.Column("payroll_employee_ids", sa.JSON(), nullable=True))
    with op.batch_alter_table("checklist_templates") as b:
        b.add_column(sa.Column("auto_status_keys", sa.JSON(), nullable=True))

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, permissions FROM roles WHERE perms_explicit = :t"), {"t": True}).fetchall()
    for rid, raw in rows:
        perms = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
        perms = dict(perms or {})
        if "can_view_own_payroll" in perms or perms.get("can_view_own_compensation") is not True:
            continue
        perms["can_view_own_payroll"] = True
        stmt = sa.text("UPDATE roles SET permissions = :p WHERE id = :i").bindparams(
            sa.bindparam("p", type_=sa.JSON()))
        conn.execute(stmt, {"p": perms, "i": rid})


def downgrade() -> None:
    with op.batch_alter_table("checklist_templates") as b:
        b.drop_column("auto_status_keys")
    with op.batch_alter_table("roles") as b:
        b.drop_column("payroll_employee_ids")
        b.drop_column("payroll_scope")
