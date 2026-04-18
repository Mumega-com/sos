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
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sos import __version__
from sos.kernel.auth import verify_bearer as _auth_verify_bearer
from sos.kernel.health import health_response
from sos.observability.logging import get_logger
from sos.services.journeys.tracker import JourneyTracker

SERVICE_NAME = "journeys"
DEFAULT_PORT = 6070
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

app = FastAPI(title="SOS Journeys Service", version=__version__)

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


def _require_admin(authorization: Optional[str]) -> Dict[str, Any]:
    ctx = _auth_verify_bearer(authorization)
    if ctx is None:
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")
    if not (ctx.is_system or ctx.is_admin):
        raise HTTPException(
            status_code=403, detail="journeys endpoints require system or admin token"
        )
    return {
        "project": ctx.project,
        "agent": ctx.agent,
        "is_system": ctx.is_system,
        "is_admin": ctx.is_admin,
    }


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
    _require_admin(authorization)
    path = _tracker().recommend_journey(agent)
    return {"agent": agent, "path": path}


@app.post("/start")
async def start(
    req: StartRequest, authorization: Optional[str] = Header(default=None)
) -> Dict[str, Any]:
    _require_admin(authorization)
    result = _tracker().start_journey(req.agent, req.path)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/status/{agent}")
async def status(
    agent: str, authorization: Optional[str] = Header(default=None)
) -> Dict[str, Any]:
    _require_admin(authorization)
    progress = _tracker().check_progress(agent)
    return {"agent": agent, "progress": progress}


@app.get("/leaderboard")
async def leaderboard(
    path: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, List[Dict[str, Any]]]:
    _require_admin(authorization)
    leaders = _tracker().get_leaderboard(path)
    return {"leaders": leaders}
