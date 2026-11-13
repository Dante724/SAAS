"""Old code keeps working on the 23 tables corg01 gave a NOT NULL org_id (re-review of #219, H5/N1).

Code from before corg01 never sets org_id on payments, job lines, stock movements and the other
child tables (the column didn't exist). DEPLOY.md migrates while the old containers still serve,
and rolls back by starting the previous image, so with a bare NOT NULL every such insert from the
old code would be a 500 until the new containers are up, and for good after a rollback.

Postgres: each of those tables gets a BEFORE INSERT trigger that fills a missing org_id from the
row's parent (the same links as corg01's backfill and backend/db/tenancy.ORG_PARENTS). corg01 now
installs them itself, before its NOT NULL, so a fresh deploy never has a window without them; this
revision installs them on databases that ran corg01 before that (CREATE OR REPLACE: a no-op
where they already exist). A row whose links name no shop still fails: the trigger never invents a
shop. New code always sets org_id, so for it the trigger does nothing.

Why a trigger and not "NOT NULL one release later" (expand/contract): it keeps the NOT NULL
guarantee the tenant guard relies on (a NULL org_id is a row no shop can see), it already holds on
staging, which ran corg01, and it makes the previous image safe to roll back to with no
`alembic downgrade`. It is the bridge: drop the triggers (corg01.drop_trigger_sql) in a later
release once no image from before #219 can be started again (DEPLOY.md, "Rolling back").

SQLite (development only) can't change a row from a trigger; nothing to do there for org_id. It
also puts back uq_employees_email_lower (lower(email), unique), which an earlier SQLite rebuild of
employees dropped, unless two accounts differ only by email case (then it says so, as ls144r3).

Downgrade: nothing. The triggers belong to corg01 (its downgrade drops them with the column).

Revision ID: corg02
Revises: dec08
"""
import importlib.util
from pathlib import Path

from alembic import op

revision = "corg02"
down_revision = "dec08"
branch_labels = None
depends_on = None


def _corg01():
    spec = importlib.util.spec_from_file_location(
        "_corg01_helpers", Path(__file__).with_name("corg01_child_org_ids.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        corg01 = _corg01()
        for table in corg01.SOURCES:
            for sql in corg01.fill_trigger_sql(table):
                op.execute(sql)
        return

    # sqlite_master, not the inspector: alembic doesn't reflect expression indexes
    if bind.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type = 'index' "
                            "AND name = 'uq_employees_email_lower'").first():
        return
    clashes = bind.exec_driver_sql("SELECT COUNT(*) FROM (SELECT lower(email) FROM employees "
                                   "GROUP BY lower(email) HAVING COUNT(*) > 1) d").scalar()
    if clashes:
        print(f"corg02: NOT adding uq_employees_email_lower; {clashes} email(s) are shared by accounts that "
              "differ only by case. Merge or rename them, then create the index.")
        return
    op.execute("CREATE UNIQUE INDEX uq_employees_email_lower ON employees (lower(email))")


def downgrade() -> None:
    pass
