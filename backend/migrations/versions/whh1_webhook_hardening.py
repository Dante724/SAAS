"""Webhook hardening (B1-B16).

- webhook_endpoints: secret widened for encrypted `whsec_` secrets (existing secrets are kept, so
  subscribers keep verifying, and encrypted when TOKEN_ENCRYPTION_KEY is set); previous_secret /
  previous_secret_expires_at for rotation overlap; description; kind + source_id (hidden targets
  of automation-rule webhook actions); failing_since / disabled_reason for auto-disable;
  deleted_at for soft delete.
- webhook_deliveries: next_attempt_at (backoff schedule) and claimed_at (reclaim stuck rows).
- calls: unique (org_id, provider, external_id) so the generic call webhook can upsert. Older
  duplicates keep their first row's external_id; later duplicates get NULL (rows are kept).
- organisations: backfill webhook_secret for orgs created before Build 110, so inbound token
  webhooks can fail closed.

Revision ID: whh1
Revises: apih1
"""
import secrets

import sqlalchemy as sa
from alembic import op

revision = "whh1"
down_revision = "apih1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("webhook_endpoints") as b:
        b.alter_column("secret", existing_type=sa.String(length=64), type_=sa.String(length=500),
                       existing_nullable=False)
        b.add_column(sa.Column("description", sa.String(length=200), nullable=True))
        b.add_column(sa.Column("kind", sa.String(length=20), nullable=False, server_default="endpoint"))
        b.add_column(sa.Column("source_id", sa.String(), nullable=True))
        b.add_column(sa.Column("previous_secret", sa.String(length=500), nullable=True))
        b.add_column(sa.Column("previous_secret_expires_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("failing_since", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("disabled_reason", sa.String(length=200), nullable=True))
        b.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_webhook_endpoints_source_id", "webhook_endpoints", ["source_id"])

    with op.batch_alter_table("webhook_deliveries") as b:
        b.add_column(sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("claimed_at", sa.DateTime(), nullable=True))
    op.create_index("ix_webhook_deliveries_next_attempt_at", "webhook_deliveries", ["next_attempt_at"])

    bind = op.get_bind()
    # Failed rows the old sweep would still have retried: due now under the new schedule.
    bind.execute(sa.text("UPDATE webhook_deliveries SET next_attempt_at = CURRENT_TIMESTAMP "
                         "WHERE status IN ('failed', 'pending') AND attempts < 5"))

    # calls: clear external_id on later duplicates, then enforce uniqueness.
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

    # organisations: every org gets an inbound webhook token.
    for (oid,) in bind.execute(sa.text(
            "SELECT id FROM organisations WHERE webhook_secret IS NULL OR webhook_secret = ''")).fetchall():
        bind.execute(sa.text("UPDATE organisations SET webhook_secret = :s WHERE id = :i"),
                     {"s": secrets.token_urlsafe(24), "i": oid})

    # Endpoint secrets: encrypt at rest when a key is configured (values are unchanged, so
    # existing subscribers keep verifying). Without a key they stay as they are, like every
    # other encrypted column in dev.
    try:
        from backend.services import crypto
        if crypto.is_active():
            rows = bind.execute(sa.text("SELECT id, secret FROM webhook_endpoints")).fetchall()
            for eid, sec in rows:
                if sec and not str(sec).startswith("enc:"):
                    bind.execute(sa.text("UPDATE webhook_endpoints SET secret = :s WHERE id = :i"),
                                 {"s": crypto.encrypt(sec), "i": eid})
    except ImportError:
        pass


def downgrade() -> None:
    op.drop_index("uq_calls_org_provider_external", table_name="calls")
    op.drop_index("ix_webhook_deliveries_next_attempt_at", table_name="webhook_deliveries")
    with op.batch_alter_table("webhook_deliveries") as b:
        b.drop_column("claimed_at")
        b.drop_column("next_attempt_at")
    op.drop_index("ix_webhook_endpoints_source_id", table_name="webhook_endpoints")
    with op.batch_alter_table("webhook_endpoints") as b:
        b.drop_column("deleted_at")
        b.drop_column("disabled_reason")
        b.drop_column("failing_since")
        b.drop_column("previous_secret_expires_at")
        b.drop_column("previous_secret")
        b.drop_column("source_id")
        b.drop_column("kind")
        b.drop_column("description")
        # Encrypted secrets are longer than 64; widen-only on the way down.
