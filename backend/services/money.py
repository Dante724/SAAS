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
from typing import Annotated

from pydantic import Field

from backend.db.exact import Exact  # noqa: F401  (re-exported: the type every money column reads as)

_CENT = Decimal("0.01")
_TT = Decimal("0.0001")


def d2(x) -> Decimal:
    """Quantize to 2dp as an exact Decimal, round-half-up. Accepts float/int/str/Decimal/None (0).

    Always a plain ``Decimal``, also for an ``Exact`` input (whose own ``quantize`` stays Exact):
    documents print it with its two places ("10.50"). Lift it with ``dec``/``Exact`` before mixing
    it with floats."""
    return Decimal.quantize(dec(x), _CENT, rounding=ROUND_HALF_UP)


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
    """Exact sum of money values (each coerced through Decimal), quantized to 2dp. Same fast path
    as ``dsum``."""
    return float(dsum(values))


# ── Decimal end-to-end (audit R4) ────────────────────────────────────────────
# Columns mapped with plain Numeric(12, 2) (no asdecimal=False) read back as Decimal. Decimal and
# float don't mix (Decimal + float raises TypeError; Decimal("0.1") == 0.1 is False), so code that
# touches those columns lifts every operand with dec() and quantizes once with d2(). JSON output is
# unchanged: FastAPI encodes Decimal as a number (10.5, not "10.50"), and the engine's JSON-column
# serializer below does the same.
def dec(x) -> Decimal:
    """Exact Decimal of a money operand — Decimal, float, int, str or None (0). No rounding."""
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x or 0))


def dsum(values) -> Decimal:
    """Exact sum of money values as a plain Decimal quantized to 2dp (round-half-up).

    Fast path (re-review N8): ``total + Exact`` would dispatch to ``Exact.__radd__`` and re-wrap
    every partial sum (about 10x slower than Decimal). Calling ``Decimal.__add__`` directly adds an
    Exact as the Decimal it is, with no wrapper; floats and ints are lifted once each."""
    add = Decimal.__add__
    total = Decimal(0)
    for v in values:
        if isinstance(v, Decimal):
            total = add(total, v)
        elif type(v) is int:
            total = add(total, Decimal(v))
        else:
            total = add(total, dec(v))
    return Decimal.quantize(total, _CENT, rounding=ROUND_HALF_UP)


def round_to_step(x, step) -> float:
    """Cash rounding: nearest multiple of `step` (e.g. 0.05), half-up, as a 2dp float.
    round(x / 0.05) was banker's rounding on a float quotient (10.025 -> 10.00, not 10.05)."""
    s = dec(step)
    return float(d2((dec(x) / s).quantize(Decimal(1), rounding=ROUND_HALF_UP) * s))


def json_default(o):
    """json.dumps(default=…) hook: Decimal money is written as a number, as floats were."""
    if isinstance(o, Decimal):
        if not o.is_finite():
            return None   # NaN/Infinity aren't JSON numbers
        return int(o) if o.as_tuple().exponent >= 0 else float(o)   # same rule as FastAPI
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, default=json_default)


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


MAX_MONEY = 1e9      # a single price/amount above this is a typo, not a repair-shop price

# ── Input bounds for the exact NUMERIC columns (re-review N3) ────────────────
# NUMERIC(p, s) holds magnitudes below 10**(p - s); Postgres refuses more with "numeric field
# overflow" (the old double-precision columns took anything). Request models type these fields
# with the aliases below so a typo (a 13-digit EAN scanned into a qty box) is a 422 that names the
# field. Anything still reaching the database out of range is mapped to a 422 by
# backend.core.db_errors.data_error_handler.
MAX_QTY = 1_000_000_000            # NUMERIC(14,4) quantities: < 1e10
MAX_PCT = 99_999                   # NUMERIC(9,4) percentages and tax rates: < 1e5
MAX_RATE = 1_000_000_000           # NUMERIC(18,8) currency / FX rates: < 1e10
MAX_POINTS = 100_000_000_000       # NUMERIC(14,2) loyalty points: < 1e12

Qty = Annotated[float, Field(ge=-MAX_QTY, le=MAX_QTY)]
Pct = Annotated[float, Field(ge=-MAX_PCT, le=MAX_PCT)]
Rate = Annotated[float, Field(ge=-MAX_RATE, le=MAX_RATE)]
Points = Annotated[float, Field(ge=-MAX_POINTS, le=MAX_POINTS)]


def check_money(v, *, allow_negative: bool = False):
    """Pydantic-validator helper: a finite money amount, not negative, below MAX_MONEY."""
    if v is None:
        return v
    import math
    if not math.isfinite(v) or abs(v) >= MAX_MONEY:
        raise ValueError("Amount is too large")
    if v < 0 and not allow_negative:
        raise ValueError("Amount can't be negative")
    return v


def round_price(x, decimals=2) -> float:
    """A price as the shop writes it: round-half-up to the shop's price_decimals (QA 2026-10-31 B).

    Prices live in 2-decimal columns, so more than 2 decimals can't be stored and 2 is the most
    this rounds to; 0 or 1 round further (whole-number shops). None/garbage → 2."""
    try:
        d = max(0, min(int(decimals), 2))
    except (TypeError, ValueError):
        d = 2
    return float(dec(x).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP))


async def org_price_decimals(db, org_id: str) -> int:
    from sqlalchemy import select
    from backend.models import Organisation
    v = (await db.execute(select(Organisation.price_decimals).where(Organisation.id == org_id))).scalar_one_or_none()
    return 2 if v is None else v
