"""SOS Identity Service — HTTP surface for users, guilds, pairing, and avatars.

The avatar endpoints (``/avatar/generate``, ``/avatar/social/on_alpha_drift``)
expose ``AvatarGenerator.generate`` and ``SocialAutomation.on_alpha_drift``
to sibling services over HTTP. Added in v0.4.5 Wave 4 (P0-07) so
``sos.services.autonomy`` no longer imports ``sos.services.identity.avatar``
directly.

Auth: ``_verify_bearer`` / ``_check_scope`` mirror ``integrations/app.py`` —
system/admin tokens allowed; non-system tokens must carry a matching
``project``/``tenant_slug`` scope (or a non-empty scope, since avatar
endpoints aren't tenant-keyed).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from sos.contracts.identity import UV16D
from sos.kernel.auth import verify_bearer as _auth_verify_bearer
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
# Auth helpers — same shape as integrations/app.py
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


def _check_scope(entry: Dict[str, Any]) -> None:
    """Avatar endpoints aren't tenant-keyed; accept system/admin or any
    scoped token. A bare token with neither project nor tenant_slug and
    no system/admin flag is rejected."""
    if entry.get("is_system") or entry.get("is_admin"):
        return
    scope = entry.get("project") or entry.get("tenant_slug") or entry.get("agent")
    if not scope:
        raise HTTPException(
            status_code=403,
            detail="token has no scope; avatar endpoints require a scoped or system token",
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
    """
    entry = _verify_bearer(authorization)
    _check_scope(entry)

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
    """
    entry = _verify_bearer(authorization)
    _check_scope(entry)

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
