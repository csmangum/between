"""Builds the app: middleware, static files, exception handlers, routers, and the health check.
Routes live in `app/routes/`, request helpers in `app/views.py`, middleware in `app/security.py`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import Scope

from . import auth, config, search
from .db import Base, db_ok, engine, migrate
from .events import stream
from .routes import routers
from .security import SameOriginMiddleware, SecurityHeadersMiddleware
from .views import (
    APP_NAME,
    BASE_DIR,
    RedirectNeeded,
    current_user,
    epoch_ms,
    reading_time,
    render,
    when_tag,
)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    migrate()
    search.ensure_index(engine)
    auth.load_people()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield
    stream.close_all()


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers.setdefault("Cache-Control", "public, max-age=86400")
        return response


app = FastAPI(title=APP_NAME, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=400)
app.add_middleware(SameOriginMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    same_site="strict",
    https_only=config.HTTPS_ONLY,
    max_age=config.SESSION_IDLE_SECONDS,
)
app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", CachedStaticFiles(directory=str(BASE_DIR / "static")), name="static")

for router in routers:
    app.include_router(router)


@app.exception_handler(RedirectNeeded)
async def redirect_needed(_: Request, exc: RedirectNeeded) -> Response:
    return RedirectResponse(exc.url, status_code=303)


@app.exception_handler(StarletteHTTPException)
async def http_exception(request: Request, exc: StarletteHTTPException) -> Response:
    if exc.status_code != 404 or request.url.path.startswith("/static"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if not current_user(request):
        return RedirectResponse("/login", status_code=303)
    return render(request, "missing.html", status_code=404)


@app.get("/health")
def health() -> JSONResponse:
    healthy = db_ok()
    return JSONResponse(
        {"ok": healthy, "app": APP_NAME, "db": "ok" if healthy else "error"},
        status_code=200 if healthy else 503,
    )
