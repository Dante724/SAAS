"""Join the heads of the parity PRs merged together for launch into one head.

aqp01 (audit quality/perf indexes), aud3r01 (audit Medium/reliability), aul3 (audit Low),
ds144a1 (device sessions), est01 (flow check: estimates), imc01 (import centre),
mdec01 (money as Decimal), mii01 (bins/passport/stocktake) and wsb1 (profit reports) each
change different tables or columns, so order doesn't matter. No schema change here.

Revision ID: mpar01
Revises: aqp01, aud3r01, aul3, ds144a1, est01, imc01, mdec01, mii01, wsb1
"""

revision = "mpar01"
down_revision = ("aqp01", "aud3r01", "aul3", "ds144a1", "est01", "imc01", "mdec01", "mii01", "wsb1")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
