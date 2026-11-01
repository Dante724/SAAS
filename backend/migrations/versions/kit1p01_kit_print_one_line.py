"""Kits print as one line, extras still shown.

A kit (bundle) still explodes into its real component lines — money, stock, cost, commission and
tax stay on those lines. These columns only tag the lines with the kit they came from, so customer
documents can fold them into one line and a fixed kit price can stay locked when a part changes.

order_lines (all nullable, no backfill — old "(Kit)" lines stay untagged and print as before):
- bundle_group_id      one id per kit added to a job; every line from that kit shares it
- bundle_name          the kit's name when it was added (the folded line's text)
- bundle_print_mode    itemized | one_line | one_line_includes, per job (copied from the kit)
- bundle_price_locked  the kit's fixed total on this job (null = priced as the sum of its parts)
- bundle_qty           how many kits were added (the folded line's quantity)
- bundle_std_price     the part's standard unit price when added (weights for re-spreading)

bundles.print_mode: how the kit prints by default. Null/absent = itemized, so nothing changes.

New permissions on existing roles:
- can_change_kit_print: roles that can manage kits keep the matching per-job control.
- can_print_itemised_copy: every role (a customer can always get an itemised copy).
- can_change_kit_price: opt-in; granted to roles that can approve discounts.

Revision ID: kit1p01
Revises: opd1
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "kit1p01"
down_revision = "opd1"
branch_labels = None
depends_on = None

NEW_CODES = ("can_change_kit_print", "can_change_kit_price", "can_print_itemised_copy")


def upgrade() -> None:
    with op.batch_alter_table("order_lines") as b:
        b.add_column(sa.Column("bundle_group_id", sa.String(length=36), nullable=True))
        b.add_column(sa.Column("bundle_name", sa.String(length=200), nullable=True))
        b.add_column(sa.Column("bundle_print_mode", sa.String(length=20), nullable=True))
        b.add_column(sa.Column("bundle_price_locked", sa.Numeric(12, 2, asdecimal=False), nullable=True))
        b.add_column(sa.Column("bundle_qty", sa.Float(), nullable=True))
        b.add_column(sa.Column("bundle_std_price", sa.Numeric(12, 2, asdecimal=False), nullable=True))
    op.create_index("ix_order_lines_bundle_group_id", "order_lines", ["bundle_group_id"])
    with op.batch_alter_table("bundles") as b:
        b.add_column(sa.Column("print_mode", sa.String(length=20), nullable=True,
                               server_default="itemized"))
    _grant_role_perms()


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
            perms["can_change_kit_print"] = perms.get("can_manage_bundles") is True
            perms["can_print_itemised_copy"] = True
            perms["can_change_kit_price"] = perms.get("can_approve_discount") is True
        else:                                          # legacy deny-list: absent = allowed
            if perms.get("can_manage_bundles") is False:
                perms["can_change_kit_print"] = False
            # opt-in: only an explicit True grants it
            perms["can_change_kit_price"] = perms.get("can_approve_discount") is True
        _write(conn, rid, perms)


def downgrade() -> None:
    conn, rows = _rows()
    for rid, raw, _explicit in rows:
        perms = dict(raw if isinstance(raw, dict) else (json.loads(raw) if raw else {}) or {})
        if any(k in perms for k in NEW_CODES):
            _write(conn, rid, {k: v for k, v in perms.items() if k not in NEW_CODES})
    with op.batch_alter_table("bundles") as b:
        b.drop_column("print_mode")
    op.drop_index("ix_order_lines_bundle_group_id", table_name="order_lines")
    with op.batch_alter_table("order_lines") as b:
        for c in ("bundle_std_price", "bundle_qty", "bundle_price_locked", "bundle_print_mode",
                  "bundle_name", "bundle_group_id"):
            b.drop_column(c)
