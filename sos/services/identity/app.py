"""SOS Identity Service — HTTP surface for users, guilds, pairing, and avatars.

The avatar endpoints (``/avatar/generate``, ``/avatar/social/on_alpha_drift``)
expose ``AvatarGenerator.generate`` and ``SocialAutomation.on_alpha_drift``
to sibling services over HTTP. Added in v0.4.5 Wave 4 (P0-07) so
``sos.services.autonomy`` no longer imports ``sos.services.identity.avatar``
directly.

Auth: v0.5.3 — inline ``_verify_bearer`` / ``_check_scope`` replaced with
``sos.kernel.policy.gate.can_execute`` + ``_raise_on_deny`` (same pattern as
integrations/app.py v0.5.1). Identity endpoints are scope-gated, not
tenant-keyed: the caller's own tenant (from their bearer token) is used as
both ``tenant`` and ``resource`` prefix so the gate's tenant-scope pillar
trivially passes for any valid scoped token, while system/admin tokens
short-circuit as usual.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from sos.contracts.identity import UV16D
from sos.contracts.policy import PolicyDecision
from sos.kernel.auth import verify_bearer as _auth_verify_bearer
from sos.kernel.policy.gate import can_execute
from sos.services.identity.core import get_identity_core

app = FastAPI(title="SOS Identity Service", version="0.1.0")
core = get_identity_core()

# Lazy-initialised on first use so ``import sos.services.identity.app`` does
# not require PIL to be installed.
_avatar_gen = None  # type: ignore[var-annotated]
_social = None  # type: ignore[var-annotated]


def _get_avatar_generator():
    """Return the module-level AvatarGenerator, instantiating on first use."""
    global _avatar_gen
    if _avatar_gen is None:
        from sos.services.identity.avatar import AvatarGenerator

        _avatar_gen = AvatarGenerator()
    return _avatar_gen


def _get_social_automation():
    """Return the module-level SocialAutomation, instantiating on first use."""
    global _social
    if _social is None:
        from sos.services.identity.avatar import SocialAutomation

        _social = SocialAutomation()
    return _social


# ---------------------------------------------------------------------------
# Gate helper — turn a PolicyDecision into the appropriate HTTP response
# (copied verbatim from integrations/app.py v0.5.1)
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


# --- Schemas ---
class UserCreate(BaseModel):
    name: str
    bio: Optional[str] = ""
    avatar_url: Optional[str] = None


class GuildCreate(BaseModel):
    name: str
    owner_id: str
    description: Optional[str] = ""


class GuildJoin(BaseModel):
    guild_id: str
    user_id: str


class PairingCreate(BaseModel):
    channel: str
    sender_id: str
    agent_id: str
    expires_minutes: Optional[int] = 10


class PairingApprove(BaseModel):
    channel: str
    code: str
    approver_id: str


class AvatarGenerateRequest(BaseModel):
    agent_id: str
    uv: Dict[str, float]
    alpha_drift: Optional[float] = None
    event_type: str = "state_snapshot"


class SocialAlphaDriftRequest(BaseModel):
    agent_id: str
    uv: Dict[str, float]
    alpha_value: float
    insight: str
    platforms: Optional[List[str]] = None


# --- Endpoints ---


@app.post("/users/create")
async def create_user(req: UserCreate):
    try:
        user = core.create_user(req.name, req.bio, req.avatar_url)
        return user.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/users/{user_id}")
async def get_user(user_id: str):
    user = core.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user.to_dict()


@app.post("/guilds/create")
async def create_guild(req: GuildCreate):
    try:
        guild = await core.create_guild(req.name, req.owner_id, req.description)
        return guild.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/guilds/join")
async def join_guild(req: GuildJoin):
    success = await core.join_guild(req.guild_id, req.user_id)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to join (already member?)")
    return {"status": "joined", "guild_id": req.guild_id}


@app.get("/guilds/{guild_id}/members")
async def list_members(guild_id: str):
    return core.list_members(guild_id)


# --- Pairing / Allowlist Endpoints ---


@app.post("/pairing/create")
async def create_pairing(req: PairingCreate):
    try:
        return core.create_pairing(
            channel=req.channel,
            sender_id=req.sender_id,
            agent_id=req.agent_id,
            expires_minutes=req.expires_minutes or 10,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/pairing/approve")
async def approve_pairing(req: PairingApprove):
    result = core.approve_pairing(
        channel=req.channel,
        code=req.code,
        approver_id=req.approver_id,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "pairing_failed"))
    return result


@app.get("/allowlist/{channel}")
async def list_allowlist(channel: str):
    return core.list_allowlist(channel)


# --- Avatar Endpoints (P0-07) ---


@app.post("/avatar/generate")
async def avatar_generate(
    req: AvatarGenerateRequest,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Generate a QNFT avatar from a UV16D state vector.

    Wraps ``AvatarGenerator.generate`` so siblings (notably autonomy) can
    request avatars without importing ``sos.services.identity.avatar``.

    Auth: scope-gated — any valid scoped or system/admin token is accepted.
    The caller's own tenant (project or tenant_slug) is used as the gate
    tenant so the tenant-scope pillar trivially passes for scoped tokens.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    # Resolve the caller's tenant from their token so the gate can verify scope.
    ctx = _auth_verify_bearer(authorization)
    if ctx is None:
        raise HTTPException(status_code=401, detail="invalid or inactive token")
    caller_tenant = ctx.project or ctx.tenant_slug or ctx.agent
    if caller_tenant is None and not (ctx.is_system or ctx.is_admin):
        # Preserves pre-migration 403 for scopeless-but-verified tokens.
        raise HTTPException(status_code=403, detail="token has no tenant scope")
    caller_tenant = caller_tenant or "mumega"

    decision = await can_execute(
        action="identity:avatar_generate",
        resource=f"{caller_tenant}/{req.agent_id}",
        tenant=caller_tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision)

    uv = UV16D.from_dict(req.uv)
    try:
        result = _get_avatar_generator().generate(
            agent_id=req.agent_id,
            uv=uv,
            alpha_drift=req.alpha_drift,
            event_type=req.event_type,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"avatar generation failed: {exc}")
    return result


@app.post("/avatar/social/on_alpha_drift")
async def avatar_on_alpha_drift(
    req: SocialAlphaDriftRequest,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """Trigger social-automation for an alpha-drift event.

    Wraps ``SocialAutomation.on_alpha_drift`` so autonomy can drive the
    drift-triggered social post without importing identity internals.

    Auth: scope-gated — any valid scoped or system/admin token is accepted.
    The caller's own tenant (project or tenant_slug) is used as the gate
    tenant so the tenant-scope pillar trivially passes for scoped tokens.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")

    # Resolve the caller's tenant from their token so the gate can verify scope.
    ctx = _auth_verify_bearer(authorization)
    if ctx is None:
        raise HTTPException(status_code=401, detail="invalid or inactive token")
    caller_tenant = ctx.project or ctx.tenant_slug or ctx.agent
    if caller_tenant is None and not (ctx.is_system or ctx.is_admin):
        raise HTTPException(status_code=403, detail="token has no tenant scope")
    caller_tenant = caller_tenant or "mumega"

    decision = await can_execute(
        action="identity:avatar_social_drift",
        resource=f"{caller_tenant}/{req.agent_id}",
        tenant=caller_tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision)

    uv = UV16D.from_dict(req.uv)
    try:
        result = await _get_social_automation().on_alpha_drift(
            agent_id=req.agent_id,
            uv=uv,
            alpha_value=req.alpha_value,
            insight=req.insight,
            platforms=req.platforms,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"social automation failed: {exc}")
    return result
