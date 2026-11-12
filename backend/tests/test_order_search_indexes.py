"""srch01 — the work-order search box, rewritten so Postgres can use trigram indexes.

1. Same answers: `_order_search_clause` (now a UNION of one-table selects) finds exactly the jobs
   the previous OR-of-IN-subqueries form found — a frozen copy of it is the oracle here — for job
   numbers, WO- prefixes, phones, names, billed-to companies, brands/models and identifiers, on a
   varied two-shop dataset, never another shop's job, and within a scoped technician's record
   scope on the list endpoint.
2. Same expressions: the migration's and the models' index expressions are exactly what
   search_match emits, or Postgres would silently stop using them.
3. On Postgres the search's plan actually reads those indexes.
"""
import re
import uuid

import pytest
from sqlalchemy import func, literal_column, or_, select, text
from sqlalchemy.dialects.postgresql import asyncpg as pg_asyncpg

from backend.models import Client, Device, Employee, Location, Order, Organisation, Role
from backend.routers.orders import _order_search_clause
from backend.services import search_match as sm
from backend.services.auth import hash_password


def _reference_clause(s: str, org_id: str):
    """The search as it was before srch01 (OR of IN-subqueries), kept verbatim as the oracle."""
    conds = []
    num = sm.number_match(Order.order_number, s)
    if num is not None:
        conds.append(num)
    if num is not None and re.fullmatch(r"#?\s*\d{1,3}", s.strip()):
        return num
    name_ids = select(Client.id).where(Client.org_id == org_id, sm.words_match(Client.name, s))
    client_or = [Client.id.in_(name_ids), Client.payer_id.in_(name_ids)]
    phone = sm.phone_match(Client.phone, s)
    if phone is not None:
        client_or.append(phone)
    conds.append(Order.client_id.in_(select(Client.id).where(Client.org_id == org_id, or_(*client_or))))
    dev_or = [Device.brand.icontains(s, autoescape=True), Device.model.icontains(s, autoescape=True)]
    for col in (Device.serial, Device.imei, Device.vin, Device.hin):
        code = sm.code_match(col, s)
        if code is not None:
            dev_or.append(code)
    conds.append(Order.device_id.in_(select(Device.id).where(Device.org_id == org_id, or_(*dev_or))))
    return or_(*conds)


FIRST = ["Jane", "Bob", "María", "Ahmed", "Li", "O'Neill", "Tom 50%", "Sara_K"]
LAST = ["Kim", "Stone", "García", "Khan", "Wang", "Acme Plumbing Ltd", "Smith", "Lee"]
PHONES = ["(415) 555-0134", "+1 212 555 0199", "020 7946 0958", "415.555.0177", None, "+44 20 7946 1234"]
BRANDS = [("Apple", "iPhone 13 Pro"), ("Samsung", "Galaxy S21"), ("Ford", "Transit"),
          ("Boston Whaler", "Montauk"), (None, None), ("Google", "Pixel 7")]


async def _seed(db, tag: str, n: int = 36):
    """One shop with clients (some billed to a company), assets and jobs; returns (org, loc)."""
    org = Organisation(name=f"Search {tag}")
    db.add(org)
    await db.flush()
    loc = Location(org_id=org.id, name="Main")
    db.add(loc)
    await db.flush()
    company = Client(org_id=org.id, name="Acme Plumbing Ltd", client_type="organization", phone="+1 650 555 0101")
    db.add(company)
    await db.flush()
    for i in range(n):
        c = Client(org_id=org.id, name=f"{FIRST[i % 8]} {LAST[(i // 2) % 8]}", phone=PHONES[i % 6],
                   payer_id=company.id if i % 7 == 0 else None)
        db.add(c)
        await db.flush()
        brand, model = BRANDS[i % 6]
        d = Device(org_id=org.id, client_id=c.id, brand=brand, model=model,
                   serial=f"F2LX-K0{i:02d}-CD{i % 5}" if i % 2 else None,
                   imei=f"35{i:013d}" if i % 3 == 0 else None,
                   vin=f"1FTBW2CM5HKA{i:05d}" if i % 4 == 1 else None,
                   hin=f"BWC{i:05d}H506" if i % 5 == 2 else None)
        db.add(d)
        await db.flush()
        prefix = "WO-" if i % 9 else "EST-"
        db.add(Order(org_id=org.id, location_id=loc.id, order_number=f"{prefix}{i + 1:05d}",
                     client_id=c.id if i % 11 else company.id, device_id=d.id if i % 6 != 5 else None,
                     status="new"))
    await db.commit()
    return org, loc


