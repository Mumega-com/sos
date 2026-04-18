"""Bus message contracts — schema-validated envelopes for the SOS agent bus.

Cross-language source of truth (JSON Schema, used by Python, Rust, TS):
  sos/contracts/schemas/messages/announce_v1.json
  sos/contracts/schemas/messages/send_v1.json
  sos/contracts/schemas/messages/wake_v1.json
  sos/contracts/schemas/messages/ask_v1.json
  sos/contracts/schemas/messages/task.created_v1.json
  sos/contracts/schemas/messages/task.claimed_v1.json
  sos/contracts/schemas/messages/task.completed_v1.json
  sos/contracts/schemas/messages/task.routed_v1.json
  sos/contracts/schemas/messages/task.failed_v1.json
  sos/contracts/schemas/messages/skill.executed_v1.json
  sos/contracts/schemas/messages/agent_joined_v1.json

Naming convention:
  - Bus protocol types (announce, send, wake, ask, agent_joined): unchanged.
  - Squad/kernel event types: dot-separated per SQUAD_EVENTS in
    sos/contracts/squad.py (task.created, task.claimed, task.completed, etc.).
  - Rule: kernel SQUAD_EVENTS wins. Add to SQUAD_EVENTS first, then create
    v1 binding here, then update enforcement._V1_TYPES.

Pydantic models here are the Python binding; the JSON Schemas above are
authoritative.  A future Rust port will validate against those same schemas.
"""
from __future__ import annotations

import json
import uuid as _uuid_mod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MESSAGES_DIR = Path(__file__).parent / "schemas" / "messages"
MESSAGE_SCHEMA_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Shared Literal types
# ---------------------------------------------------------------------------

MessageType = Literal[
    "announce",
    "send",
    "wake",
    "ask",
    "task.created",
    "task.claimed",
    "task.completed",
    "task.routed",
    "task.scored",
    "task.failed",
    "skill.executed",
    "agent_joined",
]

# "normal" appears only in wake; merged here for a single canonical enum.
Priority = Literal["critical", "high", "medium", "low", "normal"]

TaskStatus = Literal["done", "failed", "cancelled", "timeout"]

ContentType = Literal["text/plain", "text/markdown"]


# ---------------------------------------------------------------------------
# Base envelope
# ---------------------------------------------------------------------------


