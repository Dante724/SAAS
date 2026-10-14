"""144ep — add the 11 foreign keys the models declare but no migration ever created.

Until now production did not enforce these, so a row could point at something that no longer
exists. Every parent's delete path was audited first:
  * soft-deleted (active=False) or delete-guarded -> plain FK: categories (guarded), services,
    clients (guarded), employees, locations (soft), organisations / media_blobs (never deleted).
  * HARD-deleted -> ON DELETE SET NULL so the delete keeps working and the child row survives:
    tasks (time_entries.task_id: payroll history) and suppliers (parts.default_supplier_id).

SAFETY
  * Pre-flight: counts orphan rows for every FK it will create. If any exist it ABORTS with the list
    and touches nothing — it never silently rewrites data. Fix them first (see
    scripts/check_fk_orphans.py), then re-run.
  * Postgres: each FK is added NOT VALID (instant, brief lock) and VALIDATEd in its own autocommit
    step, which scans without blocking reads or writes on the live table.
  * Idempotent: an FK that already exists with the right ON DELETE is left alone (create_all DBs
    have some); one with the wrong ON DELETE is replaced.

Revision ID: fk144ep01
Revises: sc144eo01
"""
import sqlalchemy as sa
from alembic import op

revision = "fk144ep01"
down_revision = "sc144eo01"
branch_labels = None
depends_on = None

# (child table, column, parent table, ON DELETE)
FKS = [
    ("bundles", "category_id", "categories", None),
    ("commission_rules", "service_id", "services", None),
    ("coupons", "client_id", "clients", None),
    ("devices", "org_id", "organisations", None),
    ("directory_items", "location_id", "locations", None),
    ("orders", "manager_id", "employees", None),
    ("parts", "default_supplier_id", "suppliers", "SET NULL"),
    ("purchase_orders", "supplier_invoice_blob_id", "media_blobs", None),
    ("services", "category_id", "categories", None),
    ("time_entries", "task_id", "tasks", "SET NULL"),
    ("transactions", "voided_by_id", "employees", None),
]
_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _name(child, col, parent):
    return f"fk_{child}_{col}_{parent}"     # same name create_all gives it (models' naming convention)


def _is_pg():
    return op.get_bind().dialect.name == "postgresql"


def _existing(child, col, parent):
    for fk in sa.inspect(op.get_bind()).get_foreign_keys(child):
        if fk["constrained_columns"] == [col] and fk["referred_table"] == parent:
            return fk
    return None


def _ondelete(fk):
    return (((fk or {}).get("options") or {}).get("ondelete") or "").upper() or None


def _todo():
    """FKs that are missing or have the wrong ON DELETE: [(child, col, parent, ondelete, existing_fk)]."""
    out = []
    for child, col, parent, ondelete in FKS:
        fk = _existing(child, col, parent)
        if fk is None or _ondelete(fk) != ondelete:
            out.append((child, col, parent, ondelete, fk))
    return out


def _preflight_orphans(todo):
    bind = op.get_bind()
    bad = []
    for child, col, parent, _od, _fk in todo:
        n = bind.execute(sa.text(
            f"SELECT COUNT(*) FROM {child} c WHERE c.{col} IS NOT NULL AND NOT EXISTS "
            f"(SELECT 1 FROM {parent} p WHERE p.id = c.{col})")).scalar() or 0
        if n:
            bad.append(f"{child}.{col} -> {parent}.id: {n} orphan row(s)")
    if bad:
        raise RuntimeError(
            "fk144ep01 aborted, nothing changed: these rows point at records that no longer exist.\n  "
            + "\n  ".join(bad)
            + "\nRe-point or NULL them (DATABASE_URL=... python scripts/check_fk_orphans.py lists them), "
              "then run the migration again.")


def upgrade() -> None:
    todo = _todo()
    if not todo:
        return
    _preflight_orphans(todo)

    if _is_pg():
        for child, col, parent, ondelete, fk in todo:
            if fk is not None:
                op.execute(f'ALTER TABLE {child} DROP CONSTRAINT "{fk["name"]}"')
            od = f" ON DELETE {ondelete}" if ondelete else ""
            op.execute(f"ALTER TABLE {child} ADD CONSTRAINT {_name(child, col, parent)} "
                       f"FOREIGN KEY ({col}) REFERENCES {parent} (id){od} NOT VALID")
        # VALIDATE outside the transaction: scans without blocking reads/writes on the live table.
        with op.get_context().autocommit_block():
            for child, col, parent, _od, _fk in todo:
                op.execute(f"ALTER TABLE {child} VALIDATE CONSTRAINT {_name(child, col, parent)}")
        return

    # SQLite can't ALTER constraints: batch-rebuild each child table once (data copied).
    for child, col, parent, ondelete, fk in todo:
        op.execute(f"DROP TABLE IF EXISTS _alembic_tmp_{child}")   # leftover from an interrupted run
        with op.batch_alter_table(child, recreate="always", naming_convention=_CONVENTION) as b:
            if fk is not None:
                b.drop_constraint(fk["name"] or _name(child, col, parent), type_="foreignkey")
            b.create_foreign_key(_name(child, col, parent), parent, [col], ["id"], ondelete=ondelete)


def downgrade() -> None:
    # Back to sc144eo01's migrated schema, which had none of these FKs.
    for child, col, parent, _od in FKS:
        fk = _existing(child, col, parent)
        if fk is None:
            continue
        if _is_pg():
            op.execute(f'ALTER TABLE {child} DROP CONSTRAINT IF EXISTS "{fk["name"]}"')
        else:
            op.execute(f"DROP TABLE IF EXISTS _alembic_tmp_{child}")
            with op.batch_alter_table(child, recreate="always", naming_convention=_CONVENTION) as b:
                b.drop_constraint(fk["name"] or _name(child, col, parent), type_="foreignkey")
