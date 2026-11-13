"""Audit 2026-11-12 wave 2: one-open rules, unique numbers and codes, CHECK constraints.

docs/audits/2026-11-12-backend-audit/02-schema-findings.md:

DB-04  "Only one open at a time", backed by partial unique indexes:
         cash_shifts(location_id)          WHERE closed_at IS NULL
         time_entries(employee_id)         WHERE state = 'running' AND ended_at IS NULL
         memberships(device_id, plan_id)   WHERE status is live AND device_id IS NOT NULL
DB-05  One document number per shop (FIN-17): sales, stock_transfers, write_offs,
       purchase_returns, postings (the last three allow several documents without a number).
DB-06  One coupon / gift-card code per shop (a clash made the code unusable: a 500 at checkout).
DB-07  CHECKs on stock/money signs and on status/kind values that are fixed. Payments and the
       child tables getting org_id (wave 2, another change) are left for a follow-up.

Existing data is repaired FIRST, and every change is logged (WARNING, logger alembic.w2b01):

- Open drawers: shops that tripled a drawer before the open lock (M10) still have several open at
  one location. The newest stays open (it's the one the till shows, routers/finance.current_shift);
  the others' payments, drawer movements and cash transactions move onto it, and they are closed
  balanced (counted = expected = their opening float), so no false variance is reported.
- Running timers (a double tap before LAB-05): each but the newest ends when the next one started,
  marked needs_review for a manager, so its time isn't counted twice.
- Live memberships on one asset for one plan: the oldest stays; the later ones are cancelled with
  ended_reason 'duplicate' (their invoices are left for the shop to void or credit).
- Duplicate numbers and codes: the oldest keeps its value; the others get the first free "-2",
  "-3", … suffix (the value is shortened to fit the column when needed), and each renamed row gets
  an audit_log row (action "<entity>.<column>_renamed", diff old -> new, no actor), so the shop can
  see that a gift card, coupon or receipt number it handed out now answers to another value.
  (Added after merge; the schema is unchanged. Databases that ran it before have the renames
  only in the migration log.)
- Values outside the new CHECKs: a negative on-hand is set to 0 with a matching 'adjust' stock
  movement (the ledger still sums to the shelf); a negative gift-card balance is set to 0 with a
  ledger line; a coupon used more than its limit gets its limit raised to its uses (still spent); an
  unknown timer state/category/source becomes stopped (needs_review) / other / manual. A value with
  no safe repair (an unknown transfer or document status, a membership billing) stops the upgrade
  with a list, before anything is changed.

Repairs are not undone by the downgrade, which only drops the indexes and constraints.

Postgres: CHECKs are added directly (the repairs ran in the same transaction, so a NOT VALID /
VALIDATE split would hold the same lock). Everything runs in one transaction, so the CHECKed tables
(ACCESS EXCLUSIVE) and the uniquely-indexed ones (SHARE: writes wait) stay locked until it commits:
seconds today, a maintenance window at scale (DEPLOY.md, "Migrations that lock tables"). SQLite can't add a CHECK in place: the table is rebuilt
(batch), and every index the rebuild didn't carry over exactly (expression and partial indexes
are not reflected faithfully) is recreated from its saved SQL.

Revision ID: w2b01
Revises: pv01
"""
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "w2b01"
down_revision = "pv01"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.w2b01")

STOCK_DUST = "0.000001"
MONEY_DUST = "0.005"
LIVE = ("scheduled", "active", "paused")


def _one_of(col, values):
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


