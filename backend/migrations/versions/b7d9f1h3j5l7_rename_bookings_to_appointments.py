"""rename bookings -> appointments (table, Org booking_* cols, index, stored rule values)

Terminology change: "Bookings" is renamed to "Appointments" app-wide (clearer for
US/EU/ME repair shops; the ES label also moves Reservas -> Citas). Pure rename —
no data loss. The public booking URL was intentionally hard-renamed (owner's call);
no backward-compat alias is kept.

Revision ID: b7d9f1h3j5l7
Revises: hr144dv01
"""
from alembic import op
import sqlalchemy as sa

revision = "b7d9f1h3j5l7"
down_revision = "hr144dv01"
branch_labels = None
depends_on = None

_ORG_COLS = [
    ("booking_enabled", "appointment_enabled"),
    ("booking_time_slot", "appointment_time_slot"),
    ("booking_allow_multiple", "appointment_allow_multiple"),
    ("booking_consider_orders", "appointment_consider_orders"),
    ("booking_lead_min", "appointment_lead_min"),
    ("booking_window_days", "appointment_window_days"),
    ("booking_theme", "appointment_theme"),
]


def upgrade():
    # 1) table rename
    op.rename_table("bookings", "appointments")

    # 2) partial-unique index: drop old, recreate under the new name
    #    (status value 'booked' is unchanged — it is an enum, not the term "booking")
    try:
        op.drop_index("uq_booking_emp_slot", table_name="appointments")
    except Exception:
        pass
    op.create_index(
        "uq_appointment_emp_slot", "appointments",
        ["org_id", "employee_id", "scheduled_for"], unique=True,
        sqlite_where=sa.text("status = 'booked'"),
        postgresql_where=sa.text("status = 'booked'"),
    )

    # 3) Org config columns booking_* -> appointment_*
    with op.batch_alter_table("organisations") as b:
        for old, new in _ORG_COLS:
            b.alter_column(old, new_column_name=new)

    # 4) stored rule values that encode the old term
    #    notification_rules has `module` (values order|lead|booking|…), unprefixed `event`.
    #    automation_rules has only `event` (e.g. 'booking_created').
    op.execute("UPDATE notification_rules SET module='appointment' WHERE module='booking'")
    op.execute(
        "UPDATE automation_rules SET event=REPLACE(event,'booking','appointment') "
        "WHERE event LIKE '%booking%'"
    )


def downgrade():
    op.execute("UPDATE notification_rules SET module='booking' WHERE module='appointment'")
    op.execute(
        "UPDATE automation_rules SET event=REPLACE(event,'appointment','booking') "
        "WHERE event LIKE '%appointment%'"
    )
    with op.batch_alter_table("organisations") as b:
        for old, new in _ORG_COLS:
            b.alter_column(new, new_column_name=old)
    try:
        op.drop_index("uq_appointment_emp_slot", table_name="appointments")
    except Exception:
        pass
    op.create_index(
        "uq_booking_emp_slot", "appointments",
        ["org_id", "employee_id", "scheduled_for"], unique=True,
        sqlite_where=sa.text("status = 'booked'"),
        postgresql_where=sa.text("status = 'booked'"),
    )
    op.rename_table("appointments", "bookings")
