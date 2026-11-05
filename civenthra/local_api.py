"""Shared protections for loopback-only HTTP services.

Loopback binding prevents remote hosts from connecting, but it does not stop a
web page in the user's browser or another local process from sending requests.
These helpers add strict Host/origin checks and a per-run bearer token.  The
token is CSRF resistance for local services, not an account credential.
"""
from __future__ import annotations

import ipaddress
import secrets
import socket
from urllib.parse import urlsplit

TOKEN_HEADER = "X-Civenthra-Token"
TOKEN_ENV = "CIVENTHRA_LOCAL_API_TOKEN"


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_header_is_local(value: str | None, port: int) -> bool:
    try:
        parsed = urlsplit("//" + (value or ""))
        return is_loopback_host(parsed.hostname) and parsed.port == int(port)
    except ValueError:
        return False


def origin_matches(origin: str | None, host_header: str | None) -> bool:
    """Accept no Origin (native client) or the exact local HTTP origin."""
    if not origin:
        return True
    try:
        source = urlsplit(origin)
        target = urlsplit("//" + (host_header or ""))
        return (
            source.scheme == "http"
            and source.hostname == target.hostname
            and source.port == target.port
            and is_loopback_host(source.hostname)
        )
    except ValueError:
        return False


def token_matches(provided: str | None, expected: str | None) -> bool:
    return bool(provided and expected and secrets.compare_digest(provided, expected))


def loopback_http_base_url(value: str) -> str:
    """Return a canonical local HTTP base URL or fail closed."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid local sidecar URL") from exc
    if (
        parsed.scheme != "http"
        or not is_loopback_host(parsed.hostname)
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("sidecar URL must be plain HTTP on an explicit loopback port")
    host = parsed.hostname
    authority = "[%s]" % host if ":" in host else host
    return "http://%s:%d" % (authority, port)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def free_loopback_port() -> int:
    """Pick an ephemeral loopback port for a child process."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def loopback_port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.2)
        return client.connect_ex(("127.0.0.1", int(port))) == 0
