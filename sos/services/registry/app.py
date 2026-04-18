"""SOS Registry Service — HTTP surface for the canonical agent registry.

Exposes ``sos.services.registry.read_all/read_one`` over HTTP so sibling services
(notably :mod:`sos.services.brain`) can list agents without importing this
module directly. That direct-import path is the P0-09 violation closed by
v0.4.5 Wave 5.

Endpoints:
- ``GET /health`` — canonical SOS health response.
- ``GET /agents`` — Bearer-auth'd read-through to :func:`read_all`.
  Optional ``?project=<slug>`` query param. Scoped tokens are forced to their
  own project; system / admin tokens see any project.
- ``GET /agents/{agent_id}`` — single-agent lookup via :func:`read_one`.

Auth scope:
- System / admin tokens: unconditional access; any ``project`` allowed.
- Scoped tokens (``project``/``tenant_slug`` set): the query ``project``
  parameter is overridden to the token's scope; a mismatched explicit
  ``project`` triggers 403.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from sos import __version__
from sos.contracts.policy import PolicyDecision
from sos.kernel.auth import verify_bearer as _auth_verify_bearer
from sos.kernel.health import health_response
from sos.kernel.policy.gate import can_execute
from sos.observability.logging import get_logger
from sos.services.registry import read_all, read_one

SERVICE_NAME = "registry"
DEFAULT_PORT = 6067
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

app = FastAPI(title="SOS Registry Service", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    """Announce presence to the SOS service registry."""
    try:
        from sos.services.bus.discovery import register_service

        await register_service(SERVICE_NAME, DEFAULT_PORT)
    except Exception as exc:  # pragma: no cover — discovery is best-effort
        log.warning("registry discovery registration failed", error=str(exc))


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


# ---------------------------------------------------------------------------
# Auth helper — same pattern as integrations/app.py::_verify_bearer
# ---------------------------------------------------------------------------


def _verify_bearer(authorization: Optional[str]) -> Dict[str, Any]:
    """Return a token record dict or raise 401 on failure."""
    ctx = _auth_verify_bearer(authorization)
    if ctx is None:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        raise HTTPException(status_code=401, detail="invalid or inactive token")
    return {
        "project": ctx.project,
        "tenant_slug": ctx.tenant_slug,
        "agent": ctx.agent,
        "label": ctx.label,
        "is_system": ctx.is_system,
        "is_admin": ctx.is_admin,
        "active": True,
    }


def _resolve_project_scope(
    entry: Dict[str, Any],
    requested_project: Optional[str],
) -> Optional[str]:
    """Resolve the effective ``project`` filter for a caller.

    - System / admin tokens: pass the caller's requested project through as-is
      (``None`` means "all projects").
    - Scoped tokens: the token's own scope wins. A mismatched explicit
      ``requested_project`` triggers 403.
    """
    if entry.get("is_system") or entry.get("is_admin"):
        return requested_project

    scope = entry.get("project") or entry.get("tenant_slug")
    if scope is None:
        # Non-system token with no scope — reject to avoid cross-project reads.
        raise HTTPException(status_code=403, detail="token has no project scope")

    if requested_project is not None and requested_project != scope:
        raise HTTPException(
            status_code=403,
            detail=(
                f"token is scoped to project '{scope}', "
                f"cannot read registry for '{requested_project}'"
            ),
        )
    return scope


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> Dict[str, Any]:
    return health_response(SERVICE_NAME, _START_TIME)


@app.get("/agents")
async def list_agents(
    project: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Return all agents (optionally filtered by ``project``).

    The registry's ``read_all`` call is synchronous; we delegate to it in-line
    because it is already cheap redis I/O with short timeouts.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="registry:agents_list",
        resource=project or "mumega",
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    agents = read_all(project=effective_project)
    items: List[Dict[str, Any]] = [a.to_dict() for a in agents]
    return {"agents": items, "count": len(items)}


@app.get("/agents/{agent_id}")
async def get_agent(
    agent_id: str,
    project: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Return a single agent by id, or 404 if missing."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="registry:agent_read",
        resource=agent_id,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    ident = read_one(agent_id, project=effective_project)
    if ident is None:
        raise HTTPException(
            status_code=404,
            detail=f"no agent {agent_id!r} in registry",
        )
    return ident.to_dict()
