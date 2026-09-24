from __future__ import annotations

import re
from pathlib import Path

from .db import DATABASE_URL
from .models import Comment, Topic, Writing


def data_root() -> Path:
    if DATABASE_URL.startswith("sqlite:///"):
        db_path = Path(DATABASE_URL.replace("sqlite:///", "", 1))
        return db_path.parent
    return Path(__file__).resolve().parent.parent / "data"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:48] or "untitled"


def _topic_dir(base: Path, topic: Topic) -> Path:
    folder = base / f"{topic.id:04d}-{_slug(topic.title)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _stable_writing_path(folder: Path, writing: Writing) -> Path:
    for legacy in folder.glob(f"writing-{writing.id}-*.md"):
        legacy.unlink(missing_ok=True)
    return folder / f"writing-{writing.id}.md"


def revision_path(writing: Writing) -> Path:
    root = data_root() / "local" / writing.author
    folder = _topic_dir(root, writing.topic)
    return folder / f"revision-{writing.id}.md"


def write_revision(writing: Writing) -> Path | None:
    """The unopened revision stays in the author's local folder."""
    path = revision_path(writing)
    if not writing.revision_status or not writing.revision_body:
        path.unlink(missing_ok=True)
        return None
    path.write_text(
        f"# {writing.revision_title or 'Untitled revision'}\n\n"
        f"_author: {writing.author}_\n"
        f"_revision: {writing.revision_status}_\n\n"
        f"{writing.revision_body}\n",
        encoding="utf-8",
    )
    return path


def write_local(topic: Topic, writing: Writing | None = None, comment: Comment | None = None) -> Path:
    """Keep the author's copy on disk. The other person never reads this path."""
    if writing is not None:
        root = data_root() / "local" / writing.author
    elif comment is not None:
        root = data_root() / "local" / comment.author
    else:
        root = data_root() / "local" / topic.created_by
    folder = _topic_dir(root, topic)
    if writing is None:
        if comment is not None:
            path = folder / f"comment-{comment.id}.md"
            path.write_text(
                f"_author: {comment.author}_\n"
                f"_status: {comment.share_status}_\n\n"
                f"{comment.body}\n",
                encoding="utf-8",
            )
            return path
        path = folder / "topic.md"
        path.write_text(
            f"# {topic.title}\n\n"
            f"_status: {topic.share_status}_\n\n"
            f"{topic.prompt or ''}\n",
            encoding="utf-8",
        )
        return path
    path = _stable_writing_path(folder, writing)
    path.write_text(
        f"# {writing.title or 'Untitled writing'}\n\n"
        f"_author: {writing.author}_\n"
        f"_status: {writing.share_status}_\n\n"
        f"{writing.body}\n",
        encoding="utf-8",
    )
    return path


def write_shared(topic: Topic, writing: Writing | None = None, comment: Comment | None = None) -> Path | None:
    """Only called after both people have agreed."""
    if topic.share_status != "shared" and writing is None and comment is None:
        return None
    folder = _topic_dir(data_root() / "shared", topic)
    if writing is not None:
        if writing.share_status != "shared":
            return None
        path = _stable_writing_path(folder, writing)
        path.write_text(
            f"# {writing.title or 'Untitled writing'}\n\n"
            f"_author: {writing.author}_\n\n"
            f"{writing.body}\n",
            encoding="utf-8",
        )
        return path
    if comment is not None:
        if comment.share_status != "shared":
            return None
        path = folder / f"comment-{comment.id}.md"
        path.write_text(f"_author: {comment.author}_\n\n{comment.body}\n", encoding="utf-8")
        return path
    path = folder / "topic.md"
    path.write_text(f"# {topic.title}\n\n{topic.prompt or ''}\n", encoding="utf-8")
    return path
