"""Audit 29 Oct 2026, M11: 100.64.0.0/10 (carrier-grade NAT, which includes the 100.100.100.200
cloud metadata address) and other special ranges counted as public, and the automation webhook
checked the URL then let httpx resolve it again (DNS rebinding). Public now means is_global, and
the automation call connects to the exact address that was checked, as tenant webhooks do."""
import json
import socket
from types import SimpleNamespace

import httpx
import pytest

from backend.core import ssrf
from backend.services import automation

PUBLIC = "93.184.216.34"


@pytest.mark.parametrize("ip", ["100.100.100.200", "100.64.0.1", "198.18.0.1", "192.0.0.1",
                                "2002:a9fe:a9fe::1", "2001:0:4136:e378:8000:63bf:3fff:fdd2",
                                "224.0.1.1", "::ffff:100.100.100.200"])
def test_special_ranges_are_not_public(ip):
    assert ssrf._ip_is_public(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", PUBLIC, "2606:2800:220:1::1"])
def test_real_public_addresses_still_pass(ip):
    assert ssrf._ip_is_public(ip) is True


@pytest.fixture
def dns(monkeypatch):
    answers = {"hooks.example.com": [[PUBLIC]]}
    real_gai = socket.getaddrinfo

    def fake(host, port, *a, **kw):
        if host not in answers:          # the test database resolves for real
            return real_gai(host, port, *a, **kw)
        seq = answers.get(host)
        ips = (seq.pop(0) if len(seq) > 1 else seq[0]) if seq else [host]
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in ips]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return answers


@pytest.fixture
def transport(monkeypatch):
    seen = []
    real = httpx.AsyncClient

    class Recording(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(200))[1])
            super().__init__(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", Recording)
    return seen


def _delivery():
    return SimpleNamespace(id="d1", attempts=0, status="pending", error=None, response_code=None,
                           delivered_at=None, next_attempt_at=None)


def _body():
    return json.dumps({"event": "order_created", "data": {"rule": "Ping", "order_id": "o1"}})


# Release (whh1) replaced the inline automation call with the signed outbox: automation rules
# queue a delivery on a hidden "automation" endpoint (services.webhooks.queue_automation_webhook)
# and every attempt goes through webhooks._attempt. These tests check the M11 guarantee on that
# path: the attempt connects to the address that passed the check, and CGNAT metadata is refused.

@pytest.mark.asyncio
async def test_automation_webhook_connects_to_the_checked_address(dns, transport):
    from backend.services import webhooks
    # first answer public (the check), any later answer internal (a rebinding attacker)
    dns["hooks.example.com"] = [[PUBLIC], ["169.254.169.254"]]
    d = _delivery()
    await webhooks._attempt(d, "https://hooks.example.com/in", "sec", _body())
    assert d.status == "sent", d.error
    req = transport[0]
    assert req.url.host == PUBLIC
    assert req.headers["host"] == "hooks.example.com"
    assert req.extensions.get("sni_hostname") == "hooks.example.com"


@pytest.mark.asyncio
async def test_automation_webhook_refuses_cgnat_metadata(dns, transport):
    from backend.services import webhooks
    dns["hooks.example.com"] = [["100.100.100.200"]]
    d = _delivery()
    await webhooks._attempt(d, "https://hooks.example.com/", "sec", _body())
    assert d.status == "failed" and "Blocked" in (d.error or "") and transport == []


def test_automation_webhook_action_goes_through_the_checked_outbox():
    """The automation action must use the outbox (which pins + re-checks), not its own httpx call."""
    import inspect
    src = inspect.getsource(automation._do_action)
    assert "queue_automation_webhook" in src and "httpx" not in src
