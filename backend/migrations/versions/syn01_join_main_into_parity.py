"""Join main (launch line, head bl144s1) into roadmap/orderry-parity (QA line, head f2tr01).

Merges the two heads. A database built from empty runs every revision of both lines, so for it
this revision changes nothing.

A database already upgraded on the parity line needs a catch-up. The launch line moved some of
parity's revisions onto its own chain (wsc1 now follows whh1, which follows apih1, then ojj1), so
alembic counts ojj1, apih1 and whh1 as already applied on such a database, though they never ran
there. Their schema is added here, only where it is missing:

- ojj1: orders.written_off; organisations.public_employee_name (16 wide, as wsc1 sets it)
- apih1: API key rotation/expiry columns and the api_idempotency_keys table
- whh1: webhook endpoint hardening columns and indexes, unique calls per provider id, an inbound
  webhook token for every org, and endpoint secrets encrypted at rest when a key is set.
  The parity line's aud3r01 already added webhook_deliveries.claimed_at / next_attempt_at.

The other way round, a database upgraded on the launch line counts parity's idem01, rt144a1 and
taxbf01 as applied (parity's ds144a1 follows taxbf01). rt144a1 and taxbf01 run again here; both
are safe to repeat. Platform admins enrolled in 2-step on parity stay enrolled.

Revision ID: syn01
Revises: bl144s1, f2tr01
"""
import secrets

import sqlalchemy as sa
from alembic import op

revision = "syn01"
down_revision = ("bl144s1", "f2tr01")
branch_labels = None
depends_on = None


