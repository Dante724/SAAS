"""b133 inventory settings

Revision ID: b7c4e2f1a9d3
Revises: a3f1c9d2b4e7
Create Date: 2026-07-18 16:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c4e2f1a9d3'
down_revision = 'a3f1c9d2b4e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('inv_enable_bins', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('inv_unique_identifier', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('inv_enable_reservations', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('inv_reservation_max_days', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('inv_product_serial_template', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('inv_asset_serial_template', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('inv_default_product_type', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('inv_default_vehicle_type', sa.String(length=30), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('organisations', schema=None) as batch_op:
        batch_op.drop_column('inv_default_vehicle_type')
        batch_op.drop_column('inv_default_product_type')
        batch_op.drop_column('inv_asset_serial_template')
        batch_op.drop_column('inv_product_serial_template')
        batch_op.drop_column('inv_reservation_max_days')
        batch_op.drop_column('inv_enable_reservations')
        batch_op.drop_column('inv_unique_identifier')
        batch_op.drop_column('inv_enable_bins')
