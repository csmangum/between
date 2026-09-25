from __future__ import annotations

import re
import shutil
from pathlib import Path

from .db import sqlite_path
from .models import Comment, Topic, Writing

DIR_MODE = 0o700
FILE_MODE = 0o600


def data_root() -> Path:
    db_path = sqlite_path()
    if db_path is not None:
        return db_path.parent
    return Path(__file__).resolve().parent.parent / "data"


def _chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def _secure_mkdir(path: Path) -> Path:
    """Create the directory chain below the data root, each level owner-only."""
    root = data_root()
    path.mkdir(parents=True, exist_ok=True)
    for candidate in [path, *path.parents]:
        if not candidate.is_relative_to(root):
            break
        _chmod(candidate, DIR_MODE)
        if candidate == root:
            break
    return path


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    _chmod(path, FILE_MODE)
    return path


def secure_data_root() -> None:
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    _chmod(root, DIR_MODE)
    for name in ("local", "shared"):
        child = root / name
        if child.exists():
            _chmod(child, DIR_MODE)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:48] or "untitled"


def _topic_folder(base: Path, topic: Topic) -> Path:
    return base / f"{topic.id:04d}-{_slug(topic.title)}"


def _topic_dir(base: Path, topic: Topic) -> Path:
    return _secure_mkdir(_topic_folder(base, topic))


def _shared_folder(topic: Topic) -> Path:
    return _topic_folder(data_root() / "shared", topic)


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
    return _write(
        path,
        f"# {writing.revision_title or 'Untitled revision'}\n\n"
        f"_author: {writing.author}_\n"
        f"_revision: {writing.revision_status}_\n\n"
        f"{writing.revision_body}\n",
    )


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
            return _write(
                folder / f"comment-{comment.id}.md",
                f"_author: {comment.author}_\n"
                f"_status: {comment.share_status}_\n\n"
                f"{comment.body}\n",
            )
        return _write(
            folder / "topic.md",
            f"# {topic.title}\n\n"
            f"_status: {topic.share_status}_\n\n"
            f"{topic.prompt or ''}\n",
        )
    return _write(
        _stable_writing_path(folder, writing),
        f"# {writing.title or 'Untitled writing'}\n\n"
        f"_author: {writing.author}_\n"
        f"_status: {writing.share_status}_\n\n"
        f"{writing.body}\n",
    )


def _write_shared_writing(folder: Path, writing: Writing) -> Path:
    return _write(
        _stable_writing_path(folder, writing),
        f"# {writing.title or 'Untitled writing'}\n\n"
        f"_author: {writing.author}_\n\n"
        f"{writing.body}\n",
    )


def _write_shared_comment(folder: Path, comment: Comment) -> Path:
    return _write(folder / f"comment-{comment.id}.md", f"_author: {comment.author}_\n\n{comment.body}\n")


def write_shared(topic: Topic, writing: Writing | None = None, comment: Comment | None = None) -> Path | None:
    """Only called after both people have agreed.

    With no writing or comment given, the whole readable tree of the topic is mirrored, so a
    topic that was pulled back and opened again gets its shared pages back on disk.
    """
    if topic.share_status != "shared":
        return None
    folder = _topic_dir(data_root() / "shared", topic)
    if writing is not None:
        if writing.share_status != "shared":
            return None
        return _write_shared_writing(folder, writing)
    if comment is not None:
        if comment.share_status != "shared":
            return None
        return _write_shared_comment(folder, comment)
    for w in topic.writings:
        if w.share_status == "shared":
            _write_shared_writing(folder, w)
    for c in topic.comments:
        if c.share_status == "shared":
            _write_shared_comment(folder, c)
    return _write(folder / "topic.md", f"# {topic.title}\n\n{topic.prompt or ''}\n")


def remove_shared(topic: Topic, writing: Writing | None = None, comment: Comment | None = None) -> None:
    """Access ended: the shared mirror should no longer hold the page.

    Pulling back a topic removes its whole shared folder; pulling back a writing or comment
    removes just that file. Copies the other person exported earlier are theirs to keep.
    """
    folder = _shared_folder(topic)
    if writing is not None:
        for path in folder.glob(f"writing-{writing.id}*.md"):
            path.unlink(missing_ok=True)
        return
    if comment is not None:
        (folder / f"comment-{comment.id}.md").unlink(missing_ok=True)
        return
    shutil.rmtree(folder, ignore_errors=True)
