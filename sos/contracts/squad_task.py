"""Pydantic v2 binding for the canonical `sos.contracts.squad.SquadTask` dataclass.

For programmatic validation + JSON Schema round-trip. Do NOT add new fields here —
modify the dataclass upstream and regenerate this binding.

Cross-language source of truth (JSON Schema):
  sos/contracts/schemas/squad_task_v1.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sos.contracts.squad import (
    SquadTask as SquadTaskDataclass,
    TaskPriority,
    TaskStatus,
)

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
# Pydantic model — thin wrapper over sos.contracts.squad.SquadTask
# ---------------------------------------------------------------------------


class SquadTaskV1(BaseModel):
    """Validated contract for a SquadTask crossing a service / API boundary.

    Wraps `sos.contracts.squad.SquadTask` (kernel dataclass). Field names
    are identical to the dataclass attributes, with two additions:
    - `schema_version`: wire-format discriminator (always "1")
    - `bounty_micros`: integer-micro denomination for the bounty reward
    """

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

    # --- optional (mirror dataclass fields) ---
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
    # Dataclass interop
    # ---------------------------------------------------------------------------

    @classmethod
    def from_dataclass(cls, task: SquadTaskDataclass) -> SquadTaskV1:
        """Construct a SquadTaskV1 from a kernel SquadTask dataclass instance.

        `schema_version` defaults to "1". `bounty_micros` defaults to 0
        (not present on the dataclass — enrich separately if needed).
        """
        return cls.model_validate(
            {
                "schema_version": "1",
                "id": task.id,
                "squad_id": task.squad_id,
                "title": task.title,
                "description": task.description,
                "status": task.status.value if isinstance(task.status, TaskStatus) else task.status,
                "priority": task.priority.value if isinstance(task.priority, TaskPriority) else task.priority,
                "assignee": task.assignee,
                "skill_id": task.skill_id,
                "project": task.project,
                "labels": list(task.labels),
                "blocked_by": list(task.blocked_by),
                "blocks": list(task.blocks),
                "inputs": dict(task.inputs),
                "result": dict(task.result),
                "token_budget": task.token_budget,
                "bounty": dict(task.bounty),
                "bounty_micros": 0,
                "external_ref": task.external_ref,
                "created_at": task.created_at or cls.now_iso(),
                "updated_at": task.updated_at or None,
                "completed_at": task.completed_at,
                "claimed_at": task.claimed_at,
                "attempt": task.attempt,
            }
        )

    def to_dataclass(self) -> SquadTaskDataclass:
        """Convert this Pydantic wrapper back to the kernel SquadTask dataclass.

        `schema_version` and `bounty_micros` are Pydantic-layer additions;
        they are dropped here. `bounty_micros` is stored in `bounty` by the
        caller if needed.
        """
        return SquadTaskDataclass(
            id=self.id,
            squad_id=self.squad_id,
            title=self.title,
            description=self.description,
            status=TaskStatus(self.status),
            priority=TaskPriority(self.priority),
            assignee=self.assignee,
            skill_id=self.skill_id,
            project=self.project,
            labels=list(self.labels),
            blocked_by=list(self.blocked_by),
            blocks=list(self.blocks),
            inputs=dict(self.inputs),
            result=dict(self.result),
            token_budget=self.token_budget,
            bounty=dict(self.bounty),
            external_ref=self.external_ref,
            created_at=self.created_at,
            updated_at=self.updated_at or "",
            completed_at=self.completed_at,
            claimed_at=self.claimed_at,
            attempt=self.attempt,
        )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def parse_squad_task(data: dict[str, Any]) -> SquadTaskV1:
    """Construct a SquadTaskV1 from a raw dict.

    Raises pydantic.ValidationError on malformed input.
    """
    return SquadTaskV1.model_validate(data)