# (table, constraint name, expression) — names follow the models' naming convention (ck_<table>_<name>).
CHECKS = [
    ("stock_levels", "ck_stock_levels_qty_available_nonneg", f"qty_available >= -{STOCK_DUST}"),
    ("stock_levels", "ck_stock_levels_qty_reserved_nonneg", f"qty_reserved >= -{STOCK_DUST}"),
    ("invoices", "ck_invoices_amount_paid_nonneg", f"amount_paid > -{MONEY_DUST}"),
    ("invoices", "ck_invoices_credited_nonneg", f"credited > -{MONEY_DUST}"),
    ("time_entries", "ck_time_entries_state_valid", _one_of("state", ("running", "stopped", "approved", "void"))),
    ("time_entries", "ck_time_entries_category_valid",
     _one_of("category", ("job", "task", "break", "admin", "other"))),
    ("time_entries", "ck_time_entries_source_valid", _one_of("source", ("timer", "manual"))),
    ("time_entries", "ck_time_entries_duration_nonneg", "duration_sec >= 0"),
    ("stock_transfers", "ck_stock_transfers_status_valid",
     _one_of("status", ("draft", "ready", "in_transit", "received", "completed", "canceled"))),
    ("gift_cards", "ck_gift_cards_balance_nonneg", f"balance > -{MONEY_DUST}"),
    ("gift_cards", "ck_gift_cards_initial_value_nonneg", "initial_value >= 0"),
    ("coupons", "ck_coupons_kind_valid", _one_of("kind", ("pct", "fixed"))),
    ("coupons", "ck_coupons_value_nonneg", "value >= 0"),
    ("coupons", "ck_coupons_min_total_nonneg", "min_total >= 0"),
    ("coupons", "ck_coupons_uses_in_range", "uses >= 0 AND max_uses >= 0 AND (max_uses = 0 OR uses <= max_uses)"),
    ("purchase_returns", "ck_purchase_returns_status_valid", _one_of("status", ("posted", "deleted"))),
    ("purchase_returns", "ck_purchase_returns_settlement_valid", _one_of("settlement", ("balance", "cash"))),
    ("write_offs", "ck_write_offs_status_valid", _one_of("status", ("posted", "reverted"))),
    ("postings", "ck_postings_status_valid", _one_of("status", ("posted", "deleted"))),
    ("postings", "ck_postings_settlement_valid", _one_of("settlement", ("balance", "cash"))),
    ("memberships", "ck_memberships_status_valid",
     _one_of("status", ("scheduled", "active", "paused", "lapsed", "cancelled"))),
    ("memberships", "ck_memberships_billing_valid", _one_of("billing", ("monthly", "yearly"))),
    ("memberships", "ck_memberships_price_nonneg", "price >= 0"),
]

# (name, table, columns, partial WHERE or None)
UNIQUE = [
    ("uq_cash_shifts_open_location", "cash_shifts", ["location_id"], "closed_at IS NULL"),
    ("uq_time_entries_running_employee", "time_entries", ["employee_id"], "state = 'running' AND ended_at IS NULL"),
    ("uq_memberships_live_device_plan", "memberships", ["device_id", "plan_id"],
     f"{_one_of('status', LIVE)} AND device_id IS NOT NULL"),
    ("uq_sales_org_number", "sales", ["org_id", "sale_number"], None),
    ("uq_stock_transfers_org_number", "stock_transfers", ["org_id", "transfer_number"], None),
    ("uq_write_offs_org_number", "write_offs", ["org_id", "number"], "number IS NOT NULL"),
    ("uq_purchase_returns_org_number", "purchase_returns", ["org_id", "number"], "number IS NOT NULL"),
    ("uq_postings_org_number", "postings", ["org_id", "number"], "number IS NOT NULL"),
    ("uq_coupons_org_code", "coupons", ["org_id", "code"], None),
    ("uq_gift_cards_org_code", "gift_cards", ["org_id", "code"], None),
]

# Values the CHECKs refuse that have no safe automatic repair: the upgrade stops and lists them.
REFUSE_UNKNOWN = [
    ("stock_transfers", "status", ("draft", "ready", "in_transit", "received", "completed", "canceled"),
     {"cancelled": "canceled"}),
    ("purchase_returns", "status", ("posted", "deleted"), {}),
    ("purchase_returns", "settlement", ("balance", "cash"), {}),
    ("write_offs", "status", ("posted", "reverted"), {}),
    ("postings", "status", ("posted", "deleted"), {}),
    ("postings", "settlement", ("balance", "cash"), {}),
    ("memberships", "status", ("scheduled", "active", "paused", "lapsed", "cancelled"), {"canceled": "cancelled"}),
    ("memberships", "billing", ("monthly", "yearly"), {}),
]


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _rows(bind, sql, **params):
    return bind.execute(sa.text(sql), params).fetchall()


