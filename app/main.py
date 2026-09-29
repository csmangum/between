from __future__ import annotations

import json
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import access, agent, auth, config, share, store
from .db import Base, SessionLocal, db_ok, engine, get_db, migrate
from .hub import hub
from .markdown_render import clear_markdown_cache, render_markdown
from .models import ChatMessage, Comment, Topic, Writing, utcnow
from .share import ShareStatus
from .table import build_table
from .throttle import login_throttle

log = logging.getLogger("between")

APP_NAME = config.APP_NAME
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


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
        scheme = {"ws": "http", "wss": "https"}.get(scope.get("scheme", ""), scope.get("scheme", ""))
        return (
            origin == "null"
            or origin_url.scheme.lower() != scheme.lower()
            or origin_url.netloc.lower() != host.lower()
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

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers.setdefault("Cache-Control", "public, max-age=86400")
        return response


app.mount("/static", CachedStaticFiles(directory=str(BASE_DIR / "static")), name="static")


def md(text: str) -> str:
    return render_markdown(text)


def fmt_dt(value: datetime | None) -> str:
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%b %d, %Y · %H:%M")


def fmt_dt_soft(value: datetime | None) -> str:
    """A quieter relative time for the room's pacing."""
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone()
    now = datetime.now(timezone.utc).astimezone()
    seconds = int((now - local).total_seconds())
    if seconds < 45:
        return "just now"
    if seconds < 90:
        return "a minute ago"
    if seconds < 45 * 60:
        mins = max(2, seconds // 60)
        return f"{mins} minutes ago"
    if seconds < 90 * 60:
        return "an hour ago"
    if local.date() == now.date():
        return f"today · {local.strftime('%H:%M')}"
    if local.date() == (now.date() - timedelta(days=1)):
        return f"yesterday · {local.strftime('%H:%M')}"
    if seconds < 60 * 60 * 24 * 7:
        return local.strftime("%A · %H:%M")
    return local.strftime("%b %d, %Y")


def count_label(n: int, singular: str, plural: str | None = None) -> str:
    number = int(n or 0)
    word = singular if number == 1 else (plural or f"{singular}s")
    return f"{number} {word}"


templates.env.filters["md"] = md
templates.env.filters["when"] = fmt_dt
templates.env.filters["when_soft"] = fmt_dt_soft
templates.env.filters["count_label"] = count_label
templates.env.filters["share_label"] = share.label
templates.env.globals["app_name"] = APP_NAME
templates.env.globals["display_for"] = auth.display_for


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    migrate()
    auth.load_people()


def current_user(request: Request) -> str | None:
    return auth.session_user(request.session)


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise RedirectNeeded("/login")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class RedirectNeeded(Exception):
    def __init__(self, url: str):
        self.url = url


@app.exception_handler(RedirectNeeded)
async def redirect_needed(_, exc: RedirectNeeded):
    return RedirectResponse(exc.url, status_code=303)


@app.exception_handler(StarletteHTTPException)
async def http_exception(request: Request, exc: StarletteHTTPException):
    if exc.status_code != 404 or request.url.path.startswith("/static"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if not current_user(request):
        return RedirectResponse("/login", status_code=303)
    return render(request, "missing.html", status_code=404)


def flash(request: Request, message: str, kind: str = "ok") -> None:
    flashes = request.session.get("flashes") or []
    flashes.append({"message": message, "kind": kind})
    request.session["flashes"] = flashes


def pop_flashes(request: Request) -> list[dict[str, str]]:
    return list(request.session.pop("flashes", []) or [])


def sealed_offer_count(user: str | None, db: Session | None = None) -> int:
    if not user:
        return 0
    own = db is not None
    session = db or SessionLocal()
    try:
        return (
            session.query(Topic)
            .filter(Topic.created_by != user, Topic.share_status == "offered")
            .count()
        )
    finally:
        if not own:
            session.close()


def attach_topic_counts(db: Session, topics: list[Topic], viewer: str) -> None:
    """Batch-load writing/message counts to avoid N+1 on the desk."""
    if not topics:
        return
    ids = [t.id for t in topics]
    writing_counts = dict(
        db.query(Writing.topic_id, func.count(Writing.id))
        .filter(
            Writing.topic_id.in_(ids),
            or_(Writing.author == viewer, Writing.share_status.in_(("offered", "shared"))),
        )
        .group_by(Writing.topic_id)
        .all()
    )
    message_counts = dict(
        db.query(ChatMessage.topic_id, func.count(ChatMessage.id))
        .filter(ChatMessage.topic_id.in_(ids))
        .group_by(ChatMessage.topic_id)
        .all()
    )
    for topic in topics:
        topic.writing_count = writing_counts.get(topic.id, 0)
        topic.message_count = message_counts.get(topic.id, 0)


def ctx(request: Request, db: Session | None = None, **extra: Any) -> dict[str, Any]:
    user = current_user(request)
    people = auth.load_people()
    sealed = extra.pop("sealed_count", None)
    if sealed is None:
        sealed = sealed_offer_count(user, db)
    return {
        "request": request,
        "user": user,
        "display": auth.display_for(user) if user else None,
        "other": next((p.display for name, p in people.items() if name != user), None) if user else None,
        "other_user": access.other_username(user) if user else None,
        "people": people,
        "flashes": pop_flashes(request),
        "sealed_count": sealed,
        **extra,
    }


def render(request: Request, name: str, status_code: int = 200, db: Session | None = None, **extra: Any):
    return templates.TemplateResponse(
        request,
        name,
        ctx(request, db=db, **extra),
        status_code=status_code,
    )


def _apply_status(obj, status: ShareStatus) -> None:
    share.set_status(obj, status)


def _topic_anchor(topic_id: int, writing_id: int | None = None, fragment: str | None = None) -> str:
    if writing_id:
        return f"/topics/{topic_id}#writing-{writing_id}"
    if fragment:
        return f"/topics/{topic_id}#{fragment}"
    return f"/topics/{topic_id}"


def _load_topic(db: Session, topic_id: int) -> Topic | None:
    return (
        db.query(Topic)
        .options(
            selectinload(Topic.writings),
            selectinload(Topic.comments),
            selectinload(Topic.messages),
        )
        .filter(Topic.id == topic_id)
        .one_or_none()
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = client_ip(request)
    name = username.strip().lower()[:80]
    keys = [f"ip:{ip}", f"user:{name}"]
    wait = login_throttle.retry_after(keys)
    if wait > 0:
        log.warning("login throttled user=%s ip=%s wait=%.0fs", name, ip, wait)
        response = render(
            request,
            "login.html",
            status_code=429,
            error=f"Too many tries. The door opens again in {max(1, int(wait))} seconds.",
            username=username,
        )
        response.headers["Retry-After"] = str(max(1, int(wait)))
        return response
    person = auth.verify(username, password)
    if not person:
        delay = login_throttle.failed(keys)
        log.warning("login failed user=%s ip=%s next_delay=%.0fs", name, ip, delay)
        return render(
            request,
            "login.html",
            status_code=401,
            error="That name or password did not match.",
            username=username,
        )
    login_throttle.succeeded(keys)
    log.info("login ok user=%s ip=%s", person.username, ip)
    auth.start_session(request.session, person)
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def _visible_topics(db: Session, user: str) -> list[Topic]:
    topics = db.query(Topic).order_by(Topic.updated_at.desc()).all()
    mine = access.contributed_topic_ids(db, user)
    visible = [t for t in topics if access.topic_visible(user, t, contributor=t.id in mine)]
    attach_topic_counts(db, visible, user)
    return visible


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    topics = _visible_topics(db, user)
    incoming = [t for t in topics if access.topic_awaits(user, t)]
    desk = [t for t in topics if t.share_status != "shared" and not access.topic_awaits(user, t)]
    view = build_table(_shared_topics(db), user)
    agent_on = agent.configured()
    return render(
        request,
        "home.html",
        db=db,
        desk=desk,
        prompt_access={t.id: access.topic_prompt_open(user, t) for t in desk},
        incoming=incoming,
        table_count=len(view.cards),
        latest=view.lately[0] if view.lately else None,
        table_topic=view.cards[0] if view.cards else None,
        sealed_count=len(incoming),
        agent_configured=agent_on,
        agent_consents=agent.consents(db) if agent_on else {},
        agent_host=agent.provider_host() if agent_on else "",
    )


@app.post("/me/agent")
def set_agent_consent(request: Request, allow: str = Form(""), db: Session = Depends(get_db)):
    user = require_user(request)
    granted = allow.strip().lower() in {"1", "true", "yes", "on"}
    agent.set_consent(db, user, granted)
    db.commit()
    if granted:
        flash(request, f"Drafting help allowed on your side. It only works once {auth.display_for(access.other_username(user) or '')} allows it too.")
    else:
        flash(request, "Drafting help withdrawn. Nothing you both opened will be sent anywhere.")
    return RedirectResponse("/#drafting", status_code=303)


@app.post("/topics")
def create_topic(
    request: Request,
    title: str = Form(...),
    prompt: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_user(request)
    cleaned = title.strip()
    if not cleaned:
        flash(request, "A topic needs a title before it can sit on your desk.", "warn")
        return RedirectResponse("/", status_code=303)
    topic = Topic(title=cleaned, prompt=prompt.strip(), created_by=user, share_status="private")
    db.add(topic)
    db.commit()
    store.write_local(topic)
    flash(request, "Kept on your desk — only you can see it.")
    return RedirectResponse(f"/topics/{topic.id}", status_code=303)


@app.get("/topics/{topic_id}", response_class=HTMLResponse)
def topic_page(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = _load_topic(db, topic_id)
    if not topic or not access.topic_visible(user, topic):
        return RedirectResponse("/", status_code=303)
    awaiting = access.topic_awaits(user, topic)
    if not access.topic_open(user, topic):
        return render(request, "consent.html", db=db, topic=topic)

    visible_writings = [w for w in topic.writings if access.writing_visible(user, w)]
    comments_by_writing: dict[int | None, list[Comment]] = defaultdict(list)
    for c in topic.comments:
        if access.comment_visible(user, c):
            comments_by_writing[c.writing_id].append(c)
    return render(
        request,
        "topic.html",
        db=db,
        topic=topic,
        topic_prompt=topic.prompt if access.topic_prompt_open(user, topic) else "",
        writings=visible_writings,
        comments_by_writing=comments_by_writing,
        topic_shared=topic.share_status == "shared",
        awaiting=awaiting,
        agent_configured=agent.configured(),
        agent_ready=agent.allowed(db),
        agent_note=request.query_params.get("agent"),
    )


@app.post("/topics/{topic_id}/writings")
def add_writing(
    request: Request,
    topic_id: int,
    title: str = Form(""),
    body: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if not topic or not access.topic_open(user, topic):
        return RedirectResponse(f"/topics/{topic_id}", status_code=303)
    if not body.strip():
        flash(request, "A writing needs words before it can stay on the desk.", "warn")
        return RedirectResponse(f"/topics/{topic_id}#write", status_code=303)
    writing = Writing(
        topic_id=topic.id,
        author=user,
        title=title.strip(),
        body=body.strip(),
        share_status="private",
    )
    topic.updated_at = utcnow()
    db.add(writing)
    db.commit()
    store.write_local(topic, writing)
    flash(request, "Saved privately on your desk.")
    return RedirectResponse(f"/topics/{topic_id}#writing-{writing.id}", status_code=303)


@app.post("/topics/{topic_id}/comments")
def add_comment(
    request: Request,
    topic_id: int,
    body: str = Form(...),
    writing_id: int | None = Form(None),
    parent_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if not topic or not access.topic_open(user, topic):
        return RedirectResponse(f"/topics/{topic_id}", status_code=303)
    if not body.strip():
        flash(request, "A note needs words before it can stay.", "warn")
        return RedirectResponse(_topic_anchor(topic_id, writing_id, "comments"), status_code=303)
    if writing_id:
        writing = db.get(Writing, writing_id)
        if not writing or writing.topic_id != topic.id or not access.writing_open(user, writing):
            return RedirectResponse(f"/topics/{topic_id}", status_code=303)
    if parent_id:
        parent = db.get(Comment, parent_id)
        if (
            not parent
            or parent.topic_id != topic.id
            or (parent.writing_id or None) != (writing_id or None)
            or not access.comment_open(user, parent)
        ):
            return RedirectResponse(_topic_anchor(topic_id, writing_id, "comments"), status_code=303)
    comment = Comment(
        topic_id=topic.id,
        writing_id=writing_id or None,
        parent_id=parent_id or None,
        author=user,
        body=body.strip(),
        share_status="private",
    )
    topic.updated_at = utcnow()
    db.add(comment)
    db.commit()
    store.write_local(topic, comment=comment)
    flash(request, "Note kept on your side.")
    return RedirectResponse(_topic_anchor(topic_id, writing_id, "comments"), status_code=303)


@app.post("/topics/{topic_id}/offer")
def offer_topic(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    other = auth.display_for(access.other_username(user) or "")
    if topic and topic.created_by == user and topic.share_status == "private":
        _apply_status(topic, "offered")
        topic.updated_at = utcnow()
        db.commit()
        store.write_local(topic)
        flash(request, f"Sent to {other}. It stays sealed until they open it.")
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@app.post("/topics/{topic_id}/accept")
def accept_topic(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if topic and topic.created_by != user and topic.share_status == "offered":
        _apply_status(topic, "shared")
        topic.updated_at = utcnow()
        db.commit()
        store.write_shared(topic)
        flash(request, "Opened. This sits on the table between you now.")
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


async def _close_topic(db: Session, topic: Topic) -> None:
    """Shared → private: everything inside returns to its author, the shared mirror goes, the margin closes."""
    writings, comments = share.fold_topic(topic)
    _apply_status(topic, "private")
    topic.updated_at = utcnow()
    db.commit()
    store.remove_shared(topic)
    store.write_local(topic)
    for w in writings:
        store.write_local(topic, w)
        store.write_revision(w)
    for c in comments:
        store.write_local(topic, comment=c)
    clear_markdown_cache()
    await hub.close_room(topic.id)


@app.post("/topics/{topic_id}/decline")
async def decline_topic(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = _load_topic(db, topic_id)
    if topic and topic.created_by != user and topic.share_status == "offered":
        await _close_topic(db, topic)
        flash(request, "Left unopened. It returned to their desk.")
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@app.post("/topics/{topic_id}/revoke")
async def revoke_topic(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = _load_topic(db, topic_id)
    if topic and topic.created_by == user and topic.share_status in {"offered", "shared"}:
        await _close_topic(db, topic)
        flash(request, "Pulled back. Everything inside returned to the desk it came from.")
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@app.post("/topics/{topic_id}/delete")
def delete_topic(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = _load_topic(db, topic_id)
    if not topic or topic.created_by != user or topic.share_status != "private":
        return RedirectResponse(f"/topics/{topic_id}", status_code=303)
    if access.has_words_from_others(topic, user):
        flash(request, "Their words are in this topic, so it stays. Only what you wrote is yours to remove.", "warn")
        return RedirectResponse(f"/topics/{topic_id}", status_code=303)
    store.delete_topic_files(topic)
    db.delete(topic)
    db.commit()
    clear_markdown_cache()
    flash(request, "Removed from your desk.")
    return RedirectResponse("/", status_code=303)


@app.post("/writings/{writing_id}/offer")
def offer_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    other = auth.display_for(access.other_username(user) or "")
    if writing and writing.author == user and writing.share_status == "private":
        if writing.topic.share_status != "shared":
            flash(request, "Open the topic together first, then send this writing.", "warn")
            return RedirectResponse(f"/topics/{writing.topic_id}", status_code=303)
        _apply_status(writing, "offered")
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_local(writing.topic, writing)
        flash(request, f"Writing sealed for {other}.")
    return RedirectResponse(f"/topics/{writing.topic_id}#writing-{writing_id}", status_code=303)


@app.post("/writings/{writing_id}/accept")
def accept_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    if writing.author != user and writing.share_status == "offered":
        if writing.topic.share_status != "shared":
            return RedirectResponse(f"/topics/{writing.topic_id}#writing-{writing_id}", status_code=303)
        _apply_status(writing, "shared")
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_shared(writing.topic, writing)
        flash(request, "Opened. This writing is kept between you.")
    return RedirectResponse(f"/topics/{writing.topic_id}#writing-{writing_id}", status_code=303)


@app.post("/writings/{writing_id}/decline")
def decline_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    if writing.author != user and writing.share_status == "offered":
        if writing.topic.share_status != "shared":
            return RedirectResponse(f"/topics/{writing.topic_id}", status_code=303)
        _apply_status(writing, "private")
        writing.topic.updated_at = utcnow()
        db.commit()
        store.remove_shared(writing.topic, writing)
        store.write_local(writing.topic, writing)
        flash(request, "Left unopened. Back on their desk.")
    return RedirectResponse(f"/topics/{writing.topic_id}", status_code=303)


@app.post("/writings/{writing_id}/revoke")
def revoke_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    if writing.author == user and writing.share_status in {"offered", "shared"}:
        share.fold_revision_into_private(writing)
        _apply_status(writing, "private")
        writing.topic.updated_at = utcnow()
        db.commit()
        store.remove_shared(writing.topic, writing)
        store.write_local(writing.topic, writing)
        store.write_revision(writing)
        clear_markdown_cache()
        flash(request, "Pulled back to your desk.")
    return RedirectResponse(f"/topics/{writing.topic_id}#writing-{writing_id}", status_code=303)


@app.post("/writings/{writing_id}/edit")
def edit_writing(
    request: Request,
    writing_id: int,
    title: str = Form(""),
    body: str = Form(...),
    db: Session = Depends(get_db),
):
    """Authors may revise private writings without a new accept cycle."""
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing or writing.author != user:
        return RedirectResponse("/", status_code=303)
    if writing.share_status == "shared":
        return _save_revision(request, writing, title, body, db)
    if writing.share_status != "private":
        flash(request, "Pull it back to your desk before editing.", "warn")
        return RedirectResponse(f"/topics/{writing.topic_id}#writing-{writing_id}", status_code=303)
    if not body.strip():
        return RedirectResponse(f"/topics/{writing.topic_id}#writing-{writing_id}", status_code=303)
    writing.title = title.strip()
    writing.body = body.strip()
    writing.updated_at = utcnow()
    writing.topic.updated_at = utcnow()
    db.commit()
    store.write_local(writing.topic, writing)
    flash(request, "Writing updated on your desk.")
    return RedirectResponse(f"/topics/{writing.topic_id}#writing-{writing_id}", status_code=303)


def _save_revision(request: Request, writing: Writing, title: str, body: str, db: Session):
    anchor = f"/topics/{writing.topic_id}#writing-{writing.id}"
    if writing.revision_status == "offered":
        flash(request, "Pull the revision back before you change it.", "warn")
        return RedirectResponse(anchor, status_code=303)
    if not body.strip():
        flash(request, "A revision needs words before it can stay on your desk.", "warn")
        return RedirectResponse(anchor, status_code=303)
    share.save_revision(writing, title.strip(), body.strip())
    writing.topic.updated_at = utcnow()
    db.commit()
    store.write_revision(writing)
    flash(request, "Revision kept on your desk. They still have the writing they opened.")
    return RedirectResponse(anchor, status_code=303)


@app.post("/writings/{writing_id}/revision")
def save_writing_revision(
    request: Request,
    writing_id: int,
    title: str = Form(""),
    body: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing or writing.author != user or writing.share_status != "shared":
        return RedirectResponse("/", status_code=303)
    return _save_revision(request, writing, title, body, db)


@app.post("/writings/{writing_id}/revision/offer")
def offer_writing_revision(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    anchor = f"/topics/{writing.topic_id}#writing-{writing_id}"
    other = auth.display_for(access.other_username(user) or "")
    if (
        writing.author == user
        and writing.share_status == "shared"
        and writing.revision_status == "private"
        and writing.topic.share_status == "shared"
    ):
        share.offer_revision(writing)
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_revision(writing)
        flash(request, f"Revision sealed for {other}. The writing they opened stays until they open this.")
    return RedirectResponse(anchor, status_code=303)


@app.post("/writings/{writing_id}/revision/accept")
def accept_writing_revision(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    anchor = f"/topics/{writing.topic_id}#writing-{writing_id}"
    if (
        writing.author != user
        and writing.share_status == "shared"
        and writing.revision_status == "offered"
        and writing.topic.share_status == "shared"
    ):
        share.accept_revision(writing)
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_shared(writing.topic, writing)
        store.write_local(writing.topic, writing)
        store.write_revision(writing)
        clear_markdown_cache()
        flash(request, "Opened. This revision is now the writing you keep.")
    return RedirectResponse(anchor, status_code=303)


@app.post("/writings/{writing_id}/revision/decline")
def decline_writing_revision(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    anchor = f"/topics/{writing.topic_id}#writing-{writing_id}"
    if writing.author != user and writing.revision_status == "offered" and writing.topic.share_status == "shared":
        share.return_revision(writing)
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_revision(writing)
        flash(request, "Left unopened. The revision returned to their desk. The writing you have stays.")
    return RedirectResponse(anchor, status_code=303)


@app.post("/writings/{writing_id}/revision/revoke")
def revoke_writing_revision(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    anchor = f"/topics/{writing.topic_id}#writing-{writing_id}"
    if writing.author == user and writing.revision_status == "offered":
        share.return_revision(writing)
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_revision(writing)
        flash(request, "Revision pulled back. They still have the writing they opened.")
    return RedirectResponse(anchor, status_code=303)


@app.post("/writings/{writing_id}/revision/discard")
def discard_writing_revision(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    anchor = f"/topics/{writing.topic_id}#writing-{writing_id}"
    if writing.author == user and writing.revision_status in {"private", "offered"}:
        share.clear_revision(writing)
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_revision(writing)
        flash(request, "Revision discarded. The writing you both keep is unchanged.")
    return RedirectResponse(anchor, status_code=303)


@app.post("/comments/{comment_id}/offer")
def offer_comment(request: Request, comment_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    comment = db.get(Comment, comment_id)
    if not comment:
        return RedirectResponse("/", status_code=303)
    if comment.author == user and comment.share_status == "private" and comment.topic.share_status == "shared":
        _apply_status(comment, "offered")
        comment.topic.updated_at = utcnow()
        db.commit()
        flash(request, "Comment sealed for them.")
    return RedirectResponse(_topic_anchor(comment.topic_id, comment.writing_id, "comments"), status_code=303)


@app.post("/comments/{comment_id}/accept")
def accept_comment(request: Request, comment_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    comment = db.get(Comment, comment_id)
    if not comment:
        return RedirectResponse("/", status_code=303)
    if comment.author != user and comment.share_status == "offered":
        if comment.topic.share_status != "shared":
            return RedirectResponse(_topic_anchor(comment.topic_id, comment.writing_id, "comments"), status_code=303)
        _apply_status(comment, "shared")
        comment.topic.updated_at = utcnow()
        db.commit()
        store.write_shared(comment.topic, comment=comment)
        flash(request, "Comment opened.")
    return RedirectResponse(_topic_anchor(comment.topic_id, comment.writing_id, "comments"), status_code=303)


@app.post("/comments/{comment_id}/decline")
def decline_comment(request: Request, comment_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    comment = db.get(Comment, comment_id)
    if not comment:
        return RedirectResponse("/", status_code=303)
    if comment.author != user and comment.share_status == "offered":
        if comment.topic.share_status != "shared":
            return RedirectResponse(_topic_anchor(comment.topic_id, comment.writing_id, "comments"), status_code=303)
        _apply_status(comment, "private")
        comment.topic.updated_at = utcnow()
        db.commit()
        store.remove_shared(comment.topic, comment=comment)
        store.write_local(comment.topic, comment=comment)
        flash(request, "Left unopened. Back on their side.")
    return RedirectResponse(_topic_anchor(comment.topic_id, comment.writing_id, "comments"), status_code=303)


@app.post("/comments/{comment_id}/revoke")
def revoke_comment(request: Request, comment_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    comment = db.get(Comment, comment_id)
    if not comment:
        return RedirectResponse("/", status_code=303)
    if comment.author == user and comment.share_status in {"offered", "shared"}:
        _apply_status(comment, "private")
        comment.topic.updated_at = utcnow()
        db.commit()
        store.remove_shared(comment.topic, comment=comment)
        store.write_local(comment.topic, comment=comment)
        flash(request, "Comment pulled back.")
    return RedirectResponse(_topic_anchor(comment.topic_id, comment.writing_id, "comments"), status_code=303)


def _shared_topics(db: Session) -> list[Topic]:
    return (
        db.query(Topic)
        .options(
            selectinload(Topic.writings),
            selectinload(Topic.comments),
            selectinload(Topic.messages),
        )
        .filter(Topic.share_status == "shared")
        .order_by(Topic.updated_at.desc())
        .all()
    )


@app.get("/table", response_class=HTMLResponse)
def table_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    view = build_table(_shared_topics(db), user)
    return render(request, "table.html", db=db, lately=view.lately, cards=view.cards)


def _open_topics(db: Session, user: str) -> list[Topic]:
    topics = (
        db.query(Topic)
        .options(
            selectinload(Topic.writings),
            selectinload(Topic.comments),
            selectinload(Topic.messages),
        )
        .order_by(Topic.created_at.asc())
        .all()
    )
    return [t for t in topics if access.topic_open(user, t)]


@app.get("/archive", response_class=HTMLResponse)
def archive(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    topics = _open_topics(db, user)
    return render(
        request,
        "archive.html",
        db=db,
        topics=topics,
        viewer=user,
        prompt_access={t.id: access.topic_prompt_open(user, t) for t in topics},
    )


@app.get("/export.json")
def export_json(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    topics = _open_topics(db, user)
    payload = []
    for t in topics:
        payload.append(
            {
                "id": t.id,
                "title": t.title,
                "prompt": t.prompt if access.topic_prompt_open(user, t) else "",
                "created_by": t.created_by,
                "share_status": t.share_status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "writings": [
                    {
                        "id": w.id,
                        "author": w.author,
                        "title": w.title,
                        "body": w.body,
                        "share_status": w.share_status,
                        "created_at": w.created_at.isoformat() if w.created_at else None,
                    }
                    for w in t.writings
                    if access.writing_open(user, w)
                ],
                "comments": [
                    {
                        "id": c.id,
                        "writing_id": c.writing_id,
                        "parent_id": c.parent_id,
                        "author": c.author,
                        "body": c.body,
                        "share_status": c.share_status,
                        "created_at": c.created_at.isoformat() if c.created_at else None,
                    }
                    for c in t.comments
                    if access.comment_open(user, c)
                ],
                "chat": [
                    {
                        "id": m.id,
                        "author": m.author,
                        "body": m.body,
                        "created_at": m.created_at.isoformat() if m.created_at else None,
                    }
                    for m in t.messages
                ]
                if t.share_status == "shared"
                else [],
            }
        )
    body = json.dumps({"app": APP_NAME, "exported_at": utcnow().isoformat(), "topics": payload}, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="between-archive.json"'},
    )


@app.get("/export.md")
def export_md(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    topics = _open_topics(db, user)
    lines = [f"# {APP_NAME} archive", "", f"_Exported {fmt_dt(utcnow())}_", ""]
    for t in topics:
        lines += [f"## {t.title}", ""]
        if access.topic_prompt_open(user, t) and t.prompt:
            lines += [f"> {t.prompt}", ""]
        for w in t.writings:
            if not access.writing_open(user, w):
                continue
            heading = w.title or "Untitled writing"
            lines += [f"### {heading}", f"*{auth.display_for(w.author)} · {fmt_dt(w.created_at)}*", "", w.body, ""]
            related = [c for c in t.comments if c.writing_id == w.id and access.comment_open(user, c)]
            if related:
                lines.append("**Comments**")
                for c in related:
                    lines += [f"- **{auth.display_for(c.author)}** ({fmt_dt(c.created_at)}): {c.body}"]
                lines.append("")
        loose = [c for c in t.comments if c.writing_id is None and access.comment_open(user, c)]
        if loose:
            lines.append("**Topic comments**")
            for c in loose:
                lines += [f"- **{auth.display_for(c.author)}** ({fmt_dt(c.created_at)}): {c.body}"]
            lines.append("")
        if t.share_status == "shared" and t.messages:
            lines.append("**Chat**")
            for m in t.messages:
                lines += [f"- **{auth.display_for(m.author)}** ({fmt_dt(m.created_at)}): {m.body}"]
            lines.append("")
        lines.append("---")
        lines.append("")
    body = "\n".join(lines)
    return Response(
        content=body,
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="between-archive.md"'},
    )


@app.post("/topics/{topic_id}/draft")
async def draft_reply(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = _load_topic(db, topic_id)
    if not topic or topic.share_status != "shared" or not access.topic_open(user, topic):
        return RedirectResponse("/", status_code=303)
    if not agent.allowed(db):
        return RedirectResponse(f"/topics/{topic_id}?agent=consent#write", status_code=303)
    try:
        body = await run_in_threadpool(agent.draft_reply, topic, user)
    except Exception as exc:
        note = "missing" if "missing-key" in str(exc) else "fail"
        return RedirectResponse(f"/topics/{topic_id}?agent={note}#write", status_code=303)
    writing = Writing(
        topic_id=topic.id,
        author=user,
        title="Draft reply",
        body=body,
        share_status="private",
    )
    topic.updated_at = utcnow()
    db.add(writing)
    db.commit()
    store.write_local(topic, writing)
    flash(request, "A private draft is waiting on your desk.")
    return RedirectResponse(f"/topics/{topic_id}?agent=ok#writing-{writing.id}", status_code=303)


async def _close_quietly(websocket: WebSocket, code: int) -> None:
    try:
        await websocket.close(code=code)
    except Exception:
        pass


@app.websocket("/ws/topics/{topic_id}")
async def topic_chat(websocket: WebSocket, topic_id: int):
    user = auth.session_user(websocket.scope.get("session", {}))
    if not user:
        await websocket.close(code=4401)
        return
    db = SessionLocal()
    seated = False
    try:
        topic = db.get(Topic, topic_id)
        if not topic or topic.share_status != "shared":
            await websocket.close(code=4404)
            return
        seated = await hub.join(topic_id, websocket, user)
        if not seated:
            return
        db.expire_all()
        topic = db.get(Topic, topic_id)
        if not topic or topic.share_status != "shared" or not access.topic_open(user, topic):
            await websocket.close(code=4404)
            return
        while True:
            data = await websocket.receive_json()
            seat = hub.seat(topic_id, websocket)
            if seat is None or not seat.allow_frame():
                continue
            if not isinstance(data, dict):
                continue
            db.expire_all()
            topic = db.get(Topic, topic_id)
            if not topic or not access.topic_open(user, topic) or topic.share_status != "shared":
                await websocket.close(code=4404)
                break
            kind = data.get("type") or ("chat" if data.get("body") else "")
            if kind == "typing":
                hub.set_typing(topic_id, websocket, bool(data.get("on")))
                await hub.broadcast_presence(topic_id)
                continue
            body = str(data.get("body", "")).strip()
            if kind != "chat" or not body:
                continue
            hub.set_typing(topic_id, websocket, False)
            msg = ChatMessage(topic_id=topic_id, author=user, body=body[:4000])
            topic.updated_at = utcnow()
            db.add(msg)
            db.commit()
            db.refresh(msg)
            await hub.broadcast(
                topic_id,
                {
                    "type": "chat",
                    "id": msg.id,
                    "author": msg.author,
                    "display": auth.display_for(msg.author),
                    "body": msg.body,
                    "created_at": fmt_dt_soft(msg.created_at),
                },
            )
            await hub.broadcast_presence(topic_id)
    except WebSocketDisconnect:
        pass
    except ValueError:
        # Not JSON (or not text): a misbehaving client, not a crash. Close and free the seat.
        await _close_quietly(websocket, 1003)
    except Exception:
        log.exception("websocket error topic=%s user=%s", topic_id, user)
        await _close_quietly(websocket, 1011)
    finally:
        db.close()
        if seated:
            hub.leave(topic_id, websocket)
            await hub.broadcast_presence(topic_id)


@app.get("/health")
def health():
    healthy = db_ok()
    return JSONResponse(
        {"ok": healthy, "app": APP_NAME, "db": "ok" if healthy else "error"},
        status_code=200 if healthy else 503,
    )
