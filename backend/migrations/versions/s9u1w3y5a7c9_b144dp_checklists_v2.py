"""b144dp — checklists v2: steps, sections, typed fields, per-order checklists

Extends the flat 144-era checklist into Orderry-style
Template → Steps → Sections → typed Fields, plus an OrderChecklist header so a
work order can carry several checklists, each with its own status/result.

Revision ID: s9u1w3y5a7c9
Revises: r8t0v2x4z6b8
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa

revision = "s9u1w3y5a7c9"
down_revision = "r8t0v2x4z6b8"
branch_labels = None
depends_on = None


def upgrade():
    # templates: scope + selected type ids
    with op.batch_alter_table("checklist_templates") as b:
        b.add_column(sa.Column("scope", sa.String(length=12), nullable=False, server_default="all"))
        b.add_column(sa.Column("type_ids", sa.Text(), nullable=False, server_default="[]"))

    op.create_table(
        "checklist_steps",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("template_id", sa.String(), sa.ForeignKey("checklist_templates.id", ondelete="CASCADE"), index=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "checklist_sections",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("step_id", sa.String(), sa.ForeignKey("checklist_steps.id", ondelete="CASCADE"), index=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("allow_notes", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )

    with op.batch_alter_table("checklist_template_items") as b:
        b.add_column(sa.Column("section_id", sa.String(), sa.ForeignKey("checklist_sections.id", ondelete="CASCADE"), nullable=True))
        b.add_column(sa.Column("description", sa.Text()))
        b.add_column(sa.Column("field_kind", sa.String(length=20), nullable=False, server_default="check_result"))
        b.add_column(sa.Column("options", sa.Text(), nullable=False, server_default="[]"))
        b.add_column(sa.Column("client_visible", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("affects_result", sa.Boolean(), nullable=False, server_default=sa.true()))

    op.create_table(
        "order_checklists",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), index=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id", ondelete="CASCADE"), index=True),
        sa.Column("template_id", sa.String(), sa.ForeignKey("checklist_templates.id")),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=14), nullable=False, server_default="in_progress"),
        sa.Column("result", sa.String(length=10)),
        sa.Column("author_id", sa.String(), sa.ForeignKey("employees.id")),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
    )

    with op.batch_alter_table("order_checklist_items") as b:
        b.add_column(sa.Column("checklist_id", sa.String(), sa.ForeignKey("order_checklists.id", ondelete="CASCADE"), nullable=True))
        b.add_column(sa.Column("step_title", sa.String(length=200)))
        b.add_column(sa.Column("section_title", sa.String(length=200)))
        b.add_column(sa.Column("field_kind", sa.String(length=20), nullable=False, server_default="check_result"))
        b.add_column(sa.Column("options", sa.Text(), nullable=False, server_default="[]"))
        b.add_column(sa.Column("value", sa.Text()))
        b.add_column(sa.Column("file_ids", sa.Text(), nullable=False, server_default="[]"))
        b.add_column(sa.Column("client_visible", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("affects_result", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    with op.batch_alter_table("order_checklist_items") as b:
        for c in ("checklist_id", "step_title", "section_title", "field_kind", "options",
                  "value", "file_ids", "client_visible", "affects_result"):
            b.drop_column(c)
    op.drop_table("order_checklists")
    with op.batch_alter_table("checklist_template_items") as b:
        for c in ("section_id", "description", "field_kind", "options", "client_visible", "affects_result"):
            b.drop_column(c)
    op.drop_table("checklist_sections")
    op.drop_table("checklist_steps")
    with op.batch_alter_table("checklist_templates") as b:
        b.drop_column("type_ids")
        b.drop_column("scope")
