"""Build 144cq — SSRF guard for outbound requests to user-supplied URLs.

Any URL a *tenant* can set (automation webhooks, etc.) must pass through
``assert_safe_url`` before we fetch it. We allow only http/https, and refuse
hosts that resolve to private, loopback, link-local, or otherwise non-public
IP ranges — blocking access to cloud metadata (169.254.169.254) and internal
services. Fail-closed: anything we can't resolve is rejected.
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
        if addr.sixtofour:
            return _ip_is_public(str(addr.sixtofour))
        if addr.teredo:
            return all(_ip_is_public(str(a)) for a in addr.teredo)
    # is_global, not "not private": 100.64.0.0/10 (carrier-grade NAT, which holds the
    # 100.100.100.200 cloud metadata address), 198.18.0.0/15, 192.0.0.0/24 and similar ranges are
    # neither private nor global. Multicast can be global, but is never a webhook receiver.
    return addr.is_global and not addr.is_multicast


def _resolve_public(url: str):
    """Parse and resolve `url`; return (parsed, sorted public IPs) or raise UnsafeURLError."""
    if not url or not isinstance(url, str):
        raise UnsafeURLError("empty url")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError("only http/https is allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("missing host")
    return parsed, _public_ips(host, parsed.port or (443 if parsed.scheme == "https" else 80))


def _public_ips(host: str, port: int) -> list:
    """Every address `host` resolves to, sorted; all must be public."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError, OSError) as e:
        raise UnsafeURLError(f"cannot resolve host: {e}") from e
    ips = {info[4][0] for info in infos}
    if not ips:
        raise UnsafeURLError("host did not resolve")
    for ip in ips:
        if not _ip_is_public(ip):
            raise UnsafeURLError(f"host resolves to a non-public address ({ip})")
    return sorted(ips)


def public_ip_for(host: str, port: int) -> str:
    """For non-HTTP connections to a tenant-supplied host (their SMTP server): the checked public
    address to connect to, so a DNS answer that changes afterwards can't point inside."""
    if not host or not isinstance(host, str):
        raise UnsafeURLError("missing host")
    return _public_ips(host.strip().strip("[]"), port)[0]


def assert_safe_url(url: str) -> str:
    """Return the URL if safe to fetch, else raise UnsafeURLError."""
    _resolve_public(url)
    return url


def pinned_target(url: str) -> tuple[str, dict, dict]:
    """Validate `url` and pin the connection to the address that was checked.

    Returns (url_with_ip, headers, extensions) for httpx: the request connects to the
    validated IP — so a DNS answer that changes between check and connect (DNS rebinding)
    can't redirect it to an internal host — while the Host header and TLS SNI/certificate
    check still use the original hostname."""
    parsed, ips = _resolve_public(url)
    ip = ips[0]
    ip_host = f"[{ip}]" if ":" in ip else ip
    userinfo = parsed.netloc.rpartition("@")[0]
    netloc = (userinfo + "@" if userinfo else "") + ip_host + (f":{parsed.port}" if parsed.port else "")
    host_header = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
    extensions = {"sni_hostname": parsed.hostname} if parsed.scheme == "https" else {}
    return parsed._replace(netloc=netloc).geturl(), {"Host": host_header}, extensions
