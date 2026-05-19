"""SOS Objectives Service — HTTP surface for the living objective tree.

Exposes the Redis-backed objective storage layer over HTTP so agents can
read/write the objective tree without importing storage modules directly.

Endpoints:
- ``POST /objectives``            — create a new objective node
- ``GET  /objectives``            — query open objectives (filtered)
- ``GET  /objectives/{obj_id}``   — read a single objective
- ``GET  /objectives/{obj_id}/tree``   — read the full subtree
- ``POST /objectives/{obj_id}/claim``       — claim for a holder agent
- ``POST /objectives/{obj_id}/heartbeat``   — keep-alive heartbeat
- ``POST /objectives/{obj_id}/release``     — release claim back to open
- ``POST /objectives/{obj_id}/complete``    — mark shipped with artifact
- ``POST /objectives/{obj_id}/ack``         — ack completion (pre-gate)

Auth scope:
- System / admin tokens: unconditional access; any ``project`` allowed.
- Scoped tokens: forced to their own project scope. Cross-project writes → 403.

Port: 6068 (next free after registry 6067).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sos import __version__
from sos.contracts.objective import Objective
from sos.contracts.policy import PolicyDecision
from sos.kernel.auth import verify_bearer as _auth_verify_bearer
from sos.kernel.health import health_response
from sos.kernel.policy.gate import can_execute
from sos.kernel.telemetry import init_tracing, instrument_fastapi
from sos.observability.logging import get_logger
from sos.services.objectives import (
    ack_completion,
    claim_objective,
    complete_objective,
    heartbeat_objective,
    query_open,
    read_objective,
    read_tree,
    release_objective,
    write_objective,
)

SERVICE_NAME = "objectives"
DEFAULT_PORT = 6068
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

init_tracing("objectives")

app = FastAPI(title="SOS Objectives Service", version=__version__)
instrument_fastapi(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def _startup() -> None:
    """Announce presence to the SOS service registry and bootstrap canonical nodes."""
    try:
        from sos.services.bus.discovery import register_service

        await register_service(SERVICE_NAME, DEFAULT_PORT)
    except Exception as exc:  # pragma: no cover — discovery is best-effort
        log.warning("objectives discovery registration failed", error=str(exc))

    # Bootstrap the /reviews/ subtree (Step 10 — idempotent, fail-soft).
    try:
        from sos.services.objectives.bootstrap import bootstrap_reviews_subtree

        bootstrap_reviews_subtree()
        log.info("objectives bootstrap: /reviews/ subtree bootstrapped")
    except Exception as exc:
        log.warning("objectives bootstrap: failed", error=str(exc))


# ---------------------------------------------------------------------------
# Request body models
# ---------------------------------------------------------------------------


class _CompleteBody(BaseModel):
    artifact_url: str
    notes: str = ""


class _AckBody(BaseModel):
    acker: str = ""
    # v0.8.1 — optional normalized outcome metric in [0.0, 1.0].  None means the
    # ack carries no score (existing v0.8.0 clients stay compatible).
    outcome_score: float | None = Field(default=None, ge=0.0, le=1.0)


class _ClaimBody(BaseModel):
    agent: str = ""


# ---------------------------------------------------------------------------
# Auth + gate helpers — mirror registry/app.py exactly
# ---------------------------------------------------------------------------


def _raise_on_deny(decision: PolicyDecision) -> None:
    """Map a gate decision to 401/403 if denied."""
    if not decision.allowed:
        reason = decision.reason or "unauthorized"
        if "bearer" in reason.lower() or "auth" in reason.lower():
            raise HTTPException(status_code=401, detail=reason)
        raise HTTPException(status_code=403, detail=reason)


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

    - System / admin tokens: pass the caller's requested project through as-is.
    - Scoped tokens: the token's own scope wins. A mismatched explicit
      ``requested_project`` triggers 403.
    """
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
                f"cannot access objectives for '{requested_project}'"
            ),
        )
    return scope


def _gen_id() -> str:
    """Generate a placeholder ULID-shaped id until ulid package is available.

    The fallback uses ``secrets.choice`` (cryptographically secure) rather than
    ``random.choices``.  An attacker who can guess an objective ID could claim
    a bounty on a shipped objective, so ULID-format IDs are
    cryptographic-identity-sensitive.
    """
    try:
        import ulid  # type: ignore[import-untyped]

        return ulid.new().str
    except ImportError:
        # Fallback: produce a string that satisfies the ULID pattern
        # 26 chars from Crockford base-32 (omitting I, L, O, U)
        import secrets

        alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
        return "".join(secrets.choice(alphabet) for _ in range(26))


# ---------------------------------------------------------------------------
# Audit emission — fail-soft bus envelope on state transitions
# ---------------------------------------------------------------------------

_AUDIT_STREAM = "sos:stream:global:objectives"


