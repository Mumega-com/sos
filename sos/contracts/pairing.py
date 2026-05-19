"""Agent Pairing — request/response contracts for the SOS pairing protocol.

A new agent proves control of an ed25519 keypair by signing a server-issued
nonce; the saas service validates the signature and returns a bearer token
registered in tokens.json.

See sos/contracts/schemas/pairing_v1.json for the canonical JSON Schema.
The Pydantic models here are the Python binding; the JSON Schema is the
cross-language source of truth that a future Rust port will implement against.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_PATH = Path(__file__).parent / "schemas" / "pairing_v1.json"

_SKILL_RE = re.compile(r"^[a-z][a-z0-9-]*$")


Role = Literal["specialist", "coordinator", "executor", "oracle"]
Scope = Literal["agent", "customer", "admin"]


class PairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(pattern=r"^[a-z][a-z0-9-]*$", min_length=2, max_length=64)
    pubkey: str = Field(pattern=r"^ed25519:[A-Za-z0-9+/=]{40,88}$")
    skills: list[str]
    model_provider: str = Field(pattern=r"^[a-z][a-z0-9-]*:[a-z][a-z0-9.-]*$")
    nonce: str = Field(min_length=16, max_length=128)
    signature: str = Field(pattern=r"^ed25519:[A-Za-z0-9+/=]{40,128}$")
    role: Role = "specialist"

    @field_validator("skills")
    @classmethod
    def _skills(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("skills must be non-empty")
        seen: set[str] = set()
        for s in v:
            if not _SKILL_RE.match(s):
                raise ValueError(f"invalid skill slug: {s!r}")
            if s in seen:
                raise ValueError(f"duplicate skill: {s!r}")
            seen.add(s)
        return v


class PairingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(pattern=r"^[a-f0-9]{64}$")
    agent_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    issued_at: str
    expires_at: str
    scope: Scope = "agent"

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _iso(cls, v: str) -> str:
        # Accept "2026-04-17T20:00:00Z" or explicit offset variants.
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


def load_schema() -> dict[str, Any]:
    """Return the JSON Schema document. Cross-language source of truth."""
    return json.loads(SCHEMA_PATH.read_text())
