"""The database refuses cross-shop references; real FKs, FK indexes and other integrity fixes.

Audit 2026-11-12 (docs/audits/2026-11-12-backend-audit/02-schema-findings.md):

DB-03  Money and stock rows reference their parents by (org_id, id): payments, transactions, job /
       invoice / sale lines, stock movements and levels, plus orders.client/device/location,
       invoices.order/client and stock_reservations.part. Each parent gets UNIQUE (org_id, id).
       The plain id FKs stay as they are (names and ON DELETE unchanged).
DB-13  Real FKs for single-target soft references (sales.coupon_id, order_lines.membership_id,
       transactions.reverses_txn_id, payroll_runs.cash_txn_id, the admin tables' org/admin ids…).
       Polymorphic ones stay soft (backend/tests/test_db_schema_rules.SOFT_REFS says why).
DB-10  Leading indexes for FK columns real queries filter on (tasks, sales, clients.manager_id,
       payments.voided_by_id…).
DB-12  credit_note_lines.credit_note_id and serial_events.item_id lose ON DELETE CASCADE.
DB-14  devices.org_id and purchase_orders.org_id become NOT NULL.
DB-15  employee_balance_ledger.idempotency_key is unique per shop (it was global).
DB-17  Indexes another index already covers are dropped (40-odd ix_<table>_org_id, and the
       duplicate ix_orders_barcode next to uq_orders_barcode).

**Pre-flight** (read-only, before anything changes). The migration stops, listing counts per
table/column only (never ids or names), when it finds:
- a device or PO with no shop that none of its links names, or whose links name two shops;
- a row whose parent belongs to another shop (DB-03; list them with scripts/find_cross_shop_rows.py);
- an orphan in a NOT NULL soft reference, or in a money link (transactions.reverses_txn_id,
  payroll_runs.cash_txn_id): the id names a row that doesn't exist, and a person must decide;
- two ledger rows of one shop with the same idempotency key.
Orphans in the other (nullable, non-money) soft references are set to NULL: the id points at
nothing, so only a dead id is lost. Each column's count is logged (WARNING, alembic.dbi01).

**Postgres, no long locks.** Everything runs in autocommit, one short step at a time: devices/PO
get the BEFORE INSERT fill trigger (as corg01: old code that inserts without org_id keeps
working), a backfill in batches of BATCH rows, then CHECK NOT VALID + VALIDATE + SET NOT NULL.
Indexes are built and dropped CONCURRENTLY (an INVALID one left by a failed build is rebuilt on
the retry). Every FK is added NOT VALID (a brief lock, no scan) and then VALIDATEd (reads and
writes go on). Idempotent: a rerun after a failure resumes; existing constraints are kept.

**SQLite** (development only): orphans are cleared the same way, then each changed table is rebuilt
once (batch) with every new FK and NOT NULL, and every expression / partial index the rebuild
dropped is put back from its saved SQL (the w2b01 `_sqlite_rebuild` pattern).

Downgrade drops what this added and puts back the dropped indexes and cascades (nulled orphans and
backfilled org_ids stay: they are correct values).

Revision ID: dbi01
Revises: corg02
"""
import logging

import sqlalchemy as sa
from alembic import op

revision = "dbi01"
down_revision = "corg02"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.dbi01")

BATCH = 5000

# ── DB-03 (the same lists as backend/models: TENANT_PARENTS / TENANT_FKS) ─────────────────────────
TENANT_PARENTS = ("accounts", "cash_flow_items", "cash_shifts", "clients", "devices", "employees",
                  "invoices", "locations", "order_items", "order_lines", "orders", "parts", "sales",
                  "storage_bins", "warehouses")
