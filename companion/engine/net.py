"""The one door for server-side downloads of feeds, enclosures and transcripts.

`safe_fetch` allows http(s) only, follows at most 5 redirects and caps the size. Before every
request - the first one and each redirect - it resolves the host and refuses loopback, private,
link-local, CGNAT, multicast, reserved and cloud-metadata addresses. It then connects to the
address it checked (the URL is pinned to that IP, with the real Host header and TLS name), so a
second DNS answer can never point the connection somewhere else.

`allow_private=True` (the companion's ALLOW_PRIVATE_FEEDS=true) opens loopback, LAN, CGNAT and
link-local addresses for feeds hosted on your own network. Cloud-metadata endpoints stay refused
even then: no podcast lives there, and they hand out cloud credentials.

This is the only module in the engine that touches the network.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # httpx is imported lazily, so the rest of the engine works without it
    import httpx

DEFAULT_MAX_BYTES = 600 * 1024 * 1024
MAX_REDIRECTS = 5
USER_AGENT = "Mozilla/5.0 (compatible; podcast-companion)"
_REDIRECTS = {301, 302, 303, 307, 308}

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], list[str]]

_ALWAYS_BLOCKED = [ipaddress.ip_network(net) for net in (
    "0.0.0.0/8",            # "this network"; 0.0.0.0 reaches the local host on Linux
    "192.0.0.0/24",         # IETF protocol assignments (includes Oracle's 192.0.0.192 metadata)
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",   # documentation
    "192.88.99.0/24",       # 6to4 relay anycast
    "198.18.0.0/15",        # benchmarking
    "224.0.0.0/4",          # multicast
    "240.0.0.0/4",          # reserved and broadcast
    "::/96",                # unspecified, loopback and IPv4-compatible
    "64:ff9b:1::/48",       # local-use NAT64
    "100::/64",             # discard-only
    "2001::/23",            # IETF protocol assignments (Teredo, benchmarking, ...)
    "2001:db8::/32",        # documentation
    "ff00::/8",             # multicast
)]
_PRIVATE = [ipaddress.ip_network(net) for net in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",       # private
    "100.64.0.0/10",        # CGNAT (and Tailscale); Alibaba metadata is listed below
    "127.0.0.0/8",          # loopback
    "169.254.0.0/16",       # link-local; AWS/GCP/Azure metadata is listed below
    "::1/128",
    "fc00::/7",             # unique local; AWS's IPv6 metadata is listed below
    "fe80::/10",            # link-local
    "fec0::/10",            # site-local (deprecated)
)]
METADATA_ADDRESSES = frozenset(ipaddress.ip_address(ip) for ip in (
    "169.254.169.254",      # AWS, GCP, Azure, Oracle, DigitalOcean, OpenStack
    "169.254.170.2",        # AWS ECS task credentials
    "169.254.169.253",      # AWS DNS
    "169.254.169.123",      # AWS time sync
    "100.100.100.200",      # Alibaba Cloud
    "192.0.0.192",          # Oracle Cloud (legacy)
    "fd00:ec2::254",        # AWS IPv6
    "fd00:ec2::23",         # AWS IPv6 time sync
))
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_GLOBAL_V6 = ipaddress.ip_network("2000::/3")


class FetchError(RuntimeError):
    """A download failed. The message is safe to show to the user."""


class BlockedAddressError(FetchError):
    """The URL points at an address the server must not reach."""


@dataclass(frozen=True)
class Fetched:
    path: str
    url: str            # the final URL, after redirects
    size: int
    content_type: str


def _embedded_v4(ip: IPAddress) -> ipaddress.IPv4Address | None:
    """The IPv4 address hidden inside an IPv4-mapped, NAT64 or 6to4 IPv6 address."""
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.ipv4_mapped:
        return ip.ipv4_mapped
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.sixtofour:
        return ip.sixtofour
    return None


def blocked_reason(ip: IPAddress | str, allow_private: bool = False) -> str | None:
    """Why the server must not connect to `ip`, or None when it may."""
    try:
        ip = ipaddress.ip_address(str(ip).split("%", 1)[0])
    except ValueError:
        return "not an IP address"
    if ip in METADATA_ADDRESSES:
        return "a cloud metadata address"
    inner = _embedded_v4(ip)
    if inner is not None:
        return blocked_reason(inner, allow_private)
    if any(ip in net for net in _PRIVATE):           # before the reserved list, which holds ::/96
        return None if allow_private else "a private or local address"
    if any(ip in net for net in _ALWAYS_BLOCKED):
        return "a reserved address"
    if isinstance(ip, ipaddress.IPv6Address) and ip not in _GLOBAL_V6:
        return "a reserved address"
    if ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return "a reserved address"
    if not allow_private and (ip.is_private or ip.is_loopback or ip.is_link_local):
        return "a private or local address"
    return None


def check_address(ip: IPAddress | str, allow_private: bool = False, host: str = "") -> None:
    reason = blocked_reason(ip, allow_private)
    if reason:
        where = f"{host} resolves to {reason}" if host and host != str(ip) else f"that is {reason}"
        hint = (" Set ALLOW_PRIVATE_FEEDS=true to allow feeds on your own network."
                if reason == "a private or local address" else "")
        raise BlockedAddressError(f"Refused to download: {where}.{hint}")


def system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _addresses(host: str, port: int, resolver: Resolver | None) -> list[IPAddress]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        found = (resolver or system_resolver)(host, port)
    except (OSError, UnicodeError) as exc:
        raise FetchError(f"Could not find the server {host}.") from exc
    out: list[IPAddress] = []
    for addr in found:
        try:
            ip = ipaddress.ip_address(str(addr).split("%", 1)[0])
        except ValueError as exc:
            raise FetchError(f"Could not find the server {host}.") from exc
        if ip not in out:
            out.append(ip)
    if not out:
        raise FetchError(f"Could not find the server {host}.")
    return out


def _check_url(url: httpx.URL) -> None:
    if url.scheme not in ("http", "https"):
        raise BlockedAddressError("Only http and https links can be downloaded.")
    if not url.raw_host:
        raise BlockedAddressError("The link has no server name.")


def _connect(current: httpx.URL, addresses: list[IPAddress], transport: httpx.BaseTransport | None,
             wait: httpx.Timeout, headers: Mapping[str, str] | None
             ) -> tuple[httpx.Response, Callable[[], None]]:
    """Send the GET to the first checked address that accepts a connection. The URL is pinned
    to that IP; the Host header and the TLS name stay the real host's."""
    import httpx

    host = current.raw_host.decode("ascii")
    last_error: Exception | None = None
    for ip in addresses:
        pinned = current.copy_with(host=str(ip))
        extensions: dict[str, Any] = {}
        if current.scheme == "https" and pinned.raw_host != current.raw_host:
            extensions["sni_hostname"] = host
        owned = transport is None                    # a transport we made is ours to close
        client = httpx.Client(transport=transport or httpx.HTTPTransport(retries=0),
                              follow_redirects=False, trust_env=False, timeout=wait)
        try:
            request = client.build_request(
                "GET", pinned, extensions=extensions,
                headers={"User-Agent": USER_AGENT, **dict(headers or {}),
                         "Host": current.netloc.decode("ascii")})
            response = client.send(request, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last_error = exc
            if owned:
                client.close()
            continue
        except httpx.HTTPError as exc:
            if owned:
                client.close()
            raise FetchError(f"The download from {host} failed ({type(exc).__name__}).") from exc

        def close(response: httpx.Response = response, client: httpx.Client = client,
                  owned: bool = owned) -> None:
            response.close()
            if owned:
                client.close()
        return response, close
    raise FetchError(f"Could not connect to {host}.") from last_error


def _open(url: str, *, allow_private: bool, resolver: Resolver | None,
          transport: httpx.BaseTransport | None, timeout: float,
          headers: Mapping[str, str] | None) -> tuple[httpx.Response, httpx.URL, Callable[[], None]]:
    """GET `url`, following redirects by hand so every hop is checked. Returns the open
    streaming response, the final URL and a function that closes both."""
    import httpx

    try:
        current = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError) as exc:
        raise BlockedAddressError("That is not a valid link.") from exc
    wait = httpx.Timeout(min(timeout, 30.0), connect=min(timeout, 10.0))
    for _hop in range(MAX_REDIRECTS + 1):
        _check_url(current)
        host = current.raw_host.decode("ascii")
        port = current.port or (443 if current.scheme == "https" else 80)
        addresses = _addresses(host, port, resolver)
        for ip in addresses:                         # every answer must pass, not just the first
            check_address(ip, allow_private, host)
        response, closer = _connect(current, addresses, transport, wait, headers)
        if response.status_code in _REDIRECTS and response.headers.get("location"):
            location = response.headers["location"]
            closer()
            try:
                current = current.join(location)
            except (httpx.InvalidURL, ValueError) as exc:
                raise FetchError("The server redirected to an invalid link.") from exc
            continue
        if response.status_code >= 300:
            status = response.status_code
            closer()
            raise FetchError(f"The server {host} answered {status}.")
        return response, current, closer
    raise FetchError(f"Too many redirects (more than {MAX_REDIRECTS}).")


