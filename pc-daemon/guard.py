"""Browser-boundary protection for the daemon's HTTP and WebSocket endpoints.

Loopback is trusted by the API (anything running on the PC can already control it), which makes
the daemon a target for *web pages the user visits*:

- Cross-site WebSocket hijacking: browsers do not apply CORS to WebSockets, so a page on any
  site can open ws://127.0.0.1:8765/ws. We therefore reject any request whose Origin header is
  not same-origin or explicitly allowed.
- DNS rebinding: an attacker hostname resolving to 127.0.0.1 makes the browser treat the daemon
  as same-origin. We therefore only serve requests addressed to an IP literal, localhost or a
  ``.local`` mDNS name - never an arbitrary DNS name.

Requests with no Origin (curl, the daemon's own tools, native apps) are not browser traffic and
fall through to the normal token / loopback authentication.
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Iterable, MutableMapping
from typing import Any
from urllib.parse import urlsplit

Scope = MutableMapping[str, Any]

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),  # the pairing link carries a secret
    (b"x-frame-options", b"DENY"),
    (
        b"content-security-policy",
        b"default-src 'self'; connect-src 'self' ws: wss:; "
        b"img-src 'self' data: https://i.ytimg.com https://*.ytimg.com "
        b"https://*.googleusercontent.com; "
        b"style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
    ),
)


def hostname_of(host_header: str) -> str:
    """Strip the port (and IPv6 brackets) from a Host header value."""
    host = host_header.strip().lower()
    if host.startswith("["):  # [::1]:8765
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def host_allowed(host_header: str) -> bool:
    name = hostname_of(host_header)
    if not name:
        return False
    if name == "localhost" or name.endswith(".local"):
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def origin_allowed(origin: str, host_header: str, allowed: frozenset[str]) -> bool:
    """Same-origin (Origin host:port equals the Host the request was sent to) or allow-listed."""
    if origin in allowed:
        return True
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False  # "null" (sandboxed iframes, file://) and exotic schemes
    return parts.netloc.lower() == host_header.strip().lower()


class GuardMiddleware:
    def __init__(self, app: Any, *, allowed_origins: Iterable[str] = ()) -> None:
        self.app = app
        self._allowed = frozenset(o.rstrip("/") for o in allowed_origins)

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        host = headers.get(b"host", b"").decode("latin-1")
        origin = headers.get(b"origin", b"").decode("latin-1")
        if not host_allowed(host):
            await self._deny(scope, receive, send, "forbidden host")
            return
        if origin and not origin_allowed(origin, host, self._allowed):
            await self._deny(scope, receive, send, "forbidden origin")
            return
        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in SECURITY_HEADERS if k not in present]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    async def _deny(scope: Scope, receive: Any, send: Any, reason: str) -> None:
        if scope["type"] == "websocket":
            await receive()  # the pending websocket.connect
            await send({"type": "websocket.close", "code": 1008, "reason": reason})
            return
        body = json.dumps({"detail": reason}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
