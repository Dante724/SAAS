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
- a row with **no** ``org_id`` column is checked through its foreign keys: every org-owned row
  it points at must belong to the signed-in shop. Since audit 2026-11-12 (DB-01/DB-02) every
  tenant table has its own org_id (payments, lines, shifts, stock levels and movements included),
  filled from its parent before flush (``ORG_PARENTS``), so this path is only a fallback;
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
is the safety net under them, not a replacement. Every OrgScoped table's org_id is NOT NULL
(devices and purchase orders too, since dbi01, DB-14), so no shop's row is hidden from it this way;
a new row saved without one, devices and POs included, takes its shop from its links before flush
(``ORG_PARENTS``).

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
from types import SimpleNamespace

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
    "order_statuses": "org_id NULL = the platform's default status set, read by every shop "
                      "(filtered to this shop's rows + the shared ones by _shared_criteria, SEC-13)",
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
        with_loader_criteria(OrgScoped, lambda cls: cls.org_id == tenant, include_aliases=True),
        *_shared_criteria(tenant, select_=state.is_select))


def _shared_criteria(tenant, *, select_: bool) -> tuple:
    """Audit SEC-13: tables a shop shares with the platform's defaults (``order_statuses``: org_id
    NULL = the default status set). A read sees this shop's rows and the shared ones, never another
    shop's; a bulk UPDATE / DELETE touches this shop's rows only."""
    from sqlalchemy import or_

    from backend.models import OrderStatus
    if select_:
        return (with_loader_criteria(OrderStatus, lambda cls: or_(cls.org_id == tenant, cls.org_id.is_(None)),
                                     include_aliases=True),)
    return (with_loader_criteria(OrderStatus, lambda cls: cls.org_id == tenant, include_aliases=True),)


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


# Where a new row's shop comes from when it is written without one: its parent links, tried in
# this order (audit 2026-11-12 DB-01/DB-02). The child tables (payments, lines, shifts, stock
# levels and movements, …) carry their own org_id, NOT NULL, so every one of them is OrgScoped;
# nothing in the routers has to set it, this fills it centrally before every flush. Migration corg01
# backfilled existing rows from the same links; dbi01 did the same for devices and POs and made
# their org_id NOT NULL as well (DB-14). A row none of whose links name a shop takes the request's
# shop; with no request shop either, the NOT NULL column refuses it (a payment linked to nothing
# belongs to no one).
ORG_PARENTS: dict[str, tuple[str, ...]] = {
    "devices": ("client_id", "location_id", "warehouse_id"),
    "purchase_orders": ("location_id", "supplier_id"),
    # money: a payment is its job's, else its invoice's, cash box's, client's, drawer shift's
    "payments": ("order_id", "invoice_id", "account_id", "client_id", "shift_id"),
    "cash_shifts": ("location_id", "opened_by_id"),
    "invoice_lines": ("invoice_id",),
    "invoice_orders": ("invoice_id", "order_id"),
    "credit_note_lines": ("credit_note_id",),
    "sale_lines": ("sale_id",),
    # jobs
    "order_lines": ("order_id",),
    "order_comments": ("order_id",),
    "order_status_logs": ("order_id",),
    # people and messaging
    "shifts": ("employee_id", "location_id"),
    "wage_records": ("employee_id",),
    "chat_messages": ("thread_id",),
    # stock
    "stock_levels": ("location_id", "part_id"),
    "stock_movements": ("location_id", "part_id"),
    "stock_transfer_lines": ("transfer_id", "part_id"),
    "stocktake_lines": ("stocktake_id", "part_id"),
    "stocktake_serials": ("stocktake_id",),
    "po_lines": ("po_id", "part_id"),
    "posting_lines": ("posting_id", "part_id"),
    "purchase_return_lines": ("return_id", "part_id"),
    "write_off_lines": ("write_off_id", "part_id"),
    "client_return_lines": ("return_id", "part_id"),
    "bundle_components": ("bundle_id",),
}


def _table_of(obj):
    return getattr(getattr(obj, "__table__", None), "name", None)


@cache
def _link_relationship(mapper: Mapper, attr: str):
    """The many-to-one relationship that sets ``attr`` (``OrderLine.order`` for ``order_id``), if
    the model has one: a parent attached that way has no id yet at before_flush."""
    for rel in mapper.relationships:
        if rel.direction.name == "MANYTOONE" and [c.key for c in rel.local_columns] == [attr]:
            return rel.key
    return None


@cache
def _child_collections(mapper: Mapper) -> tuple:
    """One-to-many collections of this model whose rows take their shop from it (``Invoice.lines``,
    ``CashShift.payments``): some have no backref, so the child can't name its parent itself."""
    return tuple(rel.key for rel in mapper.relationships
                 if rel.direction.name == "ONETOMANY" and rel.mapper.local_table.name in ORG_PARENTS)


@cache
def _class_for_table(table: str):
    from backend.db.database import Base
    for m in Base.registry.mappers:
        if m.local_table is not None and m.local_table.name == table and not m.inherits:
            return m.class_
    return None


def _loaded(session: Session, table: str, oid):
    """The row of `table` with this id if this session already holds it (an identity-map lookup,
    no SQL and no walk over the map)."""
    cls = _class_for_table(table)
    if cls is None:
        return None
    from sqlalchemy.orm import util as orm_util
    try:
        return session.identity_map.get(orm_util.identity_key(cls, oid))
    except Exception:       # noqa: BLE001 — composite / odd keys: fall back to the query
        return None