class BusMessage(BaseModel):
    """Abstract-ish base with the 5 always-required envelope fields."""

    model_config = ConfigDict(strict=False)

    type: MessageType
    source: str = Field(pattern=r"^agent:[a-z][a-z0-9-]*$")
    timestamp: str
    version: str = Field(pattern=r"^\d+\.\d+(\.\d+)?$", default="1.0")
    message_id: str
    target: Optional[str] = Field(
        default=None,
        pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9:_-]*)$",
    )
    trace_id: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
        description="W3C Trace Context trace-id (32 hex chars). Optional; minted at ingress when absent.",
    )

    @field_validator("timestamp")
    @classmethod
    def _parse_timestamp(cls, v: str) -> str:
        """Validate that timestamp is ISO 8601."""
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    @field_validator("message_id")
    @classmethod
    def _parse_message_id(cls, v: str) -> str:
        """Validate that message_id is a well-formed UUID."""
        _uuid_mod.UUID(v)
        return v

    @staticmethod
    def new_trace_id() -> str:
        """Mint a fresh W3C-format trace-id (32 lowercase hex chars)."""
        import uuid as _uuid
        return _uuid.uuid4().hex

    @staticmethod
    def now_iso() -> str:
        """Return current UTC time as an ISO-8601 string."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def to_redis_fields(self) -> dict[str, str]:
        """Serialize to a flat string-valued dict suitable for redis.xadd."""
        out: dict[str, str] = {}
        for key, val in self.model_dump().items():
            if val is None:
                continue
            if isinstance(val, (dict, list)):
                out[key] = json.dumps(val)
            elif isinstance(val, bool):
                out[key] = "1" if val else "0"
            else:
                out[key] = str(val)
        return out

    @classmethod
    def from_redis_fields(cls, fields: dict[str, str]) -> "BusMessage":
        """Parse a flat string-valued dict (as returned by redis.xread) back
        into the matching BusMessage subclass. Ints and nested objects are
        restored from their JSON/string encoding. Use this on the consumer
        side of the bus to recover typed access to payload fields."""
        parsed: dict[str, Any] = {}
        for key, val in fields.items():
            # Nested objects / arrays were encoded as JSON strings
            if val and val[0] in "{[":
                try:
                    parsed[key] = json.loads(val)
                    continue
                except json.JSONDecodeError:
                    pass
            parsed[key] = val
        return parse_message(parsed)


# ---------------------------------------------------------------------------
# Announce
# ---------------------------------------------------------------------------


class AnnouncePayload(BaseModel):
    """Optional runtime context snapshot captured at agent startup."""

    model_config = ConfigDict(strict=False)

    text: Optional[str] = None
    tool: Optional[str] = None
    pid: Optional[int] = None
    tty: Optional[str] = None
    cwd: Optional[str] = None


class AnnounceMessage(BusMessage):
    """Emitted by an agent when it registers on the SOS bus at startup."""

    type: Literal["announce"] = Field(default="announce")  # type: ignore[assignment]
    target: Optional[str] = Field(
        default="sos:channel:global",
        pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9:_-]*)$",
    )
    payload: Optional[AnnouncePayload] = None
    agent_card_ref: Optional[str] = None


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


class SendPayload(BaseModel):
    """Message body carrying human-readable content and its content type."""

    model_config = ConfigDict(strict=False)

    text: str = Field(max_length=16384)
    content_type: ContentType = "text/plain"


class SendMessage(BusMessage):
    """Canonical agent-to-agent chat message on the SOS bus."""

    type: Literal["send"] = Field(default="send")  # type: ignore[assignment]
    target: str = Field(pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9:_-]*)$")
    payload: SendPayload
    reply_to: Optional[str] = None
    correlation_id: Optional[str] = None
    headers: Optional[dict[str, str]] = None

    @field_validator("reply_to", "correlation_id")
    @classmethod
    def _parse_optional_uuid(cls, v: Optional[str]) -> Optional[str]:
        """Validate optional UUID fields."""
        if v is not None:
            _uuid_mod.UUID(v)
        return v


# ---------------------------------------------------------------------------
# Wake
# ---------------------------------------------------------------------------


class WakePayload(BaseModel):
    """Wake payload delivered to the target agent's tmux session."""

    model_config = ConfigDict(strict=False)

    text: str = Field(min_length=1)


class WakeMessage(BusMessage):
    """Internal signal published to Redis pubsub to wake a tmux agent."""

    type: Literal["wake"] = Field(default="wake")  # type: ignore[assignment]
    target: str = Field(pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9:_-]*)$")
    payload: WakePayload
    priority: Priority = "normal"
    expires_at: Optional[str] = None
    ref_message_id: Optional[str] = None

    @field_validator("expires_at")
    @classmethod
    def _parse_expires_at(cls, v: Optional[str]) -> Optional[str]:
        """Validate expires_at is ISO 8601 if present."""
        if v is not None:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    @field_validator("ref_message_id")
    @classmethod
    def _parse_ref_message_id(cls, v: Optional[str]) -> Optional[str]:
        """Validate ref_message_id is a UUID if present."""
        if v is not None:
            _uuid_mod.UUID(v)
        return v


# ---------------------------------------------------------------------------
# Ask
# ---------------------------------------------------------------------------


class AskPayload(BaseModel):
    """Body of an ask containing the question and reply routing information."""

    model_config = ConfigDict(strict=False)

    question: str = Field(max_length=16384)
    reply_channel: str = Field(
        pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9-]*)$"
    )


