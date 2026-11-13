"""Re-review decimal N1 / money H1: QuickBooks invoice and payment pushes must encode Exact money.

The HTTP transport is patched, not `_push`, so the payload really goes through httpx's JSON encoder."""
import json

import httpx
import pytest

from backend.models import Client, Invoice, Payment, QboConnection
from backend.services import qbo
from backend.tests.test_batch1_safety import _register


def _patch_transport(monkeypatch, sent):
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        entity = request.url.path.rsplit("/", 1)[-1].capitalize()
        return httpx.Response(200, json={entity: {"Id": "Q-1"}})

    real = httpx.AsyncClient

    class _Client(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)


@pytest.mark.asyncio
async def test_qbo_invoice_and_payment_push_encode_exact_money(client, db, monkeypatch):
    _, org, _ = await _register(client)
    monkeypatch.setattr(qbo, "is_configured", lambda: True)

    async def _no_refresh(db_, conn):
        return None
    monkeypatch.setattr(qbo, "_ensure_fresh", _no_refresh)
    monkeypatch.setattr(qbo, "_dec", lambda v: "tok")
    db.add(QboConnection(org_id=org, realm_id="r1", environment="sandbox", connected=True,
                         access_token="enc"))
    c = Client(org_id=org, name="Ravi")
    db.add(c)
    await db.flush()
    p = Payment(org_id=org, client_id=c.id, method="cash", amount=100.5)
    inv = Invoice(org_id=org, client_id=c.id, invoice_number="INV-Q1", subtotal=110.25, total=110.25)
    db.add_all([p, inv])
    await db.commit()
    pid, iid = p.id, inv.id
    db.expire_all()

    sent: list = []
    _patch_transport(monkeypatch, sent)
    r1 = await qbo.sync_entity(db, org, "payment", pid)
    r2 = await qbo.sync_entity(db, org, "invoice", iid)
    assert r1["status"] == "ok", r1
    assert r2["status"] == "ok", r2
    assert sent[0]["TotalAmt"] == 100.5
    assert sent[1]["TotalAmt"] == 110.25
