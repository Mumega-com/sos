"""
Audit Trail for Tool Calls

Records tool execution events for compliance, debugging, and ledger integration.

Format: JSONL (one JSON object per line)
Location: ~/.sos/audit/tools.jsonl

Usage:
    from sos.observability.audit import AuditLogger, record_tool_call

    # Using helper function
    record_tool_call(
        tool_name="web_search",
        agent_id="agent:River",
        input_params={"query": "test"},
        output={"results": [...]},
        duration_ms=150,
    )

    # Using logger directly
    logger = AuditLogger()
    await logger.log_tool_call(...)
"""

import os
import json
import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional, List
from pathlib import Path
from enum import Enum
import hashlib

from sos.observability.logging import get_logger

log = get_logger("audit")


class AuditEventType(str, Enum):
    """Types of audit events."""
    TOOL_CALL = "tool_call"
    TOOL_SUCCESS = "tool_success"
    TOOL_FAILURE = "tool_failure"
    TOOL_DENIED = "tool_denied"
    CAPABILITY_USED = "capability_used"
    RATE_LIMITED = "rate_limited"


@dataclass
class AuditEvent:
    """A single audit event record."""
    event_type: AuditEventType
    timestamp: str
    agent_id: str
    tool_name: str
    request_id: str

    # Tool execution details
    input_hash: Optional[str] = None  # SHA256 of input (privacy)
    input_preview: Optional[str] = None  # First 100 chars
    output_hash: Optional[str] = None
    output_preview: Optional[str] = None

    # Execution metadata
    duration_ms: Optional[int] = None
    success: bool = True
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    # Authorization
    capability_id: Optional[str] = None
    scopes_used: List[str] = field(default_factory=list)

    # Context
    session_id: Optional[str] = None
    trace_id: Optional[str] = None
    ip_address: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return {k: v for k, v in d.items() if v is not None}

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), separators=(",", ":"))