class AskMessage(BusMessage):
    """Blocking-style message sent from one agent to another expecting a reply."""

    type: Literal["ask"] = Field(default="ask")  # type: ignore[assignment]
    target: str = Field(pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9:_-]*)$")
    payload: AskPayload
    timeout_s: Optional[int] = Field(default=None, ge=1, le=3600)
    correlation_id: Optional[str] = None

    @field_validator("correlation_id")
    @classmethod
    def _parse_correlation_id(cls, v: Optional[str]) -> Optional[str]:
        """Validate correlation_id is a UUID if present."""
        if v is not None:
            _uuid_mod.UUID(v)
        return v


# ---------------------------------------------------------------------------
# TaskCreated  (canonical type: "task.created" per SQUAD_EVENTS)
# ---------------------------------------------------------------------------


class TaskCreatedPayload(BaseModel):
    """Task-specific data describing a newly created task."""

    model_config = ConfigDict(strict=False)

    task_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=280)
    priority: Literal["critical", "high", "medium", "low"]
    description: Optional[str] = Field(default=None, max_length=16384)
    assignee: Optional[str] = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    skill_id: Optional[str] = None
    project: Optional[str] = None
    labels: Optional[list[str]] = None
    token_budget: Optional[int] = Field(default=None, ge=0)
    bounty_cents: Optional[int] = Field(default=None, ge=0)


class TaskCreatedMessage(BusMessage):
    """Event emitted when a new task is added to the task board."""

    type: Literal["task.created"] = Field(default="task.created")  # type: ignore[assignment]
    target: str = Field(
        default="sos:channel:tasks",
        pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9:_-]*)$",
    )
    payload: TaskCreatedPayload


# ---------------------------------------------------------------------------
# TaskClaimed  (canonical type: "task.claimed" per SQUAD_EVENTS)
# ---------------------------------------------------------------------------


class WorkerInfo(BaseModel):
    """Optional metadata about the worker that holds the task claim."""

    model_config = ConfigDict(strict=False)

    model: Optional[str] = None
    plan: Optional[str] = None
    estimated_duration_s: Optional[int] = Field(default=None, ge=0)


class TaskClaimedPayload(BaseModel):
    """Domain data for the task.claimed event."""

    model_config = ConfigDict(strict=False)

    task_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    claimed_at: str
    claim_expires_at: Optional[str] = None
    worker_info: Optional[WorkerInfo] = None

    @field_validator("claimed_at", "claim_expires_at")
    @classmethod
    def _parse_datetime(cls, v: Optional[str]) -> Optional[str]:
        """Validate datetime fields are ISO 8601."""
        if v is not None:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class TaskClaimedMessage(BusMessage):
    """Event emitted when an agent atomically claims a task."""

    type: Literal["task.claimed"] = Field(default="task.claimed")  # type: ignore[assignment]
    target: str = Field(
        default="sos:channel:tasks",
        pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9:_-]*)$",
    )
    payload: TaskClaimedPayload


# ---------------------------------------------------------------------------
# TaskCompleted  (canonical type: "task.completed" per SQUAD_EVENTS)
# ---------------------------------------------------------------------------


class TaskError(BaseModel):
    """Structured error detail for failed, cancelled, or timed-out tasks."""

    model_config = ConfigDict(strict=False)

    code: str = Field(pattern=r"^SOS-[A-Z0-9]{4,}$")
    message: str
    traceback: Optional[str] = None


class TaskCompletedPayload(BaseModel):
    """Task-specific result data including outcome, timing, and resource usage."""

    model_config = ConfigDict(strict=False)

    task_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    status: TaskStatus
    completed_at: str
    result: Optional[dict[str, Any]] = None
    duration_s: Optional[int] = Field(default=None, ge=0)
    artifacts: Optional[list[str]] = None
    tokens_spent: Optional[int] = Field(default=None, ge=0)
    cost_cents: Optional[int] = Field(default=None, ge=0)
    error: Optional[TaskError] = None

    @field_validator("completed_at")
    @classmethod
    def _parse_completed_at(cls, v: str) -> str:
        """Validate completed_at is ISO 8601."""
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class TaskCompletedMessage(BusMessage):
    """Event emitted by an agent when it finishes a task (successfully or otherwise)."""

    type: Literal["task.completed"] = Field(default="task.completed")  # type: ignore[assignment]
    target: str = Field(
        default="sos:channel:tasks",
        pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9:_-]*)$",
    )
    payload: TaskCompletedPayload


