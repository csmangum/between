from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import bleach
import markdown
from fastapi import Depends, FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import access, agent, auth, store
from .db import Base, SessionLocal, engine, get_db, migrate
from .models import ChatMessage, Comment, Topic, Writing, utcnow

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

app = FastAPI(title=APP_NAME)
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


templates.env.filters["md"] = md
templates.env.filters["when"] = fmt_dt
templates.env.globals["app_name"] = APP_NAME
templates.env.globals["display_for"] = auth.display_for


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    migrate()
    auth.load_people()


init_db()


@app.on_event("startup")
def startup() -> None:
    init_db()


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
        **extra,
    }


def render(request: Request, name: str, status_code: int = 200, **extra: Any):
    return templates.TemplateResponse(request, name, ctx(request, **extra), status_code=status_code)


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
        if access.comment_open(user, c) or (c.author != user and c.share_status == "offered"):
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
    anchor = f"#writing-{writing_id}" if writing_id else "#comments"
    return RedirectResponse(f"/topics/{topic_id}{anchor}", status_code=303)


def _set_status(obj, status: str) -> None:
    obj.share_status = status
    if status == "offered":
        obj.offered_at = utcnow()
        obj.accepted_at = None
    elif status == "shared":
        obj.accepted_at = utcnow()
    elif status == "private":
        obj.offered_at = None
        obj.accepted_at = None


@app.post("/topics/{topic_id}/offer")
def offer_topic(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if topic and topic.created_by == user:
        _set_status(topic, "offered")
        topic.updated_at = utcnow()
        db.commit()
        store.write_local(topic)
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@app.post("/topics/{topic_id}/accept")
def accept_topic(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if topic and topic.created_by != user and topic.share_status == "offered":
        _set_status(topic, "shared")
        topic.updated_at = utcnow()
        db.commit()
        store.write_shared(topic)
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@app.post("/topics/{topic_id}/decline")
def decline_topic(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if topic and topic.created_by != user and topic.share_status == "offered":
        _set_status(topic, "private")
        topic.updated_at = utcnow()
        db.commit()
        store.write_local(topic)
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@app.post("/topics/{topic_id}/revoke")
def revoke_topic(request: Request, topic_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if topic and topic.created_by == user:
        _set_status(topic, "private")
        topic.updated_at = utcnow()
        db.commit()
        store.write_local(topic)
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@app.post("/writings/{writing_id}/offer")
def offer_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if writing and writing.author == user:
        if writing.topic.share_status != "shared":
            return RedirectResponse(f"/topics/{writing.topic_id}", status_code=303)
        _set_status(writing, "offered")
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_local(writing.topic, writing)
    return RedirectResponse(f"/topics/{writing.topic_id}#writing-{writing_id}", status_code=303)


@app.post("/writings/{writing_id}/accept")
def accept_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if writing and writing.author != user and writing.share_status == "offered":
        _set_status(writing, "shared")
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_shared(writing.topic, writing)
    return RedirectResponse(f"/topics/{writing.topic_id}#writing-{writing_id}", status_code=303)


@app.post("/writings/{writing_id}/revoke")
def revoke_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if writing and writing.author == user:
        _set_status(writing, "private")
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_local(writing.topic, writing)
    return RedirectResponse(f"/topics/{writing.topic_id}#writing-{writing_id}", status_code=303)


@app.post("/comments/{comment_id}/offer")
def offer_comment(request: Request, comment_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    comment = db.get(Comment, comment_id)
    if comment and comment.author == user and comment.topic.share_status == "shared":
        _set_status(comment, "offered")
        comment.topic.updated_at = utcnow()
        db.commit()
    anchor = f"#writing-{comment.writing_id}" if comment.writing_id else "#comments"
    return RedirectResponse(f"/topics/{comment.topic_id}{anchor}", status_code=303)


@app.post("/comments/{comment_id}/accept")
def accept_comment(request: Request, comment_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    comment = db.get(Comment, comment_id)
    if comment and comment.author != user and comment.share_status == "offered":
        _set_status(comment, "shared")
        comment.topic.updated_at = utcnow()
        db.commit()
        store.write_shared(comment.topic, comment=comment)
    anchor = f"#writing-{comment.writing_id}" if comment.writing_id else "#comments"
    return RedirectResponse(f"/topics/{comment.topic_id}{anchor}", status_code=303)


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


class Seat:
    def __init__(self, ws: WebSocket, user: str):
        self.ws = ws
        self.user = user
        self.typing = False


class Hub:
    def __init__(self) -> None:
        self.rooms: dict[int, dict[int, Seat]] = defaultdict(dict)

    async def join(self, topic_id: int, ws: WebSocket, user: str) -> None:
        await ws.accept()
        self.rooms[topic_id][id(ws)] = Seat(ws, user)
        await self.broadcast_presence(topic_id)

    def leave(self, topic_id: int, ws: WebSocket) -> None:
        self.rooms[topic_id].pop(id(ws), None)
        if not self.rooms[topic_id]:
            self.rooms.pop(topic_id, None)

    def set_typing(self, topic_id: int, ws: WebSocket, typing: bool) -> None:
        seat = self.rooms[topic_id].get(id(ws))
        if seat:
            seat.typing = typing

    def presence(self, topic_id: int) -> dict:
        seats = list(self.rooms.get(topic_id, {}).values())
        seen: dict[str, bool] = {}
        for seat in seats:
            seen[seat.user] = seen.get(seat.user, False) or seat.typing
        return {
            "type": "presence",
            "here": [{"user": name, "display": auth.display_for(name)} for name in seen],
            "typing": [auth.display_for(name) for name, flag in seen.items() if flag],
        }

    async def broadcast(self, topic_id: int, payload: dict) -> None:
        dead = []
        for key, seat in list(self.rooms.get(topic_id, {}).items()):
            try:
                await seat.ws.send_json(payload)
            except Exception:
                dead.append(key)
        for key in dead:
            self.rooms[topic_id].pop(key, None)

    async def broadcast_presence(self, topic_id: int) -> None:
        await self.broadcast(topic_id, self.presence(topic_id))


hub = Hub()


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
