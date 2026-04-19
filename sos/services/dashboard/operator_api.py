"""SOS operator dashboard — JSON API routes (v0.8.1 S5).

Three operator-facing views + an admin-only agent kill switch. All routes
sit at ``/dashboard/...`` and follow the same auth/scope/gate pattern used
by ``sos/services/objectives/app.py``:

1. Bearer presence → 401 if missing.
2. ``can_execute`` gate → 401 on auth failure, 403 on scope mismatch.
3. ``_resolve_project_scope`` — scoped tokens may only see their own
   ``project``; system/admin tokens see any.

The three routes are:

- ``GET  /dashboard/tenants/{project}/summary`` — state tallies + 24h
  $MIND burn for the given project.
- ``GET  /dashboard/tenants/{project}/agents``  — live AgentCards.
- ``POST /dashboard/agents/{name}/kill``         — admin/system only;
  writes the kill-switch key to Redis (24h TTL) and emits an audit event.

Plus ``GET /health`` — canonical SOS shape.

Data access strategy (to keep the dashboard on the right side of the
R1 contract):
- **Summary counts** come from direct Redis SCANs over the objectives
  keyspace (``sos:objectives:{project}:*``). This avoids importing
  ``sos.services.objectives`` at all.
- **Agent cards** come from ``sos.services.registry.read_all_cards``.
  ``RegistryClient`` doesn't expose card listing yet, so this is an
  in-process import with an R1 ignore; a later sprint adds
  ``list_cards`` to the HTTP client and removes the ignore.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException

from sos.contracts.policy import PolicyDecision
from sos.kernel.auth import verify_bearer as _auth_verify_bearer
from sos.kernel.health import health_response
from sos.kernel.kill_switch import kill_agent
from sos.kernel.policy.gate import can_execute
from sos.services.registry import read_all_cards  # noqa: E402  — R1 ignore; see pyproject.toml

log = logging.getLogger("dashboard.operator")

SERVICE_NAME = "dashboard"
_START_TIME = time.time()

_AUDIT_STREAM = "sos:stream:global:dashboard"

router = APIRouter(tags=["dashboard-operator"])


# ---------------------------------------------------------------------------
# Auth + scope helpers — mirror sos/services/objectives/app.py exactly
# ---------------------------------------------------------------------------


def _raise_on_deny(decision: PolicyDecision) -> None:
    if not decision.allowed:
        reason = decision.reason or "unauthorized"
        if "bearer" in reason.lower() or "auth" in reason.lower():
            raise HTTPException(status_code=401, detail=reason)
        raise HTTPException(status_code=403, detail=reason)


def _verify_bearer(authorization: Optional[str]) -> Dict[str, Any]:
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
    if entry.get("is_system") or entry.get("is_admin"):
        return requested_project

    scope = entry.get("project") or entry.get("tenant_slug")
    if scope is None:
        raise HTTPException(status_code=403, detail="token has no project scope")

    if requested_project is not None and requested_project != scope:
        raise HTTPException(
            status_code=403,
            detail=(
                f"token is scoped to project '{scope}', "
                f"cannot access dashboard for '{requested_project}'"
            ),
        )
    return scope


# ---------------------------------------------------------------------------
# Redis helpers — direct connection so we don't import from sibling services.
# ---------------------------------------------------------------------------


def _get_redis():  # type: ignore[no-untyped-def]
    import redis  # type: ignore[import-untyped]

    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        password=os.environ.get("REDIS_PASSWORD", "") or None,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def _emit_audit(event_type: str, payload: Dict[str, Any]) -> None:
    """Emit a dashboard audit envelope. Fail-soft — never raise."""
    try:
        r = _get_redis()
        envelope = {
            "type": event_type,
            "payload": json.dumps(payload),
        }
        r.xadd(_AUDIT_STREAM, envelope, maxlen=5000)
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("dashboard audit emit failed: %s", exc)


# ---------------------------------------------------------------------------
# Summary calculation — direct Redis SCAN over sos:objectives:{project}:*
# ---------------------------------------------------------------------------


_WINDOW_HOURS = 24


def _parse_iso(ts: str | None) -> Optional[datetime]:
    if not ts:
        return None
    for suffix in ("Z", "+00:00"):
        if ts.endswith(suffix):
            ts_normalized = ts[: -len(suffix)] + "+00:00"
            break
    else:
        ts_normalized = ts
    try:
        return datetime.fromisoformat(ts_normalized)
    except Exception:
        return None


def _scan_objective_keys(r, project: str):  # type: ignore[no-untyped-def]
    """Yield every objective-hash key for ``project``.

    Matches ``sos:objectives:{project}:{obj_id}`` while excluding the index
    sets ``...:children:...`` and ``...:open``.
    """
    pattern = f"sos:objectives:{project}:*"
    for key in r.scan_iter(match=pattern):
        # Skip index sets
        if ":children:" in key or key.endswith(":open"):
            continue
        yield key


def _compute_summary(project: str) -> Dict[str, Any]:
    """Tally objective states and 24h $MIND burn for the given project.

    Fail-soft: on any Redis error returns zeros — the operator UI prefers
    an empty view over a 500.
    """
    counts = {"open": 0, "claimed": 0, "shipped": 0, "paid": 0}
    mind_burn_24h = 0
    window_start = datetime.now(timezone.utc) - timedelta(hours=_WINDOW_HOURS)

    try:
        r = _get_redis()
        for key in _scan_objective_keys(r, project):
            try:
                data = r.hgetall(key)
            except Exception:
                continue
            if not data:
                continue
            state = data.get("state")
            if state in counts:
                counts[state] += 1

            if state == "paid":
                updated_at = _parse_iso(data.get("updated_at"))
                if updated_at is None or updated_at >= window_start:
                    try:
                        mind_burn_24h += int(data.get("bounty_mind") or 0)
                    except (TypeError, ValueError):
                        pass
    except Exception as exc:
        log.warning(
            "dashboard summary: failed to scan objectives for project=%s (%s)",
            project,
            exc,
        )

    return {
        "project": project,
        "counts": counts,
        "mind_burn_24h": mind_burn_24h,
        "window_hours": _WINDOW_HOURS,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/health")
async def health() -> Dict[str, Any]:
    return health_response(SERVICE_NAME, _START_TIME)


@router.get("/dashboard/tenants/{project}/summary")
async def tenant_summary(
    project: str,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="dashboard:read",
        resource=f"tenant:{project}",
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    _resolve_project_scope(entry, project)

    return _compute_summary(project)


@router.get("/dashboard/tenants/{project}/agents")
async def tenant_agents(
    project: str,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="dashboard:read",
        resource=f"tenant:{project}",
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    _resolve_project_scope(entry, project)

    cards = read_all_cards(project=project)
    items = [card.model_dump() for card in cards]
    return {"project": project, "agents": items, "count": len(items)}


@router.post("/dashboard/agents/{name}/kill")
async def kill_agent_route(
    name: str,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Admin / system only: write the kill-switch key with a 24h TTL.

    Scoped tokens (agents, tenant-scoped operators) are rejected with 403 —
    kill is a platform-level action that should only be available to the
    operator of the entire SOS instance.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="dashboard:kill_agent",
        resource=f"agent:{name}",
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    if not (entry.get("is_system") or entry.get("is_admin")):
        raise HTTPException(
            status_code=403,
            detail="kill-switch requires system or admin scope",
        )

    try:
        killed_until = kill_agent(name)
    except Exception as exc:
        log.error("dashboard kill_agent write failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"kill-switch write failed: {exc}",
        ) from exc

    _emit_audit(
        "dashboard.agent_killed",
        {
            "agent": name,
            "actor": entry.get("agent"),
            "killed_until": killed_until,
            "tenant_id": entry.get("tenant_slug") or "default",
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )

    return {"ok": True, "agent": name, "killed_until": killed_until}


__all__ = ["router"]
