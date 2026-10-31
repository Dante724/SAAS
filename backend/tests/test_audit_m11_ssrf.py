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


def _rule(url):
    return SimpleNamespace(action_type="webhook", action_config=json.dumps({"url": url}),
                           event="order_created", name="Ping")


@pytest.mark.asyncio
async def test_automation_webhook_connects_to_the_checked_address(dns, transport):
    # first answer public (the check), any later answer internal (a rebinding attacker)
    dns["hooks.example.com"] = [[PUBLIC], ["169.254.169.254"]]
    order = SimpleNamespace(id="o1", order_number="1", status="new")
    out = await automation._do_action(None, "org", _rule("https://hooks.example.com/in"), order, None)
    assert out and out.startswith("called webhook"), out
    req = transport[0]
    assert req.url.host == PUBLIC
    assert req.headers["host"] == "hooks.example.com"
    assert req.extensions.get("sni_hostname") == "hooks.example.com"


@pytest.mark.asyncio
async def test_automation_webhook_refuses_cgnat_metadata(dns, transport):
    dns["hooks.example.com"] = [["100.100.100.200"]]
    order = SimpleNamespace(id="o1", order_number="1", status="new")
    out = await automation._do_action(None, "org", _rule("http://hooks.example.com/"), order, None)
    assert "blocked" in out and transport == []
