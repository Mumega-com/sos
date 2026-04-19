"""GET /sos/mesh (HTML) + GET /sos/mesh/api (JSON) — phase3/W5.

Operator view of the v0.9.2 mesh: live enrolled agents grouped by squad,
with heartbeat age and stale badges.  HTML is admin-gated (cookie + admin
scope).  JSON is auth-gated (any valid Bearer).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from sos.kernel.auth import verify_bearer
from sos.services.registry import read_all_cards

from ..auth import _is_admin, _tenant_from_cookie
from ..config import COOKIE_NAME
from ..templates.sos_mesh import heartbeat_cell, page_html, squad_section_html
from ..templates.sos_overview import _SOS_BASE_CSS, _sos_nav
from .sos_operator import _relative_time

router = APIRouter()
logger = logging.getLogger("dashboard")

_UNSQUADDED_KEY = "__unsquadded__"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_squads(
    cards: list,
) -> tuple[dict[str, list], list]:
    """Group cards by squad slug.  Returns (squad_dict, unsquadded_list)."""
    squads: dict[str, list] = {}
    unsquadded: list = []
    for card in cards:
        if not card.squads:
            unsquadded.append(card)
        else:
            for slug in card.squads:
                squads.setdefault(slug, []).append(card)
    return squads, unsquadded


def _stale_badge(stale: bool) -> str:
    if stale:
        return '<span class="badge badge-amber">stale</span>'
    return '<span class="badge badge-green">live</span>'


def _render_squad_section(slug: str, cards: list, display: str) -> str:
    rows = ""
    for card in sorted(cards, key=lambda c: c.name):
        age_label = _relative_time(card.last_seen)
        badge = _stale_badge(card.stale)
        hb = heartbeat_cell(card.heartbeat_url)
        proj = card.project or '<span class="muted">\u2014</span>'
        rows += (
            "<tr>"
            f'<td style="padding:10px 12px;font-weight:500;color:#F8FAFC">'
            f"{card.name}</td>"
            f'<td style="padding:10px 12px;color:#CBD5E1">{card.role}</td>'
            f'<td style="padding:10px 12px">{badge}</td>'
            f'<td style="padding:10px 12px;color:#94A3B8">{age_label}</td>'
            f'<td style="padding:10px 12px;color:#94A3B8">{hb}</td>'
            f'<td style="padding:10px 12px;color:#94A3B8">{proj}</td>'
            "</tr>"
        )
    return squad_section_html(slug, rows, display)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/sos/mesh", response_class=HTMLResponse)
async def sos_mesh(request: Request) -> Response:
    """Mesh tab — live enrolled agents grouped by squad. Admin-gated."""
    cookie_val = request.cookies.get(COOKIE_NAME)
    tenant = _tenant_from_cookie(cookie_val)
    if not tenant:
        return RedirectResponse(url="/login?next=/sos/mesh", status_code=302)
    if not _is_admin(tenant):
        return HTMLResponse("<h1>403 \u2014 admin scope required</h1>", status_code=403)

    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")

    cards: list = []
    try:
        cards = read_all_cards(project=None)
    except Exception:
        logger.debug("Failed to load cards for mesh tab", exc_info=True)

    squads, unsquadded = _build_squads(cards)
    total = len(cards)

    sections = ""
    for slug in sorted(squads.keys()):
        sections += _render_squad_section(slug, squads[slug], slug)
    if unsquadded:
        sections += _render_squad_section(_UNSQUADDED_KEY, unsquadded, "No Squad")

    if not sections:
        sections = '<p class="muted" style="margin-top:24px">No enrolled agents found.</p>'

    nav_html = _sos_nav("Mesh")
    html = page_html(_SOS_BASE_CSS, nav_html, now_str, total, sections)
    return HTMLResponse(html)


@router.get("/sos/mesh/api")
async def sos_mesh_api(
    authorization: str | None = Header(None),
) -> JSONResponse:
    """JSON snapshot of the mesh — auth via any valid Bearer.

    Response shape:
      {"generated_at": ISO, "squads": {slug: [card, ...]},
       "unsquadded": [...], "total": N}
    """
    if verify_bearer(authorization) is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    cards: list = []
    try:
        cards = read_all_cards(project=None)
    except Exception:
        logger.debug("Failed to load cards for mesh api", exc_info=True)

    squads, unsquadded = _build_squads(cards)

    return JSONResponse(
        {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "squads": {
                slug: [c.model_dump() for c in squad_cards]
                for slug, squad_cards in sorted(squads.items())
            },
            "unsquadded": [c.model_dump() for c in unsquadded],
            "total": len(cards),
        }
    )
