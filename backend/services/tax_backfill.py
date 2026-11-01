"""One-off: make existing shops' default tax the tax their jobs charge (QA 2026-10-31 B).

Before #71 the "default" flag on a tax and the job tax stack (tax_rules, tickets_sales) were
separate, and shops created before the stack existed never had one: their default tax was shown
but not charged. Approved by the owner: for each shop whose job stack is empty, or holds only 0%
or switched-off taxes, while it has an active default tax above 0%, the stack becomes that tax.
A shop whose stack already holds an active non-zero tax chose it and is left alone.

Only new job lines pick the tax up; existing orders and invoices are never recomputed. Every
change is saved in tax_stack_backfill_log (the previous stack) so the migration can be undone.
Runs on a plain synchronous Connection (Alembic's op.get_bind(), or run_sync in tests)."""
import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

FAMILY = "tickets_sales"

_meta = sa.MetaData()
LOG = sa.Table(
    "tax_stack_backfill_log", _meta,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("org_id", sa.String, nullable=False, index=True),
    sa.Column("previous_tax_ids", sa.Text, nullable=False),     # JSON list, in priority order
    sa.Column("tax_id", sa.String, nullable=False),             # the tax the stack was set to
    sa.Column("created_at", sa.DateTime, nullable=False),
)


def _stack(conn, org_id: str) -> list[str]:
    return [r[0] for r in conn.execute(sa.text(
        "SELECT tax_id FROM tax_rules WHERE org_id = :o AND doc_family = :f ORDER BY priority"),
        {"o": org_id, "f": FAMILY})]


def _set_stack(conn, org_id: str, tax_ids: list[str]) -> None:
    conn.execute(sa.text("DELETE FROM tax_rules WHERE org_id = :o AND doc_family = :f"),
                 {"o": org_id, "f": FAMILY})
    for i, tid in enumerate(tax_ids):
        conn.execute(sa.text(
            "INSERT INTO tax_rules (id, org_id, doc_family, tax_id, priority) "
            "VALUES (:id, :o, :f, :t, :p)"),
            {"id": str(uuid.uuid4()), "o": org_id, "f": FAMILY, "t": tid, "p": i})


def backfill(conn, log=print) -> list[tuple[str, str]]:
    """Apply the rule above; returns [(org_id, tax name)] changed. Idempotent: a shop it changed
    now has an active non-zero tax in its stack, so a second run leaves it alone."""
    taxes = conn.execute(sa.text(
        "SELECT id, org_id, name, rate, active, is_default FROM taxes")).mappings().all()
    by_id = {t["id"]: t for t in taxes}
    defaults: dict = {}
    for t in sorted(taxes, key=lambda t: (t["name"] or "", t["id"])):
        if t["is_default"] and t["active"] and float(t["rate"] or 0) > 0:
            defaults.setdefault(t["org_id"], t)               # one per shop; name order if several
    changed = []
    for org_id, tax in sorted(defaults.items()):
        stack = _stack(conn, org_id)
        live = [tid for tid in stack if tid in by_id and by_id[tid]["active"]
                and float(by_id[tid]["rate"] or 0) > 0]
        if live:
            continue                                         # the shop chose a real tax: keep it
        conn.execute(LOG.insert().values(
            id=str(uuid.uuid4()), org_id=org_id, previous_tax_ids=json.dumps(stack),
            tax_id=tax["id"], created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        _set_stack(conn, org_id, [tax["id"]])
        log(f"taxbf01: org {org_id} now charges default tax {tax['name']!r} on new jobs")
        changed.append((org_id, tax["name"]))
    return changed


def restore(conn, log=print) -> int:
    """Undo backfill(): put back each logged shop's previous stack, unless the owner has changed
    the stack since (then theirs is kept)."""
    n = 0
    for row in conn.execute(LOG.select().order_by(LOG.c.created_at.desc())).mappings().all():
        if _stack(conn, row["org_id"]) != [row["tax_id"]]:
            log(f"taxbf01: org {row['org_id']} changed its tax list since; left as is")
            continue
        existing = {r[0] for r in conn.execute(sa.text("SELECT id FROM taxes WHERE org_id = :o"),
                                               {"o": row["org_id"]})}
        _set_stack(conn, row["org_id"], [t for t in json.loads(row["previous_tax_ids"]) if t in existing])
        n += 1
    return n
