"""Phase 4 — fast, consistent device identifier lookups.

1. Indexes (org_id, serial|imei|vin|hin) — intake "seen before" and the scanner look devices up
   by identifier; none of these columns was indexed. Built CONCURRENTLY on Postgres.
2. Tidy existing values the way services/identifiers.py now stores them (IMEI digits only;
   VIN / HIN upper-cased without spaces or dashes; serials upper-cased without spaces), so earlier
   records match scans.

Revision ID: id144p4
Revises: cr144p4
"""
import re

import sqlalchemy as sa
from alembic import op

revision = "id144p4"
down_revision = "cr144p4"
branch_labels = None
depends_on = None

_INDEXES = [("ix_devices_org_serial", "serial"), ("ix_devices_org_imei", "imei"),
            ("ix_devices_org_vin", "vin"), ("ix_devices_org_hin", "hin")]
_STRIP = re.compile(r"[\s\-_./]+")


def _norm(kind, v):
    if v is None or not str(v).strip():
        return None
    if kind == "imei":
        return re.sub(r"\D", "", v) or None
    if kind == "serial":
        return re.sub(r"\s+", "", str(v)).upper() or None
    return _STRIP.sub("", str(v).strip()).upper() or None


def _indexes() -> set:
    insp = sa.inspect(op.get_bind())
    return {i["name"] for i in insp.get_indexes("devices")} if insp.has_table("devices") else set()


def upgrade() -> None:
    bind = op.get_bind()
    have = _indexes()
    todo = [(n, c) for n, c in _INDEXES if n not in have]
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for name, col in todo:
                op.create_index(name, "devices", ["org_id", col], postgresql_concurrently=True, if_not_exists=True)
    else:
        for name, col in todo:
            op.create_index(name, "devices", ["org_id", col])

    devices = sa.table("devices", sa.column("id"), sa.column("serial"), sa.column("imei"),
                       sa.column("vin"), sa.column("hin"))
    rows = bind.execute(sa.select(devices.c.id, devices.c.serial, devices.c.imei,
                                  devices.c.vin, devices.c.hin)).all()
    for r in rows:
        new = {k: _norm(k, getattr(r, k)) for k in ("serial", "imei", "vin", "hin")}
        if any(new[k] != getattr(r, k) for k in new):
            bind.execute(devices.update().where(devices.c.id == r.id).values(**new))


def downgrade() -> None:
    # The value clean-up is not reversed (original spacing/case isn't kept); only indexes drop.
    have = _indexes()
    todo = [n for n, _ in _INDEXES if n in have]
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for name in todo:
                op.drop_index(name, table_name="devices", postgresql_concurrently=True, if_exists=True)
    else:
        for name in todo:
            op.drop_index(name, table_name="devices")