def _emit_audit(payload: Dict[str, Any]) -> None:
    """Publish an ``objective.state_changed`` envelope to the audit stream.

    Uses Redis xadd directly (same pattern as squad/tasks.py). The call is
    fully fail-soft: any exception is logged as a warning and swallowed so
    that bus unreachability never fails the HTTP request.
    """
    try:
        import redis as _redis  # local import — keeps module importable without redis

        pw = os.environ.get("REDIS_PASSWORD", "")
        host = os.environ.get("REDIS_HOST", "127.0.0.1")
        port = int(os.environ.get("REDIS_PORT", "6379"))
        r = _redis.Redis(host=host, port=port, password=pw or None, decode_responses=True)
        envelope = {
            "type": "objective.state_changed",
            "payload": json.dumps(payload),
        }
        r.xadd(_AUDIT_STREAM, envelope, maxlen=5000)
    except Exception as exc:
        log.warn("objective audit emit failed", error=str(exc))


# ---------------------------------------------------------------------------
# Routes — static paths first, path-param routes last
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> Dict[str, Any]:
    return health_response(SERVICE_NAME, _START_TIME)


@app.post("/objectives")
async def create_objective(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Create a new objective node in the tree."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="objectives:create",
        resource="objectives",
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)

    # Step 6: cross-project body check for scoped tokens
    body_project = payload.get("project")
    effective_project = _resolve_project_scope(entry, body_project)
    if effective_project is not None:
        payload["project"] = effective_project

    # Server-side fields: id, created_at, updated_at
    now = Objective.now_iso()
    if not payload.get("id"):
        payload["id"] = _gen_id()
    payload["created_at"] = now
    payload["updated_at"] = now

    # Validate via Pydantic — invalid → 422
    try:
        obj = Objective(**payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid objective: {exc}")

    write_objective(obj)

    # Step 7: audit emission — fail-soft
    try:
        _emit_audit({
            "id": obj.id,
            "prior_state": None,
            "new_state": "open",
            "holder": obj.holder_agent,
            "actor": entry.get("agent"),
            "project": obj.project,
            "tenant_id": entry.get("tenant_slug") or "default",
            "ts": Objective.now_iso(),
        })
    except Exception as exc:
        log.warn("objective audit emit failed (create)", error=str(exc))

    return obj.model_dump(mode="json")


@app.get("/objectives")
async def query_objectives(
    project: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    min_bounty: Optional[int] = Query(None),
    subtree: Optional[str] = Query(None),
    capability: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Query open objectives with optional filters."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="objectives:read",
        resource="objectives",
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    results = query_open(
        project=effective_project,
        tag=tag,
        min_bounty=min_bounty,
        subtree_root=subtree,
        capability=capability,
    )
    items: List[Dict[str, Any]] = [o.model_dump(mode="json") for o in results]
    return {"objectives": items, "count": len(items)}


@app.get("/objectives/{obj_id}/tree")
async def get_objective_tree(
    obj_id: str,
    project: Optional[str] = Query(None),
    max_depth: int = Query(10),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Return the full subtree rooted at obj_id."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="objectives:read",
        resource=obj_id,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    tree = read_tree(obj_id, project=effective_project, max_depth=max_depth)
    if not tree:
        raise HTTPException(
            status_code=404,
            detail=f"no objective {obj_id!r} found as tree root",
        )
    # Serialize Objective objects inside the tree dict
    return _serialize_tree(tree)


@app.post("/objectives/{obj_id}/claim")
async def claim(
    obj_id: str,
    body: _ClaimBody = Body(default=_ClaimBody()),
    project: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Claim an open objective for a holder agent."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="objectives:claim",
        resource=obj_id,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    # Prefer body.agent, fallback to auth context agent
    agent = body.agent or entry.get("agent") or "unknown"

    ok = claim_objective(obj_id, agent, project=effective_project)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"objective {obj_id!r} is already claimed or does not exist",
        )

    # Step 7: audit emission — fail-soft
    try:
        _emit_audit({
            "id": obj_id,
            "prior_state": "open",
            "new_state": "claimed",
            "holder": agent,
            "actor": entry.get("agent"),
            "project": effective_project,
            "tenant_id": entry.get("tenant_slug") or "default",
            "ts": Objective.now_iso(),
        })
    except Exception as exc:
        log.warn("objective audit emit failed (claim)", error=str(exc))

    return {"ok": True, "obj_id": obj_id, "holder_agent": agent}


@app.post("/objectives/{obj_id}/heartbeat")
async def heartbeat(
    obj_id: str,
    project: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Bump the heartbeat timestamp on a claimed objective."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="objectives:heartbeat",
        resource=obj_id,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    ok = heartbeat_objective(obj_id, project=effective_project)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"no objective {obj_id!r} found",
        )
    return {"ok": True}


@app.post("/objectives/{obj_id}/release")
async def release(
    obj_id: str,
    project: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Release a claimed objective back to open state."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="objectives:release",
        resource=obj_id,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    ok = release_objective(obj_id, project=effective_project)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"no objective {obj_id!r} found",
        )

    # Step 7: audit emission — fail-soft
    try:
        _emit_audit({
            "id": obj_id,
            "prior_state": "claimed",
            "new_state": "open",
            "holder": None,
            "actor": entry.get("agent"),
            "project": effective_project,
            "tenant_id": entry.get("tenant_slug") or "default",
            "ts": Objective.now_iso(),
        })
    except Exception as exc:
        log.warn("objective audit emit failed (release)", error=str(exc))

    return {"ok": True}


@app.post("/objectives/{obj_id}/complete")
async def complete(
    obj_id: str,
    body: _CompleteBody,
    project: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Mark an objective as shipped with a completion artifact."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="objectives:complete",
        resource=obj_id,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    ok = complete_objective(
        obj_id,
        artifact_url=body.artifact_url,
        notes=body.notes,
        project=effective_project,
    )
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"no objective {obj_id!r} found",
        )

    # Step 7: audit emission — fail-soft
    try:
        _emit_audit({
            "id": obj_id,
            "prior_state": "claimed",
            "new_state": "shipped",
            "holder": entry.get("agent"),
            "actor": entry.get("agent"),
            "project": effective_project,
            "tenant_id": entry.get("tenant_slug") or "default",
            "ts": Objective.now_iso(),
        })
    except Exception as exc:
        log.warn("objective audit emit failed (complete)", error=str(exc))

    return {"ok": True, "state": "shipped"}


@app.post("/objectives/{obj_id}/ack")
async def ack(
    obj_id: str,
    body: _AckBody = Body(default=_AckBody()),
    project: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Ack completion of an objective (pre-payment gate)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="objectives:ack",
        resource=obj_id,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    acker = body.acker or entry.get("agent") or "unknown"

    ok = ack_completion(obj_id, acker=acker, project=effective_project)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"no objective {obj_id!r} found",
        )

    # Read back to return current acks list.
    # Guard against Redis flake between the write and the read-back: if the
    # read fails for any reason, return a conservative fallback rather than 500.
    try:
        obj = read_objective(obj_id, project=effective_project)
    except Exception as exc:
        log.warn("ack read-back failed — returning fallback", error=str(exc))
        obj = None

    # v0.8.1 — persist outcome_score on the objective when the ack carries one.
    # Completion gate is unchanged: payout remains binary. Score is metadata
    # for the downstream auto-improve loop only.
    if body.outcome_score is not None and obj is not None:
        try:
            obj = obj.model_copy(update={"outcome_score": body.outcome_score})
            write_objective(obj)
        except Exception as exc:
            log.warn("ack outcome_score persist failed", error=str(exc))

    acks_list = obj.acks if obj is not None else [acker]
    current_state = obj.state if obj is not None else "shipped"

    # Step 7: audit emission — ack does not flip state; include acker in payload; fail-soft.
    # v0.8.1: outcome_score is included only when the ack carried one, to avoid
    # writing literal "None" values into the Redis stream.
    try:
        audit_payload: Dict[str, Any] = {
            "id": obj_id,
            "prior_state": current_state,
            "new_state": current_state,
            "holder": obj.holder_agent if obj is not None else None,
            "actor": entry.get("agent"),
            "acker": acker,
            "project": effective_project,
            "tenant_id": entry.get("tenant_slug") or "default",
            "ts": Objective.now_iso(),
        }
        if body.outcome_score is not None:
            audit_payload["outcome_score"] = body.outcome_score
        _emit_audit(audit_payload)
    except Exception as exc:
        log.warn("objective audit emit failed (ack)", error=str(exc))

    # Step 9: completion gate — shipped → paid if enough acks
    from sos.services.objectives import gate as _gate  # noqa: PLC0415

    maybe_paid = await _gate.check_completion(obj_id, project=effective_project)
    if maybe_paid is not None:
        # Emit a second audit event for the paid transition
        try:
            _emit_audit({
                "type": "objective.state_changed",
                "payload": {
                    "id": obj_id,
                    "prior_state": "shipped",
                    "new_state": "paid",
                    "holder": maybe_paid.holder_agent,
                    "actor": "gate",
                    "project": effective_project,
                    "tenant_id": entry.get("tenant_slug") or "default",
                    "ts": Objective.now_iso(),
                },
            })
        except Exception as exc:
            log.warn("objective audit emit failed (paid transition)", error=str(exc))
        current_state = "paid"
        acks_list = maybe_paid.acks

    return {"ok": True, "acks": acks_list, "state": current_state}


@app.get("/objectives/{obj_id}")
async def get_objective(
    obj_id: str,
    project: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Return a single objective by id, or 404 if missing."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="objectives:read",
        resource=obj_id,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    obj = read_objective(obj_id, project=effective_project)
    if obj is None:
        raise HTTPException(
            status_code=404,
            detail=f"no objective {obj_id!r} in store",
        )
    return obj.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Tree serialization helper
# ---------------------------------------------------------------------------


def _serialize_tree(node: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively serialize Objective objects within a read_tree dict."""
    obj = node.get("objective")
    children = node.get("children", [])
    return {
        "objective": obj.model_dump(mode="json") if isinstance(obj, Objective) else obj,
        "children": [_serialize_tree(c) for c in children],
    }