class AuditLogger:
    """
    Audit logger for tool execution events.

    Writes to:
    - ~/.sos/audit/tools.jsonl (tool calls)
    - ~/.sos/audit/ledger.jsonl (economy-relevant events)
    """

    def __init__(
        self,
        audit_dir: Optional[str] = None,
        enable_ledger: bool = True,
        max_preview_length: int = 100,
    ):
        self.audit_dir = Path(audit_dir or os.path.expanduser("~/.sos/audit"))
        self.enable_ledger = enable_ledger
        self.max_preview_length = max_preview_length

        # Ensure directories exist
        self.audit_dir.mkdir(parents=True, exist_ok=True)

        self.tools_file = self.audit_dir / "tools.jsonl"
        self.ledger_file = self.audit_dir / "ledger.jsonl"

        self._lock = asyncio.Lock()

    def _hash_data(self, data: Any) -> str:
        """Create SHA256 hash of data for privacy-preserving logging."""
        if data is None:
            return ""
        json_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()[:16]

    def _preview_data(self, data: Any) -> str:
        """Create preview of data for debugging."""
        if data is None:
            return ""
        if isinstance(data, str):
            text = data
        else:
            text = json.dumps(data, default=str)
        if len(text) > self.max_preview_length:
            return text[:self.max_preview_length] + "..."
        return text

    def _generate_request_id(self) -> str:
        """Generate unique request ID."""
        import uuid
        return f"req_{uuid.uuid4().hex[:12]}"

    async def log_tool_call(
        self,
        tool_name: str,
        agent_id: str,
        input_params: Optional[Dict[str, Any]] = None,
        output: Optional[Any] = None,
        duration_ms: Optional[int] = None,
        success: bool = True,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        capability_id: Optional[str] = None,
        scopes_used: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> AuditEvent:
        """
        Log a tool call event.

        Args:
            tool_name: Name of the tool executed
            agent_id: ID of the agent making the call
            input_params: Tool input parameters
            output: Tool output (will be hashed)
            duration_ms: Execution duration in milliseconds
            success: Whether execution succeeded
            error_code: Error code if failed
            error_message: Error message if failed
            capability_id: ID of capability used for auth
            scopes_used: Scopes consumed by the call
            session_id: Session identifier
            trace_id: Distributed trace ID
            request_id: Unique request ID (generated if not provided)

        Returns:
            The created AuditEvent
        """
        event = AuditEvent(
            event_type=AuditEventType.TOOL_SUCCESS if success else AuditEventType.TOOL_FAILURE,
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent_id=agent_id,
            tool_name=tool_name,
            request_id=request_id or self._generate_request_id(),
            input_hash=self._hash_data(input_params),
            input_preview=self._preview_data(input_params),
            output_hash=self._hash_data(output) if output else None,
            output_preview=self._preview_data(output) if output else None,
            duration_ms=duration_ms,
            success=success,
            error_code=error_code,
            error_message=error_message,
            capability_id=capability_id,
            scopes_used=scopes_used or [],
            session_id=session_id,
            trace_id=trace_id,
        )

        await self._write_event(event)
        return event

    async def log_tool_denied(
        self,
        tool_name: str,
        agent_id: str,
        reason: str,
        capability_id: Optional[str] = None,
        required_scopes: Optional[List[str]] = None,
    ) -> AuditEvent:
        """Log a denied tool call (auth failure)."""
        event = AuditEvent(
            event_type=AuditEventType.TOOL_DENIED,
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent_id=agent_id,
            tool_name=tool_name,
            request_id=self._generate_request_id(),
            success=False,
            error_code="TOOL_DENIED",
            error_message=reason,
            capability_id=capability_id,
            scopes_used=required_scopes or [],
        )

        await self._write_event(event)
        return event

    async def log_rate_limited(
        self,
        tool_name: str,
        agent_id: str,
        retry_after: Optional[int] = None,
    ) -> AuditEvent:
        """Log a rate-limited tool call."""
        event = AuditEvent(
            event_type=AuditEventType.RATE_LIMITED,
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent_id=agent_id,
            tool_name=tool_name,
            request_id=self._generate_request_id(),
            success=False,
            error_code="RATE_LIMITED",
            error_message=f"Retry after {retry_after}s" if retry_after else "Rate limited",
        )

        await self._write_event(event)
        return event

    async def _write_event(self, event: AuditEvent):
        """Write event to audit files."""
        json_line = event.to_json() + "\n"

        async with self._lock:
            # Write to tools audit log
            with open(self.tools_file, "a") as f:
                f.write(json_line)

            # Write to ledger if enabled and relevant
            if self.enable_ledger and self._is_ledger_relevant(event):
                with open(self.ledger_file, "a") as f:
                    f.write(json_line)

        log.debug(
            f"Audit event logged: {event.event_type.value}",
            tool=event.tool_name,
            agent=event.agent_id,
        )

    def _is_ledger_relevant(self, event: AuditEvent) -> bool:
        """Check if event should be mirrored to ledger."""
        # All tool executions are ledger-relevant for work tracking
        return event.event_type in (
            AuditEventType.TOOL_SUCCESS,
            AuditEventType.TOOL_FAILURE,
        )

    async def query_events(
        self,
        agent_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        event_type: Optional[AuditEventType] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[AuditEvent]:
        """
        Query audit events from the log.

        Args:
            agent_id: Filter by agent
            tool_name: Filter by tool
            event_type: Filter by event type
            since: Only events after this time
            limit: Maximum events to return

        Returns:
            List of matching AuditEvents
        """
        events = []

        if not self.tools_file.exists():
            return events

        with open(self.tools_file) as f:
            for line in f:
                if not line.strip():
                    continue

                try:
                    data = json.loads(line)

                    # Apply filters
                    if agent_id and data.get("agent_id") != agent_id:
                        continue
                    if tool_name and data.get("tool_name") != tool_name:
                        continue
                    if event_type and data.get("event_type") != event_type.value:
                        continue
                    if since:
                        event_time = datetime.fromisoformat(data["timestamp"])
                        if event_time < since:
                            continue

                    # Reconstruct event
                    data["event_type"] = AuditEventType(data["event_type"])
                    events.append(AuditEvent(**data))

                    if len(events) >= limit:
                        break

                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    log.warn(f"Skipping malformed audit entry: {e}")

        return events

    def get_stats(self) -> Dict[str, Any]:
        """Get audit log statistics."""
        stats = {
            "tools_file": str(self.tools_file),
            "ledger_file": str(self.ledger_file),
            "tools_exists": self.tools_file.exists(),
            "ledger_exists": self.ledger_file.exists(),
        }

        if self.tools_file.exists():
            stats["tools_size_bytes"] = self.tools_file.stat().st_size
            stats["tools_lines"] = sum(1 for _ in open(self.tools_file))

        if self.ledger_file.exists():
            stats["ledger_size_bytes"] = self.ledger_file.stat().st_size
            stats["ledger_lines"] = sum(1 for _ in open(self.ledger_file))

        return stats


# Singleton instance
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Get or create the singleton audit logger."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


async def record_tool_call(
    tool_name: str,
    agent_id: str,
    input_params: Optional[Dict[str, Any]] = None,
    output: Optional[Any] = None,
    duration_ms: Optional[int] = None,
    success: bool = True,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    **kwargs,
) -> AuditEvent:
    """Convenience function to record a tool call."""
    logger = get_audit_logger()
    return await logger.log_tool_call(
        tool_name=tool_name,
        agent_id=agent_id,
        input_params=input_params,
        output=output,
        duration_ms=duration_ms,
        success=success,
        error_code=error_code,
        error_message=error_message,
        **kwargs,
    )
