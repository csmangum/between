from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from .. import access, agent
from ..db import get_db
from ..queries import shared_topics, visible_topics, writing_counts
from ..table import build_table
from ..views import flash, other_display, render, require_user

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topics = visible_topics(db, user)
    incoming = [t for t in topics if access.topic_awaits(user, t)]
    desk = [t for t in topics if t.share_status != "shared" and not access.topic_awaits(user, t)]
    view = build_table(shared_topics(db), user)
    agent_on = agent.configured()
    return render(
        request,
        "home.html",
        db=db,
        desk=desk,
        writing_counts=writing_counts(db, desk, user),
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


@router.post("/me/agent")
def set_agent_consent(request: Request, allow: str = Form(""), db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    granted = allow.strip().lower() in {"1", "true", "yes", "on"}
    agent.set_consent(db, user, granted)
    db.commit()
    if granted:
        flash(request, f"Drafting help allowed on your side. It only works once {other_display(user)} allows it too.")
    else:
        flash(request, "Drafting help withdrawn. Nothing you both opened will be sent anywhere.")
    return RedirectResponse("/#drafting", status_code=303)
