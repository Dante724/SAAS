"""Re-review of PR #222 (audit DB-08 / FIN-14): the decimal-money leftovers.

* N3: NUMERIC(14,4) quantities, (9,4) percentages, (18,8) rates and (14,2) points overflow on
  Postgres where the old double-precision columns took any value. Every input that writes one is
  bounded in its pydantic model, and a database DataError that still gets through is a 422 with a
  sentence, never a 500.
* API shape: pydantic v2 writes a Decimal inside a declared model as a JSON *string*. The app's
  dict responses go through FastAPI's jsonable_encoder (numbers); a guard fails the first response
  model that would carry money as text.
* N6: ``Exact`` keeps hash consistent with the float it equals, supports divmod, keeps Decimal
  methods inside ``Exact``, and NaN compares like a float NaN instead of raising.
* N8: ``money.dsum`` / ``msum`` sum without per-element ``Exact`` dispatch.
* N7: on Postgres the cable scenario is exact in storage, not just after the read rounds it.
"""
import os
import time
import uuid
from decimal import Decimal, InvalidOperation

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text

from backend.services.money import Exact, d2, dsum, msum

pytestmark = pytest.mark.asyncio

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
pg_only = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="exact NUMERIC storage needs Postgres")


# ── N3: bounded inputs ───────────────────────────────────────────────────────
# Largest magnitude each column kind can hold (exclusive): NUMERIC(p, s) holds < 10**(p - s).
_QTY, _PCT, _RATE, _POINTS = 10 ** 10, 10 ** 5, 10 ** 10, 10 ** 12
# Fields whose name doesn't say what they store.
_OVERRIDES = {("backend.routers.catalog", "TaxIn", "rate"): _PCT}     # taxes.rate is NUMERIC(9,4)


def _limit_for(name: str):
    if name in ("qty", "set_qty") or name.startswith("qty_") or name.endswith("_qty"):
        return _QTY
    if (name.endswith("pct") or name.endswith("percent") or name.startswith("commission_")
            or name in ("work_share", "tax_rate")):
        return _PCT
    if name in ("rate", "fx_rate"):
        return _RATE
    if name.endswith("points"):
        return _POINTS
    return None


def _router_models():
    import main  # noqa: F401  (imports every router)
    seen, stack = set(), list(BaseModel.__subclasses__())
    while stack:
        m = stack.pop()
        if m in seen:
            continue
        seen.add(m)
        stack.extend(m.__subclasses__())
        if m.__module__.startswith("backend.routers") and not m.__name__.endswith(("Out", "Response")):
            yield m


def _refuses(model, field, value) -> bool:
    try:
        model.model_validate({field: value})
    except ValidationError as e:
        return any(err["loc"][:1] == (field,) for err in e.errors())
    except Exception:       # a before-validator that needs the other fields: judge the field alone
        return False
    return False


