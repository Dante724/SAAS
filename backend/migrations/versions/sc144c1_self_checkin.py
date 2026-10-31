"""Self check-in: check-in points (one QR each, several per location) and check-ins.

* checkin_points: a place customers arrive at a location, with its own unguessable token.
  Every existing location gets a "Front door" point (mode auto: walk-in in working hours,
  key drop outside them).
* checkins: one customer's check-in, tied to the inquiry (lead) staff work with.
* custom_fields.ask_at_checkin: show this field on the customer's check-in page.

Revision ID: sc144c1
Revises: rl10a1
"""
import secrets
import uuid
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "sc144c1"
down_revision = "rl10a1"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("custom_fields", "ask_at_checkin"):
        op.add_column("custom_fields", sa.Column("ask_at_checkin", sa.Boolean(), nullable=False,
                                                 server_default=sa.false()))

    if not _has_table("checkin_points"):
        op.create_table(
            "checkin_points",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
            sa.Column("location_id", sa.String(), sa.ForeignKey("locations.id"), nullable=False),
            sa.Column("name", sa.String(80), nullable=False),
            sa.Column("mode", sa.String(12), nullable=False, server_default="auto"),
            sa.Column("kiosk", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("job_type_ids", sa.JSON(), nullable=True),
            sa.Column("welcome_text", sa.Text(), nullable=True),
            sa.Column("terms_template_id", sa.String(), sa.ForeignKey("document_templates.id"), nullable=True),
            sa.Column("require_photo", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("show_wait", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("token", sa.String(64), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("scans", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("submits", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("rotated_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("uq_checkin_points_token", "checkin_points", ["token"], unique=True)
        op.create_index("ix_checkin_points_org_loc", "checkin_points", ["org_id", "location_id"])

    if not _has_table("checkins"):
        op.create_table(
            "checkins",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("org_id", sa.String(), sa.ForeignKey("organisations.id"), nullable=False),
            sa.Column("location_id", sa.String(), sa.ForeignKey("locations.id"), nullable=False),
            sa.Column("point_id", sa.String(), sa.ForeignKey("checkin_points.id"), nullable=False),
            sa.Column("lead_id", sa.String(), sa.ForeignKey("leads.id"), nullable=False),
            sa.Column("kind", sa.String(10), nullable=False, server_default="walk_in"),
            sa.Column("queue_no", sa.Integer(), nullable=False),
            sa.Column("queue_day", sa.Date(), nullable=False),
            sa.Column("state", sa.String(12), nullable=False, server_default="waiting"),
            sa.Column("job_type_id", sa.String(), sa.ForeignKey("work_order_types.id"), nullable=True),
            sa.Column("asset_group", sa.String(40), nullable=True),
            sa.Column("identifier_kind", sa.String(20), nullable=True),
            sa.Column("identifier_value", sa.String(100), nullable=True),
            sa.Column("decoded", sa.JSON(), nullable=True),
            sa.Column("reading_kind", sa.String(20), nullable=True),
            sa.Column("reading_value", sa.Float(), nullable=True),
            sa.Column("answers", sa.JSON(), nullable=True),
            sa.Column("form_snapshot", sa.JSON(), nullable=True),
            sa.Column("signature_png", sa.Text(), nullable=True),
            sa.Column("signer_name", sa.String(200), nullable=True),
            sa.Column("signer_ip", sa.String(64), nullable=True),
            sa.Column("terms_hash", sa.String(64), nullable=True),
            sa.Column("photo_ids", sa.JSON(), nullable=True),
            sa.Column("key_tag", sa.String(40), nullable=True),
            sa.Column("status_token", sa.String(64), nullable=False),
            sa.Column("called_at", sa.DateTime(), nullable=True),
            sa.Column("converted_order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("uq_checkins_status_token", "checkins", ["status_token"], unique=True)
        op.create_index("uq_checkins_lead", "checkins", ["lead_id"], unique=True)
        op.create_index("uq_checkins_queue", "checkins", ["org_id", "location_id", "queue_day", "queue_no"],
                        unique=True)

    # Every existing location gets a working "Front door" point.
    bind = op.get_bind()
    locs = bind.execute(sa.text(
        "SELECT l.id, l.org_id FROM locations l WHERE NOT EXISTS "
        "(SELECT 1 FROM checkin_points p WHERE p.location_id = l.id)")).fetchall()
    now = datetime.utcnow()
    for loc_id, org_id in locs:
        bind.execute(sa.text(
            "INSERT INTO checkin_points (id, org_id, location_id, name, mode, kiosk, job_type_ids, "
            "require_photo, show_wait, token, active, scans, submits, created_at) VALUES "
            "(:id, :org, :loc, 'Front door', 'auto', :no, '[]', :no, :yes, :tok, :yes, 0, 0, :now)"),
            {"id": str(uuid.uuid4()), "org": org_id, "loc": loc_id, "tok": secrets.token_urlsafe(24),
             "no": False, "yes": True, "now": now})


def downgrade() -> None:
    op.drop_table("checkins")
    op.drop_table("checkin_points")
    with op.batch_alter_table("custom_fields") as b:
        b.drop_column("ask_at_checkin")
