"""SSRF guard for coordinator-supplied URLs (P2-1).

Fetching a URL someone typed means making a server-side request on their behalf,
from inside our network. Unconstrained, that reaches cloud metadata endpoints,
localhost admin panels, sibling containers, and anything else on the private
side of the host — a well-known way to turn a convenience feature into a
credential leak.

Design stance: **fail closed**. Anything not positively recognised as a public
http(s) endpoint on a web port is rejected. A false rejection costs the
coordinator one manual entry (which is a first-class path in this feature
anyway); a false acceptance costs us the host.

Known limitation, stated rather than hidden: this validates the address at check
time, so a DNS record that changes between the check and the fetch (classic DNS
rebinding) is not fully closed by validation alone. ``fetch_url`` mitigates by
pinning to the checked address; anything stronger needs a network-level egress
policy, which is out of scope for a prototype.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from typing import Callable, List, Optional

#: Ports we will fetch from. Anything else is almost certainly an internal
#: service (SSH, databases, caches, admin panels) rather than an event page.
ALLOWED_PORTS = frozenset({80, 443})

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: A URL longer than this is not an event page anyone typed.
MAX_URL_LENGTH = 2048


class UnsafeUrlError(ValueError):
    """The URL is not safe to fetch server-side. Message is user-facing."""


def is_private_address(ip: str) -> bool:
    """True for any address we must never fetch from.

    Covers loopback, RFC1918, link-local (including the 169.254.169.254 cloud
    metadata endpoint), unique-local IPv6, carrier-grade NAT, multicast and
    reserved ranges. Unparseable input is treated as private — fail closed.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return True
    if addr.is_multicast or addr.is_reserved or addr.is_unspecified:
        return True
    # Carrier-grade NAT (100.64.0.0/10) is not flagged by is_private.
    if addr.version == 4 and addr in ipaddress.ip_network("100.64.0.0/10"):
        return True
    if addr.version == 4 and addr == ipaddress.ip_address("255.255.255.255"):
        return True
    return False


def _default_resolver(host: str) -> List[str]:
    infos = socket.getaddrinfo(host, None)
    return [i[4][0] for i in infos]


def assert_fetchable(
    url: str,
    resolver: Optional[Callable[[str], List[str]]] = None,
) -> str:
    """Validate ``url`` for server-side fetching; return it normalised.

    Raises ``UnsafeUrlError`` with a message suitable for showing the
    coordinator. ``resolver`` is injectable so the guard is testable offline —
    a security check that needs the network to run is a check that gets skipped.
    """
    resolver = resolver or _default_resolver

    if not url or not url.strip():
        raise UnsafeUrlError("Enter a URL.")
    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise UnsafeUrlError(
            f"That URL is too long ({len(url)} characters, limit {MAX_URL_LENGTH})."
        )

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(
            f"Only http and https URLs can be fetched (got scheme "
            f"{parsed.scheme or 'none'!r})."
        )
    if parsed.username or parsed.password:
        raise UnsafeUrlError(
            "Remove the username/password from the URL — credentials in a link "
            "are not supported and would be stored in the decision log."
        )

    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("That URL has no host.")

    try:
        port = parsed.port
    except ValueError:
        raise UnsafeUrlError("That URL has an invalid port.") from None
    if port is not None and port not in ALLOWED_PORTS:
        raise UnsafeUrlError(
            f"Port {port} is not allowed — only standard web ports "
            f"({', '.join(str(p) for p in sorted(ALLOWED_PORTS))}) can be fetched."
        )

    try:
        addresses = resolver(host)
    except Exception:
        raise UnsafeUrlError(f"Could not resolve {host!r}. Check the address.") from None
    if not addresses:
        raise UnsafeUrlError(f"Could not resolve {host!r}. Check the address.")

    # Fail closed on ANY private answer: a host resolving to both a public and a
    # private address must not be fetched by picking the convenient one.
    for ip in addresses:
        if is_private_address(ip):
            raise UnsafeUrlError(
                f"{host!r} resolves to a private or internal address, which "
                "cannot be fetched. Use a public event page, or enter the "
                "details manually."
            )

    normalised = urllib.parse.urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path or "/",
        parsed.query,
        "",  # fragments are never sent to the server
    ))
    return normalised