def _cols(table: str) -> dict:
    return {c["name"]: c for c in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _add_missing(table: str, columns: list[sa.Column]) -> None:
    have = _cols(table)
    missing = [c for c in columns if c.name not in have]
    if missing:
        with op.batch_alter_table(table) as b:
            for c in missing:
                b.add_column(c)


def _ojj1() -> None:
    _add_missing("orders", [sa.Column("written_off", sa.Numeric(12, 2, asdecimal=False), nullable=False,
                                      server_default="0")])
    have = _cols("organisations")
    if "public_employee_name" not in have:
        _add_missing("organisations", [sa.Column("public_employee_name", sa.String(length=16),
                                                 nullable=False, server_default="full")])
    elif getattr(have["public_employee_name"]["type"], "length", 16) and \
            have["public_employee_name"]["type"].length < 16:
        with op.batch_alter_table("organisations") as b:
            b.alter_column("public_employee_name", existing_type=have["public_employee_name"]["type"],
                           type_=sa.String(length=16), existing_nullable=False,
                           existing_server_default="full")


def _apih1() -> None:
    _add_missing("api_keys", [
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("previous_key_hash", sa.String(length=64), nullable=True),
        sa.Column("previous_expires_at", sa.DateTime(), nullable=True),
        sa.Column("rotated_at", sa.DateTime(), nullable=True),
    ])
    if "ix_api_keys_previous_key_hash" not in _indexes("api_keys"):
        op.create_index("ix_api_keys_previous_key_hash", "api_keys", ["previous_key_hash"])
    if not sa.inspect(op.get_bind()).has_table("api_idempotency_keys"):
        op.create_table(
            "api_idempotency_keys",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("org_id", sa.String(), nullable=False),
            sa.Column("key_id", sa.String(), nullable=False),
            sa.Column("idem_key", sa.String(length=255), nullable=False),
            sa.Column("route", sa.String(length=80), nullable=False),
            sa.Column("request_hash", sa.String(length=64), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=True),
            sa.Column("response_body", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["org_id"], ["organisations.id"],
                                    name="fk_api_idempotency_keys_org_id_organisations"),
            sa.ForeignKeyConstraint(["key_id"], ["api_keys.id"], name="fk_api_idempotency_keys_key_id_api_keys"),
            sa.PrimaryKeyConstraint("id", name="pk_api_idempotency_keys"),
            sa.UniqueConstraint("org_id", "key_id", "idem_key", name="uq_api_idempotency_key"),
        )
        op.create_index("ix_api_idempotency_keys_org_id", "api_idempotency_keys", ["org_id"])
        op.create_index("ix_api_idempotency_keys_key_id", "api_idempotency_keys", ["key_id"])
        op.create_index("ix_api_idempotency_keys_created_at", "api_idempotency_keys", ["created_at"])


def _whh1() -> None:
    bind = op.get_bind()
    ran_before = "kind" in _cols("webhook_endpoints")
    secret = _cols("webhook_endpoints")["secret"]
    if getattr(secret["type"], "length", None) and secret["type"].length < 500:
        with op.batch_alter_table("webhook_endpoints") as b:
            b.alter_column("secret", existing_type=secret["type"], type_=sa.String(length=500),
                           existing_nullable=False)
    _add_missing("webhook_endpoints", [
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="endpoint"),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("previous_secret", sa.String(length=500), nullable=True),
        sa.Column("previous_secret_expires_at", sa.DateTime(), nullable=True),
        sa.Column("failing_since", sa.DateTime(), nullable=True),
        sa.Column("disabled_reason", sa.String(length=200), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    ])
    if "ix_webhook_endpoints_source_id" not in _indexes("webhook_endpoints"):
        op.create_index("ix_webhook_endpoints_source_id", "webhook_endpoints", ["source_id"])
    _add_missing("webhook_deliveries", [sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
                                        sa.Column("claimed_at", sa.DateTime(), nullable=True)])
    if "ix_webhook_deliveries_next_attempt_at" not in _indexes("webhook_deliveries"):
        op.create_index("ix_webhook_deliveries_next_attempt_at", "webhook_deliveries", ["next_attempt_at"])

    if "uq_calls_org_provider_external" not in _indexes("calls"):
        # Later duplicates of a provider's call id lose the id (rows are kept), as in whh1.
        dups = bind.execute(sa.text(
            "SELECT org_id, provider, external_id FROM calls WHERE external_id IS NOT NULL "
            "GROUP BY org_id, provider, external_id HAVING COUNT(*) > 1")).fetchall()
        for org_id, provider, external_id in dups:
            ids = [r[0] for r in bind.execute(sa.text(
                "SELECT id FROM calls WHERE org_id = :o AND provider = :p AND external_id = :e "
                "ORDER BY created_at, id"), {"o": org_id, "p": provider, "e": external_id}).fetchall()]
            for cid in ids[1:]:
                bind.execute(sa.text("UPDATE calls SET external_id = NULL WHERE id = :i"), {"i": cid})
        op.create_index("uq_calls_org_provider_external", "calls", ["org_id", "provider", "external_id"],
                        unique=True)

    if ran_before:
        return
    # The data steps of whh1, for a database that never ran it.
    for (oid,) in bind.execute(sa.text(
            "SELECT id FROM organisations WHERE webhook_secret IS NULL OR webhook_secret = ''")).fetchall():
        bind.execute(sa.text("UPDATE organisations SET webhook_secret = :s WHERE id = :i"),
                     {"s": secrets.token_urlsafe(24), "i": oid})
    try:
        from backend.services import crypto
        if crypto.is_active():
            for eid, sec in bind.execute(sa.text("SELECT id, secret FROM webhook_endpoints")).fetchall():
                if sec and not str(sec).startswith("enc:"):
                    bind.execute(sa.text("UPDATE webhook_endpoints SET secret = :s WHERE id = :i"),
                                 {"s": crypto.encrypt(sec), "i": eid})
    except ImportError:
        pass


def _parity_on_main() -> None:
    """The other direction: a database upgraded on the launch line counts the parity line's idem01,
    rt144a1 and taxbf01 as applied (parity put ds144a1 after taxbf01). idem01's table is gc144g1's;
    the other two are written to run again safely, so they run here on every database."""
    import importlib.util
    import os
    here = os.path.dirname(__file__)
    spec = importlib.util.spec_from_file_location("_rt144a1", os.path.join(here, "rt144a1_tighten_technician_roles.py"))
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    rt.upgrade()     # skips roles it already tightened (its backup table records them)

    from backend.services import tax_backfill
    if not sa.inspect(op.get_bind()).has_table("tax_stack_backfill_log"):
        op.create_table(
            "tax_stack_backfill_log",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("org_id", sa.String(), nullable=False),
            sa.Column("previous_tax_ids", sa.Text(), nullable=False),
            sa.Column("tax_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_tax_stack_backfill_log_org_id", "tax_stack_backfill_log", ["org_id"])
    tax_backfill.backfill(op.get_bind())     # leaves a shop alone once it charges a real tax


def _admin_two_step() -> None:
    """Platform admins who turned 2-step on with the parity line's aud6m01 (mfa_enabled) keep it:
    the launch line counts an admin as enrolled by mfa_enrolled_at. Same secret encryption."""
    if "mfa_enabled" not in _cols("platform_admins"):
        return
    op.get_bind().execute(sa.text(
        "UPDATE platform_admins SET mfa_enrolled_at = CURRENT_TIMESTAMP "
        "WHERE mfa_enabled = :t AND mfa_secret IS NOT NULL AND mfa_enrolled_at IS NULL"), {"t": True})


def upgrade() -> None:
    _ojj1()
    _apih1()
    _whh1()
    _parity_on_main()
    _admin_two_step()


def downgrade() -> None:
    # Joining heads; the catch-up only adds what the launch line's own revisions own and drop.
    pass
