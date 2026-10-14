"""144ix — feedback_settings.google_review_url (public review CTA)

Revision ID: c7e9g1i3k5m7
Revises: b6d8f0h2j4l6
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa

revision = "c7e9g1i3k5m7"
down_revision = "b6d8f0h2j4l6"
branch_labels = None
depends_on = None


def _has_col(bind, table, col):
    insp = sa.inspect(bind)
    try:
        return col in [c["name"] for c in insp.get_columns(table)]
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()
    if not _has_col(bind, "feedback_settings", "google_review_url"):
        op.add_column("feedback_settings", sa.Column("google_review_url", sa.String(length=400), nullable=True))


def downgrade():
    bind = op.get_bind()
    if _has_col(bind, "feedback_settings", "google_review_url"):
        op.drop_column("feedback_settings", "google_review_url")