TENANT_FKS = (
    ("payments", "order_id", "orders"), ("payments", "invoice_id", "invoices"),
    ("payments", "account_id", "accounts"), ("payments", "client_id", "clients"),
    ("payments", "shift_id", "cash_shifts"), ("payments", "received_by_id", "employees"),
    ("payments", "voided_by_id", "employees"),
    ("transactions", "account_id", "accounts"), ("transactions", "shift_id", "cash_shifts"),
    ("transactions", "cash_flow_item_id", "cash_flow_items"), ("transactions", "employee_id", "employees"),
    ("transactions", "voided_by_id", "employees"),
    ("order_lines", "order_id", "orders"), ("order_lines", "order_item_id", "order_items"),
    ("order_lines", "technician_id", "employees"), ("order_lines", "discount_by_id", "employees"),
    ("invoice_lines", "invoice_id", "invoices"), ("invoice_lines", "order_line_id", "order_lines"),
    ("sale_lines", "sale_id", "sales"),
    ("stock_movements", "part_id", "parts"), ("stock_movements", "location_id", "locations"),
    ("stock_movements", "warehouse_id", "warehouses"), ("stock_movements", "bin_id", "storage_bins"),
    ("stock_movements", "created_by_id", "employees"),
    ("stock_levels", "part_id", "parts"), ("stock_levels", "location_id", "locations"),
    ("stock_levels", "warehouse_id", "warehouses"), ("stock_levels", "bin_id", "storage_bins"),
    ("orders", "client_id", "clients"), ("orders", "device_id", "devices"), ("orders", "location_id", "locations"),
    ("invoices", "order_id", "orders"), ("invoices", "client_id", "clients"),
    ("stock_reservations", "part_id", "parts"),
)


def tenant_fk_name(child: str, col: str) -> str:
    return f"fk_{child}_{col}_org"


# ── DB-13: (table, column, referenced table, ON DELETE, money link) ──────────────────────────────
SN = "SET NULL"
SOFT_FKS = (
    ("sales", "coupon_id", "coupons", SN, False),
    ("order_lines", "membership_id", "memberships", SN, False),
    ("membership_usage", "order_line_id", "order_lines", SN, False),
    ("recommendations", "order_line_id", "order_lines", SN, False),
    ("transactions", "reverses_txn_id", "transactions", None, True),
    ("payroll_runs", "cash_txn_id", "transactions", None, True),
    ("orders", "converted_order_id", "orders", SN, False),
    ("orders", "converted_from_id", "orders", SN, False),
    ("locations", "price_type_id", "price_types", SN, False),
    ("organisations", "assigned_admin_id", "platform_admins", SN, False),
    ("platform_admins", "role_id", "admin_roles", None, False),
    ("admin_action_log", "admin_id", "platform_admins", SN, False),
    ("admin_action_log", "org_id", "organisations", SN, False),
    ("admin_approval_requests", "requester_id", "platform_admins", None, False),
    ("admin_approval_requests", "org_id", "organisations", None, False),
    ("admin_shop_notes", "org_id", "organisations", None, False),
    ("admin_shop_notes", "admin_id", "platform_admins", SN, False),
    ("admin_assignment_log", "org_id", "organisations", None, False),
    ("admin_assignment_log", "from_admin_id", "platform_admins", SN, False),
    ("admin_assignment_log", "to_admin_id", "platform_admins", SN, False),
    ("admin_assignment_log", "by_admin_id", "platform_admins", SN, False),
    ("directory_items", "parent_id", "directory_items", SN, False),
    ("webhook_endpoints", "source_id", "automation_rules", SN, False),
    ("asset_product_conversions", "reversed_by_id", "asset_product_conversions", SN, False),
)
# names the models give explicitly (the convention's would pass Postgres's 63-character limit)
_SOFT_FK_NAMES = {("asset_product_conversions", "reversed_by_id"): "fk_asset_product_conversions_reversed_by_id"}


def soft_fk_name(table: str, col: str, ref: str) -> str:
    return _SOFT_FK_NAMES.get((table, col), f"fk_{table}_{col}_{ref}")


# ── DB-12: FKs that lose ON DELETE CASCADE: (table, column, referenced table) ────────────────────
NO_CASCADE = (("credit_note_lines", "credit_note_id", "credit_notes"),
              ("serial_events", "item_id", "serialized_items"))

