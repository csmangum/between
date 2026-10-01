"""ASGI middleware: the headers every response carries, and the same-origin lock on writes."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import config

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class SecurityHeadersMiddleware:
    """Headers every response carries. Static files are cacheable; everything else is not."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        host = Headers(scope=scope).get("host", "")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                    f"font-src 'self'; connect-src 'self' ws://{host} wss://{host}; frame-ancestors 'none'; "
                    "form-action 'self'; base-uri 'none'; object-src 'none'",
                )
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "same-origin")
                headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), interest-cohort=()")
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                if config.HTTPS_ONLY:
                    headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
                if not path.startswith("/static/"):
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, send_wrapper)


class SameOriginMiddleware:
    """Cross-site writes and socket upgrades are refused when the browser names another origin.
    SameSite=strict already keeps the cookie home; this is the second lock."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _foreign(scope: Scope) -> bool:
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is None:
            return False
        host = headers.get("host", "")
        origin_url = urlsplit(origin)
        raw_scheme: str = scope.get("scheme") or ""
        scheme = {"ws": "http", "wss": "https"}.get(raw_scheme, raw_scheme)
        return (
            origin == "null" or origin_url.scheme.lower() != scheme.lower() or origin_url.netloc.lower() != host.lower()
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("method") in UNSAFE_METHODS and self._foreign(scope):
            response = JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
            await response(scope, receive, send)
            return
        if scope["type"] == "websocket" and self._foreign(scope):
            await send({"type": "websocket.close", "code": 4403})
            return
        await self.app(scope, receive, send)
