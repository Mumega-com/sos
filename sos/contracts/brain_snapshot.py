"""Brain Snapshot — observable snapshot of BrainService in-memory state.

BrainSnapshot is returned by GET /sos/brain on the dashboard service for
operators and the dashboard UI. It exposes the queue size, in-flight task
ids, the last 50 routing decisions, per-message-type event counters, and
service timestamps.

See sos/contracts/schemas/brain_snapshot_v1.json for the canonical schema.
Pydantic model here is the Python binding; the JSON Schema is the cross-language
source of truth that a future Rust port will implement against.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


SCHEMA_PATH = Path(__file__).parent / "schemas" / "brain_snapshot_v1.json"

_TASK_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class RoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    agent_name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    score: float = Field(ge=0)
    routed_at: str

    @field_validator("routed_at")
    @classmethod
    def _iso(cls, v: str) -> str:
        # Accept "2026-04-17T20:00:00Z" or "+00:00" variants.
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class BrainSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue_size: int = Field(ge=0)
    in_flight: list[str]
    recent_routes: list[RoutingDecision] = Field(max_length=50)
    events_by_type: dict[str, int]
    events_seen: int = Field(ge=0)
    last_update_ts: str
    service_started_at: str

    @field_validator("in_flight")
    @classmethod
    def _task_ids(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        for task_id in v:
            if not _TASK_ID_RE.match(task_id):
                raise ValueError(f"invalid task_id: {task_id!r}")
            if task_id in seen:
                raise ValueError(f"duplicate task_id: {task_id!r}")
            seen.add(task_id)
        return v

    @field_validator("events_by_type")
    @classmethod
    def _non_negative_counts(cls, v: dict[str, int]) -> dict[str, int]:
        for key, count in v.items():
            if count < 0:
                raise ValueError(f"event count for {key!r} must be >= 0, got {count}")
        return v

    @field_validator("last_update_ts", "service_started_at")
    @classmethod
    def _iso(cls, v: str) -> str:
        # Accept "2026-04-17T20:00:00Z" or "+00:00" variants.
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


def load_schema() -> dict[str, Any]:
    """Return the JSON Schema document. Cross-language source of truth."""
    return json.loads(SCHEMA_PATH.read_text())
