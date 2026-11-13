"""Tracking-link tokens stored hashed (SEC-12); organisations.offboarded_at (DB-12).

Expand step of expand -> contract (DEPLOY.md):

1. Add orders.public_token_hash (SHA-256 hex), public_token_enc (the token encrypted with
   TOKEN_ENCRYPTION_KEY, services/crypto, so documents and texts can still print the link) and
   public_token_issued_at (expiry counts from the later of the close and the last re-issue), and
   organisations.offboarded_at (services/offboarding). All nullable: instant on Postgres.
2. Backfill every existing token's hash and ciphertext in batches of BATCH orders, each batch its
   own transaction on Postgres. Existing links keep working: lookups compare hashes.
3. UNIQUE index on public_token_hash (CONCURRENTLY on Postgres).

The old plaintext column orders.public_token stays as it is, for the previous image (it reads and
writes only that column): rolled back, it still opens every link issued before this deploy. The
new code never writes it and clears it on rotate/revoke. **Contract (a later release, once no
image from before this one can start):** hash any row still holding only plaintext, then
`UPDATE orders SET public_token = NULL` and drop the column.

A deployed migrate job must have the app's TOKEN_ENCRYPTION_KEY (production requires it), or the
ciphertexts it writes would be unreadable to the app: the migration refuses to run deployed
without it, like aud10m01.

Downgrade drops the new columns (the plaintext column still holds every pre-upgrade token; links
issued after the upgrade stop working).

Revision ID: dbi02
Revises: dbi01
"""
import hashlib
import logging
import os

import sqlalchemy as sa
from alembic import op

revision = "dbi02"
down_revision = "dbi01"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.dbi02")

BATCH = 2000


def _encrypt():
    from backend.services import crypto
    if os.environ.get("ENVIRONMENT", "").lower() in ("production", "staging") and not crypto.is_active():
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be set for this migration when deployed "
                           "(it encrypts the tracking-link tokens the app decrypts).")
    return crypto.encrypt


def _columns(bind, table: str) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def backfill(bind, encrypt) -> int:
    """Hash and encrypt every token that has no hash yet, BATCH orders at a time."""
    done = 0
    while True:
        rows = bind.execute(sa.text(
            "SELECT id, public_token FROM orders WHERE public_token IS NOT NULL AND public_token_hash IS NULL "
            f"ORDER BY id LIMIT {BATCH}")).fetchall()
        if not rows:
            return done
        bind.execute(sa.text("UPDATE orders SET public_token_hash = :h, public_token_enc = :e WHERE id = :i"),
                     [{"i": i, "h": hash_token(tok), "e": encrypt(tok)} for i, tok in rows])
        done += len(rows)


def upgrade() -> None:
    bind = op.get_bind()
    encrypt = _encrypt()
    pg = bind.dialect.name == "postgresql"
    have = _columns(bind, "orders")
    new = [sa.Column(n, t, nullable=True) for n, t in (("public_token_hash", sa.String(64)),
                                                       ("public_token_enc", sa.Text()),
                                                       ("public_token_issued_at", sa.DateTime()))
           if n not in have]
    if pg:
        with op.get_context().autocommit_block():
            for c in new:
                op.add_column("orders", c)
            if "offboarded_at" not in _columns(bind, "organisations"):
                op.add_column("organisations", sa.Column("offboarded_at", sa.DateTime(), nullable=True))
            n = backfill(bind, encrypt)                       # autocommit: each batch commits on its own
            invalid = bind.execute(sa.text(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE NOT i.indisvalid AND c.relname = 'uq_orders_public_token_hash'")).first()
            if invalid:
                op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_orders_public_token_hash")
            op.execute("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_orders_public_token_hash "
                       "ON orders (public_token_hash)")
    else:
        for c in new:
            op.add_column("orders", c)
        if "offboarded_at" not in _columns(bind, "organisations"):
            op.add_column("organisations", sa.Column("offboarded_at", sa.DateTime(), nullable=True))
        n = backfill(bind, encrypt)
        op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_public_token_hash ON orders (public_token_hash)")
    if n:
        log.info("dbi02 SEC-12: %s tracking-link token(s) hashed and encrypted", n)


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS uq_orders_public_token_hash")
    for c in ("public_token_issued_at", "public_token_enc", "public_token_hash"):
        if c in _columns(bind, "orders"):
            op.drop_column("orders", c)
    if "offboarded_at" in _columns(bind, "organisations"):
        op.drop_column("organisations", "offboarded_at")