# ---------------------------------------------------------------------------
# TaskRouted  (canonical type: "task.routed" per SQUAD_EVENTS)
# ---------------------------------------------------------------------------


class TaskRoutedPayload(BaseModel):
    """Data describing the Brain's routing decision for a task."""

    model_config = ConfigDict(strict=False)

    task_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    routed_to: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    routed_at: str
    score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reason: Optional[str] = Field(default=None, max_length=1024)
    skill_matched: Optional[str] = None
    fallback: bool = False

    @field_validator("routed_at")
    @classmethod
    def _parse_routed_at(cls, v: str) -> str:
        """Validate routed_at is ISO 8601."""
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class TaskRoutedMessage(BusMessage):
    """Event emitted by the Brain service when it selects an agent for a task."""

    type: Literal["task.routed"] = Field(default="task.routed")  # type: ignore[assignment]
    target: str = Field(
        default="sos:channel:tasks",
        pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9:_-]*)$",
    )
    payload: TaskRoutedPayload


# ---------------------------------------------------------------------------
# TaskScored  (canonical type: "task.scored" — emitted by Brain after scoring)
# ---------------------------------------------------------------------------


class TaskScoredPayload(BaseModel):
    """Payload for task.scored v1 — ranking output from brain.scoring.score_task."""

    model_config = ConfigDict(strict=False)

    task_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    score: float = Field(ge=0.0, lt=10000.0)
    urgency: Literal["critical", "high", "medium", "low"]
    impact: Optional[float] = Field(default=None, ge=1.0, le=10.0)
    unblock_count: Optional[int] = Field(default=None, ge=0)
    cost: Optional[float] = Field(default=None, ge=0.1, le=10.0)
    ts: str

    @field_validator("ts")
    @classmethod
    def _parse_ts(cls, v: str) -> str:
        """Validate ts is ISO 8601."""
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class TaskScoredMessage(BusMessage):
    """Full envelope for task.scored events emitted by the Brain service."""

    type: Literal["task.scored"] = Field(default="task.scored")  # type: ignore[assignment]
    target: str = Field(
        default="sos:channel:tasks",
        pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9:_-]*)$",
    )
    payload: TaskScoredPayload


# ---------------------------------------------------------------------------
# TaskFailed  (canonical type: "task.failed" per SQUAD_EVENTS)
# ---------------------------------------------------------------------------


class TaskFailedPayload(BaseModel):
    """Data describing an explicit task failure event."""

    model_config = ConfigDict(strict=False)

    task_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    failed_at: str
    error: TaskError
    retryable: bool = False
    retry_count: Optional[int] = Field(default=None, ge=0)
    assigned_to: Optional[str] = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")

    @field_validator("failed_at")
    @classmethod
    def _parse_failed_at(cls, v: str) -> str:
        """Validate failed_at is ISO 8601."""
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class TaskFailedMessage(BusMessage):
    """Explicit failure event distinct from task.completed with status=failed.

    Use this when the failure is detected externally (watchdog, timeout
    handler, Brain service) rather than by the executing agent itself.
    """

    type: Literal["task.failed"] = Field(default="task.failed")  # type: ignore[assignment]
    target: str = Field(
        default="sos:channel:tasks",
        pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9:_-]*)$",
    )
    payload: TaskFailedPayload


# ---------------------------------------------------------------------------
# SkillExecuted  (canonical type: "skill.executed" per SQUAD_EVENTS)
# ---------------------------------------------------------------------------


