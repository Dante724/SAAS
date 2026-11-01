"""Build 144as — NotificationLog.rule_id FK gets ON DELETE SET NULL.

A notification rule that has already fired owns NotificationLog rows via rule_id. Deleting such a
rule (rule-builder "Delete") raised a FK violation under enforced FKs (Postgres always; SQLite now
that the pragma is on). SET NULL keeps the log history and just drops the rule link.

Revision ID: fk144as01
Revises: nm144ar01
"""
from alembic import op

revision = "fk144as01"
down_revision = "nm144ar01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Prod is Postgres, where the FK is named and we swap it to ON DELETE SET NULL. On SQLite the
    # old FK is unnamed, so a batch drop-by-name raises "No such constraint" at flush — and SQLite
    # deployments build the schema from create_all (models already carry ondelete="SET NULL"), so
    # the migration has nothing to do there. Hence: real swap on Postgres, no-op elsewhere.
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # The FK was created by 3b1a6ec7c56d under the naming-convention name
    # (fk_notification_logs_rule_id_notification_rules), not Postgres's default
    # notification_logs_rule_id_fkey — so drop whatever FK actually covers rule_id.
    import sqlalchemy as sa
    for fk in sa.inspect(bind).get_foreign_keys("notification_logs"):
        if fk.get("constrained_columns") == ["rule_id"] and fk.get("name"):
            op.drop_constraint(fk["name"], "notification_logs", type_="foreignkey")
    op.create_foreign_key("fk_notification_logs_rule_id", "notification_logs", "notification_rules",
                          ["rule_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.drop_constraint("fk_notification_logs_rule_id", "notification_logs", type_="foreignkey")
    op.create_foreign_key("notification_logs_rule_id_fkey", "notification_logs", "notification_rules",
                          ["rule_id"], ["id"])
