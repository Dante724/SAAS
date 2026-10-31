"""A shop's own SMTP server setting is a host the server connects to on the shop's behalf, so it
gets the same guard as webhooks: it must be a public address, and the connection goes to the
address that was checked (no DNS rebinding to an internal host)."""
import socket

import pytest

from backend.services import notifications

PUBLIC = "93.184.216.34"


@pytest.fixture
def dns(monkeypatch):
    table = {"smtp.shop.example": [PUBLIC], "evil.shop.example": ["10.0.0.5"],
             "meta.shop.example": ["100.100.100.200"]}
    real = socket.getaddrinfo

    def fake(host, port, *a, **kw):
        if host not in table:
            return real(host, port, *a, **kw)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in table[host]]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return table


@pytest.fixture
def smtp(monkeypatch):
    seen = []

    class FakeSMTP:
        def __init__(self, *a, **kw):
            self._host = None
            if a:
                seen.append(("init", a[0]))

        def connect(self, host, port):
            seen.append(("connect", host, port))
            self._host = host
            return (220, b"ok")

        def ehlo(self):
            pass

        def starttls(self, *a, **kw):
            seen.append(("starttls", self._host))

        def login(self, *a):
            pass

        def send_message(self, msg):
            seen.append(("sent", msg["To"]))

        def quit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["evil.shop.example", "meta.shop.example", "127.0.0.1", "169.254.169.254"])
async def test_shop_smtp_host_must_be_public(dns, smtp, host):
    out = await notifications.SmtpEmailAdapter({"host": host, "port": 587}).send(
        recipient="a@b.example", subject="s", body="b")
    assert out["status"] == "failed" and "public" in out["error"]
    assert not any(e[0] in ("connect", "init", "sent") for e in smtp)


@pytest.mark.asyncio
async def test_shop_smtp_connects_to_the_checked_address_and_verifies_the_name(dns, smtp):
    out = await notifications.SmtpEmailAdapter({"host": "smtp.shop.example", "port": 587, "use_tls": True}).send(
        recipient="a@b.example", subject="s", body="b")
    assert out["status"] == "sent", out
    assert ("connect", PUBLIC, 587) in smtp
    assert ("starttls", "smtp.shop.example") in smtp      # certificate checked against the name


@pytest.mark.asyncio
async def test_saving_an_internal_smtp_host_is_refused(client, tenant, dns):
    r = await client.put("/api/integrations/credentials", headers=tenant, json={
        "provider": "smtp", "data": {"host": "evil.shop.example", "port": "587"}})
    assert r.status_code == 422 and "public" in r.json()["detail"]
    ok = await client.put("/api/integrations/credentials", headers=tenant, json={
        "provider": "smtp", "data": {"host": "smtp.shop.example", "port": "587"}})
    assert ok.status_code == 200, ok.text
