"""
Tests for Audit Trail System

Tests tool call logging, ledger integration, and query capabilities.
"""

import pytest
import json
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

from sos.observability.audit import (
    AuditLogger,
    AuditEvent,
    AuditEventType,
    record_tool_call,
    get_audit_logger,
)


class TestAuditEvent:
    """Tests for AuditEvent dataclass."""

    def test_basic_event_creation(self):
        """Test creating a basic audit event."""
        event = AuditEvent(
            event_type=AuditEventType.TOOL_SUCCESS,
            timestamp="2026-02-03T12:00:00+00:00",
            agent_id="agent:Test",
            tool_name="web_search",
            request_id="req_abc123",
        )

        assert event.event_type == AuditEventType.TOOL_SUCCESS
        assert event.agent_id == "agent:Test"
        assert event.tool_name == "web_search"
        assert event.success is True

    def test_event_to_dict(self):
        """Test converting event to dictionary."""
        event = AuditEvent(
            event_type=AuditEventType.TOOL_FAILURE,
            timestamp="2026-02-03T12:00:00+00:00",
            agent_id="agent:Test",
            tool_name="file_read",
            request_id="req_def456",
            success=False,
            error_code="FILE_NOT_FOUND",
            error_message="File does not exist",
        )

        d = event.to_dict()

        assert d["event_type"] == "tool_failure"
        assert d["agent_id"] == "agent:Test"
        assert d["error_code"] == "FILE_NOT_FOUND"
        # None values should be excluded
        assert "input_hash" not in d

    def test_event_to_json(self):
        """Test converting event to JSON string."""
        event = AuditEvent(
            event_type=AuditEventType.TOOL_SUCCESS,
            timestamp="2026-02-03T12:00:00+00:00",
            agent_id="agent:Test",
            tool_name="bash",
            request_id="req_ghi789",
            duration_ms=150,
        )

        json_str = event.to_json()
        parsed = json.loads(json_str)

        assert parsed["event_type"] == "tool_success"
        assert parsed["duration_ms"] == 150

    def test_event_types_all_values(self):
        """Test all event types serialize correctly."""
        types = [
            AuditEventType.TOOL_CALL,
            AuditEventType.TOOL_SUCCESS,
            AuditEventType.TOOL_FAILURE,
            AuditEventType.TOOL_DENIED,
            AuditEventType.CAPABILITY_USED,
            AuditEventType.RATE_LIMITED,
        ]

        for event_type in types:
            event = AuditEvent(
                event_type=event_type,
                timestamp="2026-02-03T12:00:00+00:00",
                agent_id="agent:Test",
                tool_name="test",
                request_id="req_test",
            )
            # Should not raise
            json_str = event.to_json()
            assert event_type.value in json_str


