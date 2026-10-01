"""The live margin. The socket is accepted only while the topic is shared, and rechecked on every frame."""

from __future__ import annotations

import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import access, auth
from ..db import SessionLocal
from ..hub import hub
from ..markdown_render import render_markdown
from ..models import ChatMessage, Topic, utcnow
from ..views import iso_utc

log = logging.getLogger("between")

router = APIRouter()


async def _close_quietly(websocket: WebSocket, code: int) -> None:
    with contextlib.suppress(Exception):
        await websocket.close(code=code)


@router.websocket("/ws/topics/{topic_id}")
async def topic_chat(websocket: WebSocket, topic_id: int) -> None:
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
                    "html": render_markdown(msg.body),
                    "created_at": iso_utc(msg.created_at),
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
