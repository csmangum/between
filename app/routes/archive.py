"""The kept record: the table, the archive page, and the two exports. All four read through `access`."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from .. import access, auth
from ..db import get_db
from ..models import utcnow
from ..queries import open_topics, shared_topics
from ..table import build_table
from ..views import APP_NAME, fmt_dt_utc, iso_utc, render, require_user

router = APIRouter()


@router.get("/table", response_class=HTMLResponse)
def table_page(request: Request, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    view = build_table(shared_topics(db), user)
    return render(request, "table.html", db=db, lately=view.lately, cards=view.cards)


@router.get("/archive", response_class=HTMLResponse)
def archive(request: Request, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topics = open_topics(db, user)
    return render(
        request,
        "archive.html",
        db=db,
        topics=topics,
        viewer=user,
        prompt_access={t.id: access.topic_prompt_open(user, t) for t in topics},
    )


def _md_item(author: str, when: datetime | None, body: str) -> str:
    """One list item; later lines are indented so a multi-line note stays inside its bullet."""
    text = "\n  ".join(body.splitlines())
    return f"- **{auth.display_for(author)}** ({fmt_dt_utc(when)}): {text}"


def _export_filename(ext: str) -> str:
    return f"between-archive-{utcnow().date().isoformat()}.{ext}"


@router.get("/export.json")
def export_json(request: Request, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topics = open_topics(db, user)
    payload = []
    for t in topics:
        payload.append(
            {
                "id": t.id,
                "title": t.title,
                "prompt": t.prompt if access.topic_prompt_open(user, t) else "",
                "created_by": t.created_by,
                "share_status": t.share_status,
                "created_at": iso_utc(t.created_at) or None,
                "writings": [
                    {
                        "id": w.id,
                        "author": w.author,
                        "title": w.title,
                        "body": w.body,
                        "share_status": w.share_status,
                        "created_at": iso_utc(w.created_at) or None,
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
                        "created_at": iso_utc(c.created_at) or None,
                    }
                    for c in t.comments
                    if access.comment_open(user, c)
                ],
                "chat": [
                    {
                        "id": m.id,
                        "author": m.author,
                        "body": m.body,
                        "created_at": iso_utc(m.created_at) or None,
                    }
                    for m in t.messages
                ]
                if t.share_status == "shared"
                else [],
            }
        )
    body = json.dumps({"app": APP_NAME, "exported_at": iso_utc(utcnow()), "topics": payload}, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("json")}"'},
    )


@router.get("/export.md")
def export_md(request: Request, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topics = open_topics(db, user)
    lines = [f"# {APP_NAME} archive", "", f"_Exported {fmt_dt_utc(utcnow())}_", ""]
    for t in topics:
        lines += [f"## {t.title}", ""]
        if access.topic_prompt_open(user, t) and t.prompt:
            lines += [f"> {t.prompt}", ""]
        for w in t.writings:
            if not access.writing_open(user, w):
                continue
            heading = w.title or "Untitled writing"
            lines += [f"### {heading}", f"*{auth.display_for(w.author)} · {fmt_dt_utc(w.created_at)}*", "", w.body, ""]
            related = [c for c in t.comments if c.writing_id == w.id and access.comment_open(user, c)]
            if related:
                lines.append("**Comments**")
                for c in related:
                    lines.append(_md_item(c.author, c.created_at, c.body))
                lines.append("")
        loose = [c for c in t.comments if c.writing_id is None and access.comment_open(user, c)]
        if loose:
            lines.append("**Topic comments**")
            for c in loose:
                lines.append(_md_item(c.author, c.created_at, c.body))
            lines.append("")
        if t.share_status == "shared" and t.messages:
            lines.append("**Chat**")
            for m in t.messages:
                lines.append(_md_item(m.author, m.created_at, m.body))
            lines.append("")
        lines.append("---")
        lines.append("")
    body = "\n".join(lines)
    return Response(
        content=body,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("md")}"'},
    )