def _exec(bind, sql, **params):
    return bind.execute(sa.text(sql), params)


def _columns(bind, table):
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


# ── pre-flight: values with no safe repair ──────────────────────────────────────────────────────

def _refuse_unknown_values(bind):
    problems = []
    for table, col, allowed, _fixes in REFUSE_UNKNOWN:
        fixable = tuple(_fixes)
        for (val, n) in _rows(bind, f"SELECT {col}, count(*) FROM {table} WHERE {col} IS NOT NULL "
                                    f"AND {col} NOT IN ({', '.join(repr(v) for v in allowed + fixable)}) "
                                    f"GROUP BY {col}"):
            problems.append(f"{table}.{col} = {val!r} on {n} row(s)")
    if problems:
        raise RuntimeError("w2b01: these values have no safe automatic repair — fix them by hand, then upgrade "
                           "again (nothing was changed):\n  " + "\n  ".join(problems))


# ── DB-04 repairs ───────────────────────────────────────────────────────────────────────────────

def _merge_open_drawers(bind, now):
    rows = _rows(bind, "SELECT id, location_id, opening_float FROM cash_shifts WHERE closed_at IS NULL "
                       "ORDER BY location_id, opened_at, id")
    by_loc = defaultdict(list)
    for r in rows:
        by_loc[r.location_id].append(r)
    movers = [t for t in ("payments", "cash_drawer_movements", "transactions") if "shift_id" in _columns(bind, t)]
    for loc, shifts in by_loc.items():
        if len(shifts) < 2:
            continue
        keep, dups = shifts[-1], shifts[:-1]
        for d in dups:
            moved = {t: _exec(bind, f"UPDATE {t} SET shift_id = :keep WHERE shift_id = :dup",
                              keep=keep.id, dup=d.id).rowcount for t in movers}
            _exec(bind, "UPDATE cash_shifts SET closed_at = :now, closing_float = opening_float, "
                        "expected_cash = opening_float WHERE id = :id", now=now, id=d.id)
            log.warning("w2b01 DB-04: location %s had %d open drawers; closed duplicate drawer %s (balanced at "
                        "its opening float) and moved its rows to drawer %s: %s",
                        loc, len(shifts), d.id, keep.id, moved)


def _stop_double_timers(bind):
    q = sa.text("SELECT id, employee_id, started_at FROM time_entries WHERE state = 'running' "
                "AND ended_at IS NULL ORDER BY employee_id, started_at, id").columns(started_at=sa.DateTime)
    by_emp = defaultdict(list)
    for r in bind.execute(q):
        by_emp[r.employee_id].append(r)
    upd = sa.text("UPDATE time_entries SET state = 'stopped', ended_at = :end, duration_sec = :dur, "
                  "needs_review = :yes WHERE id = :id").bindparams(
        sa.bindparam("end", type_=sa.DateTime), sa.bindparam("yes", type_=sa.Boolean))
    for emp, running in by_emp.items():
        for cur, nxt in zip(running, running[1:]):
            end = nxt.started_at
            dur = max(0, int((end - cur.started_at).total_seconds()))
            bind.execute(upd, {"end": end, "dur": dur, "yes": True, "id": cur.id})
            log.warning("w2b01 DB-04: employee %s had %d running timers; stopped %s at %s (when %s started), "
                        "marked for review", emp, len(running), cur.id, end, nxt.id)


def _cancel_double_memberships(bind, today):
    rows = _rows(bind, "SELECT id, device_id, plan_id FROM memberships WHERE device_id IS NOT NULL AND "
                       f"{_one_of('status', LIVE)} ORDER BY device_id, plan_id, created_at, id")
    groups = defaultdict(list)
    for r in rows:
        groups[(r.device_id, r.plan_id)].append(r)
    upd = sa.text("UPDATE memberships SET status = 'cancelled', ended_reason = 'duplicate', ended_on = :today, "
                  "renews = :no WHERE id = :id").bindparams(sa.bindparam("today", type_=sa.Date),
                                                             sa.bindparam("no", type_=sa.Boolean))
    for (device, plan), ms in groups.items():
        for m in ms[1:]:
            bind.execute(upd, {"today": today, "no": False, "id": m.id})
            log.warning("w2b01 DB-04: asset %s had %d live memberships on plan %s; kept %s, cancelled %s "
                        "(ended_reason 'duplicate'; its invoices are unchanged)", device, len(ms), plan, ms[0].id, m.id)


