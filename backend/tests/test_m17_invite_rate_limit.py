"""M17 — the three teammate e-mails a shop can trigger are a bounded, fixed-subject relay.

Invite, "send reset" and the passwordless account e-mail each went to any address the shop typed,
without limit, with the shop's own name in the subject line: a spam relay with Tekly's sender
reputation. Now one per-shop bucket allows 20 of them a day across the three, the subject is
fixed, and the shop's name is only in the body, capped in length.
"""
import uuid

import pytest

from backend.services import notifications
from backend.tests.invite_role import any_role

LIMIT = 20


@pytest.fixture
def sent(monkeypatch):
    box = []

    async def fake_send(to, subject, body, *a, **kw):
        box.append({"to": to, "subject": subject, "body": body})
    monkeypatch.setattr(notifications, "send_direct_email", fake_send)
    return box


async def _owner(client, org_name="Rate Co"):
    r = await client.post("/api/auth/register", json={
        "org_name": org_name, "name": "Owner", "email": f"o-{uuid.uuid4().hex[:8]}@t.dev", "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()


def _addr():
    return f"t-{uuid.uuid4().hex[:8]}@t.dev"


@pytest.mark.asyncio
async def test_a_shop_gets_20_teammate_emails_a_day_across_invite_reset_and_create(client, sent):
    H, me = await _owner(client)
    codes = [(await client.post("/api/staff/invites", json={"email": _addr(), "name": "T", "role_id": await any_role(client, H)}, headers=H)).status_code
             for _ in range(LIMIT)]
    assert codes == [201] * LIMIT and len(sent) == LIMIT
    # The 21st of ANY of the three kinds is refused, and sends nothing.
    r = await client.post("/api/staff/invites", json={"email": _addr(), "name": "T", "role_id": await any_role(client, H)}, headers=H)
    assert r.status_code == 429, r.text
    assert r.headers.get("Retry-After")
    emp = (await client.get("/api/auth/me", headers=H)).json()
    r = await client.post(f"/api/staff/employees/{emp['id']}/send-reset", headers=H)
    assert r.status_code == 429, r.text
    r = await client.post("/api/staff/employees", json={"name": "New", "email": _addr()}, headers=H)   # no password => e-mail
    assert r.status_code == 429, r.text
    assert len(sent) == LIMIT
    # A refused invite left nothing behind, and a password-ful create (no e-mail) is not limited.
    pending = (await client.get("/api/staff/invites", headers=H)).json()["items"]
    assert len(pending) == LIMIT
    r = await client.post("/api/staff/employees", json={"name": "New", "email": _addr(), "password": "pw-test-12345"}, headers=H)
    assert r.status_code == 201, r.text
    assert len(sent) == LIMIT


@pytest.mark.asyncio
async def test_the_limit_is_per_shop(client, sent):
    H1, _ = await _owner(client)
    H2, _ = await _owner(client)
    for _ in range(LIMIT):
        assert (await client.post("/api/staff/invites", json={"email": _addr(), "name": "T", "role_id": await any_role(client, H1)}, headers=H1)).status_code == 201
    assert (await client.post("/api/staff/invites", json={"email": _addr(), "name": "T", "role_id": await any_role(client, H1)}, headers=H1)).status_code == 429
    assert (await client.post("/api/staff/invites", json={"email": _addr(), "name": "T", "role_id": await any_role(client, H2)}, headers=H2)).status_code == 201


@pytest.mark.asyncio
async def test_subject_is_fixed_and_the_shop_name_only_appears_capped_in_the_body(client, sent):
    name = "WIN A PRIZE click https://evil.example " + "x" * 150       # within the 200-char column
    H, _ = await _owner(client, org_name=name)
    assert (await client.post("/api/staff/invites", json={"email": _addr(), "name": "T", "role_id": await any_role(client, H)}, headers=H)).status_code == 201
    assert (await client.post("/api/staff/employees", json={"name": "New", "email": _addr()}, headers=H)).status_code == 201
    assert [m["subject"] for m in sent] == ["You're invited to join your team on Tekly",
                                            "You've been added to your team on Tekly"]
    for m in sent:
        assert "evil.example" not in m["subject"] and "PRIZE" not in m["subject"]
        assert "WIN A PRIZE" in m["body"]                      # the shop's name still identifies the sender
        assert "x" * 150 not in m["body"]                      # but not 150 characters of it
