"""
SOS Kernel Schema - Message and Response definitions for all service communication.

All inter-service communication uses this schema. Messages are versioned
to enable backward compatibility during migrations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
import uuid
import json


class MessageType(Enum):
    """Types of messages in SOS."""
    # Engine messages
    CHAT = "chat"
    COMPLETION = "completion"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"

    # Memory messages
    MEMORY_STORE = "memory_store"
    MEMORY_QUERY = "memory_query"
    MEMORY_DELETE = "memory_delete"

    # Economy messages
    LEDGER_READ = "ledger_read"
    LEDGER_WRITE = "ledger_write"
    PAYOUT = "payout"
    SLASH = "slash"

    # System messages
    SIGNAL = "signal"
    BROADCAST = "broadcast"
    ANNOUNCE = "announce"
    HOOK_EVENT = "hook_event"
    HEALTH_CHECK = "health_check"
    CAPABILITY_REQUEST = "capability_request"
    CAPABILITY_GRANT = "capability_grant"

    # Task messages
    TASK_CREATE = "task_create"
    TASK_CLAIM = "task_claim"
    TASK_UPDATE = "task_update"
    TASK_COMPLETE = "task_complete"

    # Witness messages
    WITNESS_REQUEST = "witness_request"
    WITNESS_RESPONSE = "witness_response"


class ResponseStatus(Enum):
    """Response status codes."""
    SUCCESS = "success"
    ERROR = "error"
    PENDING = "pending"
    UNAUTHORIZED = "unauthorized"
    RATE_LIMITED = "rate_limited"
    NOT_FOUND = "not_found"
    VALIDATION_ERROR = "validation_error"


@dataclass
class Message:
    """
    Standard message format for all SOS service communication.

    Attributes:
        id: Unique message identifier
        type: Message type (see MessageType enum)
        source: Source service or agent ID
        target: Target service or agent ID
        payload: Message-specific data
        trace_id: Distributed tracing ID
        capability_id: Capability token authorizing this message
        version: Schema version for compatibility
        timestamp: When message was created
        metadata: Additional context
    """
    type: MessageType
    source: str
    target: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: Optional[str] = None
    capability_id: Optional[str] = None
    version: str = "1.0"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize message to dictionary."""
        return {
            "id": self.id,
            "type": self.type.value,
            "source": self.source,
            "target": self.target,
            "payload": self.payload,
            "trace_id": self.trace_id,
            "capability_id": self.capability_id,
            "version": self.version,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        """Serialize message to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """Deserialize message from dictionary."""
        raw_type = data["type"]
        try:
            msg_type = MessageType(raw_type)
        except ValueError:
            msg_type = MessageType.SIGNAL
            metadata = dict(data.get("metadata", {}))
            metadata["original_type"] = raw_type
        else:
            metadata = data.get("metadata", {})
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            type=msg_type,
            source=data.get("source", "unknown"),
            target=data.get("target", "broadcast"),
            payload=data.get("payload", {}),
            trace_id=data.get("trace_id"),
            capability_id=data.get("capability_id"),
            version=data.get("version", "1.0"),
            timestamp=datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else datetime.now(timezone.utc),
            metadata=metadata,
        )

    @classmethod
    def from_json(cls, json_str: str) -> Message:
        """Deserialize message from JSON string."""
        return cls.from_dict(json.loads(json_str))

    def with_trace(self, trace_id: str) -> Message:
        """Return copy of message with trace ID set."""
        return Message(
            id=self.id,
            type=self.type,
            source=self.source,
            target=self.target,
            payload=self.payload,
            trace_id=trace_id,
            capability_id=self.capability_id,
            version=self.version,
            timestamp=self.timestamp,
            metadata=self.metadata,
        )


@dataclass
class Response:
    """
    Standard response format for all SOS service communication.

    Attributes:
        message_id: ID of the message this responds to
        status: Response status (success, error, etc.)
        data: Response payload
        error: Error details if status is error
        trace_id: Distributed tracing ID
        version: Schema version
        timestamp: When response was created
        metadata: Additional context
    """
    message_id: str
    status: ResponseStatus
    data: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None
    trace_id: Optional[str] = None
    version: str = "1.0"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize response to dictionary."""
        return {
            "message_id": self.message_id,
            "status": self.status.value,
            "data": self.data,
            "error": self.error,
            "trace_id": self.trace_id,
            "version": self.version,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        """Serialize response to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Response:
        """Deserialize response from dictionary."""
        return cls(
            message_id=data["message_id"],
            status=ResponseStatus(data["status"]),
            data=data.get("data"),
            error=data.get("error"),
            trace_id=data.get("trace_id"),
            version=data.get("version", "1.0"),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> Response:
        """Deserialize response from JSON string."""
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def success(
        cls,
        message_id: str,
        data: dict[str, Any],
        trace_id: Optional[str] = None
    ) -> Response:
        """Create a success response."""
        return cls(
            message_id=message_id,
            status=ResponseStatus.SUCCESS,
            data=data,
            trace_id=trace_id,
        )

    @classmethod
    def error(
        cls,
        message_id: str,
        code: str,
        message: str,
        details: Optional[dict] = None,
        trace_id: Optional[str] = None
    ) -> Response:
        """Create an error response."""
        return cls(
            message_id=message_id,
            status=ResponseStatus.ERROR,
            error={
                "code": code,
                "message": message,
                "details": details or {},
            },
            trace_id=trace_id,
        )

    @property
    def is_success(self) -> bool:
        """Check if response indicates success."""
        return self.status == ResponseStatus.SUCCESS

    @property
    def is_error(self) -> bool:
        """Check if response indicates error."""
        return self.status in [ResponseStatus.ERROR, ResponseStatus.UNAUTHORIZED]