async def test_every_numeric_input_that_writes_a_dec08_column_is_bounded():
    unbounded = []
    for model in _router_models():
        for name, f in model.model_fields.items():
            limit = _OVERRIDES.get((model.__module__, model.__name__, name)) or _limit_for(name)
            if limit is None or "str" in str(f.annotation):
                continue
            for v in (float(limit), -float(limit)):
                if not _refuses(model, name, v):
                    unbounded.append(f"{model.__module__}.{model.__name__}.{name} accepts {v:g}")
    assert unbounded == [], "inputs that would overflow NUMERIC on Postgres:\n" + "\n".join(unbounded)


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def test_scanned_ean_in_a_qty_field_is_a_422_not_a_500(client, tenant):
    loc = await _loc(client, tenant)
    cid = (await client.post("/api/clients", json={"name": f"N3 {uuid.uuid4().hex[:4]}"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                                  "is_estimate": False, "lines": []}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json={
        "line_type": "labour", "kind": "custom", "description": "Work", "qty": 4006381333931, "unit_price": 1})
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("path,body", [
    ("/api/pricing/types", {"name": "Huge", "markup_pct": 250000}),
    ("/api/cash/accounts", {"name": "Huge fee", "fee_pct": 1e6}),
])
async def test_huge_percentages_are_refused(client, tenant, path, body):
    r = await client.post(path, json=body, headers=tenant)
    assert r.status_code == 422, r.text


async def test_huge_commission_is_refused(client, tenant):
    from backend.routers.staff import EmployeeUpdate
    with pytest.raises(ValidationError):
        EmployeeUpdate(commission_labour=1e6)
    with pytest.raises(ValidationError):
        EmployeeUpdate(commission_parts=-1e6)
    assert EmployeeUpdate(commission_labour=12.5).commission_labour == 12.5


async def test_database_data_error_is_a_422_with_a_sentence():
    import json

    from sqlalchemy.exc import DataError

    from main import app
    handler = app.exception_handlers[DataError]
    overflow = DataError("UPDATE stock_levels SET qty_available=$1", ("9e12",),
                         Exception("numeric field overflow DETAIL: A field with precision 14, scale 4 ..."))
    r = await handler(None, overflow)
    body = json.loads(r.body)
    assert r.status_code == 422 and "too large" in body["detail"]
    assert "stock_levels" not in body["detail"] and "$1" not in body["detail"]     # no SQL leaks out
    other = DataError("INSERT ...", {}, Exception("value too long for type character varying(32)"))
    r = await handler(None, other)
    assert r.status_code == 422 and "too long" in json.loads(r.body)["detail"]


# ── API shape: money is a JSON number ────────────────────────────────────────
async def test_decimal_inside_a_dict_response_is_a_json_number():
    """The app's routes return dicts with no response_model, so FastAPI encodes them with
    jsonable_encoder: Exact and Decimal, however deeply nested, are JSON numbers."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    mini = FastAPI()

    @mini.get("/x")
    async def x():
        return {"total": Exact("10.50"), "lines": [{"qty": Exact("2.0000"), "tax": Decimal("0.83")}],
                "nested": {"a": {"b": Exact("7")}}}

    async with AsyncClient(transport=ASGITransport(app=mini), base_url="http://t") as c:
        assert (await c.get("/x")).json() == {"total": 10.5, "lines": [{"qty": 2.0, "tax": 0.83}],
                                              "nested": {"a": {"b": 7}}}


def _money_unsafe(tp) -> str | None:
    """Why a response-model annotation could carry money as a JSON string, or None."""
    import typing
    if tp is typing.Any or tp is object or tp in (dict, list, set, tuple):
        return "untyped container"
    if isinstance(tp, type) and issubclass(tp, Decimal):
        return "Decimal field (pydantic writes it as a string)"
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        for f in tp.model_fields.values():
            why = _money_unsafe(f.annotation)
            if why:
                return why
        return None
    origin = typing.get_origin(tp)
    if origin in (dict, list, set, tuple) and not typing.get_args(tp):
        return "untyped container"
    for a in typing.get_args(tp):
        if a is not Ellipsis and not isinstance(a, (str, int)):
            why = _money_unsafe(a)
            if why:
                return why
    return None


async def test_no_response_model_can_carry_money_as_a_string():
    """pydantic v2 serialises Decimal (and so Exact) inside Any/dict/list fields, and in Decimal
    fields, as strings ("2.5"). A response model declared that way would silently turn money into
    text, so declared response models must type money as float or stay off untyped containers."""
    from fastapi.routing import APIRoute

    import main

    def walk(routes):
        for r in routes:
            if hasattr(r, "original_router"):
                yield from walk(r.original_router.routes)
            elif isinstance(r, APIRoute):
                yield r

    bad = [f"{sorted(r.methods)} {r.path}: {why}" for r in walk(main.app.routes)
           if r.response_model is not None and (why := _money_unsafe(r.response_model))]
    assert bad == [], "response models that would write money as JSON strings:\n" + "\n".join(bad)


async def test_the_guard_catches_the_shapes_that_stringify():
    from typing import Any, Optional

    class Loose(BaseModel):
        extra: dict

    class Typed(BaseModel):
        total: float
        lines: list[dict[str, float]]

    class Dec(BaseModel):
        total: Optional[Decimal] = None

    class AnyVal(BaseModel):
        meta: dict[str, Any]

    assert _money_unsafe(Loose) and _money_unsafe(Dec) and _money_unsafe(AnyVal)
    assert _money_unsafe(Typed) is None
    # and the reason the guard exists:
    assert AnyVal(meta={"x": Exact("2.5")}).model_dump(mode="json") == {"meta": {"x": "2.5"}}


# ── N6: Exact gaps ───────────────────────────────────────────────────────────
async def test_exact_hashes_like_the_float_it_equals():
    assert Exact("30.01") == 30.01 and hash(Exact("30.01")) == hash(30.01)
    assert {30.01: "x"}.get(Exact("30.01")) == "x" and len({Exact("0.1"), 0.1}) == 1
    assert hash(Exact("2.50")) == hash(Exact("2.5")) == hash(2.5) == hash(Decimal("2.5"))
    assert hash(Exact("7")) == hash(7)
    # A value no float holds keeps Decimal's hash (its equal float doesn't exist).
    long = Exact("0.1000000000000000000000001")
    assert hash(long) == hash(Decimal(long)) and long != 0.1
    # The documented trade-off: Exact("0.1") equals both 0.1 and Decimal("0.1"), which aren't
    # equal to each other, so it can share a hash with only one of them. It sides with the float.
    assert Exact("0.1") == Decimal("0.1") and hash(Exact("0.1")) != hash(Decimal("0.1"))


async def test_exact_divmod_and_three_argument_pow():
    q, r = divmod(Exact("7.5"), 2.0)
    assert (q, r) == (3, Decimal("1.5")) and isinstance(q, Exact) and isinstance(r, Exact)
    q, r = divmod(7.5, Exact("2"))
    assert (q, r) == (3, Decimal("1.5")) and isinstance(r, Exact)
    assert divmod(Exact("10"), 3) == (3, 1)
    assert pow(Exact(2), 10, 1000) == 24 and isinstance(pow(Exact(2), 10, 1000), Exact)


async def test_mod_and_floordiv_follow_decimal_signs():
    """Documented: % and // keep Decimal semantics (the result of % takes the dividend's sign,
    // truncates toward zero), not float's floor semantics. Money code only divides positives."""
    assert Exact(-7) % 3 == -1 and Exact(-7) // 2 == -3
    assert -7.0 % 3 == 2 and -7.0 // 2 == -4          # float, for contrast
    assert Exact(7) % 3 == 1 and Exact(7) // 2 == 3 and Exact("7.5") % 2.5 == 0


async def test_decimal_methods_stay_exact():
    x = Exact("2.675")
    for r in (x.quantize(Decimal("0.01")), Exact(2).sqrt(), Exact("-1.5").copy_abs(),
              Exact("-1.5").copy_negate(), Exact("2.500").normalize(), x.max(3), x.min(1.5),
              x.to_integral_value(), Exact(5).scaleb(-1), x.copy_sign(-1), x.remainder_near(1)):
        assert isinstance(r, Exact), r
    assert x.quantize(Decimal("0.01")) + 0.1 == Decimal("2.78")       # no TypeError any more
    assert Exact("-1.5").copy_abs() + 0.25 == Decimal("1.75")
    assert x.max(3.5) == Decimal("3.5") and x.min(1.5) == Decimal("1.5")   # float operands lift
    # d2 keeps its contract: a plain Decimal quantized to cents (credit-note PDFs print "10.50").
    assert type(d2(Exact("10.5"))) is Decimal and f"{d2(Exact('10.5'))}" == "10.50"


async def test_percent_formatting_is_documented():
    """printf-style "%.2f" goes through float() (no hook can change that), so it can differ from
    f"{x:.2f}" (half-up) on a half-cent: use f-strings or d2 for money text."""
    assert f"{Exact('2.675'):.2f}" == "2.68"
    assert "%.2f" % Exact("2.675") == "2.67"  # noqa: UP031  (the printf path is the point)
    assert "%.2f" % Exact("10.5") == "10.50" == f"{Exact('10.5'):.2f}"  # noqa: UP031


async def test_nan_compares_like_float_nan_instead_of_raising():
    nan = Exact("NaN")
    assert not (nan < 1) and not (nan > 1) and not (nan <= 1) and not (nan >= 1)
    assert not (1 < nan) and not (0.5 > nan) and nan != nan and not (nan == 1.0)
    assert not (Exact(1) < Decimal("NaN"))
    assert max(Exact(0), nan) == 0 or max(Exact(0), nan) != max(Exact(0), nan)   # no raise
    sorted([Exact(2), nan, Exact(1)])                                               # no raise
    with pytest.raises(InvalidOperation):          # plain Decimal still raises: Exact is the fix
        bool(Decimal("NaN") < 1)


# ── N8: summing is fast ──────────────────────────────────────────────────────
async def test_dsum_has_a_fast_path_for_exact_values():
    vals = [Exact("19.99")] * 100_000 + [0.01, 1, Decimal("0.10"), None]
    assert dsum(vals) == Decimal("1999001.11") and type(dsum(vals)) is Decimal
    assert msum([0.1, 0.2]) == 0.3 and msum([Exact("10.005"), 0.005]) == 10.01
    exact = [Exact("19.99")] * 50_000

    def slow_sum():
        total = Exact(0)
        for v in exact:
            total = total + v               # the per-element Exact dispatch dsum used to pay
        return total

    def best(fn):                           # best of 3, so a busy CI box doesn't flake it
        runs = []
        for _ in range(3):
            t = time.perf_counter()
            fn()
            runs.append(time.perf_counter() - t)
        return min(runs)

    fast, slow = best(lambda: dsum(exact)), best(slow_sum)
    assert fast * 2 < slow, (fast, slow)    # measured about 4.5x


# ── N7: Postgres stores the cable scenario exactly ──────────────────────────
@pg_only
async def test_stock_used_in_tenths_is_zero_in_storage_on_postgres(client, tenant, db):
    from backend.models import Part, StockLevel
    from backend.services.money import add_to_column
    loc = await _loc(client, tenant)
    r = await client.post("/api/inventory/parts", json={"name": f"Cable {uuid.uuid4().hex[:4]}", "uom": "m",
                                                        "sell_price": 1, "cost_price": 0.5}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    part = (await db.execute(select(Part).where(Part.id == r.json()["id"]))).scalar_one()
    lvl = StockLevel(org_id=part.org_id, part_id=part.id, location_id=loc, qty_available=3)
    db.add(lvl)
    await db.commit()
    for _ in range(30):
        await add_to_column(db, lvl, "qty_available", -0.1, round_to=None)
    await db.commit()
    # The stored text and an SQL-side comparison, so no read-side rounding is involved.
    stored = (await db.execute(text("SELECT qty_available::text, qty_available = 0 FROM stock_levels WHERE id = :i"),
                               {"i": lvl.id})).one()
    assert stored == ("0.0000", True)
    typ = (await db.execute(text("SELECT pg_typeof(qty_available)::text FROM stock_levels WHERE id = :i"),
                            {"i": lvl.id})).scalar_one()
    assert typ == "numeric"
