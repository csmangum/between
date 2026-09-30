from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import access, share, store
from ..db import get_db
from ..markdown_render import clear_markdown_cache
from ..models import Topic, Writing, utcnow
from ..views import flash, other_display, require_user, topic_anchor

router = APIRouter()


def _anchor(writing: Writing) -> str:
    return topic_anchor(writing.topic_id, writing.id)


@router.post("/topics/{topic_id}/writings")
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
    return RedirectResponse(_anchor(writing), status_code=303)


@router.post("/writings/{writing_id}/offer")
def offer_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    if writing.author == user and writing.share_status == "private":
        if writing.topic.share_status != "shared":
            flash(request, "Open the topic together first, then send this writing.", "warn")
            return RedirectResponse(f"/topics/{writing.topic_id}", status_code=303)
        share.set_status(writing, "offered")
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_local(writing.topic, writing)
        flash(request, f"Writing sealed for {other_display(user)}.")
    return RedirectResponse(_anchor(writing), status_code=303)


@router.post("/writings/{writing_id}/accept")
def accept_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    if writing.author != user and writing.share_status == "offered":
        if writing.topic.share_status != "shared":
            return RedirectResponse(_anchor(writing), status_code=303)
        share.set_status(writing, "shared")
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_shared(writing.topic, writing)
        flash(request, "Opened. This writing is kept between you.")
    return RedirectResponse(_anchor(writing), status_code=303)


@router.post("/writings/{writing_id}/decline")
def decline_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    if writing.author != user and writing.share_status == "offered":
        if writing.topic.share_status != "shared":
            return RedirectResponse(f"/topics/{writing.topic_id}", status_code=303)
        share.set_status(writing, "private")
        writing.topic.updated_at = utcnow()
        db.commit()
        store.remove_shared(writing.topic, writing)
        store.write_local(writing.topic, writing)
        flash(request, "Left unopened. Back on their desk.")
    return RedirectResponse(f"/topics/{writing.topic_id}", status_code=303)


@router.post("/writings/{writing_id}/revoke")
def revoke_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    if writing.author == user and writing.share_status in {"offered", "shared"}:
        share.fold_revision_into_private(writing)
        share.set_status(writing, "private")
        writing.topic.updated_at = utcnow()
        db.commit()
        store.remove_shared(writing.topic, writing)
        store.write_local(writing.topic, writing)
        store.write_revision(writing)
        clear_markdown_cache()
        flash(request, "Pulled back to your desk.")
    return RedirectResponse(_anchor(writing), status_code=303)


@router.post("/writings/{writing_id}/edit")
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
        return RedirectResponse(_anchor(writing), status_code=303)
    if not body.strip():
        return RedirectResponse(_anchor(writing), status_code=303)
    writing.title = title.strip()
    writing.body = body.strip()
    writing.updated_at = utcnow()
    writing.topic.updated_at = utcnow()
    db.commit()
    store.write_local(writing.topic, writing)
    flash(request, "Writing updated on your desk.")
    return RedirectResponse(_anchor(writing), status_code=303)


@router.post("/writings/{writing_id}/delete")
def delete_writing(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    topic = writing.topic
    anchor = _anchor(writing)
    if writing.author != user:
        return RedirectResponse(f"/topics/{topic.id}", status_code=303)
    if writing.share_status != "private" or writing.revision_status:
        flash(request, "Pull it back to your desk before removing it.", "warn")
        return RedirectResponse(anchor, status_code=303)
    if not access.writing_removable(user, writing):
        flash(request, "Their notes hang on this writing, so it stays. Only what you wrote is yours to remove.", "warn")
        return RedirectResponse(anchor, status_code=303)
    own_notes = sorted(
        (c for c in topic.comments if c.writing_id == writing.id),
        key=lambda c: c.created_at,
        reverse=True,
    )
    for c in own_notes:
        store.delete_comment_file(c)
        db.delete(c)
        db.flush()
    store.delete_writing_files(writing)
    db.delete(writing)
    topic.updated_at = utcnow()
    db.commit()
    clear_markdown_cache()
    flash(request, "Writing removed from your desk.")
    return RedirectResponse(f"/topics/{topic.id}", status_code=303)


def _save_revision(request: Request, writing: Writing, title: str, body: str, db: Session) -> RedirectResponse:
    anchor = _anchor(writing)
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


@router.post("/writings/{writing_id}/revision")
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


@router.post("/writings/{writing_id}/revision/offer")
def offer_writing_revision(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
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
        flash(
            request,
            f"Revision sealed for {other_display(user)}. The writing they opened stays until they open this.",
        )
    return RedirectResponse(_anchor(writing), status_code=303)


@router.post("/writings/{writing_id}/revision/accept")
def accept_writing_revision(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
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
    return RedirectResponse(_anchor(writing), status_code=303)


@router.post("/writings/{writing_id}/revision/decline")
def decline_writing_revision(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    if writing.author != user and writing.revision_status == "offered" and writing.topic.share_status == "shared":
        share.return_revision(writing)
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_revision(writing)
        flash(request, "Left unopened. The revision returned to their desk. The writing you have stays.")
    return RedirectResponse(_anchor(writing), status_code=303)


@router.post("/writings/{writing_id}/revision/revoke")
def revoke_writing_revision(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    if writing.author == user and writing.revision_status == "offered":
        share.return_revision(writing)
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_revision(writing)
        flash(request, "Revision pulled back. They still have the writing they opened.")
    return RedirectResponse(_anchor(writing), status_code=303)


@router.post("/writings/{writing_id}/revision/discard")
def discard_writing_revision(request: Request, writing_id: int, db: Session = Depends(get_db)):
    user = require_user(request)
    writing = db.get(Writing, writing_id)
    if not writing:
        return RedirectResponse("/", status_code=303)
    if writing.author == user and writing.revision_status in {"private", "offered"}:
        share.clear_revision(writing)
        writing.topic.updated_at = utcnow()
        db.commit()
        store.write_revision(writing)
        flash(request, "Revision discarded. The writing you both keep is unchanged.")
    return RedirectResponse(_anchor(writing), status_code=303)
