"""Exact numbers for money, rates, percentages, points and quantities (audit DB-08 / FIN-14).

``Exact`` is a ``Decimal`` that tolerates a ``float`` on the other side of an operator. Plain
``Decimal`` raises ``TypeError`` on ``Decimal + float`` and compares ``Decimal("30.01") < 30.01`` by
the float's binary value, so flipping hundreds of columns to plain Decimal would turn every
remaining float expression in the app into a 500. ``Exact`` lifts a float operand through its
shortest decimal text (``repr``), exactly as ``money.dec`` does, so ``Exact("0.10") + 0.2`` is
``0.30`` and ``Exact("30.01") == 30.01``. Results stay ``Exact``. ``round()`` is half-up, the money
convention, not Python's banker's rounding.

Known edges, decided on purpose (re-review N6):

* ``hash``: ``Exact("0.1")`` equals both ``0.1`` and ``Decimal("0.1")``, which aren't equal to each
  other, so it can share a hash with only one of them. It hashes like the float it equals (request
  values are floats), so ``{30.01: x}.get(Exact("30.01"))`` finds ``x``. Don't mix plain
  ``Decimal`` and ``Exact`` keys in one dict or set; values no float holds keep Decimal's hash.
* ``%`` and ``//`` keep Decimal's sign rules: ``Exact(-7) % 3 == -1`` and ``Exact(-7) // 2 == -3``
  (float gives 2 and -4). ``divmod`` agrees with them.
* Decimal methods (``quantize``, ``sqrt``, ``copy_abs``, ``max`` ...) return ``Exact`` and lift a
  float argument, so their results still mix with floats. ``money.d2`` deliberately returns a
  plain ``Decimal`` (documents print it as "10.50").
* ``"%.2f" % x`` goes through ``float()`` and can't be hooked: on a half-cent it rounds like the
  float (``"%.2f" % Exact("2.675") == "2.67"``) where ``f"{x:.2f}"`` rounds half-up ("2.68").
  Format money with f-strings or ``money.d2``.
* NaN (Postgres ``numeric`` allows it) compares like a float NaN: ``<``, ``>``, ``==`` are False
  and nothing raises, where plain Decimal raises ``InvalidOperation``.

``ExactNumeric(p, s)`` is the column type: ``NUMERIC(p, s)`` in the database (same DDL as
``Numeric``), ``Exact`` in Python. A float written to it is lifted the same way, so the stored
value is the float's decimal text rounded by the database to the column's scale.
"""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

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
    # NaN compares like a float NaN (False, never InvalidOperation); see the module notes.
    def __eq__(self, other):
        o = _lift(other)
        if o is NotImplemented:
            return NotImplemented
        try:
            return Decimal.__eq__(self, o)
        except InvalidOperation:                 # signalling NaN
            return False

    def __ne__(self, other):
        r = self.__eq__(other)
        return r if r is NotImplemented else not r

    def __lt__(self, other):
        return _compare(Decimal.__lt__, self, other)

    def __le__(self, other):
        return _compare(Decimal.__le__, self, other)

    def __gt__(self, other):
        return _compare(Decimal.__gt__, self, other)

    def __ge__(self, other):
        return _compare(Decimal.__ge__, self, other)

    def __hash__(self):
        # Equal to the float it round-trips with, so it must hash like it (see the module notes).
        f = self._as_float()
        return hash(f) if f is not None else Decimal.__hash__(self)

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


def _compare(base, self, other):
    o = _lift(other)
    if o is NotImplemented:
        return NotImplemented
    try:
        return base(self, o)
    except InvalidOperation:                     # NaN on either side: False, like float
        return False


def _wrap(r):
    if isinstance(r, Decimal):
        return r if isinstance(r, Exact) else Exact(r)
    if isinstance(r, tuple):                     # divmod
        return tuple(_wrap(x) for x in r)
    return r


def _binary(name):
    base = getattr(Decimal, name)

    def op(self, other, *modulo):                # *modulo: pow(x, y, m)
        o = _lift(other)
        if o is NotImplemented:
            return NotImplemented
        if modulo:
            m = _lift(modulo[0])
            if m is NotImplemented:
                return NotImplemented
            return _wrap(base(self, o, m))
        return _wrap(base(self, o))
    op.__name__ = name
    return op


for _n in ("add", "sub", "mul", "truediv", "floordiv", "mod", "divmod", "pow"):
    setattr(Exact, f"__{_n}__", _binary(f"__{_n}__"))
    setattr(Exact, f"__r{_n}__", _binary(f"__r{_n}__"))


def _method(name):
    base = getattr(Decimal, name)

    def m(self, *args, **kwargs):
        args = tuple(Decimal(repr(a)) if isinstance(a, float) else a for a in args)
        return _wrap(base(self, *args, **kwargs))
    m.__name__ = name
    m.__doc__ = base.__doc__
    return m


# Decimal methods that return a number stay Exact (re-review N6), so the result still mixes with
# floats. Float arguments (x.max(3.5), x.quantize(0.01)) are lifted like operator operands.
for _n in ("quantize", "sqrt", "exp", "ln", "log10", "copy_abs", "copy_negate", "copy_sign",
           "normalize", "max", "min", "max_mag", "min_mag", "fma", "remainder_near", "scaleb",
           "to_integral", "to_integral_value", "to_integral_exact", "next_plus", "next_minus",
           "next_toward", "logb", "rotate", "shift"):
    setattr(Exact, _n, _method(_n))


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
