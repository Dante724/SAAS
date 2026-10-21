"""Team B (billing) — credit notes, invoice credited amount, credit-note permissions.

- credit_notes + credit_note_lines (draft → issued → canceled; number taken on issue).
- invoices.credited: sum of issued credit notes linked to the invoice.
- Existing explicit (allow-list) roles get the credit-note codes that match what they can already
  do with invoices, so a Manager role does not silently lose access to a new document type:
  can_view_invoices → can_view_credit_notes, can_create_invoices → can_create_credit_notes,
  can_edit_issued_invoices → can_issue_credit_notes.

Revision ID: opb1
Revises: ls144r4
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "opb1"
down_revision = "ls144r4"
branch_labels = None
depends_on = None

_GRANTS = {
    "can_view_credit_notes": "can_view_invoices",
    "can_create_credit_notes": "can_create_invoices",
    "can_issue_credit_notes": "can_edit_issued_invoices",
}


def upgrade() -> None:
    op.create_table(
        "credit_notes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("number", sa.String(length=50), nullable=True),
        sa.Column("client_id", sa.String(), nullable=True),
        sa.Column("invoice_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 2, asdecimal=False), nullable=False),
        sa.Column("tax_total", sa.Numeric(12, 2, asdecimal=False), nullable=False),
        sa.Column("total", sa.Numeric(12, 2, asdecimal=False), nullable=False),
        sa.Column("created_by_id", sa.String(), nullable=True),
        sa.Column("issued_by_id", sa.String(), nullable=True),
        sa.Column("canceled_by_id", sa.String(), nullable=True),
        sa.Column("issued_at", sa.DateTime(), nullable=True),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"], name=op.f("fk_credit_notes_org_id_organisations")),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name=op.f("fk_credit_notes_client_id_clients")),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], name=op.f("fk_credit_notes_invoice_id_invoices")),
        sa.ForeignKeyConstraint(["created_by_id"], ["employees.id"], name=op.f("fk_credit_notes_created_by_id_employees")),
        sa.ForeignKeyConstraint(["issued_by_id"], ["employees.id"], name=op.f("fk_credit_notes_issued_by_id_employees")),
        sa.ForeignKeyConstraint(["canceled_by_id"], ["employees.id"], name=op.f("fk_credit_notes_canceled_by_id_employees")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_notes")),
        sa.UniqueConstraint("org_id", "number", name="uq_credit_notes_org_number"),
    )
    with op.batch_alter_table("credit_notes") as b:
        b.create_index(b.f("ix_credit_notes_org_id"), ["org_id"], unique=False)
        b.create_index(b.f("ix_credit_notes_client_id"), ["client_id"], unique=False)
        b.create_index(b.f("ix_credit_notes_invoice_id"), ["invoice_id"], unique=False)
        b.create_index("ix_credit_notes_org_created", ["org_id", "created_at"], unique=False)

    op.create_table(
        "credit_note_lines",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("credit_note_id", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("qty", sa.Numeric(12, 3, asdecimal=False), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2, asdecimal=False), nullable=False),
        sa.Column("tax_rate", sa.Numeric(7, 3, asdecimal=False), nullable=False),
        sa.Column("net", sa.Numeric(12, 2, asdecimal=False), nullable=False),
        sa.Column("tax", sa.Numeric(12, 2, asdecimal=False), nullable=False),
        sa.Column("total", sa.Numeric(12, 2, asdecimal=False), nullable=False),
        sa.ForeignKeyConstraint(["credit_note_id"], ["credit_notes.id"], ondelete="CASCADE",
                                name=op.f("fk_credit_note_lines_credit_note_id_credit_notes")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_note_lines")),
    )
    with op.batch_alter_table("credit_note_lines") as b:
        b.create_index(b.f("ix_credit_note_lines_credit_note_id"), ["credit_note_id"], unique=False)

    with op.batch_alter_table("invoices") as b:
        b.add_column(sa.Column("credited", sa.Numeric(12, 2, asdecimal=False), nullable=False, server_default="0"))

    # Grant the new codes to explicit roles that already hold the matching invoice code.
    bind = op.get_bind()
    roles = sa.table("roles", sa.column("id", sa.String), sa.column("permissions", sa.JSON),
                     sa.column("perms_explicit", sa.Boolean))
    for rid, perms, explicit in bind.execute(sa.select(roles.c.id, roles.c.permissions, roles.c.perms_explicit)).fetchall():
        if not explicit:
            continue        # legacy roles are default-allow: they already get the new codes
        p = perms if isinstance(perms, dict) else json.loads(perms or "{}")
        changed = False
        for new, source in _GRANTS.items():
            if new not in p and p.get(source) is True:
                p[new] = True
                changed = True
        if changed:
            bind.execute(roles.update().where(roles.c.id == rid).values(permissions=p))


def downgrade() -> None:
    with op.batch_alter_table("invoices") as b:
        b.drop_column("credited")
    with op.batch_alter_table("credit_note_lines") as b:
        b.drop_index(b.f("ix_credit_note_lines_credit_note_id"))
    op.drop_table("credit_note_lines")
    with op.batch_alter_table("credit_notes") as b:
        b.drop_index("ix_credit_notes_org_created")
        b.drop_index(b.f("ix_credit_notes_invoice_id"))
        b.drop_index(b.f("ix_credit_notes_client_id"))
        b.drop_index(b.f("ix_credit_notes_org_id"))
    op.drop_table("credit_notes")
