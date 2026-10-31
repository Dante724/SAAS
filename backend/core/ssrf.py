"""Build 144cq — SSRF guard for outbound requests to user-supplied URLs.

Any URL a *tenant* can set (automation webhooks, etc.) must pass through
``assert_safe_url`` before we fetch it. We allow only http/https, and refuse
hosts that resolve to anything but a globally routable unicast address — blocking
cloud metadata (169.254.169.254, Alibaba's 100.100.100.200 in CGNAT space),
private and internal services. Fail-closed: anything we can't resolve is rejected.

Outbound webhooks (endpoints and automation actions) use the stricter
``assert_webhook_url`` / ``pinned_target(..., webhook=True)``: once deployed only
https on port 443 or 8443 (local development also allows http and port 80).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    pass


_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    # IPv6 forms that carry an IPv4 address (::ffff:a.b.c.d, and NAT64 64:ff9b::a.b.c.d which
    # IPv6-only networks synthesize for every IPv4 host) are judged by the address they carry.
    if addr.version == 6:
        if addr.ipv4_mapped:
            return _ip_is_public(str(addr.ipv4_mapped))
        if addr in _NAT64:
            return _ip_is_public(str(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)))
        # 6to4 (2002::/16) and Teredo carry an IPv4 address too.
        if addr.sixtofour is not None:
            return _ip_is_public(str(addr.sixtofour))
        if addr.teredo is not None:
            return _ip_is_public(str(addr.teredo[1]))
    # B3: an allow-list, not a deny-list. `is_global` is False for private, loopback,
    # link-local, CGNAT (100.64/10, where Alibaba Cloud's metadata service lives at
    # 100.100.100.200), benchmarking, documentation and reserved ranges.
    return addr.is_global and not addr.is_multicast


# B3: ports an outbound webhook may use. Deployed: https on 443/8443 only.
WEBHOOK_PORTS_DEPLOYED = (443, 8443)
WEBHOOK_PORTS_DEV = (80, 443, 8443)


def _webhook_policy(parsed) -> None:
    from backend.config import settings
    deployed = settings.is_deployed
    if deployed and parsed.scheme != "https":
        raise UnsafeURLError("webhooks must use https")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as e:
        raise UnsafeURLError("bad port") from e
    if port not in (WEBHOOK_PORTS_DEPLOYED if deployed else WEBHOOK_PORTS_DEV):
        raise UnsafeURLError("port not allowed")
    if parsed.username or parsed.password:
        raise UnsafeURLError("credentials in the URL are not allowed")


def _resolve_public(url: str, *, webhook: bool = False):
    """Parse and resolve `url`; return (parsed, sorted public IPs) or raise UnsafeURLError."""
    if not url or not isinstance(url, str):
        raise UnsafeURLError("empty url")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError("only http/https is allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("missing host")
    if webhook:
        _webhook_policy(parsed)
    # Resolve every address the host maps to; all must be public.
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError) as e:
        raise UnsafeURLError(f"cannot resolve host: {e}") from e
    ips = {info[4][0] for info in infos}
    if not ips:
        raise UnsafeURLError("host did not resolve")
    for ip in ips:
        if not _ip_is_public(ip):
            raise UnsafeURLError(f"host resolves to a non-public address ({ip})")
    return parsed, sorted(ips)


def assert_safe_url(url: str) -> str:
    """Return the URL if safe to fetch, else raise UnsafeURLError."""
    _resolve_public(url)
    return url


def assert_webhook_url(url: str) -> str:
    """assert_safe_url plus the outbound-webhook scheme/port policy (B3)."""
    _resolve_public(url, webhook=True)
    return url


def pinned_target(url: str, *, webhook: bool = False) -> tuple[str, dict, dict]:
    """Validate `url` and pin the connection to the address that was checked.

    Returns (url_with_ip, headers, extensions) for httpx: the request connects to the
    validated IP — so a DNS answer that changes between check and connect (DNS rebinding)
    can't redirect it to an internal host — while the Host header and TLS SNI/certificate
    check still use the original hostname."""
    parsed, ips = _resolve_public(url, webhook=webhook)
    ip = ips[0]
    ip_host = f"[{ip}]" if ":" in ip else ip
    userinfo = parsed.netloc.rpartition("@")[0]
    netloc = (userinfo + "@" if userinfo else "") + ip_host + (f":{parsed.port}" if parsed.port else "")
    host_header = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
    extensions = {"sni_hostname": parsed.hostname} if parsed.scheme == "https" else {}
    return parsed._replace(netloc=netloc).geturl(), {"Host": host_header}, extensions