# ── DB-05 / DB-06 repairs ───────────────────────────────────────────────────────────────────────

def _audit_rename(bind, now, org, table, rid, col, old, new, copies):
    """One audit_log row per renamed row, so the shop can see (Settings > Audit log) that a gift
    card, coupon or document it knows by a number now answers to another one. Actor: none (system)."""
    entity = {"gift_cards": "gift_card", "coupons": "coupon", "sales": "sale", "stock_transfers": "stock_transfer",
              "write_offs": "write_off", "purchase_returns": "purchase_return", "postings": "posting"}[table]
    vals = {"id": str(uuid.uuid4()), "org_id": org, "actor_id": None, "action": f"{entity}.{col}_renamed",
            "entity_type": entity, "entity_id": rid, "created_at": now,
            "diff": {col: [old, new], "reason": f"w2b01: {copies} rows of this shop shared {col} {old!r}; "
                                                f"the oldest kept it, this one was renamed so each is unique"}}
    vals = {k: v for k, v in vals.items() if k in _columns(bind, "audit_log")}
    bind.execute(sa.text(f"INSERT INTO audit_log ({', '.join(vals)}) VALUES ({', '.join(':' + k for k in vals)})")
                 .bindparams(sa.bindparam("created_at", type_=sa.DateTime), sa.bindparam("diff", type_=sa.JSON)),
                 vals)


def _suffix_duplicates(bind, table, col, maxlen, what, now):
    groups = _rows(bind, f"SELECT org_id, {col} AS val FROM {table} WHERE {col} IS NOT NULL AND org_id IS NOT NULL "
                         f"GROUP BY org_id, {col} HAVING count(*) > 1")
    for org, val in groups:
        taken = {v for (v,) in _rows(bind, f"SELECT {col} FROM {table} WHERE org_id = :o AND {col} IS NOT NULL",
                                     o=org)}
        dups = _rows(bind, f"SELECT id FROM {table} WHERE org_id = :o AND {col} = :v ORDER BY created_at, id",
                     o=org, v=val)
        n = 2
        for (rid,) in dups[1:]:
            while True:
                sfx = f"-{n}"
                new = val[:maxlen - len(sfx)] + sfx
                n += 1
                if new not in taken:
                    break
            taken.add(new)
            _exec(bind, f"UPDATE {table} SET {col} = :new WHERE id = :id", new=new, id=rid)
            _audit_rename(bind, now, org, table, rid, col, val, new, len(dups))
            log.warning("w2b01 %s: shop %s had %d %s %r; %s %s renumbered to %r",
                        "DB-06" if what.endswith("code") else "DB-05", org, len(dups), what, val, table, rid, new)


# ── DB-07 repairs ───────────────────────────────────────────────────────────────────────────────

