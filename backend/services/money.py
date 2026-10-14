"""Canonical money arithmetic (H2 Phase 1).

Money is STORED exactly — every money column is ``Numeric(12,2, asdecimal=False)`` (build 144aq),
so the database never holds a binary-float approximation. The remaining exposure is *compute*:
Python's built-in ``round()`` uses banker's rounding (round-half-to-even), so ``round(2.675, 2)``
is ``2.67`` and ``round(0.5, 0)`` is ``0`` — neither is what a shop expects on a receipt. Float
representation also makes sums like ``0.1 + 0.2`` land at ``0.30000000000000004``.

This module is the one place money gets quantized. It converts through ``Decimal(str(x))`` (so the
float's decimal *text*, not its binary noise, is what rounds) and applies ROUND_HALF_UP — the
money convention. Returns plain floats so it drops into the existing float-typed code with zero
ripple; callers that want exact Decimal end-to-end (the eventual Phase 3) can use ``d2`` instead.

Rollout (see SPEC_money_decimal.md): Phase 1 ships this module + the highest-value boundaries
(invoice/line totals, tax, proration). Phases 2–3 migrate the remaining ~330 ``round(...)`` sites
and, finally, carry Decimal through the hot compute paths unconverted.
"""
from decimal import Decimal, ROUND_HALF_UP

_CENT = Decimal("0.01")
_TT = Decimal("0.0001")


def d2(x) -> Decimal:
    """Quantize to 2dp as an exact Decimal, round-half-up. Accepts float/int/str/Decimal."""
    return Decimal(str(x)).quantize(_CENT, rounding=ROUND_HALF_UP)


def q2(x) -> float:
    """Money rounded to 2dp (round-half-up), returned as float for drop-in use."""
    return float(d2(x))


def q4(x) -> float:
    """Unit-cost precision (4dp, round-half-up) — mirrors avg_cost's Numeric(12,4)."""
    return float(Decimal(str(x)).quantize(_TT, rounding=ROUND_HALF_UP))


def msum(values) -> float:
    """Exact sum of money values (each coerced through Decimal), quantized to 2dp."""
    total = Decimal("0")
    for v in values:
        total += Decimal(str(v or 0))
    return float(total.quantize(_CENT, rounding=ROUND_HALF_UP))


def demo() -> None:
    # Banker's rounding gives the "wrong" penny for money; q2 is half-up.
    assert round(2.675, 2) == 2.67          # built-in: round-half-to-even + float noise
    assert q2(2.675) == 2.68                # money: round-half-up
    assert round(0.5) == 0                  # built-in half-to-even
    assert q2(0.5) == 0.5                    # (no rounding needed at 2dp, sanity)
    assert q2(1.005) == 1.01                # the classic float-repr penny bug, fixed
    assert msum([0.1, 0.2]) == 0.3          # 0.1+0.2 == 0.30000000000000004 in float
    assert msum([10.005, 0.005]) == 10.01
    assert q4(1.23455) == 1.2346
    print("money.demo OK")


if __name__ == "__main__":
    demo()
