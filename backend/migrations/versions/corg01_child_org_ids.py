"""Every tenant table carries its own shop: org_id on payments and 22 child tables, plus FK indexes.

Audit 2026-11-12 DB-01, DB-02, DB-10, PERF-09 (SEC-01). Payments and the child tables below had no
org_id: their shop was guessed from parent links (each reader guessing differently), the database
accepted a payment linked to nothing, and the tenant read guard could not filter them at all.

1. Pre-flight, before anything is changed: every existing row must resolve to a shop through its
   links (the same links, in the same order, as backend/db/tenancy.ORG_PARENTS uses for new rows).
   If any row can't, the migration ABORTS and lists the counts per table; nothing is touched. Fix
   or delete those rows (they belong to no shop and no report can see them), then run it again.
2. Add org_id, backfill it from the parent, then make it NOT NULL with an FK to organisations.
   Postgres: the FK is added NOT VALID and validated outside the transaction (no long lock).
   SQLite: one batch rebuild per table (it can't ALTER a column to NOT NULL).
3. Indexes: org_id on those tables (payments and stock_movements get (org_id, paid_at) /
   (org_id, created_at) instead), the hot unindexed FKs (payments.shift_id, summed at every drawer
   close, first), and org_id on 28 other tables. Postgres builds them CONCURRENTLY (writes go on);
   an INVALID one left by a failed build is dropped and rebuilt on the retry.

Idempotent: a column, FK or index that already exists (create_all databases, a retried run) is
left as it is. Downgrade drops what this added.

Revision ID: corg01
Revises: sso01
"""
import logging

import sqlalchemy as sa
from alembic import op

revision = "corg01"
down_revision = "sso01"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.corg01")


def _org(parent: str, col: str) -> str:
    return f"(SELECT p.org_id FROM {parent} p WHERE p.id = t.{col})"


# A cash shift's shop is its location's; a payment that only names its drawer shift is that shop's.
_SHIFT_ORG = ("(SELECT l.org_id FROM cash_shifts cs JOIN locations l ON l.id = cs.location_id "
              "WHERE cs.id = t.shift_id)")

# table -> where its shop comes from, in order (tenancy.ORG_PARENTS). Order of the dict matters
# only for readability: every source reads a parent's own org_id, never another child's.
SOURCES: dict[str, list[str]] = {
    "payments": [_org("orders", "order_id"), _org("invoices", "invoice_id"), _org("accounts", "account_id"),
                 _org("clients", "client_id"), _SHIFT_ORG],
    "cash_shifts": [_org("locations", "location_id"), _org("employees", "opened_by_id")],
    "invoice_lines": [_org("invoices", "invoice_id")],
    "invoice_orders": [_org("invoices", "invoice_id"), _org("orders", "order_id")],
    "credit_note_lines": [_org("credit_notes", "credit_note_id")],
    "sale_lines": [_org("sales", "sale_id")],
    "order_lines": [_org("orders", "order_id")],
    "order_comments": [_org("orders", "order_id")],
    "order_status_logs": [_org("orders", "order_id")],
    "shifts": [_org("employees", "employee_id"), _org("locations", "location_id")],
    "wage_records": [_org("employees", "employee_id")],
    "chat_messages": [_org("chat_threads", "thread_id")],
    "stock_levels": [_org("locations", "location_id"), _org("parts", "part_id")],
    "stock_movements": [_org("locations", "location_id"), _org("parts", "part_id")],
    "stock_transfer_lines": [_org("stock_transfers", "transfer_id"), _org("parts", "part_id")],
    "stocktake_lines": [_org("stocktakes", "stocktake_id"), _org("parts", "part_id")],
    "stocktake_serials": [_org("stocktakes", "stocktake_id")],
    "po_lines": [_org("purchase_orders", "po_id"), _org("parts", "part_id")],
    "posting_lines": [_org("postings", "posting_id"), _org("parts", "part_id")],
    "purchase_return_lines": [_org("purchase_returns", "return_id"), _org("parts", "part_id")],
    "write_off_lines": [_org("write_offs", "write_off_id"), _org("parts", "part_id")],
    "client_return_lines": [_org("client_returns", "return_id"), _org("parts", "part_id")],
    "bundle_components": [_org("bundles", "bundle_id")],
}

# Tables whose org index is a composite (declared in the models' __table_args__).
_COMPOSITE_ORG = {"payments", "stock_movements"}

