"""Wave 4 schema: redemption lines by role, money CHECKs, one open shift, membership assets, bin flag.

Review follow-ups (review-out/FOLLOWUPS.md, 13 Nov 2026):

FIN-03  order_lines.line_role: 'item' | 'giftcard' | 'coupon' | 'loyalty'. A redemption line was
        recognised by its description ("Gift card …", "Coupon …", "Loyalty points (…") and sign, so
        a hand-typed negative fee line named "Coupon X" was treated as one. Existing redemption lines
        are backfilled from that same rule (the last time it is used); gift-card lines also get
        ref_id = the card, from the code in their description.
DB-07   CHECKs: payments.amount >= 0 and payments.kind in (payment, prepayment, refund);
        transactions.status in (active, corrected, deleted); qty <> 0 on the document lines (sale,
        client return, stock transfer, purchase return, write-off, posting, credit note lines).
LAB     shifts.needs_review (a shift the 16 h cap closed and no manager corrected yet; until now this
        lived only in the audit log) and at most one open attendance shift per employee
        (uq_shifts_open_employee, partial unique on ended_at IS NULL).
DB-04   membership_assets.plan_id / .live, copied from the membership by triggers, and at most one
        live membership of a plan per asset across every asset a membership covers
        (uq_membership_assets_live_device_plan). w2b01's index only saw memberships.device_id.
DB-16   Circular FK warehouses.default_bin_id <-> storage_bins.warehouse_id, release 1 of 2:
        storage_bins.is_default, kept equal to warehouses.default_bin_id by triggers (the app still
        writes default_bin_id), one default per warehouse (uq_storage_bins_default_warehouse).
        Release 2 (DEPLOY.md) moves the app onto the flag and drops the FK that closes the cycle.

**Pre-flight** (read-only, before anything changes). It stops with counts per table (never ids,
names or amounts) on: a negative payment or prepayment, an unknown payment kind, an unknown
transaction status, a zero qty on a document line, and two live memberships of one plan covering
one asset. Repaired and logged (WARNING, alembic.w4s01), as the app reads them already: a refund
stored negative is stored positive (every reader takes its absolute value); duplicate open shifts close at their own start with 0 hours
and needs_review (what clock-out already does to them), the newest stays open.

**Old image during the deploy (expand -> contract).** Every new column is nullable or has a constant
default; old code inserts without them. On Postgres a BEFORE INSERT trigger gives an order line the
old image writes (line_role NULL) its role by the old rule, so its redemptions stay redemptions; the
contract release sets line_role NOT NULL and drops that trigger. The membership and bin triggers are
how the columns stay right whichever image writes.

**Postgres, no long locks.** Autocommit, one short step at a time: columns with constant defaults
(catalog-only), backfills in batches of BATCH rows (each its own transaction), CHECKs added NOT
VALID (brief lock, no scan) then VALIDATEd (reads and writes go on), unique indexes built
CONCURRENTLY (an INVALID leftover is rebuilt). Idempotent: a rerun resumes.

**SQLite** (development): each table that gets a CHECK is rebuilt once (batch), and every
expression / partial index or trigger the rebuild lost is put back (the w2b01 `_sqlite_rebuild`).

Downgrade drops what this added (repairs and backfilled values stay: they are correct values).

Revision ID: w4s01
Revises: w4i01
"""
import logging
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "w4s01"
down_revision = "w4i01"   # group E (#238), itself on dbi03
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.w4s01")

BATCH = 5000
LIVE = ("scheduled", "active", "paused")
ROLES = ("item", "giftcard", "coupon", "loyalty")


def _one_of(col, values):
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


QTY_TABLES = ("sale_lines", "client_return_lines", "stock_transfer_lines", "purchase_return_lines",
              "write_off_lines", "posting_lines", "credit_note_lines")

# (table, constraint name, expression) — names follow the models' convention ck_<table>_<name>
CHECKS = (
    [("order_lines", "ck_order_lines_line_role_valid", _one_of("line_role", ROLES)),
     ("payments", "ck_payments_amount_nonneg", "amount >= 0"),
     ("payments", "ck_payments_kind_valid", _one_of("kind", ("payment", "prepayment", "refund"))),
     ("transactions", "ck_transactions_status_valid", _one_of("status", ("active", "corrected", "deleted")))]
    + [(t, f"ck_{t}_qty_nonzero", "qty <> 0") for t in QTY_TABLES]
)

