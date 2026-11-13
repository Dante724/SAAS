"""Exact numbers for money, rates, percentages, points and quantities (audit DB-08 / FIN-14).

``Exact`` is a ``Decimal`` that tolerates a ``float`` on the other side of an operator. Plain
``Decimal`` raises ``TypeError`` on ``Decimal + float`` and compares ``Decimal("30.01") < 30.01`` by
the float's binary value, so flipping hundreds of columns to plain Decimal would turn every
remaining float expression in the app into a 500. ``Exact`` lifts a float operand through its
shortest decimal text (``repr``), exactly as ``money.dec`` does, so ``Exact("0.10") + 0.2`` is
``0.30`` and ``Exact("30.01") == 30.01``. Results stay ``Exact``. ``round()`` is half-up, the money
convention, not Python's banker's rounding.

``ExactNumeric(p, s)`` is the column type: ``NUMERIC(p, s)`` in the database (same DDL as
``Numeric``), ``Exact`` in Python. A float written to it is lifted the same way, so the stored
value is the float's decimal text rounded by the database to the column's scale.
"""
import re
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Numeric
from sqlalchemy.types import TypeDecorator


_FIXED = re.compile(r"^(?:.?[<>=^])?[+\- ]?#?0?\d*[,_]?\.(?P<prec>\d+)[fF]$")


def _lift(o):
    if isinstance(o, Decimal):
        return o
    if isinstance(o, float):
        return Decimal(repr(o))
    if isinstance(o, int):          # includes bool
        return o
    return NotImplemented


class Exact(Decimal):
    __slots__ = ()

    def __new__(cls, value="0", context=None):
        if isinstance(value, float):
            value = repr(value)
        return Decimal.__new__(cls, value, context)

    def __repr__(self):
        return f"Exact('{Decimal.__str__(self)}')"

    # ── text: the same as the float the column used to return ───────────────
    # Receipts, CSV/XLSX exports, messages and f-strings were written against floats: str(2.0) is
    # "2.0" and f"{2.0:g}" is "2", where a NUMERIC(14,4) Decimal would print "2.0000". So text and
    # float-style formats render through the float when it carries the exact same value (every
    # stored money/qty value does), and fixed-point formats ("{:.2f}") round half-up exactly.
    def _as_float(self):
        f = float(self)
        return f if Decimal(repr(f)) == Decimal.__pos__(self) else None

    def __str__(self):
        f = self._as_float()
        return repr(f) if f is not None else Decimal.__str__(self)

    def __format__(self, spec, *args):
        m = _FIXED.match(spec or "")
        if m:                                     # "{:.2f}", "{:,.2f}", "{:>10.3f}"
            q = Decimal.quantize(self, Decimal(1).scaleb(-int(m.group("prec"))), rounding=ROUND_HALF_UP)
            return Decimal.__format__(q, spec)
        if not spec or spec[-1] in "gGn" or spec[-1] not in "eEfF%":
            f = self._as_float()
            if f is not None:
                return format(f, spec)
        return Decimal.__format__(self, spec)

    # ── comparisons: lift floats so 30.01 == Exact("30.01") ─────────────────
    def __eq__(self, other):
        o = _lift(other)
        return NotImplemented if o is NotImplemented else Decimal.__eq__(self, o)

    def __ne__(self, other):
        o = _lift(other)
        return NotImplemented if o is NotImplemented else Decimal.__ne__(self, o)

    def __lt__(self, other):
        o = _lift(other)
        return NotImplemented if o is NotImplemented else Decimal.__lt__(self, o)

    def __le__(self, other):
        o = _lift(other)
        return NotImplemented if o is NotImplemented else Decimal.__le__(self, o)

    def __gt__(self, other):
        o = _lift(other)
        return NotImplemented if o is NotImplemented else Decimal.__gt__(self, o)

    def __ge__(self, other):
        o = _lift(other)
        return NotImplemented if o is NotImplemented else Decimal.__ge__(self, o)

    __hash__ = Decimal.__hash__

    # ── unary ────────────────────────────────────────────────────────────────
    def __neg__(self, context=None):
        return Exact(Decimal.__neg__(self))

    def __pos__(self, context=None):
        return Exact(Decimal.__pos__(self))

    def __abs__(self, round=True, context=None):
        return Exact(Decimal.__abs__(self))

    def __round__(self, n=None):
        if n is None:
            return int(self.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        return Exact(self.quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP))


def _binary(name):
    base = getattr(Decimal, name)

    def op(self, other):
        o = _lift(other)
        if o is NotImplemented:
            return NotImplemented
        r = base(self, o)
        return Exact(r) if isinstance(r, Decimal) and not isinstance(r, Exact) else r
    op.__name__ = name
    return op


for _n in ("add", "sub", "mul", "truediv", "floordiv", "mod", "pow"):
    setattr(Exact, f"__{_n}__", _binary(f"__{_n}__"))
    setattr(Exact, f"__r{_n}__", _binary(f"__r{_n}__"))


class ExactNumeric(TypeDecorator):
    """NUMERIC(precision, scale) that reads back as ``Exact``."""
    impl = Numeric
    cache_ok = True

    def __init__(self, precision=12, scale=2):
        super().__init__(precision=precision, scale=scale, asdecimal=True)

    def process_bind_param(self, value, dialect):
        if isinstance(value, float):
            return Decimal(repr(value))
        if isinstance(value, Exact):            # drivers may str() a Decimal: hand them the exact one
            return Decimal(Decimal.__str__(value))
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, Exact) else Exact(value)

    @property
    def python_type(self):
        return Exact


# Readable aliases for model definitions.
Money = ExactNumeric