# (name, table, columns): every index this revision adds, named as the models name them.
INDEXES: list[tuple[str, str, str]] = (
    [(f"ix_{t}_org_id", t, "org_id") for t in SOURCES if t not in _COMPOSITE_ORG]
    + [
        ("ix_payments_org_id_paid_at", "payments", "org_id, paid_at"),
        ("ix_stock_movements_org_id_created_at", "stock_movements", "org_id, created_at"),
        # DB-10: hot FK columns with no leading index
        ("ix_payments_shift_id", "payments", "shift_id"),
        ("ix_payments_received_by_id", "payments", "received_by_id"),
        ("ix_stock_levels_location_id", "stock_levels", "location_id"),
        ("ix_stock_movements_created_by_id", "stock_movements", "created_by_id"),
        ("ix_invoice_lines_order_line_id", "invoice_lines", "order_line_id"),
        ("ix_refunds_payment_id", "refunds", "payment_id"),
        ("ix_refunds_account_id", "refunds", "account_id"),
        ("ix_refunds_return_id", "refunds", "return_id"),
        ("ix_sales_org_id_location_id_created_at", "sales", "org_id, location_id, created_at"),
        ("ix_asset_movements_asset_id", "asset_movements", "asset_id"),
        ("ix_invoices_payer_client_id", "invoices", "payer_client_id"),
        ("ix_leads_client_id", "leads", "client_id"),
        ("ix_leads_converted_order_id", "leads", "converted_order_id"),
        ("ix_appointments_org_id_employee_id", "appointments", "org_id, employee_id"),
        ("ix_appointments_client_id", "appointments", "client_id"),
        ("ix_calls_client_id", "calls", "client_id"),
        ("ix_transactions_cash_flow_item_id", "transactions", "cash_flow_item_id"),
        ("ix_devices_location_id", "devices", "location_id"),
        ("ix_serialized_items_po_id", "serialized_items", "po_id"),
        ("ix_serialized_items_batch_id", "serialized_items", "batch_id"),
        ("ix_notification_logs_rule_id", "notification_logs", "rule_id"),
        ("ix_notification_logs_template_id", "notification_logs", "template_id"),
        ("ix_notification_logs_employee_id", "notification_logs", "employee_id"),
        ("ix_audit_log_actor_id", "audit_log", "actor_id"),
    ]
    # DB-10: org_id with no index on tables every tenant-filtered read touches
    + [(f"ix_{t}_org_id", t, "org_id") for t in (
        "accounts", "asset_movements", "bundles", "cash_flow_items", "commission_rules",
        "currency_rate_history", "custom_fields", "document_templates", "employee_coefficients",
        "employees", "feedback_settings", "generated_documents", "locations", "notification_templates",
        "payroll_runs", "price_types", "resources", "roles", "saved_filters", "services",
        "status_transitions", "stock_item_batches", "stock_reservations", "stock_transfers",
        "stocktakes", "suppliers", "taxes", "user_prefs")]
)

_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
_FK = "fk_{}_org_id_organisations"


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _derived(table: str) -> str:
    srcs = SOURCES[table]
    return srcs[0] if len(srcs) == 1 else f"COALESCE({', '.join(srcs)})"


def _has_column(table: str) -> bool:
    return "org_id" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _preflight() -> None:
    """Abort before changing anything if a row has no shop to take."""
    bind = op.get_bind()
    bad = []
    for table in SOURCES:
        own = "t.org_id IS NULL AND " if _has_column(table) else ""
        n = bind.exec_driver_sql(
            f"SELECT COUNT(*) FROM {table} t WHERE {own}{_derived(table)} IS NULL").scalar() or 0
        if n:
            bad.append(f"{table}: {n} row(s)")
    if bad:
        raise RuntimeError(
            "corg01 aborted, nothing changed: these rows have no shop (none of their links names "
            "one), so org_id can't be filled:\n  " + "\n  ".join(bad)
            + "\nLink or delete them, then run the migration again.")


def upgrade() -> None:
    _preflight()
    bind = op.get_bind()
    pg = _is_pg()
    for table in SOURCES:
        if not _has_column(table):
            op.add_column(table, sa.Column("org_id", sa.String(), nullable=True))
        res = bind.exec_driver_sql(
            f"UPDATE {table} AS t SET org_id = {_derived(table)} WHERE t.org_id IS NULL" if pg else
            f"UPDATE {table} SET org_id = {_derived(table).replace('t.', table + '.')} WHERE org_id IS NULL")
        if res.rowcount:
            log.info("corg01: %s %s got their shop from their parent", res.rowcount, table)

    insp = sa.inspect(bind)
    fk_todo = [t for t in SOURCES
               if not any(fk["constrained_columns"] == ["org_id"] for fk in insp.get_foreign_keys(t))]
    if pg:
        for table in SOURCES:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN org_id SET NOT NULL")
        for table in fk_todo:
            op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {_FK.format(table)} "
                       f"FOREIGN KEY (org_id) REFERENCES organisations (id) NOT VALID")
        with op.get_context().autocommit_block():
            for table in fk_todo:
                op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {_FK.format(table)}")
            names = [n for n, _t, _c in INDEXES]
            invalid = bind.execute(sa.text(
                "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE NOT i.indisvalid AND c.relname = ANY(:names)"), {"names": names}).scalars().all()
            for name in invalid:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            for name, table, cols in INDEXES:
                op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} ({cols})")
        return

    nullable = {t for t in SOURCES
                if next(c for c in insp.get_columns(t) if c["name"] == "org_id")["nullable"]}
    for table in SOURCES:
        if table not in nullable and table not in fk_todo:
            continue
        op.execute(f"DROP TABLE IF EXISTS _alembic_tmp_{table}")   # leftover from an interrupted run
        with op.batch_alter_table(table, recreate="always", naming_convention=_CONVENTION) as b:
            b.alter_column("org_id", existing_type=sa.String(), nullable=False)
            if table in fk_todo:
                b.create_foreign_key(_FK.format(table), "organisations", ["org_id"], ["id"])
    for name, table, cols in INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols})")


def downgrade() -> None:
    pg = _is_pg()
    if pg:
        with op.get_context().autocommit_block():
            for name, _table, _cols in reversed(INDEXES):
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
        for table in SOURCES:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS org_id")
        return
    for name, _table, _cols in reversed(INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
    for table in SOURCES:
        if not _has_column(table):
            continue
        op.execute(f"DROP TABLE IF EXISTS _alembic_tmp_{table}")
        with op.batch_alter_table(table, recreate="always", naming_convention=_CONVENTION) as b:
            for fk in sa.inspect(op.get_bind()).get_foreign_keys(table):
                if fk["constrained_columns"] == ["org_id"]:
                    b.drop_constraint(fk["name"] or _FK.format(table), type_="foreignkey")
            b.drop_column("org_id")
