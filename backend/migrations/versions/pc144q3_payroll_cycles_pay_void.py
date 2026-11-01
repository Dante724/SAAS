"""Payroll bookkeeping: commission cycles, payout cash leg, voidable runs.

- employee_balance_ledger.accrual_key: groups a commission with its claw-backs so a reopened
  and re-closed order pays again, and a refund followed by an archive reverses only what is
  still open. Backfilled: a commission's group is its own idempotency key; a legacy claw-back
  ("clawback:<id>" / "del-clawback:<id>") joins the group of the entry it reversed.
- payroll_runs.account_id / cash_txn_id: the cashbox a run was paid from and the expense
  transaction posted there.
- payroll_runs.voided_at / voided_by_id / void_reason: a paid or accrued run can be voided.

Revision ID: pc144q3
Revises: ow144q2
"""
import sqlalchemy as sa
from alembic import op

revision = "pc144q3"
down_revision = "ow144q2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("employee_balance_ledger") as b:
        b.add_column(sa.Column("accrual_key", sa.String(length=120), nullable=True))
        b.create_index("ix_employee_balance_ledger_accrual_key", ["accrual_key"])
    with op.batch_alter_table("payroll_runs") as b:
        b.add_column(sa.Column("account_id", sa.String(), nullable=True))
        b.add_column(sa.Column("cash_txn_id", sa.String(), nullable=True))
        b.add_column(sa.Column("voided_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("voided_by_id", sa.String(), nullable=True))
        b.add_column(sa.Column("void_reason", sa.String(length=300), nullable=True))
        b.create_foreign_key("fk_payroll_runs_account_id_accounts", "accounts", ["account_id"], ["id"])
        b.create_foreign_key("fk_payroll_runs_voided_by_id_employees", "employees", ["voided_by_id"], ["id"])

    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE employee_balance_ledger SET accrual_key = idempotency_key "
        "WHERE entry_type = 'commission' AND accrual_key IS NULL AND idempotency_key IS NOT NULL"))
    keys = dict(conn.execute(sa.text(
        "SELECT id, idempotency_key FROM employee_balance_ledger WHERE entry_type = 'commission'")).fetchall())
    claws = conn.execute(sa.text(
        "SELECT id, idempotency_key FROM employee_balance_ledger "
        "WHERE entry_type = 'clawback' AND accrual_key IS NULL")).fetchall()
    for cid, key in claws:
        if not key or ":" not in key:
            continue
        prefix, ref = key.split(":", 1)
        if prefix in ("clawback", "del-clawback") and keys.get(ref):
            conn.execute(sa.text("UPDATE employee_balance_ledger SET accrual_key = :k WHERE id = :i"),
                         {"k": keys[ref], "i": cid})


def downgrade() -> None:
    with op.batch_alter_table("payroll_runs") as b:
        b.drop_constraint("fk_payroll_runs_voided_by_id_employees", type_="foreignkey")
        b.drop_constraint("fk_payroll_runs_account_id_accounts", type_="foreignkey")
        for col in ("void_reason", "voided_by_id", "voided_at", "cash_txn_id", "account_id"):
            b.drop_column(col)
    with op.batch_alter_table("employee_balance_ledger") as b:
        b.drop_index("ix_employee_balance_ledger_accrual_key")
        b.drop_column("accrual_key")
