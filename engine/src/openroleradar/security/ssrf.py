"""SSRF protection via IP range validation and DNS resolution checks."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from functools import lru_cache

# Hostname → (monotonic_expiry, ips). Avoids getaddrinfo on every Greenhouse board.
_DNS_CACHE: dict[str, tuple[float, tuple[str, ...]]] = {}
_DNS_CACHE_TTL_SECONDS = 300.0
_DNS_NEGATIVE_TTL_SECONDS = 30.0
_DNS_NEGATIVE: dict[str, float] = {}

BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("100::/64"),
    ipaddress.ip_network("64:ff9b:1::/48"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
)

METADATA_IPV4 = frozenset({"169.254.169.254", "169.254.170.2"})
METADATA_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
    }
)


class SSRFError(ValueError):
    """Raised when a URL targets a private, local, or otherwise blocked destination."""


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address(value.strip().strip("[]"))


def is_private_or_reserved_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True when an IP is non-public or in a blocked range."""
    if str(address) in METADATA_IPV4:
        return True
    if address.is_private or address.is_loopback or address.is_link_local:
        return True
    if address.is_multicast or address.is_reserved or address.is_unspecified:
        return True
    if isinstance(address, ipaddress.IPv4Address) and (
        address.is_private or address in ipaddress.ip_network("100.64.0.0/10")
    ):
        return True
    return any(address in network for network in BLOCKED_NETWORKS)


def is_blocked_ip(ip_str: str) -> bool:
    """Return True when the IP string resolves to a blocked address."""
    try:
        return is_private_or_reserved_ip(_parse_ip(ip_str))
    except ValueError:
        return True


def _resolve_host_ips_sync(hostname: str) -> list[str]:
    hostname = hostname.strip().lower().rstrip(".")
    if hostname in METADATA_HOSTNAMES:
        raise SSRFError(f"Blocked metadata hostname: {hostname}")
    if hostname == "localhost":
        raise SSRFError("Blocked localhost hostname")

    try:
        literal = _parse_ip(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        if is_private_or_reserved_ip(literal):
            raise SSRFError(f"Blocked IP literal in URL host: {hostname}")
        return [str(literal)]

    try:
        infos = socket.getaddrinfo(
            hostname,
            None,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise SSRFError(f"DNS resolution failed for {hostname}: {exc}") from exc

    if not infos:
        raise SSRFError(f"No DNS records found for {hostname}")

    ips: list[str] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip = str(sockaddr[0])
        if ip in seen:
            continue
        seen.add(ip)
        ips.append(ip)

    if not ips:
        raise SSRFError(f"No usable DNS records for {hostname}")

    blocked = [ip for ip in ips if is_blocked_ip(ip)]
    if blocked:
        raise SSRFError(
            f"Hostname {hostname} resolves to blocked address(es): {', '.join(blocked)}"
        )
    return ips


async def resolve_host_ips(hostname: str) -> list[str]:
    """Resolve a hostname and return only public IP addresses.

    Successful lookups are cached briefly so sharded syncs against shared ATS
    hosts (boards-api.greenhouse.io, etc.) do not pay DNS on every board.
    """
    key = hostname.strip().lower().rstrip(".")
    now = time.monotonic()
    cached = _DNS_CACHE.get(key)
    if cached is not None and cached[0] > now:
        return list(cached[1])
    negative_until = _DNS_NEGATIVE.get(key)
    if negative_until is not None and negative_until > now:
        raise SSRFError(f"DNS resolution recently failed for {hostname} (cached)")

    try:
        ips = await asyncio.to_thread(_resolve_host_ips_sync, hostname)
    except SSRFError:
        _DNS_NEGATIVE[key] = now + _DNS_NEGATIVE_TTL_SECONDS
        _DNS_CACHE.pop(key, None)
        raise

    _DNS_CACHE[key] = (now + _DNS_CACHE_TTL_SECONDS, tuple(ips))
    _DNS_NEGATIVE.pop(key, None)
    return list(ips)


def clear_dns_cache() -> None:
    """Clear hostname resolution caches (tests / long-running workers)."""
    _DNS_CACHE.clear()
    _DNS_NEGATIVE.clear()


async def assert_public_host(hostname: str) -> list[str]:
    """Validate that a hostname resolves only to public IPs."""
    return await resolve_host_ips(hostname)


async def validate_url_target(hostname: str, port: int | None = None) -> None:
    """Validate hostname and optional port for outbound HTTP requests."""
    if port is not None and not (1 <= port <= 65535):
        raise SSRFError(f"Invalid port: {port}")
    await assert_public_host(hostname)


@lru_cache(maxsize=256)
def cached_is_blocked_ip(ip_str: str) -> bool:
    """Cached wrapper for repeated IP checks within a process."""
    return is_blocked_ip(ip_str)
