"""Clear NaN / Infinity already stored in number columns (launch blocker B9).

Before B9 a part saved with cost NaN returned 201 on Postgres, and every inventory list then
returned 500; the only way back was hand-written SQL. This sets each stored NaN/Infinity to NULL
(0 where the column is NOT NULL) so the record shows up again and the shop can enter the real
value. See backend/core/finite_numbers.py. Downgrade changes nothing: NaN was never a real value.

Revision ID: b9nan01
Revises: trg01
"""
import logging

from alembic import op

revision = "b9nan01"
down_revision = "trg01"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.b9nan01")


def upgrade() -> None:
    from backend.core.finite_numbers import clear_non_finite
    n = clear_non_finite(op.get_bind())
    log.info("b9nan01: cleared NaN/Infinity in %s row(s)", n)


def downgrade() -> None:
    pass