# ── DB-14 ────────────────────────────────────────────────────────────────────────────────────────
def _org(parent: str, col: str, row: str) -> str:
    return f"(SELECT src.org_id FROM {parent} src WHERE src.id = {row}.{col})"


# table -> where a missing org_id comes from, in order (backend/db/tenancy.ORG_PARENTS, trg01)
ORG_SOURCES = {
    "devices": [("clients", "client_id"), ("locations", "location_id"), ("warehouses", "warehouse_id")],
    "purchase_orders": [("locations", "location_id"), ("suppliers", "supplier_id")],
}


def derived_org(table: str, row: str) -> str:
    srcs = [_org(p, c, row) for p, c in ORG_SOURCES[table]]
    return f"COALESCE({', '.join(srcs)})"


def fill_trigger_sql(table: str) -> list[str]:
    """Postgres: BEFORE INSERT, a row without org_id takes its parent's (corg01's bridge, for code
    that inserts devices/POs with Core statements, and for the previous image)."""
    fn = f"fill_org_id_{table}"
    return [
        f"CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        f"IF NEW.org_id IS NULL THEN NEW.org_id := {derived_org(table, 'NEW')}; END IF; RETURN NEW; END $$",
        f"DROP TRIGGER IF EXISTS fill_org_id ON {table}",
        f"CREATE TRIGGER fill_org_id BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION {fn}()",
    ]


# ── Indexes ──────────────────────────────────────────────────────────────────────────────────────
# (name, table, columns, unique)
NEW_INDEXES = (
    [(f"uq_{t}_org_id_id", t, "org_id, id", True) for t in TENANT_PARENTS]
    + [("uq_employee_balance_ledger_org_idempotency_key", "employee_balance_ledger", "org_id, idempotency_key", True)]
    + [(f"ix_{t}_{c}", t, c, False) for t, c in (
        # DB-10: FK columns real queries filter or join on
        ("tasks", "assigned_to_id"), ("tasks", "created_by_id"), ("tasks", "parent_task_id"),
        ("tasks", "location_id"), ("sales", "seller_id"), ("sales", "account_id"), ("clients", "manager_id"),
        ("payments", "voided_by_id"), ("shifts", "employee_id"), ("po_lines", "reserved_order_id"),
        ("chat_threads", "client_id"), ("gift_cards", "client_id"), ("feedback", "client_id"),
        # DB-13: the new FKs whose parent can be deleted, or that are looked up by
        ("transactions", "reverses_txn_id"), ("payroll_runs", "cash_txn_id"),
        ("recommendations", "order_line_id"))]
)
# DB-17 / DB-15: indexes dropped (another index leads with the same columns): (name, table, columns, unique)
DROPPED_INDEXES = (
    [(f"ix_{t}_org_id", t, "org_id", False) for t in (
        "accounts", "api_idempotency_keys", "asset_brands", "asset_groups", "asset_identifier_rules",
        "asset_models", "calls", "cash_flow_items", "cash_shifts", "client_returns", "coupons", "credit_notes",
        "devices", "directories", "employee_balance_ledger", "employees", "form_layouts", "gift_cards",
        "idempotency_records", "invoices", "locations", "memberships", "number_sequences", "order_items",
        "order_lines", "order_statuses", "orders", "org_integration_credentials", "payment_terminals",
        "postings", "purchase_orders", "recommendations", "sales", "stock_transfers", "transactions",
        "warehouses", "write_offs")]
    + [("ix_price_type_items_price_type_id", "price_type_items", "price_type_id", False),
       ("ix_taggables_tag_id", "taggables", "tag_id", False),
       ("ix_user_prefs_employee_id", "user_prefs", "employee_id", False),
       ("ix_membership_assets_membership_id", "membership_assets", "membership_id", False),
       ("ix_order_items_order_id", "order_items", "order_id", False),
       ("ix_invoice_orders_invoice_id", "invoice_orders", "invoice_id", False),
       ("ix_chat_messages_thread_id", "chat_messages", "thread_id", False),
       ("ix_orders_barcode", "orders", "barcode", False),
       ("ix_employee_balance_ledger_idempotency_key", "employee_balance_ledger", "idempotency_key", True)]
)

