"""Work-order search box: trigram indexes (Postgres only).

Every search filtered all of a shop's jobs, clients and assets row by row: the box matches
substrings (`ILIKE '%jane%'`, a phone or job number with its formatting stripped), which a
btree can't answer. pg_trgm's GIN indexes can, on exactly the expressions the search compares
(backend/services/search_match.py, used by routers/orders._order_search_clause):

- clients.name                              words_match (ILIKE per word)
- clients.phone, PHONE_STRIP removed         phone_match
- lower(orders.order_number), NUM_STRIP gone number_match (substring)
- devices.brand, devices.model               ILIKE
- lower(devices.serial|imei|vin|hin), NUM_STRIP removed  code_match

plus two btrees: (org_id, the job number's digits with prefix and zero padding trimmed) for
number_match's "WO12 = WO-00012" branch, and clients.payer_id for "people billed to a matching
company".

The expressions must stay byte-for-byte what SQLAlchemy emits for search_match, or Postgres
won't use the index; backend/tests/test_order_search_indexes.py compares them (and the models
declare the same indexes, Postgres only, so the drift gate and create_all databases agree).

CREATE EXTENSION pg_trgm: a trusted extension since Postgres 13, so the database owner can
create it without superuser (the official postgres:16 image ships it; managed Postgres services
allow-list it). Indexes are built CONCURRENTLY so a deploy doesn't block writes; IF NOT EXISTS
makes a half-finished run safe to retry. The extension is left in place on downgrade (other
objects may come to use it, and dropping it needs no undo here). SQLite gets only the payer_id
btree (LIKE '%x%' can't use an index there).

Revision ID: srch01
Revises: tk02
"""
import sqlalchemy as sa
from alembic import op

revision = "srch01"
down_revision = "tk02"
branch_labels = None
depends_on = None

NUM_STRIP = (" ", "-", "#", "/", ".", "_")
PHONE_STRIP = (" ", "-", "(", ")", ".", "/", "+")
LETTERS_AND_ZERO = "abcdefghijklmnopqrstuvwxyz0"


def stripped(expr: str, chars) -> str:
    for ch in chars:
        expr = f"replace({expr}, '{ch}', '')"
    return expr


def flat(col: str) -> str:
    return stripped(f"lower({col})", NUM_STRIP)


TRGM = [
    ("ix_clients_name_trgm", "clients", "name"),
    ("ix_clients_phone_trgm", "clients", f"({stripped('phone', PHONE_STRIP)})"),
    ("ix_orders_number_trgm", "orders", f"({flat('order_number')})"),
    ("ix_devices_brand_trgm", "devices", "brand"),
    ("ix_devices_model_trgm", "devices", "model"),
    ("ix_devices_serial_trgm", "devices", f"({flat('serial')})"),
    ("ix_devices_imei_trgm", "devices", f"({flat('imei')})"),
    ("ix_devices_vin_trgm", "devices", f"({flat('vin')})"),
    ("ix_devices_hin_trgm", "devices", f"({flat('hin')})"),
]
BTREE = [
    ("ix_orders_number_digits", "orders", f"org_id, ltrim({flat('order_number')}, '{LETTERS_AND_ZERO}')"),
    ("ix_clients_payer_id", "clients", "payer_id"),
]


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        # payer_id's index helps any database; the trigram ones need Postgres
        op.execute("CREATE INDEX IF NOT EXISTS ix_clients_payer_id ON clients (payer_id)")
        return
    with op.get_context().autocommit_block():
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        # A CONCURRENTLY build that failed part-way leaves an INVALID index behind, which IF NOT
        # EXISTS would then keep forever: drop those first so a retry builds them properly.
        names = [n for n, _t, _e in TRGM + BTREE]
        invalid = op.get_bind().execute(sa.text(
            "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE NOT i.indisvalid AND c.relname = ANY(:names)"), {"names": names}).scalars().all()
        for name in invalid:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
        for name, table, expr in TRGM:
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} USING gin ({expr} gin_trgm_ops)")
        for name, table, expr in BTREE:
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} ({expr})")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_clients_payer_id")
        return
    with op.get_context().autocommit_block():
        for name, _table, _expr in reversed(TRGM + BTREE):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
