"""Templates, Jinja filters, flashes, and the request-level helpers every route uses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.responses import Response

from . import access, auth, config, share
from .markdown_render import render_markdown
from .queries import sealed_offer_count

APP_NAME = config.APP_NAME
BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class RedirectNeeded(Exception):
    """Raised from a dependency when the person is not signed in; the app turns it into a 303."""

    def __init__(self, url: str):
        self.url = url


def md(text: str) -> str:
    return render_markdown(text)


def fmt_dt(value: datetime | None) -> str:
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone().strftime("%b %d, %Y · %H:%M")


def fmt_dt_soft(value: datetime | None) -> str:
    """A quieter relative time for the room's pacing."""
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    local = value.astimezone()
    now = datetime.now(UTC).astimezone()
    seconds = int((now - local).total_seconds())
    if seconds < 45:
        return "just now"
    if seconds < 90:
        return "a minute ago"
    if seconds < 45 * 60:
        mins = max(2, seconds // 60)
        return f"{mins} minutes ago"
    if seconds < 90 * 60:
        return "an hour ago"
    if local.date() == now.date():
        return f"today · {local.strftime('%H:%M')}"
    if local.date() == (now.date() - timedelta(days=1)):
        return f"yesterday · {local.strftime('%H:%M')}"
    if seconds < 60 * 60 * 24 * 7:
        return local.strftime("%A · %H:%M")
    return local.strftime("%b %d, %Y")


def count_label(n: int, singular: str, plural: str | None = None) -> str:
    number = int(n or 0)
    word = singular if number == 1 else (plural or f"{singular}s")
    return f"{number} {word}"


templates.env.filters["md"] = md
templates.env.filters["when"] = fmt_dt
templates.env.filters["when_soft"] = fmt_dt_soft
templates.env.filters["count_label"] = count_label
templates.env.filters["share_label"] = share.label
templates.env.globals["app_name"] = APP_NAME
templates.env.globals["display_for"] = auth.display_for


def current_user(request: Request) -> str | None:
    return auth.session_user(request.session)


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise RedirectNeeded("/login")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def flash(request: Request, message: str, kind: str = "ok") -> None:
    flashes = request.session.get("flashes") or []
    flashes.append({"message": message, "kind": kind})
    request.session["flashes"] = flashes


def pop_flashes(request: Request) -> list[dict[str, str]]:
    return list(request.session.pop("flashes", []) or [])


def ctx(request: Request, db: Session | None = None, **extra: Any) -> dict[str, Any]:
    user = current_user(request)
    people = auth.load_people()
    sealed = extra.pop("sealed_count", None)
    if sealed is None:
        sealed = sealed_offer_count(user, db)
    return {
        "request": request,
        "user": user,
        "display": auth.display_for(user) if user else None,
        "other": next((p.display for name, p in people.items() if name != user), None) if user else None,
        "other_user": access.other_username(user) if user else None,
        "people": people,
        "flashes": pop_flashes(request),
        "sealed_count": sealed,
        **extra,
    }


def render(request: Request, name: str, status_code: int = 200, db: Session | None = None, **extra: Any) -> Response:
    return templates.TemplateResponse(
        request,
        name,
        ctx(request, db=db, **extra),
        status_code=status_code,
    )


def topic_anchor(topic_id: int, writing_id: int | None = None, fragment: str | None = None) -> str:
    if writing_id:
        return f"/topics/{topic_id}#writing-{writing_id}"
    if fragment:
        return f"/topics/{topic_id}#{fragment}"
    return f"/topics/{topic_id}"


def other_display(user: str) -> str:
    return auth.display_for(access.other_username(user) or "")