def org_for_new_row(session: Session, table: str, values: dict, tenant=None):
    """The shop a new ``table`` row with these column values belongs to: the first parent link
    that names one (``ORG_PARENTS``), else ``tenant``. For Core inserts (``insert_ignore``)."""
    row = SimpleNamespace(**values)
    return _org_from_links(session, row, table, {}, tenant)


def _org_from_links(session: Session, obj, table: str, owners: dict, tenant, depth: int = 0):
    parents = _parent_tables(table)
    mapper = getattr(inspect(obj, raiseerr=False), "mapper", None)
    for attr in ORG_PARENTS[table]:
        val = obj.__dict__.get(attr)
        org = None
        if val:
            target = parents[attr]
            key = (target.name, val)
            if key not in owners:
                parent = owners.get(("obj", target.name, val))
                if parent is None:
                    parent = _loaded(session, target.name, val)
                if parent is not None and not parent.__dict__.get("org_id") and target.name in ORG_PARENTS \
                        and depth < 3:
                    owners[key] = _org_from_links(session, parent, target.name, owners, None, depth + 1)
                elif parent is not None and parent.__dict__.get("org_id"):
                    owners[key] = parent.__dict__["org_id"]
                else:
                    # Plain table columns, explicit opt-out: only the linked row's shop id is read.
                    with session.no_autoflush:
                        owners[key] = session.execute(
                            select(target.c.org_id).where(target.c.id == val)
                            .execution_options(skip_tenant_filter=True)).scalar()
            org = owners[key]
        elif mapper is not None and (rel := _link_relationship(mapper, attr)):
            parent = obj.__dict__.get(rel)
            if parent is not None:
                org = parent.__dict__.get("org_id")
                ptable = _table_of(parent)
                if not org and ptable in ORG_PARENTS and depth < 3:
                    org = _org_from_links(session, parent, ptable, owners, None, depth + 1)
                    if org:
                        parent.org_id = org
        if org:
            return org
    return tenant


@cache
def _parent_tables(table: str) -> dict:
    """{link column: parent Table} for ORG_PARENTS[table]."""
    from backend.db.database import Base
    t = Base.metadata.tables[table]
    return {attr: next(iter(t.c[attr].foreign_keys)).column.table for attr in ORG_PARENTS[table]}


def _fill_missing_org(session: Session, tenant) -> None:
    todo = [o for o in session.new if _table_of(o) in ORG_PARENTS and not o.__dict__.get("org_id")]
    if not todo:
        return
    # Re-review N8: this runs on every flush, and a bulk import autoflushes once per row in a
    # session holding thousands of loaded rows. So it looks only at what changed: new rows by id
    # here (a loaded parent is found by its identity key in `_org_from_links`), and parents among
    # the new and modified rows below (appending to a loaded parent's collection marks it modified).
    owners: dict = {}
    for o in session.new:
        oid = o.__dict__.get("id")
        if oid:
            owners[("obj", _table_of(o), oid)] = o
    # Children reachable only from a parent's collection (no backref): they take the parent's shop.
    from_collection: dict = {}
    for o in list(session.new) + list(session.dirty):
        mapper = getattr(inspect(o, raiseerr=False), "mapper", None)
        if mapper is None:
            continue
        for key in _child_collections(mapper):
            for child in o.__dict__.get(key) or ():
                from_collection.setdefault(id(child), o)
    for obj in todo:
        if obj.__dict__.get("org_id"):
            continue      # filled while resolving a child of it
        table = _table_of(obj)
        org = _org_from_links(session, obj, table, owners, None)
        if not org and (parent := from_collection.get(id(obj))) is not None:
            org = parent.__dict__.get("org_id")
            if not org and _table_of(parent) in ORG_PARENTS:
                org = _org_from_links(session, parent, _table_of(parent), owners, None)
        obj.org_id = org or tenant


@event.listens_for(Session, "before_flush")
def _guard_cross_tenant_references(session: Session, flush_context, instances) -> None:
    tenant = session.info.get("tenant_org")
    _fill_missing_org(session, tenant)
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
    # Owners already known this request: ids checked earlier (every autoflush runs this guard, so
    # a 50-line sale would otherwise re-query the same rows), and the referenced rows this session
    # holds. Those are found by identity key and among the new/modified rows of the referenced
    # tables only: walking the whole identity map on every flush made a bulk import, which
    # autoflushes once per row in a session of thousands of rows, quadratic (re-review follow-up).
    known: dict = session.info.setdefault("tenant_known", {})
    targets = {target.name for target, _pk in wanted}
    for o in list(session.new) + list(session.dirty):
        table = _table_of(o)
        if table in targets:
            loaded = o.__dict__          # loaded values only: never trigger a lazy load mid-flush
            oid, oorg = loaded.get("id"), loaded.get("org_id")
            if oid and oorg:
                known[(table, oid)] = oorg
    with session.no_autoflush:
        for (target, pk), refs in wanted.items():
            for i in refs:
                held = _loaded(session, target.name, i)
                oorg = held.__dict__.get("org_id") if held is not None else None
                if oorg:
                    known[(target.name, i)] = oorg
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
