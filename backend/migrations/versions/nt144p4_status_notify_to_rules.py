"""Phase 4 — retire order_statuses.triggers_notification; notification rules decide.

Data only. For each org, every status with triggers_notification on becomes the per-status
notification rules the old fallback would have sent (only where no rule already fires for
that status, and only when a status-change template exists), then the flag is cleared.
Shared planner: backend/services/legacy_status_notifs.plan. The column stays (unused) so
old clients posting it don't break; downgrade is a no-op (the created rules are ordinary
rules the shop can see and edit).

Revision ID: nt144p4
Revises: mr144p4
"""
import uuid
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "nt144p4"
down_revision = "mr144p4"
branch_labels = None
depends_on = None


def _rows(bind, sql, **kw):
    return [dict(r._mapping) for r in bind.execute(sa.text(sql), kw)]


def upgrade() -> None:
    from backend.services.legacy_status_notifs import plan

    bind = op.get_bind()
    orgs = [r["org_id"] for r in _rows(bind,
            "SELECT DISTINCT org_id FROM order_statuses WHERE triggers_notification = :t", t=True)]
    for org in orgs:
        statuses = _rows(bind, "SELECT key, record_type, is_terminal FROM order_statuses "
                               "WHERE org_id = :o AND triggers_notification = :t AND active = :t", o=org, t=True)
        rules = _rows(bind, "SELECT module, scope, event, trigger_status_key, record_type, active "
                            "FROM notification_rules WHERE org_id = :o", o=org)
        tpls = _rows(bind, "SELECT id, channel, trigger_event, trigger_status, active "
                           "FROM notification_templates WHERE org_id = :o", o=org)
        for spec in plan(statuses, rules, tpls):
            cols = {"id": str(uuid.uuid4()), "org_id": org, "created_at": datetime.utcnow(),
                    "location_id": None, **spec}
            bind.execute(sa.text(
                f"INSERT INTO notification_rules ({', '.join(cols)}) "
                f"VALUES ({', '.join(':' + c for c in cols)})"), cols)
        bind.execute(sa.text("UPDATE order_statuses SET triggers_notification = :f WHERE org_id = :o"),
                     {"f": False, "o": org})


def downgrade() -> None:
    pass
