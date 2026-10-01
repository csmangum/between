from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import access, agent, events, share, store
from ..db import get_db
from ..hub import hub
from ..markdown_render import clear_markdown_cache
from ..models import Comment, Topic, Writing, utcnow
from ..queries import load_topic, topic_withdrawals
from ..views import flash, other_display, render, require_user

router = APIRouter()


@router.post("/topics")
def create_topic(
    request: Request,
    title: str = Form(...),
    prompt: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
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


@router.get("/topics/{topic_id}", response_class=HTMLResponse)
def topic_page(request: Request, topic_id: int, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topic = load_topic(db, topic_id)
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
    stubs = topic_withdrawals(db, topic_id)
    returned = [s for s in stubs if access.withdrawal_visible(user, s)]
    return render(
        request,
        "topic.html",
        db=db,
        topic=topic,
        topic_prompt=topic.prompt if access.topic_prompt_open(user, topic) else "",
        writings=visible_writings,
        comments_by_writing=comments_by_writing,
        topic_shared=topic.share_status == "shared",
        topic_editable=access.topic_editable(user, topic),
        topic_removable=access.topic_editable(user, topic) and not access.has_words_from_others(topic, user),
        removable_writings={w.id for w in visible_writings if access.writing_removable(user, w)},
        removable_comments={c.id for c in topic.comments if access.comment_removable(user, c)},
        awaiting=awaiting,
        returned_topic=next((s for s in returned if s.kind == "topic"), None),
        returned_writings=[s for s in returned if s.kind == "writing"],
        returned_notes=[s for s in returned if s.kind == "comment"],
        returned_mine={s.object_id: s for s in stubs if s.kind == "writing" and s.author == user},
        agent_configured=agent.configured(),
        agent_ready=agent.allowed(db),
        agent_note=request.query_params.get("agent"),
    )


@router.post("/topics/{topic_id}/offer")
def offer_topic(request: Request, topic_id: int, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if topic and topic.created_by == user and topic.share_status == "private":
        share.set_status(topic, "offered")
        topic.updated_at = utcnow()
        db.commit()
        store.write_local(topic)
        events.tell(db, user, "sealed", topic.id)
        flash(request, f"Sent to {other_display(user)}. It stays sealed until they open it.")
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@router.post("/topics/{topic_id}/accept")
def accept_topic(request: Request, topic_id: int, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if topic and topic.created_by != user and topic.share_status == "offered":
        share.set_status(topic, "shared")
        share.clear_withdrawals(db, "topic", topic.source_id)
        topic.updated_at = utcnow()
        db.commit()
        store.write_shared(topic)
        events.tell(db, user, "opened", topic.id)
        flash(request, "Opened. This sits on the table between you now.")
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


async def _close_topic(db: Session, topic: Topic, actor: str) -> bool:
    """Shared → private: everything inside returns to its author, the shared mirror goes, the margin closes.
    Returns whether anyone lost access they had, in which case they keep a stub of what was open."""
    writings, comments, stubs = share.fold_topic(topic, actor)
    share.set_status(topic, "private")
    topic.updated_at = utcnow()
    db.add_all(stubs)
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
    return bool(stubs)


@router.post("/topics/{topic_id}/decline")
async def decline_topic(request: Request, topic_id: int, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topic = load_topic(db, topic_id)
    if topic and topic.created_by != user and topic.share_status == "offered":
        await _close_topic(db, topic, user)
        events.tell(db, user, "unopened", topic.id)
        flash(request, "Left unopened. It returned to their desk.")
        return RedirectResponse("/", status_code=303)
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@router.post("/topics/{topic_id}/revoke")
async def revoke_topic(request: Request, topic_id: int, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topic = load_topic(db, topic_id)
    if topic and topic.created_by == user and topic.share_status in {"offered", "shared"}:
        was_open = await _close_topic(db, topic, user)
        events.tell(db, user, "returned", topic.id)
        if was_open:
            other = other_display(user)
            flash(request, f"Pulled back. {other} keeps a note that it was open between you, not the pages.")
        else:
            flash(request, "Pulled back. Everything inside returned to the desk it came from.")
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@router.post("/topics/{topic_id}/edit")
def edit_topic(
    request: Request,
    topic_id: int,
    title: str = Form(...),
    prompt: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if not topic or not access.topic_editable(user, topic):
        return RedirectResponse(f"/topics/{topic_id}", status_code=303)
    cleaned = title.strip()
    if not cleaned:
        flash(request, "A topic needs a title. The old one stays.", "warn")
        return RedirectResponse(f"/topics/{topic_id}", status_code=303)
    topic.title = cleaned
    topic.prompt = prompt.strip()
    topic.updated_at = utcnow()
    db.commit()
    store.rename_local_topic_mirrors(topic)
    store.write_local(topic)
    flash(request, "Topic updated on your desk.")
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@router.post("/topics/{topic_id}/delete")
def delete_topic(request: Request, topic_id: int, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topic = load_topic(db, topic_id)
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


@router.post("/topics/{topic_id}/draft")
async def draft_reply(request: Request, topic_id: int, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    topic = load_topic(db, topic_id)
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
