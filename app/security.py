"""Small pure-ASGI middlewares: origin checks, security headers, request-size limits."""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def _request_host(scope: Scope) -> str:
    return (_header(scope, b"host") or "").strip().lower()


def _netloc(url: str) -> str | None:
    """The host[:port] of an absolute URL, or None when it cannot be trusted."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return parts.netloc.lower()


async def _plain_response(send: Send, status: int, text: str) -> None:
    body = text.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class OriginCheckMiddleware:
    """Rejects state-changing requests and WebSocket handshakes whose Origin is not this host.

    Browsers always attach ``Origin`` to cross-site POSTs and WebSocket handshakes, so this
    stops CSRF and cross-site WebSocket hijacking without per-form tokens. ``Referer`` is
    accepted as a fallback for the rare same-site POST that lacks ``Origin``.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    def _allowed(self, scope: Scope) -> bool:
        host = _request_host(scope)
        if not host:
            return False
        source = _header(scope, b"origin")
        if source is None or source.strip().lower() == "null":
            source = _header(scope, b"referer")
        if not source:
            return False
        return _netloc(source) == host

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("method", "GET").upper() in STATE_CHANGING_METHODS:
            if not self._allowed(scope):
                await _plain_response(send, 403, "Cross-site request refused.")
                return
        elif scope["type"] == "websocket":
            if not self._allowed(scope):
                await send({"type": "websocket.close", "code": 4403})
                return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Adds a strict CSP and the usual hardening headers to every HTTP response."""

    def __init__(self, app: ASGIApp, *, https_only: bool, static_prefix: str = "/static") -> None:
        self.app = app
        self.https_only = https_only
        self.static_prefix = static_prefix

    def _csp(self, scope: Scope) -> bytes:
        host = _request_host(scope)
        sockets = f" ws://{host} wss://{host}" if host else ""
        policy = (
            "default-src 'none'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            f"connect-src 'self'{sockets}; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'; "
            "object-src 'none'"
        )
        return policy.encode("ascii")

    def _extra_headers(self, scope: Scope) -> list[tuple[bytes, bytes]]:
        headers = [
            (b"content-security-policy", self._csp(scope)),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), interest-cohort=()"),
            (b"cross-origin-opener-policy", b"same-origin"),
            (b"cross-origin-resource-policy", b"same-origin"),
        ]
        if not scope.get("path", "").startswith(self.static_prefix):
            headers.append((b"cache-control", b"no-store"))
        if self.https_only:
            headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
        return headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = list(message.get("headers", []))
                present = {key for key, _ in existing}
                for key, value in self._extra_headers(scope):
                    if key not in present:
                        existing.append((key, value))
                message["headers"] = existing
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Caps request bodies so an unauthenticated client cannot make the server buffer megabytes."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _header(scope, b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            await _plain_response(send, 413, "Request body too large.")
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge:
            if not response_started:
                await _plain_response(send, 413, "Request body too large.")