# (name, table, columns, partial WHERE)
UNIQUE = (
    ("uq_shifts_open_employee", "shifts", "employee_id", "ended_at IS NULL"),
    ("uq_membership_assets_live_device_plan", "membership_assets", "device_id, plan_id", "live"),
    ("uq_storage_bins_default_warehouse", "storage_bins", "warehouse_id", "is_default"),
)

# (table, column, SQL type, server default or None)
COLUMNS = (
    ("order_lines", "line_role", "VARCHAR(10)", None),
    ("shifts", "needs_review", "BOOLEAN", "false"),
    ("membership_assets", "plan_id", "VARCHAR", None),
    ("membership_assets", "live", "BOOLEAN", "false"),
    ("storage_bins", "is_default", "BOOLEAN", "false"),
)

# ── the old redemption rule (engage.redemption_kind before FIN-03), used once to backfill ─────────
_REDEMPTION = ("line_type = 'fee' AND coalesce(kind, 'custom') = 'custom' "
               "AND (coalesce(total, 0) < 0 OR coalesce(unit_price, 0) < 0)")
ROLE_SQL = ("CASE WHEN {r} AND substr(description, 1, 10) = 'Gift card ' THEN 'giftcard' "
            "WHEN {r} AND substr(description, 1, 7) = 'Coupon ' THEN 'coupon' "
            "WHEN {r} AND substr(description, 1, 16) = 'Loyalty points (' THEN 'loyalty' ELSE 'item' END")


def role_sql(prefix: str = "") -> str:
    p = prefix
    r = (_REDEMPTION.replace("line_type", f"{p}line_type").replace("(kind", f"({p}kind")
         .replace("(total", f"({p}total").replace("(unit_price", f"({p}unit_price"))
    return ROLE_SQL.format(r=r).replace("(description", f"({p}description")


# ── triggers (the same SQL as the models' after_create DDL) ──────────────────────────────────────
_LIVE_SQL = _one_of("status", LIVE)

PG_TRIGGERS = [
    # FIN-03 bridge for the previous image (contract release drops it)
    "CREATE OR REPLACE FUNCTION fill_line_role() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
    f"IF NEW.line_role IS NULL THEN NEW.line_role := {role_sql('NEW.')}; "
    "IF NEW.line_role = 'giftcard' AND NEW.ref_id IS NULL THEN NEW.ref_id := (SELECT g.id FROM gift_cards g "
    "WHERE g.org_id = NEW.org_id AND g.code = upper(trim(substr(NEW.description, 11)))); END IF; "
    "END IF; RETURN NEW; END $$",
    "DROP TRIGGER IF EXISTS fill_line_role ON order_lines",
    "CREATE TRIGGER fill_line_role BEFORE INSERT ON order_lines FOR EACH ROW EXECUTE FUNCTION fill_line_role()",
]


