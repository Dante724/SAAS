"""Tenant-isolation guard: a shop's records may only reference that shop's records.

Most create/update endpoints take related ids straight from the request body (client_id,
device_id, part_id, supplier_id, location_id, …). Validating each one by hand in ~80 routers
left gaps where org A could attach — and then read, message or charge — org B's rows by id.

This guard closes the whole class at the ORM layer. Before every flush it looks at each new or
changed row that carries an ``org_id``, and for every foreign key on it that points at another
org-owned table, it checks the referenced row belongs to the same org. A mismatch aborts the
flush with a 404 (indistinguishable from "no such record", so ids can't be probed).

Rows whose own ``org_id`` is NULL, and referenced rows with a NULL ``org_id`` (shared/global
reference data), are not constrained. Referenced rows not yet in the database (inserted in the
same flush) are left to the database's own FK checks.
"""
from functools import cache

from fastapi import HTTPException
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Mapper, Session


class CrossTenantReference(HTTPException):
    def __init__(self, column: str):
        super().__init__(404, "Referenced record not found")
        self.column = column


@cache
def _org_fks(mapper: Mapper) -> tuple:
    """(attribute key, column name, target table) for each FK on this mapper whose target
    table is itself org-scoped and keyed by a single-column id."""
    out = []
    for prop in mapper.column_attrs:
        for col in prop.columns:
            for fk in getattr(col, "foreign_keys", ()):
                target = fk.column.table
                if "org_id" in target.c and target.name != "organisations" and fk.column.primary_key:
                    out.append((prop.key, col.name, target, fk.column))
    return tuple(out)


@event.listens_for(Session, "before_flush")
def _guard_cross_tenant_references(session: Session, flush_context, instances) -> None:
    wanted: dict = {}   # (target table, pk column) -> {referenced id: {(referencing org, column)}}
    for obj in list(session.new) + list(session.dirty):
        org = getattr(obj, "org_id", None)
        if not org:
            continue
        state = inspect(obj)
        fks = _org_fks(state.mapper)
        if not fks:
            continue
        is_new = obj in session.new
        for key, colname, target, pk in fks:
            val = getattr(obj, key, None)
            if val is None:
                continue
            if not is_new and not state.attrs[key].history.added:
                continue      # FK unchanged on an existing row
            wanted.setdefault((target, pk), {}).setdefault(val, set()).add((org, f"{state.mapper.local_table.name}.{colname}"))
    if not wanted:
        return
    with session.no_autoflush:
        for (target, pk), refs in wanted.items():
            owners = dict(session.execute(
                select(pk, target.c.org_id).where(pk.in_(list(refs)))).all())
            for ref_id, users in refs.items():
                owner = owners.get(ref_id)
                if owner is None:
                    continue          # global row, or not inserted yet (DB FK decides)
                for org, column in users:
                    if org != owner:
                        raise CrossTenantReference(column)
