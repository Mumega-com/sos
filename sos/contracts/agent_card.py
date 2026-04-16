"""Agent Card — schema-validated self-description of an agent on the SOS bus.

See sos/contracts/schemas/agent_card_v1.json for the canonical schema.
Pydantic model here is the Python binding; the JSON Schema is the cross-language
source of truth that a future Rust port will implement against.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


AGENT_CARD_VERSION = "1.0.0"
SCHEMA_PATH = Path(__file__).parent / "schemas" / "agent_card_v1.json"


ToolName = Literal[
    "claude-code", "codex", "gemini-cli", "openclaw",
    "hermes", "custom-http", "sdk", "cron", "service",
]
AgentType = Literal["tmux", "openclaw", "remote", "webhook", "service"]
AgentRole = Literal[
    "coordinator", "executor", "specialist",
    "oracle", "medic", "service", "human",
]
WarmPolicy = Literal["warm", "cold"]
PlanTier = Optional[Literal["starter", "growth", "scale", "enterprise"]]


class AgentCard(BaseModel):
    agent_card_version: str = AGENT_CARD_VERSION
    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$", min_length=2, max_length=64)
    tool: ToolName
    type: AgentType
    role: AgentRole
    model: Optional[str] = None
    session: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    squads: list[str] = Field(default_factory=list)
    project: Optional[str] = None
    tenant_subdomain: Optional[str] = None
    plan: PlanTier = None
    warm_policy: WarmPolicy = "cold"
    cache_ttl_s: int = Field(default=300, ge=0, le=86400)
    last_cache_hit_rate: Optional[float] = Field(default=None, ge=0, le=1)
    pid: Optional[int] = None
    host: Optional[str] = None
    cwd: Optional[str] = None
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
        """
        out: dict[str, str] = {}
        for key, val in self.model_dump().items():
            if val is None:
                continue
            if isinstance(val, list):
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
        for key, val in h.items():
            if key in list_fields:
                parsed[key] = [s for s in val.split(",") if s]
            elif key in int_fields and val != "":
                parsed[key] = int(val)
            elif key in float_fields and val != "":
                parsed[key] = float(val)
            else:
                parsed[key] = val
        return cls(**parsed)

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_schema() -> dict[str, Any]:
    """Return the JSON Schema document. Cross-language source of truth."""
    return json.loads(SCHEMA_PATH.read_text())
