"""GET /sos/brain — observable snapshot of BrainService.

Reads a BrainSnapshot JSON blob that BrainService writes to
``sos:state:brain:snapshot`` (TTL 30s) after every tick. The dashboard
never imports BrainService directly — the redis key is the hand-off.

v0.7.0 adds ``GET /sos/brain/html`` — an operator-facing HTML page that
renders the snapshot plus the live ProviderMatrix breaker state. The
JSON endpoint stays untouched for programmatic callers.
"""
from __future__ import annotations

import html as _html
import logging

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse

from sos.contracts.brain_snapshot import BrainSnapshot
from sos.kernel.auth import verify_bearer

from ..redis_helper import _get_redis
from ..templates.brain import BRAIN_HTML

logger = logging.getLogger("sos.dashboard.brain")

router = APIRouter(tags=["brain"])

_BRAIN_SNAPSHOT_KEY = "sos:state:brain:snapshot"


@router.get("/sos/brain", response_model=BrainSnapshot)
async def sos_brain(
    authorization: str | None = Header(None),
) -> BrainSnapshot:
    """Return the latest BrainSnapshot persisted by BrainService.

    Auth: any valid Bearer token. Returns 401 on missing/invalid bearer,
    503 when the snapshot key is absent (brain is down or has not ticked
    within the last 30 s).
    """
    if verify_bearer(authorization) is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        raw = _get_redis().get(_BRAIN_SNAPSHOT_KEY)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="brain snapshot unavailable"
        ) from exc

    if not raw:
        raise HTTPException(status_code=503, detail="brain snapshot unavailable")

    if isinstance(raw, bytes):
        raw = raw.decode()

    try:
        return BrainSnapshot.model_validate_json(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="brain snapshot unavailable"
        ) from exc


def _load_provider_state() -> list[dict]:
    """Return a list of provider summaries (id, tier, breaker state, failures).

    Fails soft: if the matrix YAML isn't present or yaml isn't installed, we
    return an empty list so the HTML page still renders.
    """
    try:
        from sos.providers.matrix import get_breaker, load_matrix
    except Exception:
        return []
    try:
        cards = load_matrix()
    except Exception as exc:
        logger.info("provider matrix load failed: %s", exc)
        return []
    summaries: list[dict] = []
    for card in cards:
        b = get_breaker(card)
        summaries.append(
            {
                "id": card.id,
                "tier": card.tier,
                "backend": card.backend,
                "model": card.model,
                "state": b.state,
                "failures": b.failures,
            }
        )
    return summaries


def _render_provider_rows(providers: list[dict]) -> str:
    if not providers:
        return '<tr><td colspan="6" class="empty">no provider matrix loaded</td></tr>'
    out: list[str] = []
    for p in providers:
        out.append(
            "<tr>"
            f"<td>{_html.escape(p['id'])}</td>"
            f"<td><span class='tier'>{_html.escape(p['tier'])}</span></td>"
            f"<td>{_html.escape(p['backend'])}</td>"
            f"<td>{_html.escape(p['model'])}</td>"
            f"<td class='state-{_html.escape(p['state'])}'>{_html.escape(p['state'])}</td>"
            f"<td>{p['failures']}</td>"
            "</tr>"
        )
    return "".join(out)


def _render_route_rows(snapshot: BrainSnapshot) -> str:
    if not snapshot.recent_routes:
        return '<tr><td colspan="4" class="empty">no routes yet</td></tr>'
    out: list[str] = []
    # Show newest first.
    for route in reversed(snapshot.recent_routes[-20:]):
        out.append(
            "<tr>"
            f"<td><code>{_html.escape(route.task_id)}</code></td>"
            f"<td>{_html.escape(route.agent_name)}</td>"
            f"<td>{route.score:.2f}</td>"
            f"<td><code>{_html.escape(route.routed_at)}</code></td>"
            "</tr>"
        )
    return "".join(out)


def _render_event_type_rows(events_by_type: dict[str, int]) -> str:
    if not events_by_type:
        return '<tr><td colspan="2" class="empty">no events recorded</td></tr>'
    out: list[str] = []
    for event_type, count in sorted(events_by_type.items(), key=lambda kv: -kv[1]):
        out.append(
            "<tr>"
            f"<td><code>{_html.escape(event_type)}</code></td>"
            f"<td>{count}</td>"
            "</tr>"
        )
    return "".join(out)


@router.get("/sos/brain/html", response_class=HTMLResponse)
async def sos_brain_html(
    authorization: str | None = Header(None),
) -> HTMLResponse:
    """Render the BrainSnapshot + ProviderMatrix breaker state as HTML.

    Auth + 503 semantics match ``/sos/brain``. On snapshot miss we return a
    503; on success we render the inline template.
    """
    if verify_bearer(authorization) is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        raw = _get_redis().get(_BRAIN_SNAPSHOT_KEY)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="brain snapshot unavailable"
        ) from exc

    if not raw:
        raise HTTPException(status_code=503, detail="brain snapshot unavailable")

    if isinstance(raw, bytes):
        raw = raw.decode()

    try:
        snapshot = BrainSnapshot.model_validate_json(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="brain snapshot unavailable"
        ) from exc

    providers = _load_provider_state()
    healthy = sum(1 for p in providers if p["state"] == "closed")

    body = BRAIN_HTML.format(
        last_update_ts=_html.escape(snapshot.last_update_ts),
        service_started_at=_html.escape(snapshot.service_started_at),
        queue_size=snapshot.queue_size,
        in_flight_count=len(snapshot.in_flight),
        events_seen=snapshot.events_seen,
        provider_count=len(providers),
        provider_healthy=healthy,
        provider_rows=_render_provider_rows(providers),
        route_rows=_render_route_rows(snapshot),
        event_type_rows=_render_event_type_rows(snapshot.events_by_type),
    )
    return HTMLResponse(content=body)