QUERIES = ["12", "#3", "007", "1234", "WO-00012", "wo12", "WO 0001", "EST-00010", "est10", "00010",
           "4155550134", "+1 415 555 0134", "555-0134", "(212) 555-0199", "+44 20 7946 0958", "7946",
           "jane", "JANE kim", "o'neill", "kim jane", "garcía", "Acme", "plumbing ltd", "acme jane",
           "Tom 50%", "50%", "sara_k", "_", "%",
           "iphone", "iPhone 13", "galaxy", "Boston", "transit", "f2lxk0", "F2LX-K011", "cd3",
           "1FTBW2CM5HKA00005", "hka0000", "bwc00002h506", "350000000000", "nobody", "zz top"]


async def _ids(db, clause, org_id):
    return set((await db.execute(select(Order.id).where(Order.org_id == org_id, clause))).scalars().all())


@pytest.mark.asyncio
async def test_union_search_finds_exactly_what_the_old_search_found(db):
    a, _ = await _seed(db, "a")
    b, _ = await _seed(db, "b")
    hits = 0
    for org in (a, b):
        for s in QUERIES:
            s = " ".join(s.split())
            new, old = await _ids(db, _order_search_clause(s, org.id), org.id), \
                await _ids(db, _reference_clause(s, org.id), org.id)
            assert new == old, (org.name, s, len(new), len(old))
            hits += bool(new)
            # never another shop's job, even without the outer org filter
            every = set((await db.execute(select(Order.id).where(_order_search_clause(s, org.id)))).scalars().all())
            other = set((await db.execute(select(Order.id).where(Order.org_id != org.id))).scalars().all())
            if not re.fullmatch(r"#?\s*\d{1,3}", s):   # the short-number form filters the outer query only
                assert not (every & other), s
    assert hits > 40          # the dataset really exercises the matches (not "both found nothing")


@pytest.mark.asyncio
async def test_list_endpoint_search_respects_the_record_scope(client, tenant, db):
    """A technician whose role sees only their own jobs finds only those by search."""
    org = (await db.execute(select(Organisation))).scalars().first()
    admin = (await db.execute(select(Employee).where(Employee.org_id == org.id))).scalars().first()
    role = Role(org_id=org.id, name="Bench", order_scope="own", permissions={})
    db.add(role)
    await db.flush()
    email = f"t-{uuid.uuid4().hex[:6]}@srch.dev"
    tech = Employee(org_id=org.id, location_id=admin.location_id, role_id=role.id, name="T", email=email,
                    password_hash=hash_password("pw-tech-12345"), active=True)
    db.add(tech)
    await db.commit()
    cid = (await client.post("/api/clients", json={"name": "Jane Kim", "phone": "(415) 555-0134"},
                             headers=tenant)).json()["id"]
    made = [(await client.post("/api/orders", headers=tenant, json={
        "client_id": cid, "complaint": "x", "asset_brand": "Apple", "asset_model": "iPhone 13"})).json()
        for _ in range(3)]
    for o, who in zip(made, (tech.id, admin.id, admin.id)):
        (await db.execute(select(Order).where(Order.id == o["id"]))).scalar_one().assigned_to_id = who
    await db.commit()
    tok = (await client.post("/api/auth/token", data={"username": email, "password": "pw-tech-12345"})).json()
    th = {"Authorization": f"Bearer {tok['access_token']}"}
    for term in ("jane", "4155550134", "iphone 13", made[1]["order_number"]):
        seen = (await client.get("/api/orders", params={"q": term}, headers=th)).json()
        assert {o["id"] for o in seen["items"]} <= {made[0]["id"]}, term
        assert seen["total"] == len(seen["items"])
        everyone = (await client.get("/api/orders", params={"q": term}, headers=tenant)).json()
        assert everyone["total"] >= 1, term


# ── the index expressions ─────────────────────────────────────────────────────
def _pg(expr) -> str:
    return str(expr.compile(dialect=pg_asyncpg.dialect(), compile_kwargs={"literal_binds": True}))


def _norm(sql: str) -> str:
    return re.sub(r"\s+", "", sql).replace('"', "")


