from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import auth
from ..throttle import login_throttle
from ..views import client_ip, current_user, render

log = logging.getLogger("between")

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
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
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
