"""org_id NOT NULL on the 16 tenant tables whose migrations left it nullable.

The models declare org_id NOT NULL on every one of these tables and the tenant read guard
(backend/db/tenancy.py) filters every query by it, so a row with a NULL org_id belongs to no shop
and is visible to none. On Postgres the migrations that created these tables never added the
constraint (the SQLite drift gate tolerated "nullable in DB, NOT NULL in model"; the Postgres gate
in test_144eo_migrated_schema.py now refuses it for org_id).

SAFETY
  * Pre-flight: counts rows with org_id IS NULL on every table it will touch. If any exist it
    ABORTS with the list and changes nothing — it never guesses a shop for a row. Re-point or
    delete them first, then re-run.
  * Only columns that are still nullable are altered, so a database built by create_all (or one
    that already has the constraint) is left alone.
  * Downgrade drops NOT NULL on the same columns.

Revision ID: nn01
Revises: idg02
"""
import sqlalchemy as sa
from alembic import op

revision = "nn01"
down_revision = "idg02"
branch_labels = None
depends_on = None

TABLES = (
    "checklist_sections", "checklist_steps", "consent_log", "currencies", "currency_rate_history",
    "document_signatures", "form_layouts", "insight_cards", "merge_logs", "order_checklists",
    "price_type_tiers", "recommendations", "taggables", "tags", "telephony_config", "time_entry_audits",
)


def _nullable(bind, table: str) -> bool | None:
    """True/False for org_id's nullability, None when the table or column does not exist."""
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return None
    for col in insp.get_columns(table):
        if col["name"] == "org_id":
            return bool(col["nullable"])
    return None


def upgrade() -> None:
    bind = op.get_bind()
    todo = [t for t in TABLES if _nullable(bind, t) is True]
    orphans = []
    for t in todo:
        n = bind.execute(sa.text(f'SELECT count(*) FROM "{t}" WHERE org_id IS NULL')).scalar()
        if n:
            orphans.append(f"{t}: {n} row(s) with org_id IS NULL")
    if orphans:
        raise RuntimeError(
            "nn01 refuses to run: rows with no shop would violate NOT NULL org_id.\n  "
            + "\n  ".join(orphans)
            + "\nRe-point them to their shop or delete them, then re-run `alembic upgrade head`.")
    for t in todo:
        with op.batch_alter_table(t) as b:
            b.alter_column("org_id", existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    for t in TABLES:
        if _nullable(bind, t) is False:
            with op.batch_alter_table(t) as b:
                b.alter_column("org_id", existing_type=sa.String(), nullable=True)
