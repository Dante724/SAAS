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

Background jobs, migrations and the platform console don't set a tenant, so they keep the
original behaviour. Public token pages, check-in and inbound webhooks set it with ``set_tenant``
as soon as they know which shop the request is for.

**Reads and bulk writes (architecture hardening, tenant read guard).** Every model whose rows
belong to one shop is marked ``OrgScoped``. While a session has a tenant, every ORM SELECT, and
every ORM bulk ``update(Model)`` / ``delete(Model)``, gets ``WHERE <model>.org_id = :tenant`` for
each OrgScoped entity in it (joins and aliases included). So ``db.get(Part, other_shops_id)`` is
None, ``update(Client).where(Client.id == other_shops_id)`` changes 0 rows, and a query that
forgets its ``org_id`` filter returns only this shop's rows. The hand-written filters stay: this
is the safety net under them, not a replacement. A row whose org_id is NULL (devices and POs
still allow it, from an old backfill: migration trg01 fills what it can) is visible to no shop.

- Not filtered: sessions with no tenant; refreshes of rows already loaded; and statements that
  opt out with ``.execution_options(skip_tenant_filter=True)``. Each such call site says why
  (global uniqueness checks such as sign-in emails and order barcodes).
- ``UNSCOPED_ORG_TABLES`` lists the tables with an org_id that are deliberately not filtered.

Known gaps (the guard only sees ORM entities, through SQL it actually runs):

- ``text()`` SQL, ``select(...).from_statement(...)``, statements on plain ``Table`` objects or
  ``Model.__table__`` columns, and Core constructs that never name the mapped class (a bare
  ``exists()`` over table columns) are not filtered. The flush guard's owner lookup below
  relies on exactly that (it reads ``target.c.org_id``) and also opts out explicitly.
- ``session.get`` answers from the identity map without SQL, so a row loaded earlier in the same
  session (before the tenant was set, or through an opt-out) comes back unfiltered.
- Rule that follows: a read made through an opt-out, or before ``set_tenant``, selects columns
  (ids, org ids), not whole rows, so no other shop's row lands in the session. The only entity
  opt-outs are the two legacy device reads (industry scan-resolve, inventory conversions), whose
  own WHERE clause limits them to this shop's rows and this shop's clients' org-less rows.
- ``MODE`` (setting ``TENANT_READ_GUARD``): "enforce" (default), "report" (filter nothing, record
  and log every other-shop row loaded: a rollout/diagnostic switch) or "off".
"""
import logging
from functools import cache

from fastapi import HTTPException
from sqlalchemy import column as sa_column
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Mapper, ORMExecuteState, Session, with_loader_criteria

log = logging.getLogger("tekly.tenancy")


class _TemplateOrgId:
    """``Mixin.org_id`` for with_loader_criteria, which calls its lambda once with the mixin itself
    to build a cached template. Each model declares its own real ``org_id`` column (FKs, indexes
    and ondelete differ), which shadows this; declarative ignores it (not a SQL object)."""

    def __get__(self, obj, owner):
        return sa_column("org_id")


class OrgScoped:
    """Marker mixin: every row belongs to exactly one shop through a non-null ``org_id``. Reads
    in a session with a tenant only see that shop's rows."""
    org_id = _TemplateOrgId()


# Tables that carry an org_id but are not tenant-filtered, and why. A test fails when a new model
# with an org_id is neither marked nor listed here.
UNSCOPED_ORG_TABLES = {
    "order_statuses": "org_id NULL = the platform's default status set, read by every shop",
    "admin_action_log": "platform console audit trail; org_id is the shop acted on, nullable",
    "billing_events": "Stripe Billing webhook ledger (platform), org_id nullable",
    "role_permission_backups": "written and read by migrations only",
}


def _configured_mode() -> str:
    from backend.config import settings
    mode = (getattr(settings, "TENANT_READ_GUARD", "") or "enforce").strip().lower()
    return mode if mode in ("enforce", "report", "off") else "enforce"


