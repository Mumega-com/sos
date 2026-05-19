"""Objective — the living-objective-tree coordination primitive for SOS v0.8.0.

Each Objective is a node in a shared gradient tree that every agent reads
locally and pulls work from. Fields carry claim, heartbeat, completion-gate,
and bounty state so the storage layer can derive dispatch, channels, and
bounty-fractal from a single primitive.

Children are NOT stored here — they are derived by the storage layer via
parent_id index queries (pattern avoids recursive serialization and keeps
the contract pure).

See sos/contracts/schemas/objective.schema.json for the canonical JSON Schema.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from sos.contracts.done_check import DoneCheck


class Objective(BaseModel):
    """A single node in the living objective tree."""

    model_config = {"extra": "forbid"}

    # ---- identity ---------------------------------------------------------
    # Accepts both ULID-format IDs (26 chars from Crockford base-32) and
    # stable system canonical slugs (lowercase alphanumeric + hyphens, e.g.
    # "reviews-primitive").  The two forms are disjoint: ULIDs are uppercase
    # and exactly 26 chars; slugs are lowercase and may contain hyphens.
    id: str = Field(pattern=r"^(?:[0-9A-HJKMNP-TV-Z]{26}|[a-z0-9][a-z0-9\-]{1,62}[a-z0-9])$")
    parent_id: str | None = None

    # ---- content ----------------------------------------------------------
    title: str = Field(min_length=1)
    description: str = ""

    # ---- economy ----------------------------------------------------------
    bounty_mind: int = Field(default=0, ge=0)  # $MIND payout on paid

    # ---- lifecycle state --------------------------------------------------
    state: Literal["open", "claimed", "shipped", "paid", "blocked"] = "open"

    # ---- claim / heartbeat ------------------------------------------------
    holder_agent: str | None = None          # set on claim, cleared on release
    holder_heartbeat_at: str | None = None   # ISO-8601, bumped every heartbeat

    # ---- routing / subscription ------------------------------------------
    subscribers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    capabilities_required: list[str] = Field(default_factory=list)

    # ---- completion gate --------------------------------------------------
    completion_artifact_url: str | None = None
    completion_notes: str = ""
    acks: list[str] = Field(default_factory=list)  # agent IDs that acked
    done_when: list[DoneCheck] = Field(default_factory=list)

    # ---- provenance -------------------------------------------------------
    created_by: str
    created_at: str
    updated_at: str

    # ---- multi-tenancy ----------------------------------------------------
    tenant_id: str = "default"
    project: str | None = None

    # ---- outcome score (v0.8.1) ------------------------------------------
    outcome_score: float | None = Field(default=None, ge=0.0, le=1.0)
    """Outcome metric for auto-improve loop. None when not scored yet."""

    # ---- validators -------------------------------------------------------

    @field_validator("created_at", "updated_at", "holder_heartbeat_at", mode="before")
    @classmethod
    def _validate_iso(cls, v: str | None) -> str | None:
        if v is None:
            return v
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    # ---- helpers ----------------------------------------------------------

    @classmethod
    def now_iso(cls) -> str:
        """Return current UTC time as ISO-8601 with Z suffix."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def to_redis_hash(self) -> dict[str, str]:
        """Serialize to the flat string-valued hash Redis HSET expects.

        - Lists are JSON-encoded (not comma-joined) to preserve entries with commas.
        - None values are stored as the JSON literal "null" so from_redis_hash
          can distinguish absence from empty string.
        - ``outcome_score`` is JSON-encoded when set; the key is omitted entirely
          when ``None`` (v0.8.1 forward-compatible with v0.8.0 hashes).
        - Scalars are cast to str.
        """
        out: dict[str, str] = {}
        for key, val in self.model_dump().items():
            if key == "outcome_score":
                if val is None:
                    # Skip — do not store when unset.
                    continue
                out[key] = json.dumps(val)
            elif isinstance(val, list):
                out[key] = json.dumps(val)
            elif val is None:
                out[key] = "null"
            else:
                out[key] = str(val)
        return out

    @classmethod
    def from_redis_hash(cls, data: dict[str, str]) -> "Objective":
        """Parse a flat Redis hash (all string values) back into a typed Objective."""
        list_fields = {"subscribers", "tags", "capabilities_required", "acks", "done_when"}
        parsed: dict = {}
        for key, val in data.items():
            if key == "outcome_score":
                parsed[key] = None if val == "null" else float(val)
            elif key in list_fields:
                parsed[key] = json.loads(val)
            elif val == "null":
                parsed[key] = None
            else:
                parsed[key] = val
        # bounty_mind must be int
        if "bounty_mind" in parsed and parsed["bounty_mind"] is not None:
            parsed["bounty_mind"] = int(parsed["bounty_mind"])
        return cls(**parsed)