class TestAuditLogger:
    """Tests for AuditLogger class."""

    @pytest.fixture
    def temp_audit_dir(self):
        """Create a temporary directory for audit logs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def logger(self, temp_audit_dir):
        """Create an AuditLogger with temporary directory."""
        return AuditLogger(audit_dir=temp_audit_dir)

    @pytest.mark.asyncio
    async def test_log_tool_call_success(self, logger):
        """Test logging a successful tool call."""
        event = await logger.log_tool_call(
            tool_name="web_search",
            agent_id="agent:River",
            input_params={"query": "test search"},
            output={"results": ["result1", "result2"]},
            duration_ms=250,
            success=True,
        )

        assert event.event_type == AuditEventType.TOOL_SUCCESS
        assert event.tool_name == "web_search"
        assert event.agent_id == "agent:River"
        assert event.duration_ms == 250
        assert event.input_hash is not None
        assert event.output_hash is not None
        assert "test search" in event.input_preview

    @pytest.mark.asyncio
    async def test_log_tool_call_failure(self, logger):
        """Test logging a failed tool call."""
        event = await logger.log_tool_call(
            tool_name="file_write",
            agent_id="agent:Kasra",
            input_params={"path": "/etc/passwd"},
            success=False,
            error_code="PERMISSION_DENIED",
            error_message="Cannot write to system files",
        )

        assert event.event_type == AuditEventType.TOOL_FAILURE
        assert event.success is False
        assert event.error_code == "PERMISSION_DENIED"

    @pytest.mark.asyncio
    async def test_log_tool_denied(self, logger):
        """Test logging a denied tool call."""
        event = await logger.log_tool_denied(
            tool_name="shell_exec",
            agent_id="agent:Untrusted",
            reason="Missing shell:execute scope",
            capability_id="cap_123",
            required_scopes=["shell:execute"],
        )

        assert event.event_type == AuditEventType.TOOL_DENIED
        assert event.error_code == "TOOL_DENIED"
        assert "Missing shell:execute scope" in event.error_message
        assert event.scopes_used == ["shell:execute"]

    @pytest.mark.asyncio
    async def test_log_rate_limited(self, logger):
        """Test logging a rate-limited call."""
        event = await logger.log_rate_limited(
            tool_name="api_call",
            agent_id="agent:Spammer",
            retry_after=60,
        )

        assert event.event_type == AuditEventType.RATE_LIMITED
        assert event.error_code == "RATE_LIMITED"
        assert "60s" in event.error_message

    @pytest.mark.asyncio
    async def test_writes_to_tools_file(self, logger, temp_audit_dir):
        """Test that events are written to tools.jsonl."""
        await logger.log_tool_call(
            tool_name="test_tool",
            agent_id="agent:Test",
        )

        tools_file = Path(temp_audit_dir) / "tools.jsonl"
        assert tools_file.exists()

        with open(tools_file) as f:
            line = f.readline()
            data = json.loads(line)
            assert data["tool_name"] == "test_tool"

    @pytest.mark.asyncio
    async def test_writes_to_ledger_file(self, logger, temp_audit_dir):
        """Test that relevant events are written to ledger.jsonl."""
        await logger.log_tool_call(
            tool_name="ledger_tool",
            agent_id="agent:Test",
            success=True,
        )

        ledger_file = Path(temp_audit_dir) / "ledger.jsonl"
        assert ledger_file.exists()

        with open(ledger_file) as f:
            line = f.readline()
            data = json.loads(line)
            assert data["tool_name"] == "ledger_tool"

    @pytest.mark.asyncio
    async def test_denied_not_in_ledger(self, logger, temp_audit_dir):
        """Test that denied events are not written to ledger."""
        await logger.log_tool_denied(
            tool_name="denied_tool",
            agent_id="agent:Test",
            reason="No access",
        )

        ledger_file = Path(temp_audit_dir) / "ledger.jsonl"
        # Ledger should not exist or be empty
        if ledger_file.exists():
            content = ledger_file.read_text()
            assert "denied_tool" not in content

    @pytest.mark.asyncio
    async def test_disable_ledger(self, temp_audit_dir):
        """Test disabling ledger writes."""
        logger = AuditLogger(audit_dir=temp_audit_dir, enable_ledger=False)

        await logger.log_tool_call(
            tool_name="test",
            agent_id="agent:Test",
        )

        ledger_file = Path(temp_audit_dir) / "ledger.jsonl"
        assert not ledger_file.exists()

    def test_hash_data(self, logger):
        """Test data hashing for privacy."""
        hash1 = logger._hash_data({"key": "value"})
        hash2 = logger._hash_data({"key": "value"})
        hash3 = logger._hash_data({"key": "different"})

        assert hash1 == hash2  # Same data = same hash
        assert hash1 != hash3  # Different data = different hash
        assert len(hash1) == 16  # Truncated to 16 chars

    def test_hash_data_none(self, logger):
        """Test hashing None returns empty string."""
        assert logger._hash_data(None) == ""

    def test_preview_data_string(self, logger):
        """Test preview of string data."""
        short = "short string"
        long = "x" * 200

        assert logger._preview_data(short) == short
        assert len(logger._preview_data(long)) == 103  # 100 + "..."
        assert logger._preview_data(long).endswith("...")

    def test_preview_data_dict(self, logger):
        """Test preview of dictionary data."""
        data = {"key": "value", "nested": {"a": 1}}
        preview = logger._preview_data(data)

        assert "key" in preview
        assert "value" in preview

    def test_preview_data_none(self, logger):
        """Test preview of None returns empty string."""
        assert logger._preview_data(None) == ""

    def test_generate_request_id(self, logger):
        """Test request ID generation."""
        id1 = logger._generate_request_id()
        id2 = logger._generate_request_id()

        assert id1.startswith("req_")
        assert id2.startswith("req_")
        assert id1 != id2  # Should be unique


class TestAuditLoggerQuery:
    """Tests for audit log querying."""

    @pytest.fixture
    def temp_audit_dir(self):
        """Create a temporary directory for audit logs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    async def populated_logger(self, temp_audit_dir):
        """Create a logger with some events."""
        logger = AuditLogger(audit_dir=temp_audit_dir)

        # Log various events
        await logger.log_tool_call(
            tool_name="web_search",
            agent_id="agent:River",
            success=True,
        )
        await logger.log_tool_call(
            tool_name="file_read",
            agent_id="agent:River",
            success=True,
        )
        await logger.log_tool_call(
            tool_name="web_search",
            agent_id="agent:Kasra",
            success=False,
        )
        await logger.log_tool_denied(
            tool_name="shell",
            agent_id="agent:Untrusted",
            reason="No access",
        )

        return logger

    @pytest.mark.asyncio
    async def test_query_all_events(self, populated_logger):
        """Test querying all events."""
        events = await populated_logger.query_events()

        assert len(events) == 4

    @pytest.mark.asyncio
    async def test_query_by_agent(self, populated_logger):
        """Test filtering by agent."""
        events = await populated_logger.query_events(agent_id="agent:River")

        assert len(events) == 2
        assert all(e.agent_id == "agent:River" for e in events)

    @pytest.mark.asyncio
    async def test_query_by_tool(self, populated_logger):
        """Test filtering by tool name."""
        events = await populated_logger.query_events(tool_name="web_search")

        assert len(events) == 2
        assert all(e.tool_name == "web_search" for e in events)

    @pytest.mark.asyncio
    async def test_query_by_event_type(self, populated_logger):
        """Test filtering by event type."""
        events = await populated_logger.query_events(
            event_type=AuditEventType.TOOL_DENIED
        )

        assert len(events) == 1
        assert events[0].event_type == AuditEventType.TOOL_DENIED

    @pytest.mark.asyncio
    async def test_query_with_limit(self, populated_logger):
        """Test limiting results."""
        events = await populated_logger.query_events(limit=2)

        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_query_empty_log(self, temp_audit_dir):
        """Test querying empty log returns empty list."""
        logger = AuditLogger(audit_dir=temp_audit_dir)
        events = await logger.query_events()

        assert events == []