_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _is_pg(bind) -> bool:
    return bind.dialect.name == "postgresql"


def _scalar(bind, sql: str) -> int:
    return bind.exec_driver_sql(sql).scalar() or 0


# ── pre-flight ───────────────────────────────────────────────────────────────────────────────────

def _effective_org(parent: str, alias: str) -> str:
    """A parent row's shop; for devices still without one, the shop the backfill will give it."""
    if parent == "devices":
        return f"COALESCE({alias}.org_id, {derived_org('devices', alias)})"
    return f"{alias}.org_id"


def cross_shop_sql(child: str, col: str, parent: str) -> str:
    """Rows of `child` whose `col` names a `parent` row of another shop."""
    return (f"SELECT COUNT(*) FROM {child} c JOIN {parent} p ON p.id = c.{col} "
            f"WHERE c.org_id <> {_effective_org(parent, 'p')}")


def orphan_sql(table: str, col: str, ref: str) -> str:
    return (f"SELECT COUNT(*) FROM {table} t WHERE t.{col} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {ref} r WHERE r.id = t.{col})")


def _nullable(bind, table: str, col: str) -> bool:
    return next(c for c in sa.inspect(bind).get_columns(table) if c["name"] == col)["nullable"]


def preflight(bind) -> None:
    problems = []
    no_shop, mixed = [], []
    for table, srcs in ORG_SOURCES.items():
        n = _scalar(bind, f"SELECT COUNT(*) FROM {table} t WHERE t.org_id IS NULL AND {derived_org(table, 't')} IS NULL")
        if n:
            no_shop.append(f"{table}: {n} row(s)")
        orgs = [_org(p, c, "t") for p, c in srcs]
        cond = " OR ".join(f"({a} <> {b})" for i, a in enumerate(orgs) for b in orgs[i + 1:])
        n = _scalar(bind, f"SELECT COUNT(*) FROM {table} t WHERE t.org_id IS NULL AND ({cond})")
        if n:
            mixed.append(f"{table}: {n} row(s)")
    if no_shop:
        problems.append("these rows have no shop and none of their links names one:\n  " + "\n  ".join(no_shop)
                        + "\nLink them to a client/location (devices) or location (purchase orders), or delete them.")
    if mixed:
        problems.append("these rows have no shop and their links name two shops:\n  " + "\n  ".join(mixed)
                        + "\nCorrect the wrong link.")
    cross = []
    for child, col, parent in TENANT_FKS:
        n = _scalar(bind, cross_shop_sql(child, col, parent))
        if n:
            cross.append(f"{child}.{col} -> {parent}: {n} row(s)")
    if cross:
        problems.append("these rows point at another shop's record:\n  " + "\n  ".join(cross)
                        + "\nList them with scripts/find_cross_shop_rows.py and correct the link (or delete the row).")
    stuck = []
    for table, col, ref, _ondelete, money in SOFT_FKS:
        if not (money or not _nullable(bind, table, col)):
            continue
        n = _scalar(bind, orphan_sql(table, col, ref))
        if n:
            stuck.append(f"{table}.{col}: {n} row(s) name a {ref} row that doesn't exist")
    if stuck:
        problems.append("these links point at nothing and can't simply be cleared (a required link, or a money "
                        "link):\n  " + "\n  ".join(stuck) + "\nDecide per row: point them at the right record, or "
                        "clear the link by hand (UPDATE … SET … = NULL) where that is correct.")
    n = _scalar(bind, "SELECT COUNT(*) FROM (SELECT org_id, idempotency_key FROM employee_balance_ledger "
                      "WHERE idempotency_key IS NOT NULL GROUP BY org_id, idempotency_key HAVING COUNT(*) > 1) d")
    if n:
        problems.append(f"employee_balance_ledger: {n} idempotency key(s) are used twice in one shop.")
    if problems:
        raise RuntimeError("dbi01 aborted, nothing changed: " + "\n".join(problems) + "\nThen run the migration again.")


