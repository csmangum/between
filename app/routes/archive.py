"""The kept record: the table, the archive page, and the two exports. All four read through `access`."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from .. import access, auth
from ..db import get_db
from ..models import Topic, Withdrawal, utcnow
from ..queries import open_topics, shared_topics, withdrawals_for
from ..table import build_table
from ..views import APP_NAME, fmt_dt_utc, iso_utc, render, require_user

router = APIRouter()


@router.get("/table", response_class=HTMLResponse)
def table_page(request: Request, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    view = build_table(shared_topics(db), user)
    return render(request, "table.html", db=db, lately=view.lately, cards=view.cards)


Groups = tuple[dict[int, list[Withdrawal]], list[list[Withdrawal]]]


def _returned(db: Session, user: str, topics: list[Topic]) -> Groups:
    """Stubs grouped by topic: those inside topics this person still opens, and whole topics that are gone."""
    by_topic: dict[int, list[Withdrawal]] = defaultdict(list)
    for stub in withdrawals_for(db, user):
        by_topic[stub.topic_id].append(stub)
    open_ids = {t.id for t in topics}
    inside = {tid: [s for s in group if s.kind != "topic"] for tid, group in by_topic.items() if tid in open_ids}
    gone = [group for tid, group in by_topic.items() if tid not in open_ids]
    return inside, gone


@router.get("/archive", response_class=HTMLResponse)
def archive(request: Request, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topics = open_topics(db, user)
    returned_by_topic, returned_gone = _returned(db, user, topics)
    return render(
        request,
        "archive.html",
        db=db,
        topics=topics,
        viewer=user,
        prompt_access={t.id: access.topic_prompt_open(user, t) for t in topics},
        returned_by_topic=returned_by_topic,
        returned_gone=returned_gone,
    )


def _stub_json(s: Withdrawal) -> dict[str, Any]:
    return {
        "kind": s.kind,
        "id": s.object_id,
        "topic_id": s.topic_id,
        "topic_title": s.topic_title,
        "title": s.title,
        "author": s.author,
        "returned_by": s.actor,
        "opened_at": iso_utc(s.opened_at) or None,
        "returned_at": iso_utc(s.withdrawn_at),
    }


def _stub_md(s: Withdrawal) -> str:
    if s.kind == "comment":
        name = "a note"
    else:
        name = f"“{s.topic_title if s.kind == 'topic' else s.title or 'Untitled writing'}”"
    opened = f"open between you from {fmt_dt_utc(s.opened_at)}; " if s.opened_at else ""
    return f"- {name} — {opened}returned to {auth.display_for(s.author)}'s desk {fmt_dt_utc(s.withdrawn_at)}"


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
    returned_by_topic, returned_gone = _returned(db, user, topics)
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
                "returned": [_stub_json(s) for s in returned_by_topic.get(t.id, [])],
            }
        )
    body = json.dumps(
        {
            "app": APP_NAME,
            "exported_at": iso_utc(utcnow()),
            "topics": payload,
            "returned_topics": [[_stub_json(s) for s in group] for group in returned_gone],
        },
        indent=2,
    )
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("json")}"'},
    )


@router.get("/export.md")
def export_md(request: Request, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topics = open_topics(db, user)
    returned_by_topic, returned_gone = _returned(db, user, topics)
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
        if returned_by_topic.get(t.id):
            lines.append("**Returned**")
            lines += [_stub_md(s) for s in returned_by_topic[t.id]]
            lines.append("")
        lines.append("---")
        lines.append("")
    if returned_gone:
        note = "_Topics you opened together that are theirs again. Titles and dates stay; pages do not._"
        lines += ["## Returned", "", note, ""]
        for group in returned_gone:
            lines += [_stub_md(s) for s in group]
            lines.append("")
    body = "\n".join(lines)
    return Response(
        content=body,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{_export_filename("md")}"'},
    )
