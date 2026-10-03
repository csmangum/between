from __future__ import annotations

import logging
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import auth
from ..throttle import login_throttle
from ..views import client_ip, current_user, render

log = logging.getLogger("between")

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)) -> Response:
    ip = client_ip(request)
    name = username.strip().lower()[:80]
    keys = [f"ip:{ip}", f"user:{name}"]
    wait = login_throttle.retry_after(keys)
    if wait > 0:
        log.warning("login throttled user=%s ip=%s wait=%.0fs", name, ip, wait)
        response = render(
            request,
            "login.html",
            status_code=429,
            error=f"Too many tries. The door opens again in {max(1, int(wait))} seconds.",
            username=username,
        )
        response.headers["Retry-After"] = str(max(1, int(wait)))
        return response
    person = auth.verify(username, password)
    if not person:
        delay = login_throttle.failed(keys)
        log.warning("login failed user=%s ip=%s next_delay=%.0fs", name, ip, delay)
        return render(
            request,
            "login.html",
            status_code=401,
            error="That name or password did not match.",
            username=username,
        )
    login_throttle.succeeded(keys)
    log.info("login ok user=%s ip=%s", person.username, ip)
    auth.start_session(request.session, person)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def _safe_return(target: str) -> str:
    target = target.strip()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in target):
        return "/"
    try:
        parts = urlsplit(target)
    except ValueError:
        return "/"
    if parts.scheme or parts.netloc:
        return "/"
    path = parts.path or "/"
    if not path.startswith("/") or path.startswith("//") or "\\" in path or "\\" in parts.query:
        return "/"
    safe_path = quote(path, safe="/-._~")
    safe_query = urlencode(parse_qsl(parts.query, keep_blank_values=True))
    return f"{safe_path}?{safe_query}" if safe_query else safe_path


@router.post("/switch")
def switch_profile(request: Request, username: str = Form(...), return_to: str = Form("")) -> Response:
    user = auth.session_user(request.session)
    if user and auth.switch_profile(request.session, username):
        log.warning(
            "admin switch from=%s to=%s ip=%s",
            user,
            request.session.get(auth.SESSION_USER),
            client_ip(request),
        )
        return RedirectResponse(_safe_return(return_to), status_code=303)
    return RedirectResponse("/" if user else "/login", status_code=303)