def _repair_check_values(bind, now):
    # stock: a negative on-hand goes to 0 with an 'adjust' movement, so the ledger still sums to it
    mv_cols = _columns(bind, "stock_movements")
    org_col = "org_id" if "org_id" in _columns(bind, "stock_levels") else "NULL AS org_id"
    for r in _rows(bind, f"SELECT id, {org_col}, part_id, location_id, warehouse_id, bin_id, qty_available "
                         f"FROM stock_levels WHERE qty_available < -{STOCK_DUST}"):
        vals = {"id": f"w2b01-{r.id}"[:36], "part_id": r.part_id, "location_id": r.location_id,
                "warehouse_id": r.warehouse_id, "bin_id": r.bin_id, "movement_type": "adjust",
                "qty": -float(r.qty_available), "unit_cost": 0, "ref_type": "w2b01", "ref_id": r.id, "created_at": now}
        if "org_id" in mv_cols:      # the stock ledger carries the shop since corg01 (wave 2, DB-02)
            vals["org_id"] = r.org_id or (_rows(bind, "SELECT org_id FROM locations WHERE id = :l",
                                                l=r.location_id) or [(None,)])[0][0]
        vals = {k: v for k, v in vals.items() if k in mv_cols}
        bind.execute(sa.text(f"INSERT INTO stock_movements ({', '.join(vals)}) VALUES "
                             f"({', '.join(':' + k for k in vals)})").bindparams(
            *([sa.bindparam("created_at", type_=sa.DateTime)] if "created_at" in vals else [])), vals)
        _exec(bind, "UPDATE stock_levels SET qty_available = 0 WHERE id = :id", id=r.id)
        log.warning("w2b01 DB-07: stock row %s (product %s) was at %s on hand; set to 0 with an adjust movement",
                    r.id, r.part_id, r.qty_available)
    n = _exec(bind, f"UPDATE stock_levels SET qty_reserved = 0 WHERE qty_reserved < -{STOCK_DUST}").rowcount
    if n:
        log.warning("w2b01 DB-07: %d stock row(s) had a negative reserved quantity; set to 0", n)

    # gift cards: a negative balance goes to 0 with a ledger line (balance stays derivable)
    for r in _rows(bind, f"SELECT id, org_id, balance FROM gift_cards WHERE balance <= -{MONEY_DUST}"):
        bind.execute(sa.text("INSERT INTO gift_card_ledger (id, org_id, gift_card_id, amount, ref_type, note, "
                             "created_at) VALUES (:id, :org, :gc, :amt, 'manual', :note, :now)").bindparams(
            sa.bindparam("now", type_=sa.DateTime)),
            {"id": f"w2b01-{r.id}"[:36], "org": r.org_id, "gc": r.id, "amt": -float(r.balance),
             "note": "w2b01: negative balance set to 0", "now": now})
        _exec(bind, "UPDATE gift_cards SET balance = 0 WHERE id = :id", id=r.id)
        log.warning("w2b01 DB-07: gift card %s had balance %s; set to 0 with a ledger line", r.id, r.balance)

    simple = [
        ("gift_cards", "initial_value = 0", "initial_value < 0"),
        ("coupons", "uses = 0", "uses < 0"),
        ("coupons", "max_uses = 0", "max_uses < 0"),
        ("coupons", "max_uses = uses", "max_uses > 0 AND uses > max_uses"),       # still spent
        ("coupons", "min_total = 0", "min_total < 0"),
        ("coupons", "value = 0, active = :no", "value < 0"),
        ("coupons", "kind = 'fixed', active = :no", "kind IS NULL OR kind NOT IN ('pct', 'fixed')"),
        ("invoices", "amount_paid = 0", f"amount_paid <= -{MONEY_DUST}"),
        ("invoices", "credited = 0", f"credited <= -{MONEY_DUST}"),
        ("memberships", "price = 0", "price < 0"),
        ("stock_transfers", "status = 'canceled'", "status = 'cancelled'"),
        ("memberships", "status = 'cancelled'", "status = 'canceled'"),
        ("time_entries", "ended_at = coalesce(ended_at, started_at), state = 'stopped', needs_review = :yes",
         "state IS NULL OR state NOT IN ('running', 'stopped', 'approved', 'void')"),
        ("time_entries", "category = 'other'",
         "category IS NULL OR category NOT IN ('job', 'task', 'break', 'admin', 'other')"),
        ("time_entries", "source = 'manual'", "source IS NULL OR source NOT IN ('timer', 'manual')"),
        ("time_entries", "duration_sec = 0, needs_review = :yes", "duration_sec < 0"),
    ]
    for table, setter, where in simple:
        ids = [i for (i,) in _rows(bind, f"SELECT id FROM {table} WHERE {where}")]
        if not ids:
            continue
        stmt = sa.text(f"UPDATE {table} SET {setter} WHERE {where}")
        params = {}
        if ":no" in setter:
            stmt = stmt.bindparams(sa.bindparam("no", type_=sa.Boolean))
            params["no"] = False
        if ":yes" in setter:
            stmt = stmt.bindparams(sa.bindparam("yes", type_=sa.Boolean))
            params["yes"] = True
        bind.execute(stmt, params)
        log.warning("w2b01 DB-07: %s rows %s had %s; set %s", table, ids, where, setter.replace(":no", "false")
                    .replace(":yes", "true"))


