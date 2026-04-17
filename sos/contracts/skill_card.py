"""SkillCard v1 — Pydantic v2 binding for the SOS / ToRivers skill registry schema.

Cross-language source of truth (JSON Schema):
  sos/contracts/schemas/skill_card_v1.json

This module is the Python binding; the JSON Schema above is authoritative.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).parent / "schemas" / "skill_card_v1.json"

_SEMVER_PATTERN = r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
_AGENT_PATTERN = r"^agent:[a-z][a-z0-9-]*$"
# Verifier pattern tightened (Athena review 2026-04-17): must start with a
# letter, matching author_agent / Agent Card agent_id pattern, so identifiers
# are consistent across contracts.
_VERIFIER_PATTERN = r"^(agent|human):[a-z][a-z0-9-]*$"
_CURRENCY_PATTERN = r"^[A-Z0-9]{2,10}$"
# id: either UUID v4 or a constrained slug. Blocks ' ', '..', '../../...'.
_ID_PATTERN = (
    r"^[a-z0-9][a-z0-9_-]{2,63}$"
    r"|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------


def load_schema() -> dict[str, Any]:
    """Return the parsed JSON Schema dict from the canonical path."""
    return json.loads(_SCHEMA_PATH.read_text())


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class LineageEntry(BaseModel):
    """One upstream skill this skill was derived from."""

    model_config = ConfigDict(strict=False)

    parent_skill_id: str = Field(min_length=1, description="id of the upstream skill")
    relation: Literal["forked", "refined", "composed", "inspired_by"]
    notes: Optional[str] = Field(default=None, max_length=500)


class EarningsInfo(BaseModel):
    """Materialized view of lifetime earnings across all invocations."""

    model_config = ConfigDict(strict=False)

    total_invocations: int = Field(default=0, ge=0)
    total_earned_micros: int = Field(
        default=0, ge=0, description="Integer micros (1e-6 currency units) of gross lifetime earnings."
    )
    currency: str = Field(
        default="USD",
        pattern=_CURRENCY_PATTERN,
        description="Upper-case 2-10 alphanumeric. USD | MIND | operator-defined.",
    )
    last_invocation_at: Optional[str] = None
    invocations_by_tenant: Optional[dict[str, int]] = None

    @field_validator("last_invocation_at")
    @classmethod
    def _parse_last_invocation_at(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class VerificationInfo(BaseModel):
    """Proof that outputs from this skill have been checked."""

    model_config = ConfigDict(strict=False)

    status: Literal["unverified", "auto_verified", "human_verified", "disputed"] = "unverified"
    sample_output_refs: list[str] = Field(default_factory=list, max_length=50)
    verified_by: list[str] = Field(
        default_factory=list,
        description="agent: or human: URIs of verifiers.",
    )
    verified_at: Optional[str] = None
    dispute_reason: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("verified_by", mode="before")
    @classmethod
    def _check_verifier_patterns(cls, v: list[str]) -> list[str]:
        import re
        for item in v:
            if not re.match(_VERIFIER_PATTERN, item):
                raise ValueError(f"verified_by item {item!r} does not match {_VERIFIER_PATTERN}")
        return v

    @field_validator("verified_at")
    @classmethod
    def _parse_verified_at(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class RevenueSplit(BaseModel):
    """Fractional revenue split; each key is optional, values 0..1.

    If any field is set, the populated fields must sum to 1.0 ±0.001
    (Athena gate 2026-04-17 — prevents silent under-payment when a card
    is written with only {"author": 0.1} and the rest drops to zero).
    """

    model_config = ConfigDict(strict=False)

    author: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    operator: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    network: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_sum(self) -> "RevenueSplit":
        populated = [v for v in (self.author, self.operator, self.network) if v is not None]
        if not populated:
            return self  # all unset is valid (omit the field entirely)
        total = sum(populated)
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"revenue_split fields must sum to 1.0 (tolerance ±0.001); got {total:.4f}"
            )
        return self


class CommerceInfo(BaseModel):
    """How this skill is sold on ToRivers / the marketplace."""

    model_config = ConfigDict(strict=False)

    price_per_call_micros: int = Field(default=0, ge=0)
    currency: str = Field(default="USD", pattern=_CURRENCY_PATTERN)
    revenue_split: Optional[RevenueSplit] = None
    marketplace_listed: bool = Field(default=False)


class RuntimeInfo(BaseModel):
    """Where and how the skill executes."""

    model_config = ConfigDict(strict=False)

    entry_point: Optional[str] = Field(default=None, max_length=500)
    backend: Optional[
        Literal[
            "claude-code",
            "cma",
            "openai-agents-sdk",
            "langgraph",
            "crewai",
            "local-python",
            "custom",
        ]
    ] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=3600)
    memory_mb: Optional[int] = Field(default=None, ge=64)


# ---------------------------------------------------------------------------
# Top-level model
# ---------------------------------------------------------------------------


class SkillCard(BaseModel):
    """Canonical skill record in the SOS / ToRivers skill registry."""

    model_config = ConfigDict(strict=False)

    # --- required ---
    schema_version: Literal["1"] = Field(
        default="1",
        description="SkillCard schema version. v1 cards carry '1' so future v1.x/v2 are distinguishable at read time.",
    )
    id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(pattern=_SEMVER_PATTERN)
    author_agent: str = Field(pattern=_AGENT_PATTERN)
    created_at: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]

    # --- optional ---
    description: Optional[str] = Field(default=None, max_length=4000)
    tags: Optional[list[str]] = Field(default=None, max_length=20)
    authored_by_ai: bool = False
    lineage: Optional[list[LineageEntry]] = Field(default=None, max_length=20)
    updated_at: Optional[str] = None
    earnings: Optional[EarningsInfo] = None
    verification: Optional[VerificationInfo] = None
    required_tools: Optional[list[str]] = None
    required_models: Optional[list[str]] = None
    commerce: Optional[CommerceInfo] = None
    runtime: Optional[RuntimeInfo] = None
    metadata: Optional[dict[str, Any]] = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def _parse_datetime(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    @field_validator("tags", "required_tools", "required_models", mode="after")
    @classmethod
    def _check_nonempty_items(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("list items must be non-empty strings")
        return v

    @field_validator("input_schema", "output_schema", mode="after")
    @classmethod
    def _check_schema_shape(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Every input/output_schema must declare at least `$schema` or `type`.

        Prevents `{"input_schema": {"foo": "bar"}}` from validating — that's
        not a JSON Schema document, it's just a dict, and downstream
        validators will choke on it.
        """
        if "$schema" not in v and "type" not in v:
            raise ValueError("input_schema / output_schema must carry '$schema' or 'type'")
        return v

    @model_validator(mode="after")
    def _check_marketplace_invariants(self) -> "SkillCard":
        """marketplace-listed skills must have an entry_point.

        A listed skill with no runtime.entry_point is structurally unshippable
        (the marketplace can't invoke it). Enforce at contract boundary.
        """
        if self.commerce and self.commerce.marketplace_listed:
            if not self.runtime or not self.runtime.entry_point:
                raise ValueError(
                    "commerce.marketplace_listed=true requires runtime.entry_point"
                )
        return self

    @staticmethod
    def now_iso() -> str:
        """Return current UTC time as an ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def parse_skill_card(data: dict[str, Any]) -> SkillCard:
    """Construct a SkillCard from a raw dict.

    Raises pydantic.ValidationError on malformed input.
    """
    return SkillCard.model_validate(data)