def _search_exprs() -> dict:
    """What search_match compares, per index, compiled for Postgres."""
    col = literal_column
    flat = {c: _pg(sm.stripped(func.lower(col(c)), sm.NUM_STRIP)) for c in ("order_number", "serial", "imei", "vin", "hin")}
    return {
        "ix_clients_name_trgm": "name",
        "ix_clients_phone_trgm": f"({_pg(sm.stripped(col('phone'), sm.PHONE_STRIP))})",
        "ix_orders_number_trgm": f"({flat['order_number']})",
        "ix_devices_brand_trgm": "brand",
        "ix_devices_model_trgm": "model",
        **{f"ix_devices_{c}_trgm": f"({flat[c]})" for c in ("serial", "imei", "vin", "hin")},
        "ix_orders_number_digits": f"org_id, {_pg(func.ltrim(col(flat['order_number']), sm._LETTERS + '0'))}",
        "ix_clients_payer_id": "payer_id",
    }


def _migration():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "srch01_search_trigram_indexes.py"
    spec = importlib.util.spec_from_file_location("srch01", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_indexes_are_the_search_expressions():
    mig = _migration()
    want = _search_exprs()
    got = {name: expr for name, _table, expr in mig.TRGM + mig.BTREE}
    assert set(got) == set(want)
    for name, expr in got.items():
        assert _norm(expr) == _norm(want[name]), name
    # the number index really is number_match's "same number" expression
    assert "ltrim(" in _pg(sm.number_match(literal_column("order_number"), "WO12"))


def test_models_declare_the_same_indexes():
    """The drift gate compares models with migrated databases; create_all databases (tests, dev)
    get the same indexes from the models."""
    mig = _migration()
    declared = {}
    for model in (Client, Device, Order):
        for ix in model.__table__.indexes:
            declared[ix.name] = ", ".join(
                (e.text if hasattr(e, "text") else e.name) for e in ix.expressions)
    for name, _table, expr in mig.TRGM:
        assert _norm(declared[name]) == _norm(f"({expr}) gin_trgm_ops") or \
            _norm(declared[name]) == _norm(f"{expr} gin_trgm_ops"), name
    for name, _table, expr in mig.BTREE:
        assert _norm(declared[name]) == _norm(expr), name


@pytest.mark.asyncio
async def test_postgres_plan_reads_the_trigram_indexes(db):
    """Each condition the search box builds is answered from its index: the planner only matches
    an expression index when the expression is identical, so this fails if search_match drifts.
    (The full query's plan on 20k jobs per shop is in the srch01 PR.)"""
    if db.bind.dialect.name != "postgresql":
        pytest.skip("Postgres-only: SQLite has no trigram indexes")
    await _seed(db, "plan", n=12)
    flat = {c: lambda c=c: sm.code_match(getattr(Device, c), "F2LXK011") for c in ("serial", "imei", "vin", "hin")}
    expect = [
        ("ix_orders_number_trgm", select(Order.id).where(sm.number_match(Order.order_number, "WO-00012").clauses[0])),
        ("ix_orders_number_digits", select(Order.id).where(
            Order.org_id == "x", sm.number_match(Order.order_number, "WO-00012").clauses[1])),
        ("ix_clients_phone_trgm", select(Client.id).where(sm.phone_match(Client.phone, "4155550134"))),
        ("ix_clients_name_trgm", select(Client.id).where(sm.words_match(Client.name, "jane kim"))),
        ("ix_clients_payer_id", select(Client.id).where(Client.payer_id == "x")),
        ("ix_devices_brand_trgm", select(Device.id).where(Device.brand.icontains("iphone", autoescape=True))),
        ("ix_devices_model_trgm", select(Device.id).where(Device.model.icontains("iphone", autoescape=True))),
        *((f"ix_devices_{c}_trgm", select(Device.id).where(f())) for c, f in flat.items()),
    ]
    conn = await db.connection()
    await conn.exec_driver_sql("SET enable_seqscan = off")   # a 12-row table would be scanned otherwise
    # Within this transaction only (rolled back below): the single-column shop indexes tie with the
    # (org_id, job digits) one on a near-empty table, which would make the digits check a coin toss.
    for ix in ("ix_orders_org_id", "ix_orders_org_created", "ix_orders_org_status"):
        await conn.exec_driver_sql(f"DROP INDEX IF EXISTS {ix}")
    try:
        for name, q in expect:
            plan = "\n".join(r[0] for r in (await conn.exec_driver_sql("EXPLAIN " + _pg(q))).all())
            assert name in plan, f"{name} not used:\n{plan}"
    finally:
        await conn.exec_driver_sql("RESET enable_seqscan")
        await db.rollback()