def membership_trigger_sql(dialect: str) -> list:
    """DB-04: membership_assets.plan_id / live follow their membership, whoever writes it."""
    if dialect == "postgresql":
        return [
            "CREATE OR REPLACE FUNCTION membership_assets_fill() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "SELECT m.plan_id, (" + _one_of("m.status", LIVE) + ") INTO NEW.plan_id, NEW.live FROM memberships m "
            "WHERE m.id = NEW.membership_id; NEW.live := coalesce(NEW.live, false); RETURN NEW; END $$",
            "DROP TRIGGER IF EXISTS membership_assets_fill ON membership_assets",
            "CREATE TRIGGER membership_assets_fill BEFORE INSERT OR UPDATE OF membership_id ON membership_assets "
            "FOR EACH ROW EXECUTE FUNCTION membership_assets_fill()",
            "CREATE OR REPLACE FUNCTION memberships_sync_assets() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "UPDATE membership_assets SET plan_id = NEW.plan_id, live = (" + _one_of("NEW.status", LIVE)
            + ") WHERE membership_id = NEW.id AND (plan_id IS DISTINCT FROM NEW.plan_id OR live IS DISTINCT FROM ("
            + _one_of("NEW.status", LIVE) + ")); RETURN NEW; END $$",
            "DROP TRIGGER IF EXISTS memberships_sync_assets ON memberships",
            "CREATE TRIGGER memberships_sync_assets AFTER UPDATE OF status, plan_id ON memberships "
            "FOR EACH ROW EXECUTE FUNCTION memberships_sync_assets()",
        ]
    pick = ("plan_id = (SELECT m.plan_id FROM memberships m WHERE m.id = NEW.membership_id), "
            "live = coalesce((SELECT " + _one_of("m.status", LIVE) + " FROM memberships m "
            "WHERE m.id = NEW.membership_id), 0)")
    return [
        "CREATE TRIGGER IF NOT EXISTS membership_assets_fill AFTER INSERT ON membership_assets "
        f"BEGIN UPDATE membership_assets SET {pick} WHERE id = NEW.id; END",
        "CREATE TRIGGER IF NOT EXISTS membership_assets_refill AFTER UPDATE OF membership_id ON membership_assets "
        f"BEGIN UPDATE membership_assets SET {pick} WHERE id = NEW.id; END",
        "CREATE TRIGGER IF NOT EXISTS memberships_sync_assets AFTER UPDATE OF status, plan_id ON memberships "
        "BEGIN UPDATE membership_assets SET plan_id = NEW.plan_id, live = (" + _one_of("NEW.status", LIVE)
        + ") WHERE membership_id = NEW.id; END",
    ]


def bin_trigger_sql(dialect: str) -> list:
    """DB-16 release 1: storage_bins.is_default = (warehouses.default_bin_id = this bin). Cleared
    first, then set, in two statements: a unique index is checked row by row."""
    clear = "UPDATE storage_bins SET is_default = {f} WHERE warehouse_id = NEW.id AND is_default AND id IS NOT NEW.default_bin_id"
    put = ("UPDATE storage_bins SET is_default = {t} WHERE id = NEW.default_bin_id AND warehouse_id = NEW.id "
           "AND NOT is_default")
    moved = ("UPDATE storage_bins SET is_default = EXISTS (SELECT 1 FROM warehouses w WHERE w.id = NEW.warehouse_id "
             "AND w.default_bin_id = NEW.id) WHERE id = NEW.id")
    if dialect == "postgresql":
        clear_pg = clear.format(f="false").replace("IS NOT NEW.default_bin_id", "IS DISTINCT FROM NEW.default_bin_id")
        return [
            "CREATE OR REPLACE FUNCTION warehouses_sync_default_bin() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            f"{clear_pg}; {put.format(t='true')}; RETURN NEW; END $$",
            "DROP TRIGGER IF EXISTS warehouses_sync_default_bin ON warehouses",
            "CREATE TRIGGER warehouses_sync_default_bin AFTER INSERT OR UPDATE OF default_bin_id ON warehouses "
            "FOR EACH ROW EXECUTE FUNCTION warehouses_sync_default_bin()",
            "CREATE OR REPLACE FUNCTION storage_bins_sync_default() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            f"IF NEW.warehouse_id IS DISTINCT FROM OLD.warehouse_id THEN {moved}; END IF; RETURN NEW; END $$",
            "DROP TRIGGER IF EXISTS storage_bins_sync_default ON storage_bins",
            "CREATE TRIGGER storage_bins_sync_default AFTER UPDATE OF warehouse_id ON storage_bins "
            "FOR EACH ROW EXECUTE FUNCTION storage_bins_sync_default()",
        ]
    return [
        "CREATE TRIGGER IF NOT EXISTS warehouses_sync_default_bin AFTER UPDATE OF default_bin_id ON warehouses "
        f"BEGIN {clear.format(f='0')}; {put.format(t='1')}; END",
        "CREATE TRIGGER IF NOT EXISTS warehouses_sync_default_bin_ins AFTER INSERT ON warehouses "
        f"BEGIN {clear.format(f='0')}; {put.format(t='1')}; END",
        "CREATE TRIGGER IF NOT EXISTS storage_bins_sync_default AFTER UPDATE OF warehouse_id ON storage_bins "
        f"BEGIN {moved}; END",
    ]


TRIGGER_NAMES = {   # table -> triggers (for the downgrade)
    "order_lines": ["fill_line_role"],
    "membership_assets": ["membership_assets_fill", "membership_assets_refill"],
    "memberships": ["memberships_sync_assets"],
    "warehouses": ["warehouses_sync_default_bin", "warehouses_sync_default_bin_ins"],
    "storage_bins": ["storage_bins_sync_default"],
}
PG_FUNCTIONS = ("fill_line_role", "membership_assets_fill", "memberships_sync_assets",
                "warehouses_sync_default_bin", "storage_bins_sync_default")


def _is_pg(bind) -> bool:
    return bind.dialect.name == "postgresql"


def _scalar(bind, sql: str, **params):
    return bind.execute(sa.text(sql), params).scalar() or 0


def _columns(bind, table: str) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


# ── pre-flight ───────────────────────────────────────────────────────────────────────────────────

def preflight(bind) -> None:
    problems = []
    for kind in ("payment", "prepayment"):
        n = _scalar(bind, "SELECT COUNT(*) FROM payments WHERE kind = :k AND amount < 0", k=kind)
        if n:
            problems.append(f"payments: {n} {kind} row(s) with a negative amount")
    for kind, n in bind.execute(sa.text(
            "SELECT kind, COUNT(*) FROM payments WHERE kind IS NOT NULL AND kind NOT IN "
            "('payment', 'prepayment', 'refund') GROUP BY kind")).fetchall():
        problems.append(f"payments.kind = {kind!r} on {n} row(s)")
    for status, n in bind.execute(sa.text(
            "SELECT status, COUNT(*) FROM transactions WHERE status IS NOT NULL AND status NOT IN "
            "('active', 'corrected', 'deleted') GROUP BY status")).fetchall():
        problems.append(f"transactions.status = {status!r} on {n} row(s)")
    for t in QTY_TABLES:
        n = _scalar(bind, f"SELECT COUNT(*) FROM {t} WHERE qty = 0")
        if n:
            problems.append(f"{t}: {n} line(s) with qty 0")
    n = _scalar(bind, "SELECT COUNT(*) FROM (SELECT a.device_id, m.plan_id FROM membership_assets a "
                      "JOIN memberships m ON m.id = a.membership_id WHERE " + _one_of("m.status", LIVE)
                      + " GROUP BY a.device_id, m.plan_id HAVING COUNT(DISTINCT m.id) > 1) d")
    if n:
        problems.append(f"membership_assets: {n} asset(s) covered by two live memberships of one plan "
                        "(cancel one in Memberships)")
    if problems:
        raise RuntimeError("w4s01 aborted, nothing changed: these rows have no safe automatic repair:\n  "
                           + "\n  ".join(problems) + "\nCorrect them by hand, then run the migration again.")


# ── repairs and backfills (batched; on Postgres each batch commits on its own) ───────────────────

def _batched(bind, table: str, setter: str, where: str, **params) -> int:
    """UPDATE `table` SET `setter` WHERE `where`, walking the table once in id order, BATCH ids per
    statement (keyset on the primary key: each batch reads only its own range)."""
    done, last = 0, ""
    while True:
        hi = bind.execute(sa.text(f"SELECT max(id) FROM (SELECT id FROM {table} WHERE id > :lo "
                                  f"ORDER BY id LIMIT {BATCH}) b"), {"lo": last}).scalar()
        if hi is None:
            return done
        done += bind.execute(sa.text(f"UPDATE {table} SET {setter} WHERE id > :lo AND id <= :hi AND ({where})"),
                             {**params, "lo": last, "hi": hi}).rowcount or 0
        last = hi


def repair(bind) -> None:
    n = _batched(bind, "payments", "amount = -amount", "kind = 'refund' AND amount < 0")
    if n:
        log.warning("w4s01 DB-07: %s refund payment(s) stored negative are stored positive", n)
    # two open attendance shifts: the newest stays open, the others close at their own start
    rows = bind.execute(sa.text("SELECT id, employee_id FROM shifts WHERE ended_at IS NULL "
                                "ORDER BY employee_id, started_at, id")).fetchall()
    by_emp = defaultdict(list)
    for sid, emp in rows:
        by_emp[emp].append(sid)
    closed = 0
    for ids in by_emp.values():
        for sid in ids[:-1]:
            bind.execute(sa.text("UPDATE shifts SET ended_at = started_at, hours_worked = 0, needs_review = :y "
                                 "WHERE id = :id").bindparams(sa.bindparam("y", type_=sa.Boolean)),
                         {"y": True, "id": sid})
            closed += 1
    if closed:
        log.warning("w4s01 LAB: %s duplicate open shift(s) closed at their own start with 0 hours, "
                    "marked for review", closed)


def backfill(bind) -> None:
    pg = _is_pg(bind)
    n = _batched(bind, "order_lines", f"line_role = {role_sql()}", "line_role IS NULL")
    log.info("w4s01 FIN-03: %s job line(s) given a role", n)
    _batched(bind, "order_lines", "ref_id = (SELECT g.id FROM gift_cards g WHERE g.org_id = order_lines.org_id "
                                  "AND g.code = upper(trim(substr(order_lines.description, 11))))",
             "line_role = 'giftcard' AND ref_id IS NULL AND EXISTS (SELECT 1 FROM gift_cards g "
             "WHERE g.org_id = order_lines.org_id AND g.code = upper(trim(substr(order_lines.description, 11))))")
    yes = "true" if pg else "1"
    _batched(bind, "membership_assets",
             "plan_id = (SELECT m.plan_id FROM memberships m WHERE m.id = membership_assets.membership_id), "
             "live = coalesce((SELECT " + _one_of("m.status", LIVE) + " FROM memberships m "
             "WHERE m.id = membership_assets.membership_id), " + ("false" if pg else "0") + ")",
             "plan_id IS NULL AND EXISTS (SELECT 1 FROM memberships m WHERE m.id = membership_assets.membership_id)")
    _batched(bind, "storage_bins", f"is_default = {yes}",
             "NOT is_default AND EXISTS (SELECT 1 FROM warehouses w WHERE w.id = storage_bins.warehouse_id "
             "AND w.default_bin_id = storage_bins.id)")
    # a shift the cap closed and no manager corrected since (timeclock._shifts_to_review)
    last: dict = {}
    for sid, action in bind.execute(sa.text(
            "SELECT entity_id, action FROM audit_log WHERE entity_type = 'shift' AND action IN "
            "('shift.auto_capped', 'shift.correct') ORDER BY created_at, id")).fetchall():
        last[sid] = action
    flag = [s for s, a in last.items() if a == "shift.auto_capped" and s]
    for i in range(0, len(flag), BATCH):
        bind.execute(sa.text("UPDATE shifts SET needs_review = :y WHERE id IN :ids").bindparams(
            sa.bindparam("ids", expanding=True), sa.bindparam("y", type_=sa.Boolean)),
            {"ids": flag[i:i + BATCH], "y": True})
    if flag:
        log.info("w4s01 LAB: %s auto-capped shift(s) marked needs_review", len(flag))


# ── Postgres ─────────────────────────────────────────────────────────────────────────────────────

def _pg_checks(bind) -> dict:
    return dict(bind.execute(sa.text("SELECT conname, convalidated FROM pg_constraint WHERE contype = 'c' "
                                     "AND conname = ANY(:n)"), {"n": [c[1] for c in CHECKS]}).fetchall())


def _upgrade_pg(bind) -> None:
    with op.get_context().autocommit_block():
        for table, col, typ, default in COLUMNS:
            if col not in _columns(bind, table):
                op.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"
                           + (f" NOT NULL DEFAULT {default}" if default else ""))
        for sql in PG_TRIGGERS + membership_trigger_sql("postgresql") + bin_trigger_sql("postgresql"):
            op.execute(sql)
        repair(bind)
        backfill(bind)
        have = _pg_checks(bind)
        for table, name, expr in CHECKS:
            if name not in have:
                op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expr}) NOT VALID")
            if not have.get(name):
                op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")
        for (name,) in bind.execute(sa.text(
                "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE NOT i.indisvalid AND c.relname = ANY(:n)"), {"n": [u[0] for u in UNIQUE]}).fetchall():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
        for name, table, cols, where in UNIQUE:
            op.execute(f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} ({cols}) WHERE {where}")