class TestAuditLoggerStats:
    """Tests for audit statistics."""

    @pytest.fixture
    def temp_audit_dir(self):
        """Create a temporary directory for audit logs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_stats_empty(self, temp_audit_dir):
        """Test stats for empty audit dir."""
        logger = AuditLogger(audit_dir=temp_audit_dir)
        stats = logger.get_stats()

        assert stats["tools_exists"] is False
        assert stats["ledger_exists"] is False

    @pytest.mark.asyncio
    async def test_stats_with_events(self, temp_audit_dir):
        """Test stats after logging events."""
        logger = AuditLogger(audit_dir=temp_audit_dir)

        await logger.log_tool_call(tool_name="test1", agent_id="agent:Test")
        await logger.log_tool_call(tool_name="test2", agent_id="agent:Test")

        stats = logger.get_stats()

        assert stats["tools_exists"] is True
        assert stats["ledger_exists"] is True
        assert stats["tools_lines"] == 2
        assert stats["ledger_lines"] == 2
        assert stats["tools_size_bytes"] > 0


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_get_audit_logger_singleton(self):
        """Test singleton logger retrieval."""
        logger1 = get_audit_logger()
        logger2 = get_audit_logger()

        assert logger1 is logger2

    @pytest.mark.asyncio
    async def test_record_tool_call(self, monkeypatch):
        """Test convenience function for recording tool calls."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Replace singleton with temp-dir logger
            temp_logger = AuditLogger(audit_dir=tmpdir)
            monkeypatch.setattr(
                "sos.observability.audit._audit_logger",
                temp_logger,
            )

            event = await record_tool_call(
                tool_name="test_tool",
                agent_id="agent:Test",
                input_params={"arg": "value"},
                output="result",
                duration_ms=100,
            )

            assert event.tool_name == "test_tool"
            assert event.duration_ms == 100


class TestConcurrency:
    """Tests for concurrent access."""

    @pytest.fixture
    def temp_audit_dir(self):
        """Create a temporary directory for audit logs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.mark.asyncio
    async def test_concurrent_writes(self, temp_audit_dir):
        """Test concurrent writes don't corrupt the log."""
        logger = AuditLogger(audit_dir=temp_audit_dir)

        async def write_events(agent_id: str, count: int):
            for i in range(count):
                await logger.log_tool_call(
                    tool_name=f"tool_{i}",
                    agent_id=agent_id,
                )

        # Write from multiple "agents" concurrently
        await asyncio.gather(
            write_events("agent:A", 10),
            write_events("agent:B", 10),
            write_events("agent:C", 10),
        )

        # Verify all events were written
        events = await logger.query_events(limit=100)
        assert len(events) == 30

        # Verify each line is valid JSON
        tools_file = Path(temp_audit_dir) / "tools.jsonl"
        with open(tools_file) as f:
            for line in f:
                json.loads(line)  # Should not raise


class TestPrivacy:
    """Tests for privacy-preserving features."""

    @pytest.fixture
    def temp_audit_dir(self):
        """Create a temporary directory for audit logs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.mark.asyncio
    async def test_large_data_truncated_in_preview(self, temp_audit_dir):
        """Test that large data is truncated in preview."""
        logger = AuditLogger(audit_dir=temp_audit_dir, max_preview_length=50)

        # Create input larger than preview length
        large_input = {
            "password": "super_secret_" + "x" * 100,
            "api_key": "sk_live_" + "y" * 100,
        }

        event = await logger.log_tool_call(
            tool_name="auth",
            agent_id="agent:Test",
            input_params=large_input,
        )

        # Read raw log
        tools_file = Path(temp_audit_dir) / "tools.jsonl"
        with open(tools_file) as f:
            raw = f.read()

        # Full secret strings should NOT appear (truncated)
        assert ("x" * 100) not in raw
        assert ("y" * 100) not in raw

        # But hash should exist for verification
        assert event.input_hash is not None
        assert event.input_hash in raw

        # Preview should be truncated
        assert event.input_preview.endswith("...")

    @pytest.mark.asyncio
    async def test_preview_truncation(self, temp_audit_dir):
        """Test that previews are truncated."""
        logger = AuditLogger(audit_dir=temp_audit_dir, max_preview_length=50)

        long_input = {"data": "x" * 200}

        event = await logger.log_tool_call(
            tool_name="test",
            agent_id="agent:Test",
            input_params=long_input,
        )

        assert len(event.input_preview) <= 53  # 50 + "..."
