from __future__ import annotations

import json
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import bleach
import markdown
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import access, agent, auth, share, store
from .db import Base, SessionLocal, engine, get_db, migrate
from .hub import hub
from .models import ChatMessage, Comment, Topic, Writing, utcnow
from .share import ShareStatus

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "Between")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")

ALLOWED_TAGS = bleach.sanitizer.ALLOWED_TAGS.union(
    {
        "p",
        "pre",
        "code",
        "h1",
        "h2",
        "h3",
        "h4",
        "blockquote",
        "hr",
        "ul",
        "ol",
        "li",
        "em",
        "strong",
        "a",
        "br",
        "img",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
    }
)
ALLOWED_ATTRS = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    "a": ["href", "title", "rel"],
    "img": ["src", "alt", "title"],
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=False)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def md(text: str) -> str:
    raw = markdown.markdown(
        text or "",
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
    )
    return bleach.clean(raw, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)


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
    delta = now - local
    seconds = int(delta.total_seconds())
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        mins = max(1, seconds // 60)
        return f"{mins}m ago"
    if local.date() == now.date():
        return f"today · {local.strftime('%H:%M')}"
    if local.date() == (now.date() - timedelta(days=1)):
        return f"yesterday · {local.strftime('%H:%M')}"
    if seconds < 60 * 60 * 24 * 7:
        return local.strftime("%A · %H:%M")
    return local.strftime("%b %d, %Y")


templates.env.filters["md"] = md
templates.env.filters["when"] = fmt_dt
templates.env.filters["when_soft"] = fmt_dt_soft
templates.env.filters["share_label"] = share.label
templates.env.globals["app_name"] = APP_NAME
templates.env.globals["display_for"] = auth.display_for


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    migrate()
    auth.load_people()


def current_user(request: Request) -> str | None:
    return request.session.get("user")


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise RedirectNeeded("/login")
    return user


class RedirectNeeded(Exception):
    def __init__(self, url: str):
        self.url = url


@app.exception_handler(RedirectNeeded)
async def redirect_needed(_, exc: RedirectNeeded):
    return RedirectResponse(exc.url, status_code=303)


def flash(request: Request, message: str, kind: str = "ok") -> None:
    flashes = request.session.get("flashes") or []
    flashes.append({"message": message, "kind": kind})
    request.session["flashes"] = flashes


def pop_flashes(request: Request) -> list[dict[str, str]]:
    return list(request.session.pop("flashes", []) or [])


def sealed_offer_count(user: str | None) -> int:
    if not user:
        return 0
    db = SessionLocal()
    try:
        return (
            db.query(Topic)
            .filter(Topic.created_by != user, Topic.share_status == "offered")
            .count()
        )
    finally:
        db.close()


def ctx(request: Request, **extra: Any) -> dict[str, Any]:
    user = current_user(request)
    people = auth.load_people()
    return {
        "request": request,
        "user": user,
        "display": auth.display_for(user) if user else None,
        "other": next((p.display for name, p in people.items() if name != user), None) if user else None,
        "other_user": access.other_username(user) if user else None,
        "people": people,
        "flashes": pop_flashes(request),
        "sealed_count": extra.pop("sealed_count", sealed_offer_count(user)),
        **extra,
    }


def render(request: Request, name: str, status_code: int = 200, **extra: Any):
    return templates.TemplateResponse(request, name, ctx(request, **extra), status_code=status_code)


def _apply_status(obj, status: ShareStatus) -> None:
    share.set_status(obj, status)


def _topic_anchor(topic_id: int, writing_id: int | None = None, fragment: str | None = None) -> str:
    if writing_id:
        return f"/topics/{topic_id}#writing-{writing_id}"
    if fragment:
        return f"/topics/{topic_id}#{fragment}"
    return f"/topics/{topic_id}"


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    person = auth.verify(username, password)
    if not person:
        return render(request, "login.html", status_code=401, error="That name or password did not match.")
    request.session["user"] = person.username
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def _visible_topics(db: Session, user: str) -> list[Topic]:
    topics = db.query(Topic).order_by(Topic.updated_at.desc()).all()
    return [t for t in topics if access.topic_visible(user, t)]


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    topics = _visible_topics(db, user)
    desk = [t for t in topics if t.created_by == user]
    incoming = [t for t in topics if t.created_by != user and t.share_status == "offered"]
    shared = [t for t in topics if t.share_status == "shared"]
    return render(request, "home.html", desk=desk, incoming=incoming, shared=shared)


@app.post("/topics")
def create_topic(
    request: Request,
    title: str = Form(...),
    prompt: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_user(request)
    topic = Topic(title=title.strip(), prompt=prompt.strip(), created_by=user, share_status="private")
    db.add(topic)
    db.commit()
    store.write_local(topic)
    flash(request, "Kept on your desk — only you can see it.")
    return RedirectResponse(f"/topics/{topic.id}", status_code=303)


@app.get("/topics/{topic_id}", response_class=HTMLResponse)
def topic_page(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if not topic or not access.topic_visible(user, topic):
        return RedirectResponse("/", status_code=303)
    if not access.topic_open(user, topic):
        return render(request, "consent.html", topic=topic)

    visible_writings = [w for w in topic.writings if access.writing_visible(user, w)]
    comments_by_writing: dict[int | None, list[Comment]] = defaultdict(list)
    for c in topic.comments:
        if access.comment_visible(user, c):
            comments_by_writing[c.writing_id].append(c)
    return render(
        request,
        "topic.html",
        topic=topic,
        writings=visible_writings,
        comments_by_writing=comments_by_writing,
        topic_shared=topic.share_status == "shared",
        agent_ready=agent.configured(),
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
    if not topic or not access.topic_open(user, topic) or not body.strip():
        return RedirectResponse(f"/topics/{topic_id}", status_code=303)
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
    if not topic or not access.topic_open(user, topic) or not body.strip():
        return RedirectResponse(f"/topics/{topic_id}", status_code=303)
    if writing_id:
        writing = db.get(Writing, writing_id)
        if not writing or not access.writing_open(user, writing):
            return RedirectResponse(f"/topics/{topic_id}", status_code=303)
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


@app.post("/topics/{topic_id}/decline")
def decline_topic(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if topic and topic.created_by != user and topic.share_status == "offered":
        _apply_status(topic, "private")
        topic.updated_at = utcnow()
        db.commit()
        store.write_local(topic)
        flash(request, "Left unopened. It returned to their desk.")
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@app.post("/topics/{topic_id}/revoke")
def revoke_topic(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if topic and topic.created_by == user and topic.share_status in {"offered", "shared"}:
        _apply_status(topic, "private")
        topic.updated_at = utcnow()
        db.commit()
        store.write_local(topic)
        flash(request, "Pulled back. Quiet on your desk again.")
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@app.post("/writings/{writing_id}/offer")
def offer_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
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
    if writing and writing.author != user and writing.share_status == "offered":
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
    if writing and writing.author != user and writing.share_status == "offered":
        _apply_status(writing, "private")
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_local(writing.topic, writing)
        flash(request, "Left unopened. Back on their desk.")
    return RedirectResponse(f"/topics/{writing.topic_id}", status_code=303)


@app.post("/writings/{writing_id}/revoke")
def revoke_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if writing and writing.author == user and writing.share_status in {"offered", "shared"}:
        _apply_status(writing, "private")
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_local(writing.topic, writing)
        flash(request, "Pulled back to your desk.")
    return RedirectResponse(f"/topics/{writing.topic_id}#writing-{writing_id}", status_code=303)


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
        _apply_status(comment, "private")
        comment.topic.updated_at = utcnow()
        db.commit()
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
        flash(request, "Comment pulled back.")
    return RedirectResponse(_topic_anchor(comment.topic_id, comment.writing_id, "comments"), status_code=303)


@app.get("/archive", response_class=HTMLResponse)
def archive(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    topics = [t for t in db.query(Topic).order_by(Topic.created_at.asc()).all() if access.topic_open(user, t)]
    return render(request, "archive.html", topics=topics, viewer=user)


@app.get("/export.json")
def export_json(request: Request, db: Session = Depends(get_db)):
    user = require_user(request)
    topics = [t for t in db.query(Topic).order_by(Topic.created_at.asc()).all() if access.topic_open(user, t)]
    payload = []
    for t in topics:
        payload.append(
            {
                "id": t.id,
                "title": t.title,
                "prompt": t.prompt,
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
    topics = [t for t in db.query(Topic).order_by(Topic.created_at.asc()).all() if access.topic_open(user, t)]
    lines = [f"# {APP_NAME} archive", "", f"_Exported {fmt_dt(utcnow())}_", ""]
    for t in topics:
        lines += [f"## {t.title}", ""]
        if t.prompt:
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
def draft_reply(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if not topic or not access.topic_open(user, topic):
        return RedirectResponse("/", status_code=303)
    try:
        body = agent.draft_reply(topic, user)
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


@app.websocket("/ws/topics/{topic_id}")
async def topic_chat(websocket: WebSocket, topic_id: int):
    user = websocket.scope.get("session", {}).get("user")
    if not user:
        await websocket.close(code=4401)
        return
    db = SessionLocal()
    try:
        topic = db.get(Topic, topic_id)
        if not topic or topic.share_status != "shared":
            await websocket.close(code=4404)
            return
        await hub.join(topic_id, websocket, user)
        try:
            while True:
                data = await websocket.receive_json()
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
                        "created_at": fmt_dt(msg.created_at),
                    },
                )
                await hub.broadcast_presence(topic_id)
        except WebSocketDisconnect:
            hub.leave(topic_id, websocket)
            await hub.broadcast_presence(topic_id)
    finally:
        db.close()


@app.get("/health")
def health():
    return JSONResponse({"ok": True, "app": APP_NAME})
