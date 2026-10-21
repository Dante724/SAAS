"""Team C (C3/C4) — chat inbox statuses, spam folder, provider message ids.

chat_threads gains status (new|reply_needed|replied|completed), last_inbound_at,
last_outbound_at, is_spam and spam_at, indexed for the status tabs. chat_messages gains
external_id so provider webhooks (Twilio, Meta, the generic inbound hook) are idempotent.

Existing threads are backfilled from their messages: the latest inbound / outbound times,
and a status: never answered → new, customer wrote last → reply_needed, shop wrote last → replied
(the 24h job then completes the old replied ones).

Roles: the new can_manage_chats permission (status / spam changes) is on in every built-in
role template, so existing explicit roles get it too instead of silently losing the action.

Revision ID: opc1
Revises: ope1
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "opc1"
down_revision = "ope1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_threads") as b:
        b.add_column(sa.Column("status", sa.String(length=16), nullable=False, server_default="new"))
        b.add_column(sa.Column("last_inbound_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("last_outbound_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("is_spam", sa.Boolean(), nullable=False, server_default=sa.text("false")))
        b.add_column(sa.Column("spam_at", sa.DateTime(), nullable=True))
        # Email addresses and Meta sender ids land here too now (C4), not just phone numbers.
        b.alter_column("contact_phone", existing_type=sa.String(length=40), type_=sa.String(length=160),
                       existing_nullable=True)
    op.create_index("ix_chat_threads_org_status", "chat_threads", ["org_id", "is_spam", "status"])

    with op.batch_alter_table("chat_messages") as b:
        b.add_column(sa.Column("external_id", sa.String(length=160), nullable=True))
    op.create_index("ix_chat_messages_external_id", "chat_messages", ["external_id"])
    op.create_index("uq_chat_messages_thread_external", "chat_messages", ["thread_id", "external_id"], unique=True)

    # Backfill from history. System notes (STOP/START confirmations) are not replies.
    op.execute(sa.text(
        "UPDATE chat_threads SET "
        "last_inbound_at = (SELECT max(m.created_at) FROM chat_messages m "
        "  WHERE m.thread_id = chat_threads.id AND m.direction = 'in'), "
        "last_outbound_at = (SELECT max(m.created_at) FROM chat_messages m "
        "  WHERE m.thread_id = chat_threads.id AND m.direction = 'out' AND m.status <> 'system')"))
    op.execute(sa.text(
        "UPDATE chat_threads SET status = CASE "
        "  WHEN last_outbound_at IS NULL THEN 'new' "
        "  WHEN last_inbound_at IS NOT NULL AND last_inbound_at > last_outbound_at THEN 'reply_needed' "
        "  ELSE 'replied' END"))

    conn = op.get_bind()
    for rid, raw in conn.execute(sa.text("SELECT id, permissions FROM roles WHERE perms_explicit")).fetchall():
        perms = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
        if "can_manage_chats" in (perms or {}):
            continue
        perms = {**(perms or {}), "can_manage_chats": True}
        conn.execute(sa.text("UPDATE roles SET permissions = :p WHERE id = :i").bindparams(
            sa.bindparam("p", type_=sa.JSON())), {"p": perms, "i": rid})


def downgrade() -> None:
    op.drop_index("uq_chat_messages_thread_external", table_name="chat_messages")
    op.drop_index("ix_chat_messages_external_id", table_name="chat_messages")
    with op.batch_alter_table("chat_messages") as b:
        b.drop_column("external_id")
    op.drop_index("ix_chat_threads_org_status", table_name="chat_threads")
    with op.batch_alter_table("chat_threads") as b:
        b.drop_column("spam_at")
        b.alter_column("contact_phone", existing_type=sa.String(length=160), type_=sa.String(length=40),
                       existing_nullable=True)
        b.drop_column("is_spam")
        b.drop_column("last_outbound_at")
        b.drop_column("last_inbound_at")
        b.drop_column("status")
