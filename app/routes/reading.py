"""One topic laid out to be read. Access is the topic page's; what is sealed stays on the topic page."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from .. import access
from ..db import get_db
from ..queries import load_topic
from ..reading import build_reading
from ..views import render, require_user

router = APIRouter()


@router.get("/topics/{topic_id}/read", response_class=HTMLResponse)
def read_topic(request: Request, topic_id: int, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topic = load_topic(db, topic_id)
    if not topic or not access.topic_visible(user, topic):
        return RedirectResponse("/", status_code=303)
    if not access.topic_open(user, topic):
        return RedirectResponse(f"/topics/{topic_id}", status_code=303)
    return render(request, "read.html", db=db, reading=build_reading(db, user, topic), topic=topic)
