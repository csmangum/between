from __future__ import annotations

import os

import httpx

from . import access, auth
from .models import Topic


def configured() -> bool:
    return bool(os.getenv("AGENT_API_KEY") or os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY"))


def _settings() -> tuple[str, str, str]:
    key = os.getenv("AGENT_API_KEY") or os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    base = (
        os.getenv("AGENT_BASE_URL")
        or ("https://api.x.ai/v1" if os.getenv("XAI_API_KEY") else "https://api.openai.com/v1")
    ).rstrip("/")
    model = os.getenv("AGENT_MODEL") or ("grok-4" if "x.ai" in base else "gpt-4o-mini")
    return key, base, model


def archive_excerpt(topic: Topic, user: str) -> str:
    """Only what this person is already allowed to read."""
    lines = [f"# {topic.title}", ""]
    if topic.prompt:
        lines += [topic.prompt, ""]
    for w in topic.writings:
        if not access.writing_open(user, w):
            continue
        heading = w.title or "Untitled writing"
        lines += [
            f"## {heading}",
            f"Author: {auth.display_for(w.author)}",
            "",
            w.body,
            "",
        ]
        if w.author == user and w.revision_status and w.revision_body:
            lines += ["Revision still on your desk:", w.revision_body, ""]
        for c in topic.comments:
            if c.writing_id != w.id or not access.comment_open(user, c):
                continue
            lines.append(f"- Comment by {auth.display_for(c.author)}: {c.body}")
        lines.append("")
    loose = [c for c in topic.comments if c.writing_id is None and access.comment_open(user, c)]
    if loose:
        lines.append("## Topic comments")
        for c in loose:
            lines.append(f"- {auth.display_for(c.author)}: {c.body}")
        lines.append("")
    if topic.share_status == "shared" and topic.messages:
        lines.append("## Chat")
        for m in topic.messages[-40:]:
            lines.append(f"- {auth.display_for(m.author)}: {m.body}")
    return "\n".join(lines).strip()


def draft_reply(topic: Topic, user: str) -> str:
    key, base, model = _settings()
    if not key:
        raise RuntimeError("missing-key")
    excerpt = archive_excerpt(topic, user)
    if not excerpt:
        raise RuntimeError("empty")
    other = access.other_username(user)
    other_display = auth.display_for(other) if other else "the other person"
    me = auth.display_for(user)
    system = (
        "You draft a reply for one person in a two-person correspondence. "
        "Write in first person as the named author. "
        "Use only the record you are given. Do not invent facts they did not write. "
        "Do not mention that you are an AI. Markdown is fine. "
        "Aim for a substantial reply — a page, not a chat bubble."
    )
    user_msg = (
        f"Author you are writing as: {me}\n"
        f"Counterpart: {other_display}\n"
        f"Topic: {topic.title}\n\n"
        f"Readable record:\n\n{excerpt}\n\n"
        "Draft the next writing from this author on this topic."
    )
    payload = {
        "model": model,
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=90.0) as client:
        response = client.post(f"{base}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    text = data["choices"][0]["message"]["content"].strip()
    if not text:
        raise RuntimeError("empty-reply")
    return text