def _chunks(response: httpx.Response, max_bytes: int, deadline: float, host: str) -> Iterator[bytes]:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise FetchError(f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.")
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise FetchError(f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.")
        if time.monotonic() > deadline:
            raise FetchError(f"The download from {host} took too long.")
        yield chunk


def safe_fetch(url: str, dest: str | os.PathLike[str], max_bytes: int = DEFAULT_MAX_BYTES,
               allow_private: bool = False, *, resolver: Resolver | None = None,
               transport: httpx.BaseTransport | None = None, timeout: float = 900.0,
               headers: Mapping[str, str] | None = None) -> Fetched:
    """Download `url` to `dest` under the rules in this module's docstring.

    The file appears at `dest` only when complete: it is written to a private temporary file
    in the same folder and renamed. `resolver(host, port) -> [ip, ...]` and `transport` (an
    httpx transport) exist for tests; by default the system resolver and a fresh connection
    are used. Raises BlockedAddressError or FetchError with a message the user can act on."""
    import httpx

    dest = os.fspath(dest)
    folder = os.path.dirname(os.path.abspath(dest))
    os.makedirs(folder, exist_ok=True)
    deadline = time.monotonic() + timeout
    response, final, close = _open(url, allow_private=allow_private, resolver=resolver,
                                   transport=transport, timeout=timeout, headers=headers)
    handle, part = tempfile.mkstemp(dir=folder, prefix=".download-", suffix=".part")
    size = 0
    try:
        with os.fdopen(handle, "wb") as out:
            for chunk in _chunks(response, max_bytes, deadline, final.raw_host.decode("ascii")):
                out.write(chunk)
                size += len(chunk)
        os.replace(part, dest)
    except httpx.HTTPError as exc:
        raise FetchError(f"The download was interrupted ({type(exc).__name__}).") from exc
    finally:
        close()
        if os.path.exists(part):
            os.remove(part)
    return Fetched(dest, str(final), size, response.headers.get("content-type", ""))


def safe_read(url: str, max_bytes: int = 20 * 1024 * 1024, allow_private: bool = False, *,
              resolver: Resolver | None = None, transport: httpx.BaseTransport | None = None,
              timeout: float = 120.0, headers: Mapping[str, str] | None = None) -> bytes:
    """Like `safe_fetch`, for a feed or transcript small enough to keep in memory."""
    import httpx

    deadline = time.monotonic() + timeout
    response, final, close = _open(url, allow_private=allow_private, resolver=resolver,
                                   transport=transport, timeout=timeout, headers=headers)
    try:
        return b"".join(_chunks(response, max_bytes, deadline, final.raw_host.decode("ascii")))
    except httpx.HTTPError as exc:
        raise FetchError(f"The download was interrupted ({type(exc).__name__}).") from exc
    finally:
        close()
