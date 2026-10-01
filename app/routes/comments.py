from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from .. import access, events, share, store
from ..db import get_db
from ..markdown_render import clear_markdown_cache
from ..models import Comment, Topic, Writing, utcnow
from ..views import flash, require_user, topic_anchor

router = APIRouter()


def _anchor(comment: Comment) -> str:
    return topic_anchor(comment.topic_id, comment.writing_id, "comments")


@router.post("/topics/{topic_id}/comments")
def add_comment(
    request: Request,
    topic_id: int,
    body: str = Form(...),
    writing_id: int | None = Form(None),
    parent_id: int | None = Form(None),
    db: Session = Depends(get_db),
) -> Response:
    user = require_user(request)
    topic = db.get(Topic, topic_id)
    if not topic or not access.topic_open(user, topic):
        return RedirectResponse(f"/topics/{topic_id}", status_code=303)
    if not body.strip():
        flash(request, "A note needs words before it can stay.", "warn")
        return RedirectResponse(topic_anchor(topic_id, writing_id, "comments"), status_code=303)
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
            return RedirectResponse(topic_anchor(topic_id, writing_id, "comments"), status_code=303)
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
    return RedirectResponse(topic_anchor(topic_id, writing_id, "comments"), status_code=303)


@router.post("/comments/{comment_id}/offer")
def offer_comment(request: Request, comment_id: int, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    comment = db.get(Comment, comment_id)
    if not comment:
        return RedirectResponse("/", status_code=303)
    if comment.author == user and comment.share_status == "private" and comment.topic.share_status == "shared":
        share.set_status(comment, "offered")
        comment.topic.updated_at = utcnow()
        db.commit()
        events.tell(db, user, "sealed", comment.topic_id)
        flash(request, "Comment sealed for them.")
    return RedirectResponse(_anchor(comment), status_code=303)


@router.post("/comments/{comment_id}/accept")
def accept_comment(request: Request, comment_id: int, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    comment = db.get(Comment, comment_id)
    if not comment:
        return RedirectResponse("/", status_code=303)
    if comment.author != user and comment.share_status == "offered":
        if comment.topic.share_status != "shared":
            return RedirectResponse(_anchor(comment), status_code=303)
        share.set_status(comment, "shared")
        share.clear_withdrawals(db, "comment", comment.id)
        comment.topic.updated_at = utcnow()
        db.commit()
        store.write_shared(comment.topic, comment=comment)
        events.tell(db, user, "opened", comment.topic_id)
        flash(request, "Comment opened.")
    return RedirectResponse(_anchor(comment), status_code=303)


@router.post("/comments/{comment_id}/decline")
def decline_comment(request: Request, comment_id: int, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    comment = db.get(Comment, comment_id)
    if not comment:
        return RedirectResponse("/", status_code=303)
    if comment.author != user and comment.share_status == "offered":
        if comment.topic.share_status != "shared":
            return RedirectResponse(_anchor(comment), status_code=303)
        share.set_status(comment, "private")
        comment.topic.updated_at = utcnow()
        db.commit()
        store.remove_shared(comment.topic, comment=comment)
        store.write_local(comment.topic, comment=comment)
        events.tell(db, user, "unopened", comment.topic_id)
        flash(request, "Left unopened. Back on their side.")
    return RedirectResponse(_anchor(comment), status_code=303)


@router.post("/comments/{comment_id}/revoke")
def revoke_comment(request: Request, comment_id: int, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    comment = db.get(Comment, comment_id)
    if not comment:
        return RedirectResponse("/", status_code=303)
    if comment.author == user and comment.share_status in {"offered", "shared"}:
        stub = share.withdrawal("comment", comment, user)
        share.set_status(comment, "private")
        comment.topic.updated_at = utcnow()
        if stub:
            db.add(stub)
        db.commit()
        store.remove_shared(comment.topic, comment=comment)
        store.write_local(comment.topic, comment=comment)
        events.tell(db, user, "returned", comment.topic_id)
        flash(request, "Comment pulled back." + (" They keep a note that it was open." if stub else ""))
    return RedirectResponse(_anchor(comment), status_code=303)


@router.post("/comments/{comment_id}/delete")
def delete_comment(request: Request, comment_id: int, db: Session = Depends(get_db)) -> Response:
    user = require_user(request)
    comment = db.get(Comment, comment_id)
    if not comment:
        return RedirectResponse("/", status_code=303)
    anchor = _anchor(comment)
    if comment.author != user:
        return RedirectResponse(anchor, status_code=303)
    if comment.share_status != "private":
        flash(request, "Pull the note back before removing it.", "warn")
        return RedirectResponse(anchor, status_code=303)
    if not access.comment_removable(user, comment):
        flash(request, "A reply hangs on this note, so it stays.", "warn")
        return RedirectResponse(anchor, status_code=303)
    store.delete_comment_file(comment)
    comment.topic.updated_at = utcnow()
    db.delete(comment)
    db.commit()
    clear_markdown_cache()
    flash(request, "Note removed.")
    return RedirectResponse(anchor, status_code=303)