def clear_orphans(bind) -> None:
    """Nullable, non-money soft references that point at nothing: set them to NULL (count logged)."""
    for table, col, ref, _ondelete, money in SOFT_FKS:
        if money or not _nullable(bind, table, col):
            continue
        res = bind.exec_driver_sql(f"UPDATE {table} SET {col} = NULL WHERE {col} IS NOT NULL "
                                   f"AND NOT EXISTS (SELECT 1 FROM {ref} r WHERE r.id = {table}.{col})")
        if res.rowcount:
            log.warning("dbi01 DB-13: %s.%s: %s link(s) to a %s row that doesn't exist were cleared",
                        table, col, res.rowcount, ref)


# ── Postgres ─────────────────────────────────────────────────────────────────────────────────────

def _pg_constraints(bind, table: str) -> dict:
    """{name: convalidated} of the table's FK constraints."""
    return dict(bind.execute(sa.text(
        "SELECT conname, convalidated FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) AND contype = 'f'"),
        {"t": table}).fetchall())


def _pg_add_fk(bind, table: str, name: str, cols: str, ref: str, ref_cols: str, ondelete) -> None:
    have = _pg_constraints(bind, table)
    if name not in have:
        on = f" ON DELETE {ondelete}" if ondelete else ""
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} FOREIGN KEY ({cols}) "
                   f"REFERENCES {ref} ({ref_cols}){on} NOT VALID")
    if not have.get(name):
        op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def _pg_fk_on(bind, table: str, col: str, ref: str):
    for fk in sa.inspect(bind).get_foreign_keys(table):
        if fk["constrained_columns"] == [col] and fk["referred_table"] == ref:
            return fk
    return None


def _pg_backfill(bind, table: str) -> int:
    done, last = 0, ""
    while True:
        hi = bind.execute(sa.text(
            f"SELECT max(id) FROM (SELECT id FROM {table} WHERE org_id IS NULL AND id > :lo "
            f"ORDER BY id LIMIT {BATCH}) b"), {"lo": last}).scalar()
        if hi is None:
            return done
        done += bind.execute(sa.text(
            f"UPDATE {table} AS t SET org_id = {derived_org(table, 't')} "
            f"WHERE t.org_id IS NULL AND t.id > :lo AND t.id <= :hi"), {"lo": last, "hi": hi}).rowcount or 0
        last = hi


def _upgrade_pg(bind) -> None:
    with op.get_context().autocommit_block():
        # DB-14: trigger first (rows the old code adds meanwhile are filled), then backfill, then NOT NULL
        for table in ORG_SOURCES:
            for sql in fill_trigger_sql(table):
                op.execute(sql)
            n = _pg_backfill(bind, table)
            if n:
                log.info("dbi01 DB-14: %s %s got their shop from their links", n, table)
            if _nullable(bind, table, "org_id"):
                ck = f"ck_{table}_org_id_not_null"
                op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {ck}")
                op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {ck} CHECK (org_id IS NOT NULL) NOT VALID")
                op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {ck}")
                op.execute(f"ALTER TABLE {table} ALTER COLUMN org_id SET NOT NULL")
                op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {ck}")

        # indexes, CONCURRENTLY: an INVALID leftover of a failed build is dropped and rebuilt
        names = [n for n, *_ in NEW_INDEXES]
        for (name,) in bind.execute(sa.text(
                "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE NOT i.indisvalid AND c.relname = ANY(:n)"), {"n": names}).fetchall():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
        for name, table, cols, unique in NEW_INDEXES:
            op.execute(f"CREATE {'UNIQUE ' if unique else ''}INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} ({cols})")
        for name, _table, _cols, _unique in DROPPED_INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")

        # DB-13: orphans cleared, then each FK added NOT VALID and validated
        clear_orphans(bind)
        for table, col, ref, ondelete, _money in SOFT_FKS:
            name = soft_fk_name(table, col, ref)
            if _pg_fk_on(bind, table, col, ref) is None or name in _pg_constraints(bind, table):
                _pg_add_fk(bind, table, name, col, ref, "id", ondelete)

        # DB-12: swap the cascading FK for a plain one in one statement (never a moment without it)
        for table, col, ref in NO_CASCADE:
            fk = _pg_fk_on(bind, table, col, ref)
            if fk and (fk.get("options") or {}).get("ondelete"):
                name = fk["name"]
                op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}, ADD CONSTRAINT {name} FOREIGN KEY ({col}) "
                           f"REFERENCES {ref} (id) NOT VALID")
                op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")

        # DB-03: (org_id, x) -> parent (org_id, id)
        for child, col, parent in TENANT_FKS:
            _pg_add_fk(bind, child, tenant_fk_name(child, col), f"org_id, {col}", parent, "org_id, id", None)


