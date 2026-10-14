"""144eo — converge the migrated schema with the models.

Found by comparing a database built from the migrations against the models (tests build from the
models, production from the migrations, so these gaps were invisible to the test suite):

1. notification_logs.rule_id FK must be ON DELETE SET NULL (keep log history when a fired rule is
   deleted). fk144as01 fixed this on Postgres only, assuming SQLite DBs come from create_all — but
   create_all never alters an existing table, so older SQLite DBs (and any migrated SQLite DB) still
   had NO ACTION: deleting a rule that ever fired was rejected. Now fixed on every dialect.
2. document_templates.page_size is String(16) in the model but 12 in migrated DBs. Postgres enforces
   VARCHAR length, so 13-16 char values failed only in production. Widened to 16.
3. Seven single-column indexes the models declare were never created by a migration.
4. ix_bookings_scheduled_for still carried the pre-rename name (b7d9f1h3j5l7) -> renamed.

IDEMPOTENT by design: databases in the field differ (Postgres prod via migrations; SQLite dev via
create_all, which already has some of these). Every step inspects first and only changes what is
actually wrong, so this is safe on all of them and safe to re-run.

On Postgres indexes are built CONCURRENTLY (no write lock on live tables), as in ix144dh01.

Revision ID: sc144eo01
Revises: c8e0g2i4k6m8
"""
from alembic import op
import sqlalchemy as sa

revision = "sc144eo01"
down_revision = "c8e0g2i4k6m8"
branch_labels = None
depends_on = None

FK_NAME = "fk_notification_logs_rule_id"  # same name fk144as01 uses on Postgres

# (index name, table, column) — exactly as the models declare them (all non-unique)
MISSING_INDEXES = [
    ("ix_bundles_category_id", "bundles", "category_id"),
    ("ix_categories_template_key", "categories", "template_key"),
    ("ix_checklist_template_items_section_id", "checklist_template_items", "section_id"),
    ("ix_coupons_client_id", "coupons", "client_id"),
    ("ix_directory_items_location_id", "directory_items", "location_id"),
    ("ix_merge_logs_created_at", "merge_logs", "created_at"),
    ("ix_order_checklist_items_checklist_id", "order_checklist_items", "checklist_id"),
]
OLD_APPT_IX, NEW_APPT_IX = "ix_bookings_scheduled_for", "ix_appointments_scheduled_for"


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _rule_fk():
    for fk in sa.inspect(op.get_bind()).get_foreign_keys("notification_logs"):
        if fk["constrained_columns"] == ["rule_id"] and fk["referred_table"] == "notification_rules":
            return fk
    return None


def _ondelete(fk) -> str:
    return (((fk or {}).get("options") or {}).get("ondelete") or "").upper()


def _set_rule_fk(ondelete):
    """Replace the notification_logs.rule_id FK with one using `ondelete` (None = NO ACTION)."""
    fk = _rule_fk()
    if _is_pg():
        if fk:
            op.drop_constraint(fk["name"], "notification_logs", type_="foreignkey")
        op.create_foreign_key(FK_NAME, "notification_logs", "notification_rules",
                              ["rule_id"], ["id"], ondelete=ondelete)
        return
    # SQLite can't ALTER a constraint: batch mode rebuilds the table (data copied). Migration-built
    # FKs are unnamed, so the naming convention gives the reflected one a name we can drop it by;
    # a named FK (create_all, or this migration's own) is dropped by its real name.
    _clear_batch_leftover("notification_logs")
    with op.batch_alter_table(
        "notification_logs", recreate="always",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
    ) as b:
        if fk:
            b.drop_constraint(fk["name"] or "fk_notification_logs_rule_id_notification_rules", type_="foreignkey")
        b.create_foreign_key(FK_NAME, "notification_rules", ["rule_id"], ["id"], ondelete=ondelete)


def _clear_batch_leftover(table: str) -> None:
    """SQLite DDL isn't transactional under Alembic, so an interrupted batch rebuild leaves its temp
    table behind and every retry then fails with 'table _alembic_tmp_<t> already exists'. The temp
    table is only ever a partial copy (the original table is untouched), so dropping it is safe."""
    if not _is_pg():
        op.execute(f"DROP TABLE IF EXISTS _alembic_tmp_{table}")


def _page_size_column():
    cols = [c for c in sa.inspect(op.get_bind()).get_columns("document_templates") if c["name"] == "page_size"]
    return cols[0] if cols else None


def _set_page_size_length(length: int):
    col = _page_size_column()
    if col is None or getattr(col["type"], "length", None) == length:
        return
    _clear_batch_leftover("document_templates")
    with op.batch_alter_table("document_templates") as b:
        b.alter_column("page_size", existing_type=col["type"], type_=sa.String(length),
                       existing_nullable=col["nullable"])


def _index_names(table):
    return {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    # --- transactional DDL first (the autocommit block below commits the current transaction) ---
    if _ondelete(_rule_fk()) != "SET NULL":          # Postgres after fk144as01: already correct
        _set_rule_fk("SET NULL")
    _set_page_size_length(16)

    appt = _index_names("appointments")
    if NEW_APPT_IX in appt:
        if OLD_APPT_IX in appt:                       # create_all DBs can have both
            op.drop_index(OLD_APPT_IX, table_name="appointments")
    elif OLD_APPT_IX in appt:
        if _is_pg():
            op.execute(f"ALTER INDEX {OLD_APPT_IX} RENAME TO {NEW_APPT_IX}")   # metadata-only, instant
        else:
            op.drop_index(OLD_APPT_IX, table_name="appointments")
            op.create_index(NEW_APPT_IX, "appointments", ["scheduled_for"])
    else:
        op.create_index(NEW_APPT_IX, "appointments", ["scheduled_for"])

    # --- missing indexes: non-blocking on Postgres, idempotent everywhere ---
    if _is_pg():
        with op.get_context().autocommit_block():
            for name, table, col in MISSING_INDEXES:
                op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} ({col})")
    else:
        for name, table, col in MISSING_INDEXES:
            op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({col})")


def downgrade() -> None:
    # Restore the c8e0g2i4k6m8 schema. On Postgres fk144as01 had already made the FK SET NULL, so
    # it stays; only SQLite goes back to NO ACTION.
    if _is_pg():
        with op.get_context().autocommit_block():
            for name, _table, _col in MISSING_INDEXES:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    else:
        for name, _table, _col in MISSING_INDEXES:
            op.execute(f"DROP INDEX IF EXISTS {name}")

    appt = _index_names("appointments")
    if NEW_APPT_IX in appt and OLD_APPT_IX not in appt:
        if _is_pg():
            op.execute(f"ALTER INDEX {NEW_APPT_IX} RENAME TO {OLD_APPT_IX}")
        else:
            op.drop_index(NEW_APPT_IX, table_name="appointments")
            op.create_index(OLD_APPT_IX, "appointments", ["scheduled_for"])

    # Narrowing could truncate/reject data: only narrow when every value still fits.
    too_long = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM document_templates WHERE LENGTH(page_size) > 12")).scalar()
    if too_long:
        raise RuntimeError(f"Cannot downgrade: {too_long} document_templates.page_size value(s) exceed 12 chars.")
    _set_page_size_length(12)

    if not _is_pg() and _ondelete(_rule_fk()) == "SET NULL":
        _set_rule_fk(None)