# ── DDL ─────────────────────────────────────────────────────────────────────────────────────────

def _sqlite_saved_sql(bind, table):
    return {n: s for n, s in _rows(bind, "SELECT name, sql FROM sqlite_master WHERE type IN ('index', 'trigger') "
                                         "AND tbl_name = :t AND sql IS NOT NULL", t=table)}


def _sqlite_rebuild(bind, table, change):
    """Rebuild a SQLite table through batch mode, then put back every index or trigger the rebuild
    lost or changed (alembic doesn't reflect expression indexes, and partial ones only partly)."""
    before = _sqlite_saved_sql(bind, table)
    with op.batch_alter_table(table, recreate="always") as b:
        change(b)
    after = _sqlite_saved_sql(bind, table)
    for name, sql in before.items():
        if " ".join(after.get(name, "").split()) != " ".join(sql.split()):
            if name in after:
                kind = "TRIGGER" if sql.lstrip().upper().startswith("CREATE TRIGGER") else "INDEX"
                op.execute(f'DROP {kind} "{name}"')
            op.execute(sql)


def _add_checks(bind):
    by_table = defaultdict(list)
    for table, name, expr in CHECKS:
        by_table[table].append((name, expr))
    for table, checks in by_table.items():
        if bind.dialect.name == "sqlite":
            def change(b, checks=checks):
                for name, expr in checks:
                    b.create_check_constraint(op.f(name), sa.text(expr))
            _sqlite_rebuild(bind, table, change)
        else:
            for name, expr in checks:
                op.create_check_constraint(op.f(name), table, sa.text(expr))


# corg01's SQLite table rebuild dropped this expression index (alembic doesn't reflect expression
# indexes): put it back. Postgres never lost it. Same definition as models.StockLevel.
_UNBINNED = ("CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_levels_part_loc_unbinned ON stock_levels "
             "(part_id, location_id, coalesce(warehouse_id, '')) WHERE bin_id IS NULL")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(_UNBINNED)
    now = _now()
    _refuse_unknown_values(bind)          # before anything is written
    _merge_open_drawers(bind, now)
    _stop_double_timers(bind)
    _cancel_double_memberships(bind, now.date())
    for table, col, maxlen, what in (
            ("sales", "sale_number", 20, "sales numbered"),
            ("stock_transfers", "transfer_number", 20, "transfers numbered"),
            ("write_offs", "number", 40, "write-offs numbered"),
            ("purchase_returns", "number", 40, "purchase returns numbered"),
            ("postings", "number", 40, "postings numbered"),
            ("coupons", "code", 40, "coupons with code"),
            ("gift_cards", "code", 40, "gift cards with code")):
        _suffix_duplicates(bind, table, col, maxlen, what, now)
    _repair_check_values(bind, now)
    _add_checks(bind)          # table rebuilds on SQLite: before the new indexes, so those aren't rebuilt
    for name, table, cols, where in UNIQUE:
        kw = {"sqlite_where": sa.text(where), "postgresql_where": sa.text(where)} if where else {}
        op.create_index(name, table, cols, unique=True, **kw)


def downgrade() -> None:
    bind = op.get_bind()
    for name, table, _cols, _where in reversed(UNIQUE):
        op.drop_index(name, table_name=table)
    by_table = defaultdict(list)
    for table, name, _expr in CHECKS:
        by_table[table].append(name)
    for table, names in by_table.items():
        if bind.dialect.name == "sqlite":
            def change(b, names=names):
                for name in names:
                    b.drop_constraint(op.f(name), type_="check")
            _sqlite_rebuild(bind, table, change)
        else:
            for name in names:
                op.drop_constraint(op.f(name), table, type_="check")