# ── SQLite ───────────────────────────────────────────────────────────────────────────────────────

def _sqlite_saved_sql(bind, table):
    return dict(bind.execute(sa.text("SELECT name, sql FROM sqlite_master WHERE type IN ('index', 'trigger') "
                                     "AND tbl_name = :t AND sql IS NOT NULL"), {"t": table}).fetchall())


def _sqlite_rebuild(bind, table, change):
    """Rebuild a SQLite table through batch mode, then put back every index or trigger the rebuild
    lost or changed (alembic doesn't reflect expression indexes, and partial ones only partly).
    The w2b01 pattern."""
    before = _sqlite_saved_sql(bind, table)
    op.execute(f"DROP TABLE IF EXISTS _alembic_tmp_{table}")      # leftover of an interrupted run
    with op.batch_alter_table(table, recreate="always", naming_convention=_CONVENTION) as b:
        change(b)
    after = _sqlite_saved_sql(bind, table)
    for name, sql in before.items():
        if " ".join(after.get(name, "").split()) != " ".join(sql.split()):
            if name in after:
                kind = "TRIGGER" if sql.lstrip().upper().startswith("CREATE TRIGGER") else "INDEX"
                op.execute(f'DROP {kind} "{name}"')
            op.execute(sql)


def _sqlite_plan(bind) -> dict:
    """table -> list of changes (still to do) for its one rebuild."""
    plan: dict = {}
    insp = sa.inspect(bind)
    fks = {t: insp.get_foreign_keys(t) for t in {x for x, *_ in TENANT_FKS} | {x for x, *_ in SOFT_FKS}
           | {x for x, *_ in NO_CASCADE}}
    for table in ORG_SOURCES:
        if _nullable(bind, table, "org_id"):
            plan.setdefault(table, []).append(("not_null",))
    for table, col, ref, ondelete, _m in SOFT_FKS:
        if not any(f["constrained_columns"] == [col] and f["referred_table"] == ref for f in fks[table]):
            plan.setdefault(table, []).append(("fk", soft_fk_name(table, col, ref), ref, [col], ["id"], ondelete))
    for table, col, ref in NO_CASCADE:
        fk = next((f for f in fks[table] if f["constrained_columns"] == [col] and f["referred_table"] == ref), None)
        if fk and (fk.get("options") or {}).get("ondelete"):
            plan.setdefault(table, []).append(("no_cascade", fk["name"] or f"fk_{table}_{col}_{ref}", ref, col))
    for child, col, parent in TENANT_FKS:
        if not any(f["constrained_columns"] == ["org_id", col] for f in fks[child]):
            plan.setdefault(child, []).append(("fk", tenant_fk_name(child, col), parent, ["org_id", col],
                                               ["org_id", "id"], None))
    return plan


