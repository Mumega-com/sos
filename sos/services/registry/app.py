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

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, Header, HTTPException, Path
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from sos import __version__
from sos.contracts.agent_card import AgentCard
from sos.contracts.policy import PolicyDecision
from sos.kernel.auth import verify_bearer as _auth_verify_bearer
from sos.kernel.health import health_response
from sos.kernel.policy.gate import can_execute
from sos.kernel.telemetry import init_tracing, instrument_fastapi
from sos.observability.logging import get_logger
from sos.kernel.crypto import canonical_payload_hash, enroll_message, verify as _sig_verify
from sos.kernel.identity import AgentIdentity, VerificationStatus
from sos.services.registry import (
    read_all,
    read_all_cards,
    read_card,
    read_one,
    write,
    write_card,
)
from sos.services.registry import nonce_store

SERVICE_NAME = "registry"
DEFAULT_PORT = 6067
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

init_tracing("registry")

app = FastAPI(title="SOS Registry Service", version=__version__)
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
    """Announce presence to the SOS service registry and start the pruner."""
    try:
        from sos.services.bus.discovery import register_service

        await register_service(SERVICE_NAME, DEFAULT_PORT)
    except Exception as exc:  # pragma: no cover — discovery is best-effort
        log.warn("registry discovery registration failed", error=str(exc))

    from sos.services.registry.pruner import HeartbeatPruner

    pruner = HeartbeatPruner()
    task = asyncio.create_task(pruner.run())
    app.state.pruner = pruner
    app.state.pruner_task = task


@app.on_event("shutdown")
async def _shutdown() -> None:
    """Stop the heartbeat pruner gracefully."""
    pruner = getattr(app.state, "pruner", None)
    task = getattr(app.state, "pruner_task", None)
    if pruner is not None:
        pruner.stop()
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass


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


# ---------------------------------------------------------------------------
# AgentCard routes — v0.7.2 runtime overlay (registered BEFORE ``/agents/{id}``
# so ``cards`` is never captured as an ``agent_id`` path param).
# ---------------------------------------------------------------------------


