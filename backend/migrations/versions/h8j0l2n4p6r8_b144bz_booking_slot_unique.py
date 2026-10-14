"""b144bz — DB guard against the booking concurrent-create race

Partial unique index: one 'booked' appointment per (org, employee, start time).
Catches two clients grabbing the same offered slot in the same instant.

Revision ID: h8j0l2n4p6r8
Revises: g7i9k1m3o5q7
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "h8j0l2n4p6r8"
down_revision = "g7i9k1m3o5q7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "uq_booking_emp_slot", "bookings",
        ["org_id", "employee_id", "scheduled_for"], unique=True,
        sqlite_where=sa.text("status = 'booked'"),
        postgresql_where=sa.text("status = 'booked'"))


def downgrade():
    op.drop_index("uq_booking_emp_slot", table_name="bookings")