# ── SQLite ───────────────────────────────────────────────────────────────────────────────────────

def _sqlite_saved_sql(bind, table):
    return dict(bind.execute(sa.text("SELECT name, sql FROM sqlite_master WHERE type IN ('index', 'trigger') "
                                     "AND tbl_name = :t AND sql IS NOT NULL"), {"t": table}).fetchall())


def _sqlite_rebuild(bind, table, change):
    """The w2b01 pattern: rebuild through batch mode, then put back every index or trigger the
    rebuild lost or changed."""
    before = _sqlite_saved_sql(bind, table)
    op.execute(f"DROP TABLE IF EXISTS _alembic_tmp_{table}")
    with op.batch_alter_table(table, recreate="always") as b:
        change(b)
    after = _sqlite_saved_sql(bind, table)
    for name, sql in before.items():
        if " ".join(after.get(name, "").split()) != " ".join(sql.split()):
            if name in after:
                kind = "TRIGGER" if sql.lstrip().upper().startswith("CREATE TRIGGER") else "INDEX"
                op.execute(f'DROP {kind} "{name}"')
            op.execute(sql)


def _upgrade_sqlite(bind) -> None:
    for table, col, typ, default in COLUMNS:
        if col not in _columns(bind, table):
            t = sa.Boolean() if typ == "BOOLEAN" else sa.String(10) if typ == "VARCHAR(10)" else sa.String()
            op.add_column(table, sa.Column(col, t, nullable=default is None,
                                           server_default=sa.text("0") if default == "false" else None))
    repair(bind)
    backfill(bind)
    by_table = defaultdict(list)
    for table, name, expr in CHECKS:
        by_table[table].append((name, expr))
    for table, checks in by_table.items():
        existing = {c["name"] for c in sa.inspect(bind).get_check_constraints(table)}
        todo = [(n, e) for n, e in checks if n not in existing]
        if not todo:
            continue

        def change(b, todo=todo):
            for name, expr in todo:
                b.create_check_constraint(op.f(name), sa.text(expr))
        _sqlite_rebuild(bind, table, change)
    for sql in membership_trigger_sql("sqlite") + bin_trigger_sql("sqlite"):
        op.execute(sql)
    for name, table, cols, where in UNIQUE:
        op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} ({cols}) WHERE {where}")


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
            for name, _t, _c, _w in UNIQUE:
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            for table, name, _e in CHECKS:
                op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
            for table, names in TRIGGER_NAMES.items():
                for n in names:
                    op.execute(f"DROP TRIGGER IF EXISTS {n} ON {table}")
            for fn in PG_FUNCTIONS:
                op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
            for table, col, _typ, _d in COLUMNS:
                op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}")
        return
    for name, _t, _c, _w in UNIQUE:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    for names in TRIGGER_NAMES.values():
        for n in names:
            op.execute(f"DROP TRIGGER IF EXISTS {n}")
    by_table = defaultdict(list)
    for table, name, _e in CHECKS:
        by_table[table].append(name)
    for table, _col, _typ, _d in COLUMNS:
        by_table.setdefault(table, [])
    drop_cols = defaultdict(list)
    for table, col, _typ, _d in COLUMNS:
        drop_cols[table].append(col)
    for table, names in by_table.items():
        existing = {c["name"] for c in sa.inspect(bind).get_check_constraints(table)}
        cols = [c for c in drop_cols.get(table, []) if c in _columns(bind, table)]
        names = [n for n in names if n in existing]

        def change(b, names=names, cols=cols):
            for name in names:
                b.drop_constraint(op.f(name), type_="check")
            for c in cols:
                b.drop_column(c)
        _sqlite_rebuild(bind, table, change)