@app.get("/agents/cards")
async def list_agent_cards(
    project: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Return all runtime AgentCards, optionally filtered by ``project``.

    Cards carry operational state (session/pid/host/warm_policy/last_seen)
    on top of the soul-level AgentIdentity returned by ``/agents``.
    Returns an empty list if Redis is unreachable — matches ``read_all_cards``.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="registry:cards_list",
        resource=project or "mumega",
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    cards = read_all_cards(project=effective_project)
    items: List[Dict[str, Any]] = [c.model_dump() for c in cards]
    return {"cards": items, "count": len(items)}


@app.post("/agents/cards")
async def upsert_agent_card(
    payload: Dict[str, Any] = Body(...),
    project: Optional[str] = None,
    ttl_seconds: int = 300,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Upsert an AgentCard into Redis under ``sos:cards[:<project>]:<name>``.

    Agents call this on boot and on a short heartbeat cadence to keep
    their runtime overlay fresh. The TTL expires stale cards so dead
    agents disappear without explicit cleanup.

    Auth:
    - Bearer required.
    - Scoped tokens are forced to their own project; trying to write
      into a different project (or with no scope at all) is 403.
    - ``project`` query param takes the same override behaviour as the
      read routes.

    Validation: the body is parsed through ``AgentCard`` — a malformed
    payload returns 422 via FastAPI's standard validation.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    try:
        card = AgentCard(**payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid agent card: {exc}")

    decision = await can_execute(
        action="registry:card_write",
        resource=card.name,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    # If the card carries its own ``project``, it must not cross the
    # caller's effective scope. System/admin tokens skip this check.
    if not (entry.get("is_system") or entry.get("is_admin")):
        if card.project and effective_project and card.project != effective_project:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"card.project '{card.project}' does not match "
                    f"token scope '{effective_project}'"
                ),
            )

    write_card(card, project=effective_project, ttl_seconds=ttl_seconds)
    return {"ok": True, "name": card.name, "project": effective_project}


@app.get("/agents/cards/{agent_name}")
async def get_agent_card(
    agent_name: str,
    project: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Return a single AgentCard by name, or 404 if no card is registered."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="registry:card_read",
        resource=agent_name,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    card = read_card(agent_name, project=effective_project)
    if card is None:
        raise HTTPException(
            status_code=404,
            detail=f"no card for agent {agent_name!r}",
        )
    return card.model_dump()


# ---------------------------------------------------------------------------
# Mesh enrollment — v0.9.2
# ---------------------------------------------------------------------------


class MeshChallengeRequest(BaseModel):
    agent_id: str  # ^agent:[a-z][a-z0-9-]*$


class MeshEnrollRequest(BaseModel):
    agent_id: str  # must match AgentCard.identity_id pattern (^agent:...)
    name: str  # must match AgentCard.name pattern
    role: str  # must be valid AgentRole literal
    skills: list[str] = []
    squads: list[str] = []
    heartbeat_url: str | None = None
    project: str | None = None  # optional override; system tokens only

    # v0.9.2.1 — signed enrollment. All three are required; set to ""
    # intentionally for test fixtures that exercise the "missing" paths.
    public_key: str = ""  # base64 Ed25519 public key (32 bytes)
    nonce: str = ""  # value returned from /mesh/challenge
    signature: str = ""  # sign(priv, enroll_message(agent_id, nonce, payload_hash))


def _enroll_payload_for_hash(body: MeshEnrollRequest) -> Dict[str, Any]:
    """Exact subset of the enroll body bound by the signature.

    Must stay in sync with the client (agents/join.py etc). Only the
    identity-shaping fields are signed — transport echoes like
    heartbeat_url can change across re-enrolls without a re-sign.
    """
    return {
        "agent_id": body.agent_id,
        "name": body.name,
        "role": body.role,
        "skills": list(body.skills),
        "squads": list(body.squads),
        "public_key": body.public_key,
    }


@app.post("/mesh/challenge")
async def mesh_challenge(body: MeshChallengeRequest) -> Dict[str, Any]:
    """Issue a single-use nonce for a subsequent /mesh/enroll.

    No bearer required — a nonce alone is useless without the matching
    private key. Rate-limited upstream via the service's shared limiter.
    Nonces live 60 s in Redis and are atomically consumed by enroll.
    """
    try:
        nonce, expires_at = nonce_store.issue(body.agent_id)
    except Exception as exc:
        log.error("mesh_challenge redis failure", error=str(exc))
        raise HTTPException(status_code=503, detail="nonce store unavailable")
    return {
        "agent_id": body.agent_id,
        "nonce": nonce,
        "expires_at": expires_at,
        "ttl_seconds": nonce_store.NONCE_TTL_SECONDS,
    }


@app.post("/mesh/enroll")
async def mesh_enroll(
    body: MeshEnrollRequest,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Enroll an agent into the mesh registry.

    Since v0.9.2.1 every enroll MUST carry a valid Ed25519 signature over
    ``enroll_message(agent_id, nonce, canonical_payload_hash(identity_fields))``.

    First enrollment for an agent_id uses TOFU: we accept + persist the
    submitted public_key onto the AgentIdentity and fire
    ``agent.enrolled.first_seen`` on the bus. Subsequent enrolls must sign
    with the same private key — a mismatched public_key in the payload
    triggers 409 and a SECURITY audit event.

    Auth:
    - Bearer required (policy gate action registry:mesh_enroll).
    - Scoped tokens are pinned to their own project.
    - Holding a valid bearer is no longer enough — the caller must also
      prove custody of the agent's private key.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="registry:mesh_enroll",
        resource=body.name,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, body.project)

    # --- Signature gate ------------------------------------------------------
    if not body.public_key or not body.nonce or not body.signature:
        raise HTTPException(
            status_code=401,
            detail="signed enrollment required: public_key, nonce, signature",
        )

    if not nonce_store.consume(body.agent_id, body.nonce):
        raise HTTPException(status_code=403, detail="nonce invalid, expired, or replayed")

    payload_hash = canonical_payload_hash(_enroll_payload_for_hash(body))
    msg = enroll_message(body.agent_id, body.nonce, payload_hash)
    if not _sig_verify(body.public_key, msg, body.signature):
        raise HTTPException(status_code=403, detail="signature verification failed")

    # --- TOFU vs. pinned pubkey ---------------------------------------------
    existing = read_one(body.agent_id, project=effective_project)
    first_seen = False
    if existing is None:
        first_seen = True
        # TOFU — pin the submitted key now. Future enrolls must match.
        ident = AgentIdentity(
            name=body.name,
            model=None,
            public_key=body.public_key,
            edition="business",
        )
        ident.verification_status = VerificationStatus.VERIFIED
        ident.verified_by = entry.get("agent") or entry.get("label") or "tofu"
        ident.metadata["project"] = effective_project or ""
        ident.metadata["role"] = body.role
        ident.metadata["status"] = "active"
        ident.capabilities = list(body.skills)
        write(ident, project=effective_project, ttl_seconds=0)
    else:
        stored_pub = existing.public_key or ""
        if stored_pub and stored_pub != body.public_key:
            log.warn(
                "SECURITY: mesh enroll public_key mismatch",
                agent_id=body.agent_id,
                project=effective_project,
                stored_fp=stored_pub[:12],
                submitted_fp=body.public_key[:12],
            )
            raise HTTPException(
                status_code=409,
                detail="public_key does not match stored identity; rotation requires admin path",
            )
        if not stored_pub:
            # Legacy identity with no pubkey — adopt this one as TOFU.
            existing.public_key = body.public_key
            existing.verification_status = VerificationStatus.VERIFIED
            existing.verified_by = entry.get("agent") or entry.get("label") or "tofu"
            write(existing, project=effective_project, ttl_seconds=0)
            first_seen = True

    # --- AgentCard upsert (the runtime overlay) -----------------------------
    now = datetime.now(timezone.utc).isoformat()
    try:
        card = AgentCard(
            identity_id=body.agent_id,
            name=body.name,
            role=body.role,
            skills=body.skills,
            squads=body.squads,
            heartbeat_url=body.heartbeat_url,
            project=effective_project,
            tool="service",
            type="service",
            registered_at=now,
            last_seen=now,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid enroll payload: {exc}")

    write_card(card, project=effective_project, ttl_seconds=900)

    # --- First-seen alert (best-effort, non-blocking) -----------------------
    if first_seen:
        await _emit_first_seen(body.agent_id, effective_project, body.public_key, now)

    return {
        "enrolled": True,
        "name": card.name,
        "project": effective_project,
        "stale_after": 300,
        "expires_in": 900,
        "first_seen": first_seen,
    }


FIRST_SEEN_STREAM = "sos:stream:mesh:first_seen"


async def _emit_first_seen(
    agent_id: str,
    project: Optional[str],
    public_key: str,
    enrolled_at: str,
) -> None:
    """Emit a ``agent.enrolled.first_seen`` record + optional webhook ping.

    Fail-soft: neither the XADD nor the webhook can break enrollment. The
    409-on-pubkey-mismatch gate is the hard guarantee; first-seen is the
    human-visible backstop.
    """
    import hashlib

    pub_fp = hashlib.sha256(public_key.encode()).hexdigest()[:12]
    fields = {
        "event": "agent.enrolled.first_seen",
        "agent_id": agent_id,
        "project": project or "",
        "public_key_fp": pub_fp,
        "enrolled_at": enrolled_at,
    }
    try:
        import redis  # type: ignore[import-untyped]
        from sos.kernel.settings import get_settings

        s = get_settings().redis
        r = redis.Redis(
            host=s.host,
            port=s.port,
            password=s.password_str or None,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        r.xadd(FIRST_SEEN_STREAM, fields, maxlen=10000)
    except Exception as exc:
        log.warn("first_seen xadd failed", error=str(exc))

    webhook = os.environ.get("SOS_ALERT_WEBHOOK", "").strip()
    if webhook:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    webhook,
                    json={
                        "content": (
                            f":new: New agent on mesh: **{agent_id}** "
                            f"(project={project}, fp={pub_fp})"
                        )
                    },
                )
        except Exception as exc:
            log.warn("first_seen webhook failed", error=str(exc))


@app.get("/mesh/squad/{slug}")
async def mesh_squad_resolve(
    slug: str = Path(..., pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"),
    project: Optional[str] = None,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Return all enrolled agents whose ``squads`` list contains *slug*.

    Scans cards in the caller's effective project scope (same bearer +
    project-scope rules as ``GET /agents/cards``) and returns the subset
    where ``slug in card.squads``.  Makes squad subjects like
    ``squad:growth-intel.{project}`` resolvable at delivery time.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    decision = await can_execute(
        action="registry:mesh_squad_resolve",
        resource=slug,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision)

    entry = _verify_bearer(authorization)
    effective_project = _resolve_project_scope(entry, project)

    cards = read_all_cards(project=effective_project)
    agents = [c for c in cards if slug in c.squads]
    return {
        "slug": slug,
        "project": effective_project,
        "agents": [c.model_dump() for c in agents],
        "count": len(agents),
    }


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


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("SOS_REGISTRY_PORT", DEFAULT_PORT))
    uvicorn.run(app, host="0.0.0.0", port=port)
