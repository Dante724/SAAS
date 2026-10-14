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


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


def assert_safe_url(url: str) -> str:
    """Return the URL if safe to fetch, else raise UnsafeURLError."""
    if not url or not isinstance(url, str):
        raise UnsafeURLError("empty url")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError("only http/https is allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("missing host")
    # Resolve every address the host maps to; all must be public.
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeURLError(f"cannot resolve host: {e}") from e
    ips = {info[4][0] for info in infos}
    if not ips:
        raise UnsafeURLError("host did not resolve")
    for ip in ips:
        if not _ip_is_public(ip):
            raise UnsafeURLError(f"host resolves to a non-public address ({ip})")
    return url