MODE = _configured_mode()

# (table, row's org, request's tenant) for every other-shop row an OrgScoped load returned while a
# tenant was set. Report mode fills it; in enforce mode only an explicit opt-out can. A bounded
# test/diagnostic hook, not an audit log.
cross_org_reads: list = []
_MAX_RECORDED = 1000


def set_tenant(db, org_id) -> None:
    """Scope this request's session to one shop (reads filtered, writes guarded). Public pages,
    check-in and webhooks call it as soon as they know which shop the request is for."""
    if org_id:
        getattr(db, "sync_session", db).info["tenant_org"] = org_id


@event.listens_for(Session, "do_orm_execute")
def _scope_to_tenant(state: ORMExecuteState) -> None:
    tenant = state.session.info.get("tenant_org")
    if not tenant or MODE != "enforce":
        return
    # ORM bulk UPDATE / DELETE (``update(Model).where(...)``) never pass through the flush guard,
    # so they get the same criteria as reads: another shop's row matches nothing (rowcount 0).
    if not (state.is_select or state.is_update or state.is_delete):
        return
    # Column loads refresh rows this session already holds; relationship loads inherit the
    # criteria from the query that loaded their parent (propagate_to_loaders).
    if state.is_select and (state.is_column_load or state.is_relationship_load):
        return
    if state.execution_options.get("skip_tenant_filter"):
        return
    state.statement = state.statement.options(
        with_loader_criteria(OrgScoped, lambda cls: cls.org_id == tenant, include_aliases=True))


@event.listens_for(Mapper, "load")
def _note_cross_org_load(obj, context) -> None:
    if MODE == "off" or not isinstance(obj, OrgScoped):
        return
    session = context.session
    tenant = session.info.get("tenant_org") if session is not None else None
    org = obj.__dict__.get("org_id")
    if tenant and org and org != tenant:
        table = type(obj).__table__.name
        if len(cross_org_reads) < _MAX_RECORDED:
            cross_org_reads.append((table, org, tenant))
        if MODE == "report":
            log.warning("tenant read guard (report only): a %s row of another shop was read", table)


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


# Plain-string references with no FK: table -> attributes holding a Part / Service / Bundle id
# (``ref_id``) or a Tax id (``tax_id``). A job's upsell recommendations carry a catalog ref_id too.
_SOFT_REFS = {"order_lines": ("ref_id", "tax_id"), "sale_lines": ("ref_id",),
              "recommendations": ("ref_id",)}


@cache
def _soft_tables() -> dict:
    from backend.models import Bundle, Part, Service, Tax
    return {"part": Part.__table__, "service": Service.__table__, "bundle": Bundle.__table__,
            "tax": Tax.__table__}


def _soft_targets(obj, attr: str = "ref_id") -> tuple:
    """Which table(s) a line's soft reference can point at: a tax for tax_id, else the catalog
    table from the line's own kind."""
    t = _soft_tables()
    if attr == "tax_id":
        return (t["tax"],)
    kind = (getattr(obj, "kind", None) or "").lower()
    ltype = (getattr(obj, "line_type", None) or "").lower()
    if kind == "product" or ltype == "part":
        return (t["part"],)
    if kind == "service" or (ltype == "labour" and kind != "custom"):
        return (t["service"],)
    if kind == "bundle":
        return (t["bundle"],)
    return (t["part"], t["service"], t["bundle"])


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
        for soft in (_SOFT_REFS.get(table, ()) if tenant else ()):
            val = getattr(obj, soft, None)
            if val and (is_new or state.attrs[soft].history.added):
                for target in _soft_targets(obj, soft):
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
                # This check exists to SEE another shop's owner and refuse. It selects plain table
                # columns, which the read guard doesn't filter anyway; skip_tenant_filter is belt and
                # braces, so a later refactor to ORM attributes can't silently disable the guard.
                for i, o in session.execute(select(pk, target.c.org_id).where(pk.in_(missing))
                                            .execution_options(skip_tenant_filter=True)).all():
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
