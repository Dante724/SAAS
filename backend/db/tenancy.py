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

**Signed-in shop (audit 2026-10-29, H1–H3).** When a request is made by a signed-in employee,
``get_current_employee`` records their shop in ``session.info["tenant_org"]``. Then, on top of
the rule above:

- a new or changed row that carries an ``org_id`` must belong to that shop (loading another
  shop's Part by id and editing its cost or stock is refused);
- rows with **no** ``org_id`` of their own (order/PO/transfer/return/write-off lines, stock
  levels, stock movements) are checked through their foreign keys: every org-owned row they
  point at must belong to the signed-in shop;
- the plain-string catalog references on job and sale lines (``ref_id``) are checked the same
  way against products, services and bundles.

Public pages, webhooks, background jobs and the platform console don't set a tenant, so they
keep the original behaviour.
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


# Plain-string catalog references: table -> attribute holding a Part / Service / Bundle id.
_SOFT_REFS = {"order_lines": "ref_id", "sale_lines": "ref_id"}


@cache
def _soft_tables() -> dict:
    from backend.models import Bundle, Part, Service
    return {"part": Part.__table__, "service": Service.__table__, "bundle": Bundle.__table__}


def _soft_targets(obj) -> tuple:
    """Which catalog table(s) a line's ref_id can point at, from the line's own kind."""
    t = _soft_tables()
    kind = (getattr(obj, "kind", None) or "").lower()
    ltype = (getattr(obj, "line_type", None) or "").lower()
    if kind == "product" or ltype == "part":
        return (t["part"],)
    if kind == "service" or (ltype == "labour" and kind != "custom"):
        return (t["service"],)
    if kind == "bundle":
        return (t["bundle"],)
    return tuple(t.values())


@event.listens_for(Session, "before_flush")
def _guard_cross_tenant_references(session: Session, flush_context, instances) -> None:
    tenant = session.info.get("tenant_org")
    if tenant:
        for obj in session.deleted:
            org = getattr(obj, "org_id", None)
            if org and org != tenant:
                raise CrossTenantReference(f"{inspect(obj).mapper.local_table.name}.org_id")
    wanted: dict = {}   # (target table, pk column) -> {referenced id: {(referencing org, column)}}
    for obj in list(session.new) + list(session.dirty):
        has_org = hasattr(type(obj), "org_id")
        org = getattr(obj, "org_id", None)
        if has_org and org and tenant and org != tenant and (obj in session.new or session.is_modified(obj)):
            # Another shop's row, loaded by id and edited (or created for it) by this shop.
            raise CrossTenantReference(f"{inspect(obj).mapper.local_table.name}.org_id")
        if not org:
            if has_org or not tenant:
                continue
            org = tenant          # an org-less child row: everything it points at must be ours
        state = inspect(obj)
        is_new = obj in session.new
        table = state.mapper.local_table.name
        soft = _SOFT_REFS.get(table)
        if soft and tenant:
            val = getattr(obj, soft, None)
            if val and (is_new or state.attrs[soft].history.added):
                for target in _soft_targets(obj):
                    wanted.setdefault((target, target.c.id), {}).setdefault(val, set()).add((org, f"{table}.{soft}"))
        fks = _org_fks(state.mapper)
        if not fks:
            continue
        for key, colname, target, pk in fks:
            val = getattr(obj, key, None)
            if val is None:
                continue
            if has_org and not is_new and not state.attrs[key].history.added:
                continue      # FK unchanged on an existing org row (org-less rows: check them all)
            wanted.setdefault((target, pk), {}).setdefault(val, set()).add((org, f"{state.mapper.local_table.name}.{colname}"))
    if not wanted:
        return
    # Owners already known this request: rows loaded in the session, and ids checked earlier
    # (every autoflush runs this guard, so a 50-line sale would otherwise re-query the same rows).
    known: dict = session.info.setdefault("tenant_known", {})
    for o in session.identity_map.values():
        st = inspect(o)
        loaded = st.dict             # loaded values only: never trigger a lazy load mid-flush
        oid, oorg = loaded.get("id"), loaded.get("org_id")
        if oid and oorg:
            known[(st.mapper.local_table.name, oid)] = oorg
    with session.no_autoflush:
        for (target, pk), refs in wanted.items():
            owners = {i: known[(target.name, i)] for i in refs if (target.name, i) in known}
            missing = [i for i in refs if i not in owners]
            if missing:
                for i, o in session.execute(select(pk, target.c.org_id).where(pk.in_(missing))).all():
                    owners[i] = o
                    if o:
                        known[(target.name, i)] = o
            for ref_id, users in refs.items():
                owner = owners.get(ref_id)
                if owner is None:
                    continue          # global row, or not inserted yet (DB FK decides)
                for org, column in users:
                    if org != owner:
                        raise CrossTenantReference(column)
