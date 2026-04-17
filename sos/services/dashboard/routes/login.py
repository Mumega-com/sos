"""GET /login, POST /login, GET /logout."""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import _verify_token
from ..config import COOKIE_NAME
from ..templates.login import LOGIN_HTML

router = APIRouter()


@router.get("/", response_class=RedirectResponse)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def login_page() -> HTMLResponse:
    return HTMLResponse(LOGIN_HTML.replace("{error}", ""))


@router.post("/login")
async def login_submit(token: str = Form(...)) -> Response:
    entry = _verify_token(token)
    if not entry:
        html = LOGIN_HTML.replace("{error}", '<p class="error">Invalid or inactive token.</p>')
        return HTMLResponse(html, status_code=401)

    cookie_data = json.dumps({
        "token": token,
        "project": entry.get("project"),
        "label": entry.get("label", ""),
    })
    resp = RedirectResponse(url="/dashboard", status_code=303)
    resp.set_cookie(
        COOKIE_NAME,
        cookie_data,
        httponly=True,
        max_age=86400 * 7,
        samesite="lax",
    )
    return resp


@router.get("/logout")
async def logout() -> Response:
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(COOKIE_NAME)
    return resp
