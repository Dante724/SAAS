"""Build 144cv — Stage 2: document currency pricing reads the 144cr registry
(single source of truth), converting base-per-secondary → display-per-base."""
import pytest

from backend.models import Currency, Organisation
from backend.services import currency as cur


@pytest.mark.asyncio
async def test_document_rate_reads_registry_reciprocal(db):
    org = Organisation(name="Acme", currency_code="USD", currency="$")
    db.add(org)
    await db.flush()
    db.add(Currency(org_id=org.id, code="EUR", symbol="€", rate=1.25))  # 1 EUR = 1.25 USD
    await db.commit()

    # base currency resolves to 1.0
    assert (await cur.rate_for_code(db, org.id, "USD"))["rate"] == 1.0
    # secondary: display-per-base = 1 / 1.25 = 0.8
    r = await cur.rate_for_code(db, org.id, "EUR")
    assert r["rate"] == 0.8 and r["symbol"] == "€"
    # per-document override goes through the same registry
    d = await cur.resolve_for_client(db, org.id, None, override_code="EUR")
    assert d["rate"] == 0.8


@pytest.mark.asyncio
async def test_unknown_code_falls_back_to_base(db):
    org = Organisation(name="Acme2", currency_code="USD", currency="$")
    db.add(org)
    await db.commit()
    r = await cur.rate_for_code(db, org.id, "ZZZ")
    assert r["rate"] == 1.0 and r["code"] == "USD"