def _upgrade_sqlite(bind) -> None:
    for name, _table, _cols, _unique in DROPPED_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    for table in ORG_SOURCES:
        res = bind.exec_driver_sql(f"UPDATE {table} SET org_id = {derived_org(table, table)} WHERE org_id IS NULL")
        if res.rowcount:
            log.info("dbi01 DB-14: %s %s got their shop from their links", res.rowcount, table)
    clear_orphans(bind)
    for name, table, cols, unique in NEW_INDEXES:      # parents' (org_id, id) before the keys that use them
        op.execute(f"CREATE {'UNIQUE ' if unique else ''}INDEX IF NOT EXISTS {name} ON {table} ({cols})")
    for table, changes in _sqlite_plan(bind).items():
        def change(b, changes=changes):
            for c in changes:
                if c[0] == "not_null":
                    b.alter_column("org_id", existing_type=sa.String(), nullable=False)
                elif c[0] == "fk":
                    _kind, name, ref, cols, ref_cols, ondelete = c
                    b.create_foreign_key(name, ref, cols, ref_cols, ondelete=ondelete)
                else:
                    _kind, name, ref, col = c
                    b.drop_constraint(name, type_="foreignkey")
                    b.create_foreign_key(name, ref, [col], ["id"])
        _sqlite_rebuild(bind, table, change)


def upgrade() -> None:
    bind = op.get_bind()
    preflight(bind)
    if _is_pg(bind):
        _upgrade_pg(bind)
    else:
        _upgrade_sqlite(bind)


def downgrade() -> None:
    bind = op.get_bind()
    pg = _is_pg(bind)
    if pg:
        with op.get_context().autocommit_block():
            for child, col, _parent in TENANT_FKS:
                op.execute(f"ALTER TABLE {child} DROP CONSTRAINT IF EXISTS {tenant_fk_name(child, col)}")
            for table, col, ref, _ondelete, _m in SOFT_FKS:
                op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {soft_fk_name(table, col, ref)}")
            for table, col, ref in NO_CASCADE:
                fk = _pg_fk_on(bind, table, col, ref)
                if fk:
                    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {fk['name']}, ADD CONSTRAINT {fk['name']} "
                               f"FOREIGN KEY ({col}) REFERENCES {ref} (id) ON DELETE CASCADE")
            for name, table, cols, unique in DROPPED_INDEXES:
                op.execute(f"CREATE {'UNIQUE ' if unique else ''}INDEX CONCURRENTLY IF NOT EXISTS {name} "
                           f"ON {table} ({cols})")
            for name, _table, _cols, _unique in NEW_INDEXES:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            for table in ORG_SOURCES:
                op.execute(f"ALTER TABLE {table} ALTER COLUMN org_id DROP NOT NULL")
                op.execute(f"DROP TRIGGER IF EXISTS fill_org_id ON {table}")
                op.execute(f"DROP FUNCTION IF EXISTS fill_org_id_{table}()")
        return
    insp = sa.inspect(bind)
    plan: dict = {}
    for child, col, _parent in TENANT_FKS:
        plan.setdefault(child, []).append(("drop", tenant_fk_name(child, col)))
    for table, col, ref, _ondelete, _m in SOFT_FKS:
        plan.setdefault(table, []).append(("drop", soft_fk_name(table, col, ref)))
    for table, col, ref in NO_CASCADE:
        plan.setdefault(table, []).append(("cascade", f"fk_{table}_{col}_{ref}", ref, col))
    for table in ORG_SOURCES:
        plan.setdefault(table, []).append(("nullable",))
    for table, changes in plan.items():
        names = {f["name"] for f in insp.get_foreign_keys(table)}

        def change(b, changes=changes, names=names):
            for c in changes:
                if c[0] == "drop" and c[1] in names:
                    b.drop_constraint(c[1], type_="foreignkey")
                elif c[0] == "cascade":
                    if c[1] in names:
                        b.drop_constraint(c[1], type_="foreignkey")
                    b.create_foreign_key(c[1], c[2], [c[3]], ["id"], ondelete="CASCADE")
                elif c[0] == "nullable":
                    b.alter_column("org_id", existing_type=sa.String(), nullable=True)
        _sqlite_rebuild(bind, table, change)
    for name, _table, _cols, _unique in NEW_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    for name, table, cols, unique in DROPPED_INDEXES:
        op.execute(f"CREATE {'UNIQUE ' if unique else ''}INDEX IF NOT EXISTS {name} ON {table} ({cols})")
