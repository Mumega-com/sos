"""Agent Card — runtime operational registry view of an agent on the SOS bus.

AgentCard is the OVERLAY on top of the canonical AgentIdentity defined in
sos/kernel/identity.py. It carries operational/heartbeat state (session, pid,
host, cwd, cache, warm_policy) and references the soul-level identity via
identity_id (pattern: agent:<slug>).

Soul fields (public_key, dna, verification_status, capabilities) live on
AgentIdentity. Echo fields (name, model, role, etc.) are denormalized here for
display convenience — the source of truth is the resolved AgentIdentity.

See sos/contracts/schemas/agent_card_v1.json for the canonical schema.
Pydantic model here is the Python binding; the JSON Schema is the cross-language
source of truth that a future Rust port will implement against.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from sos.kernel.identity import Identity


AGENT_CARD_VERSION = "1.0.0"
SCHEMA_PATH = Path(__file__).parent / "schemas" / "agent_card_v1.json"


ToolName = Literal[
    "claude-code",
    "codex",
    "gemini-cli",
    "openclaw",
    "hermes",
    "custom-http",
    "sdk",
    "cron",
    "service",
]
# Expanded to cover multi-vendor substrates + human squad members
# per the 2026-04-18 coherence plan.
AgentType = Literal[
    "tmux",
    "openclaw",
    "remote",
    "webhook",
    "service",
    "hermes",
    "codex",
    "cma",
    "human",
]
AgentRole = Literal[
    "coordinator",
    "executor",
    "specialist",
    "oracle",
    "medic",
    "service",
    "human",
]
WarmPolicy = Literal["warm", "cold"]
PlanTier = Optional[Literal["starter", "growth", "scale", "enterprise"]]


class AgentCard(BaseModel):
    # ---- identity pointer ------------------------------------------------
    # Required. Points to the canonical AgentIdentity in sos/kernel/identity.py.
    # Source of truth for soul fields (dna, public_key, verification_status,
    # capabilities). Pattern mirrors AgentIdentity.id convention.
    identity_id: str = Field(pattern=r"^agent:[a-z][a-z0-9-]*$")

    # ---- echo fields (denormalized from AgentIdentity for display) --------
    # These are convenience copies. Source of truth = resolve_identity().
    agent_card_version: str = AGENT_CARD_VERSION
    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$", min_length=2, max_length=64)
    role: AgentRole
    model: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    squads: list[str] = Field(default_factory=list)
    project: Optional[str] = None
    tenant_subdomain: Optional[str] = None
    plan: PlanTier = None

    # ---- runtime/operational fields (unique to AgentCard) ----------------
    tool: ToolName
    type: AgentType
    session: Optional[str] = None
    warm_policy: WarmPolicy = "cold"
    cache_ttl_s: int = Field(default=300, ge=0, le=86400)
    last_cache_hit_rate: Optional[float] = Field(default=None, ge=0, le=1)
    pid: Optional[int] = None
    host: Optional[str] = None
    cwd: Optional[str] = None
    heartbeat_url: Optional[str] = None
    stale: bool = False
    registered_at: str
    last_seen: str
    summary: Optional[str] = Field(default=None, max_length=280)

    @field_validator("skills", "squads")
    @classmethod
    def _slugs(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        for s in v:
            if not s or not s.replace("-", "").replace("_", "").isalnum():
                raise ValueError(f"invalid slug: {s!r}")
            if s in seen:
                raise ValueError(f"duplicate slug: {s!r}")
            seen.add(s)
        return v

    @field_validator("registered_at", "last_seen")
    @classmethod
    def _iso(cls, v: str) -> str:
        # Accept "2026-04-16T22:25:21Z" or "+00:00" variants.
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    def to_redis_hash(self) -> dict[str, str]:
        """Serialize to the flat string-valued hash Redis HSET expects.

        Arrays are joined with ',' because Redis hash fields are strings.
        Null optional fields are omitted (Redis has no null; absence == null).
        The ``stale`` bool is serialized as "true"/"false" (lowercase) so the
        dashboard can read it without decoding the legacy "1"/"0" convention.
        """
        out: dict[str, str] = {}
        for key, val in self.model_dump().items():
            if val is None:
                continue
            if key == "stale":
                out[key] = "true" if val else "false"
            elif isinstance(val, list):
                out[key] = ",".join(val)
            elif isinstance(val, bool):
                out[key] = "1" if val else "0"
            else:
                out[key] = str(val)
        return out

    @classmethod
    def from_redis_hash(cls, h: dict[str, str]) -> "AgentCard":
        """Parse a Redis hash (all string values) back into a typed card."""
        parsed: dict[str, Any] = {}
        list_fields = {"skills", "squads"}
        int_fields = {"cache_ttl_s", "pid"}
        float_fields = {"last_cache_hit_rate"}
        # Empty string in Redis hash means None for optional string fields.
        nullable_str_fields = {"heartbeat_url"}
        bool_fields = {"stale"}
        for key, val in h.items():
            if key in list_fields:
                parsed[key] = [s for s in val.split(",") if s]
            elif key in int_fields and val != "":
                parsed[key] = int(val)
            elif key in float_fields and val != "":
                parsed[key] = float(val)
            elif key in nullable_str_fields:
                parsed[key] = None if val == "" else val
            elif key in bool_fields:
                parsed[key] = val.lower() == "true"
            else:
                parsed[key] = val
        return cls(**parsed)

    def resolve_identity(self) -> "Identity":
        """Return the canonical AgentIdentity for this card.

        TODO: implement via sos.services.registry (Registry wiring island).
        The registry will look up self.identity_id in Redis / the identity
        store and return the fully hydrated AgentIdentity (with DNA, public_key,
        verification_status, capabilities).
        """
        raise NotImplementedError(
            "resolve_identity() is a stub — wire sos.services.registry first. "
            f"identity_id={self.identity_id!r}"
        )

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_schema() -> dict[str, Any]:
    """Return the JSON Schema document. Cross-language source of truth."""
    return json.loads(SCHEMA_PATH.read_text())
