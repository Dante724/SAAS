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


# ISO-4217 currencies whose smallest unit isn't 1/100 (as payment processors count them).
ZERO_DECIMAL = {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx",
                "vnd", "vuv", "xaf", "xof", "xpf"}
THREE_DECIMAL = {"bhd", "jod", "kwd", "omr", "tnd"}


def minor_units(amount, currency: str) -> int:
    """Amount in the currency's smallest unit for gateway APIs (cents, yen, fils…). Multiplying
    every currency by 100 overcharged JPY/KRW 100x and undercharged KWD/BHD 10x."""
    c = (currency or "").lower()
    exp = 0 if c in ZERO_DECIMAL else 3 if c in THREE_DECIMAL else 2
    return int((Decimal(str(amount)) * (10 ** exp)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


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


async def add_to_column(db, obj, attr: str, delta, *, round_to=q2) -> float:
    """Atomically add `delta` to a running balance column and return the new value.

    `obj.balance = obj.balance + x` is read-modify-write: two requests touching the same till,
    client or supplier concurrently both read the old value and one update is lost. A single
    `UPDATE … SET col = col + :delta` serialises on the row (Postgres row lock; SQLite writes are
    serial), then the object is refreshed so callers see the committed-to value. Objects not yet
    in the database are just updated in memory. Pass round_to=None for non-money columns
    (stock quantities)."""
    from sqlalchemy import func, inspect, update
    st = inspect(obj)
    rnd = round_to or (lambda v: v)
    if st.pending or st.transient:
        setattr(obj, attr, rnd(float(getattr(obj, attr) or 0) + float(delta)))
        return getattr(obj, attr)
    model = type(obj)
    col = getattr(model, attr)
    await db.execute(update(model).where(model.id == obj.id)
                     .values({attr: func.coalesce(col, 0) + rnd(delta)})
                     .execution_options(synchronize_session=False))
    await db.refresh(obj, attribute_names=[attr])
    return getattr(obj, attr)


async def claim_status(db, obj, expected, new, *, attr: str = "status", where=(), **values) -> bool:
    """Move `obj.<attr>` from `expected` to `new` only if nobody else already has.

    Read-check-write lets two identical requests (a double-clicked delete, a retried cancel) both
    see the old status and both apply their side effects. A conditional
    `UPDATE … WHERE id = :id AND status = :expected` takes the row lock (Postgres) or the write
    lock (SQLite); the loser re-reads the changed row and updates nothing. Call it BEFORE the
    side effects and refuse when it returns False. `expected` may be a tuple of statuses; `where`
    adds conditions that must still hold (e.g. "not paid"); extra keyword values are written in
    the same statement."""
    from sqlalchemy import update
    model = type(obj)
    col = getattr(model, attr)
    cond = col.in_(expected) if isinstance(expected, (tuple, list, set)) else col == expected
    res = await db.execute(update(model).where(model.id == obj.id, cond, *where)
                           .values({attr: new, **values})
                           .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        return False
    await db.refresh(obj, attribute_names=[attr, *values])
    return True
