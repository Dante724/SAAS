"""H2 Phase 1 — the canonical money module quantizes with round-half-up (the money convention),
not Python's banker's rounding, and sums exactly instead of accumulating float noise."""
from decimal import Decimal

from backend.services.money import d2, msum, q2, q4


def test_half_up_not_bankers():
    assert q2(2.675) == 2.68          # round() gives 2.67 (half-to-even + float noise)
    assert q2(1.005) == 1.01          # classic float-repr penny bug
    assert q2(2.665) == 2.67
    assert q2(0.125) == 0.13


def test_exact_sum():
    assert msum([0.1, 0.2]) == 0.3    # float 0.1+0.2 == 0.30000000000000004
    assert msum([10.005, 0.005]) == 10.01
    assert msum([]) == 0.0
    assert msum([None, 1.5, None]) == 1.5


def test_d2_is_exact_decimal():
    assert d2(2.675) == Decimal("2.68")
    assert isinstance(d2(1), Decimal)


def test_q4_cost_precision():
    assert q4(1.23455) == 1.2346
    assert q4(1.23454) == 1.2345
