"""SquadTask v1 — Pydantic v2 binding for the SOS Squad Task schema.

Cross-language source of truth (JSON Schema):
  sos/contracts/schemas/squad_task_v1.json

This module is the Python binding; the JSON Schema above is authoritative.
The dataclass in sos/contracts/squad.py (SquadTask) remains for internal
service use. This Pydantic model is the *validated contract* boundary —
used when crossing service or API boundaries.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).parent / "schemas" / "squad_task_v1.json"

# Matches the pattern in the JSON Schema: UUID v4 or a constrained slug.
_ID_PATTERN = (
    r"^[a-z0-9][a-z0-9_-]{2,63}$"
    r"|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

_ISO_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"


# ---------------------------------------------------------------------------
# Enums (as Literal types — matching TaskStatus / TaskPriority in squad.py)
# ---------------------------------------------------------------------------

TaskStatusLiteral = Literal[
    "backlog",
    "queued",
    "claimed",
    "in_progress",
    "review",
    "done",
    "blocked",
    "canceled",
    "failed",
]

TaskPriorityLiteral = Literal["critical", "high", "medium", "low"]


# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------


def load_schema() -> dict[str, Any]:
    """Return the parsed JSON Schema dict from the canonical path."""
    return json.loads(_SCHEMA_PATH.read_text())


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------


class SquadTaskV1(BaseModel):
    """Validated contract for a SquadTask crossing a service / API boundary."""

    model_config = ConfigDict(strict=False)

    # --- required ---
    schema_version: Literal["1"] = Field(
        default="1",
        description="SquadTask schema version. v1 carries '1'.",
    )
    id: str = Field(pattern=_ID_PATTERN)
    squad_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    status: TaskStatusLiteral
    created_at: str

    # --- optional ---
    description: str = ""
    priority: TaskPriorityLiteral = "medium"
    assignee: Optional[str] = None
    skill_id: Optional[str] = None
    project: str = ""
    labels: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    token_budget: int = Field(default=0, ge=0)
    bounty: dict[str, Any] = Field(default_factory=dict)
    bounty_micros: int = Field(default=0, ge=0)
    external_ref: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    claimed_at: Optional[str] = None
    attempt: int = Field(default=0, ge=0)

    @field_validator("created_at", "updated_at", "completed_at", "claimed_at")
    @classmethod
    def _parse_datetime(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            # Normalize Z suffix and validate parse
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    @field_validator("labels", "blocked_by", "blocks", mode="after")
    @classmethod
    def _check_nonempty_items(cls, v: list[str]) -> list[str]:
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("list items must be non-empty strings")
        return v

    @staticmethod
    def now_iso() -> str:
        """Return current UTC time as an ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def parse_squad_task(data: dict[str, Any]) -> SquadTaskV1:
    """Construct a SquadTaskV1 from a raw dict.

    Raises pydantic.ValidationError on malformed input.
    """
    return SquadTaskV1.model_validate(data)
