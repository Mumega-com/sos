"""SOS Journeys Service — HTTP surface for progression-path management.

Exposes :class:`sos.services.journeys.tracker.JourneyTracker` over HTTP so
callers (e.g. ``sos.agents.join``) can recommend + start journeys without
importing the tracker module directly — that in-process import is the P1-05
violation closed by v0.4.6 Steps 4+5.

Endpoints:
- ``GET /health`` — canonical SOS health response.
- ``GET /recommend/{agent}`` — suggest a journey path based on skills + conductance.
- ``POST /start`` — enroll an agent on a path.
- ``GET /status/{agent}`` — return the agent's current journey state.
- ``GET /leaderboard`` — who is furthest along (optional ``?path=`` filter).

Auth: system / admin tokens only. Milestones carry $MIND rewards, so scoped
or user tokens must not drive enrollment.

v0.5.3: Replaced inline ``_require_admin`` with a single
``sos.kernel.policy.gate.can_execute`` call per route, matching the v0.5.1
POC pattern from the integrations service.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sos import __version__
from sos.contracts.policy import PolicyDecision
from sos.kernel.health import health_response
from sos.kernel.policy.gate import can_execute
from sos.kernel.telemetry import init_tracing, instrument_fastapi
from sos.observability.logging import get_logger
from sos.services.journeys.tracker import JourneyTracker

SERVICE_NAME = "journeys"
DEFAULT_PORT = 6070
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

init_tracing("journeys")

app = FastAPI(title="SOS Journeys Service", version=__version__)
instrument_fastapi(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    try:
        from sos.services.bus.discovery import register_service

        await register_service(SERVICE_NAME, DEFAULT_PORT)
    except Exception as exc:  # pragma: no cover — discovery is best-effort
        log.warn("journeys discovery registration failed", error=str(exc))


# ---------------------------------------------------------------------------
# Gate helper — turn a PolicyDecision into the appropriate HTTP response
# ---------------------------------------------------------------------------


def _raise_on_deny(decision: PolicyDecision, *, require_system: bool = False) -> None:
    """Map a gate decision to 401/403 if denied.

    When ``require_system`` is True, also enforce that the successful
    decision came via system/admin scope — the gate allows tenant-scoped
    callers into their own tenant, but OAuth callbacks are only meaningful
    from MCP's system token.
    """
    if not decision.allowed:
        reason = decision.reason or "unauthorized"
        if "bearer" in reason.lower() or "auth" in reason.lower():
            raise HTTPException(status_code=401, detail=reason)
        raise HTTPException(status_code=403, detail=reason)

    if require_system:
        pillars = set(decision.pillars_passed)
        # system/admin callers never get 'tenant_scope' added because the
        # gate short-circuits with 'system/admin scope' reason. Check that.
        if "system/admin" not in decision.reason:
            raise HTTPException(
                status_code=403,
                detail="oauth callbacks require system or admin scope",
            )


def _tracker() -> JourneyTracker:
    # Fresh instance per request — paths are cached on disk, reloading is cheap
    # and it keeps the service stateless across worker restarts.
    return JourneyTracker()


class StartRequest(BaseModel):
    agent: str
    path: str


@app.get("/health")
async def health() -> Dict[str, Any]:
    return health_response(SERVICE_NAME, _START_TIME)


@app.get("/recommend/{agent}")
async def recommend(
    agent: str, authorization: Optional[str] = Header(default=None)
) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    decision = await can_execute(
        action="journeys:recommend",
        resource=agent,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)
    path = _tracker().recommend_journey(agent)
    return {"agent": agent, "path": path}


@app.post("/start")
async def start(
    req: StartRequest, authorization: Optional[str] = Header(default=None)
) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    decision = await can_execute(
        action="journeys:start",
        resource=req.path,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)
    result = _tracker().start_journey(req.agent, req.path)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/status/{agent}")
async def status(
    agent: str, authorization: Optional[str] = Header(default=None)
) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    decision = await can_execute(
        action="journeys:status",
        resource=agent,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)
    progress = _tracker().check_progress(agent)
    return {"agent": agent, "progress": progress}


@app.get("/leaderboard")
async def leaderboard(
    path: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, List[Dict[str, Any]]]:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    decision = await can_execute(
        action="journeys:leaderboard",
        resource="leaderboard",
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)
    leaders = _tracker().get_leaderboard(path)
    return {"leaders": leaders}
