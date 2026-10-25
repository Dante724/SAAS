"""Security: tenant-configured outbound webhooks must not reach internal addresses (SSRF).

Regression for the audit finding where webhook URLs were only checked for an http(s) prefix on
create (and not at all on update or delivery), so a tenant admin could make the server POST to
cloud metadata or internal services and read the result from delivery logs.
DNS is mocked throughout so these tests never touch the network.
"""
import socket
from types import SimpleNamespace

import httpx
import pytest

from backend.core import ssrf
from backend.services import webhooks as wh

PUBLIC = "93.184.216.34"


@pytest.fixture
def dns(monkeypatch):
    """Map hostnames to fixed IPs; IP literals resolve to themselves."""
    table = {"hooks.example.com": [PUBLIC], "evil.example.com": ["169.254.169.254"],
             "mixed.example.com": [PUBLIC, "10.0.0.7"], "v6.example.com": ["2606:2800:220:1::1"]}

    def fake(host, port, *a, **kw):
        ips = table.get(host) or [host]
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in ips]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return table


# ── pinning helper ───────────────────────────────────────────────────────────

def test_pinned_target_connects_to_checked_ip_but_keeps_hostname(dns):
    url, headers, ext = ssrf.pinned_target("https://hooks.example.com:8443/in?x=1")
    assert url == f"https://{PUBLIC}:8443/in?x=1"
    assert headers == {"Host": "hooks.example.com:8443"}
    assert ext == {"sni_hostname": "hooks.example.com"}


def test_pinned_target_ipv6_and_plain_http(dns):
    url, headers, ext = ssrf.pinned_target("http://v6.example.com/x")
    assert url == "http://[2606:2800:220:1::1]/x" and headers == {"Host": "v6.example.com"} and ext == {}


@pytest.mark.parametrize("bad", ["http://evil.example.com/", "http://mixed.example.com/",
                                 "http://127.0.0.1:6379/", "http://[::1]/", "file:///etc/passwd"])
def test_pinned_target_refuses_non_public(dns, bad):
    with pytest.raises(ssrf.UnsafeURLError):
        ssrf.pinned_target(bad)


# ── API: create / update ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["http://169.254.169.254/latest/meta-data", "http://127.0.0.1:8000/",
                                 "http://evil.example.com/hook", "ftp://hooks.example.com/"])
async def test_create_endpoint_rejects_internal_urls(client, tenant, dns, bad):
    r = await client.post("/api/webhooks", headers=tenant, json={"url": bad})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_endpoint_cannot_switch_to_internal_url(client, tenant, dns):
    r = await client.post("/api/webhooks", headers=tenant, json={"url": "https://hooks.example.com/in"})
    assert r.status_code == 201
    eid = r.json()["id"]
    bad = await client.patch(f"/api/webhooks/{eid}", headers=tenant, json={"url": "http://10.0.0.5/"})
    assert bad.status_code == 400
    ok = await client.patch(f"/api/webhooks/{eid}", headers=tenant, json={"active": False})
    assert ok.status_code == 200


# ── delivery ─────────────────────────────────────────────────────────────────

def _delivery():
    return SimpleNamespace(attempts=0, status="pending", error=None, response_code=None, delivered_at=None)


@pytest.fixture
def transport(monkeypatch):
    """Route every httpx.AsyncClient through a recording mock transport."""
    seen, responses = [], []

    def handler(request):
        seen.append(request)
        return responses.pop(0) if responses else httpx.Response(200)

    real = httpx.AsyncClient

    class Recording(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", Recording)
    return SimpleNamespace(seen=seen, responses=responses)


@pytest.mark.asyncio
async def test_delivery_is_pinned_to_validated_ip(dns, transport):
    d = _delivery()
    await wh._attempt(d, "https://hooks.example.com/in", "s3", '{"event": "order.created"}')
    assert d.status == "sent"
    req = transport.seen[0]
    assert req.url.host == PUBLIC
    assert req.headers["host"] == "hooks.example.com"
    assert req.extensions.get("sni_hostname") == "hooks.example.com"


@pytest.mark.asyncio
async def test_delivery_blocks_url_that_now_resolves_internally(dns, transport):
    """An endpoint saved while public, whose DNS later points inside (rebinding)."""
    dns["hooks.example.com"] = ["10.1.2.3"]
    d = _delivery()
    await wh._attempt(d, "https://hooks.example.com/in", "s3", '{"event": "order.created"}')
    assert d.status == "failed" and d.error.startswith("Blocked")
    assert transport.seen == []


@pytest.mark.asyncio
async def test_delivery_does_not_follow_redirects(dns, transport):
    transport.responses.append(httpx.Response(302, headers={"location": "http://169.254.169.254/"}))
    d = _delivery()
    await wh._attempt(d, "https://hooks.example.com/in", "s3", '{"event": "order.created"}')
    assert d.status == "failed" and len(transport.seen) == 1


@pytest.mark.asyncio
async def test_delivery_errors_are_generic(dns, monkeypatch):
    class Boom(httpx.AsyncClient):
        async def send(self, *a, **kw):   # deliveries stream the response (B4), so fail at send
            raise httpx.ConnectError("[Errno 61] Connection refused to 10.0.0.9:6379")
    monkeypatch.setattr(httpx, "AsyncClient", Boom)
    d = _delivery()
    await wh._attempt(d, "https://hooks.example.com/in", "s3", '{"event": "order.created"}')
    assert d.status == "failed" and d.error == "Connection failed"


@pytest.mark.parametrize("ip, public", [
    ("64:ff9b::101:101", True),        # NAT64 form of 1.1.1.1 (IPv6-only networks)
    ("64:ff9b::a9fe:a9fe", False),     # NAT64 form of 169.254.169.254
    ("64:ff9b::7f00:1", False),        # NAT64 form of 127.0.0.1
    ("::ffff:10.0.0.1", False),        # IPv4-mapped private
    ("::ffff:93.184.216.34", True),
])
def test_ipv4_carried_in_ipv6_is_judged_by_the_ipv4(ip, public):
    assert ssrf._ip_is_public(ip) is public