class SkillExecutedPayload(BaseModel):
    """Data describing a completed skill invocation (regardless of task state).

    Distinct from task.completed: a single task may involve multiple skill
    invocations. skill.executed fires once per invocation.
    """

    model_config = ConfigDict(strict=False)

    skill_id: str = Field(min_length=1, max_length=128)
    invocation_id: str
    agent: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    started_at: str
    completed_at: str
    success: bool
    task_id: Optional[str] = Field(default=None, pattern=r"^[a-zA-Z0-9_-]+$")
    tokens_spent: Optional[int] = Field(default=None, ge=0)
    cost_cents: Optional[int] = Field(default=None, ge=0)
    error: Optional[TaskError] = None

    @field_validator("invocation_id")
    @classmethod
    def _parse_invocation_id(cls, v: str) -> str:
        """Validate invocation_id is a UUID."""
        _uuid_mod.UUID(v)
        return v

    @field_validator("started_at", "completed_at")
    @classmethod
    def _parse_datetime(cls, v: str) -> str:
        """Validate datetime fields are ISO 8601."""
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class SkillExecutedMessage(BusMessage):
    """Event emitted when a skill invocation completes, regardless of task state."""

    type: Literal["skill.executed"] = Field(default="skill.executed")  # type: ignore[assignment]
    target: str = Field(
        default="sos:channel:skills",
        pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9:_-]*)$",
    )
    payload: SkillExecutedPayload


# ---------------------------------------------------------------------------
# AgentJoined  (bus protocol type — not a SQUAD_EVENTS entry)
# ---------------------------------------------------------------------------


class AgentJoinedPayload(BaseModel):
    """Event body containing information about the agent that joined."""

    model_config = ConfigDict(strict=False)

    agent_name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    joined_at: str
    tenant_id: Optional[str] = None
    agent_card: Optional[dict[str, Any]] = None
    source_host: Optional[str] = None
    expiry_at: Optional[str] = None

    @field_validator("joined_at", "expiry_at")
    @classmethod
    def _parse_datetime(cls, v: Optional[str]) -> Optional[str]:
        """Validate datetime fields are ISO 8601."""
        if v is not None:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class AgentJoinedMessage(BusMessage):
    """Kernel-observed event emitted when a new agent registry entry appears."""

    type: Literal["agent_joined"] = Field(default="agent_joined")  # type: ignore[assignment]
    source: str = Field(
        pattern=r"^agent:[a-z][a-z0-9-]*$", default="agent:kernel"
    )
    target: str = Field(
        default="sos:channel:system:events",
        pattern=r"^(agent:[a-z][a-z0-9-]*|sos:channel:[a-z][a-z0-9:_-]*)$",
    )
    payload: AgentJoinedPayload


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, type[BusMessage]] = {
    "announce": AnnounceMessage,
    "send": SendMessage,
    "wake": WakeMessage,
    "ask": AskMessage,
    "task.created": TaskCreatedMessage,
    "task.claimed": TaskClaimedMessage,
    "task.completed": TaskCompletedMessage,
    "task.routed": TaskRoutedMessage,
    "task.scored": TaskScoredMessage,
    "task.failed": TaskFailedMessage,
    "skill.executed": SkillExecutedMessage,
    "agent_joined": AgentJoinedMessage,
}


def parse_message(raw: dict[str, Any]) -> BusMessage:
    """Dispatch raw dict to the matching BusMessage subclass.

    Reads raw["type"], returns the corresponding validated subclass instance.
    Raises pydantic.ValidationError on malformed shape or KeyError on unknown type.
    """
    msg_type = raw.get("type")
    cls = _TYPE_MAP.get(str(msg_type) if msg_type is not None else "")
    if cls is None:
        raise ValueError(f"Unknown message type: {msg_type!r}")
    return cls.model_validate(raw)


# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------


def load_schema(name: str) -> dict[str, Any]:
    """Return the JSON Schema dict for the named message type.

    Reads MESSAGES_DIR / f"{name}_v1.json". Cross-language source of truth.
    """
    path = MESSAGES_DIR / f"{name}_v1.json"
    return json.loads(path.read_text())
