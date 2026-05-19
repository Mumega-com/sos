"""Integration tests for RedisBusService.ack() and ensure_group().

Tests W2 bus stability: ACK semantics, PEL tracking, and group lifecycle.
Phase 2 v0.9.1 release.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from sos.contracts.ports.bus import BusAck
from sos.services.bus.redis_bus import RedisBusService


@pytest.fixture
async def bus_service():
    """Create and connect a RedisBusService, skip if Redis unavailable."""
    service = RedisBusService()
    try:
        await service.connect()
        if not service.is_connected:
            pytest.skip("Redis not available")
    except Exception as e:
        pytest.skip(f"Redis not available: {e}")
    yield service
    await service.disconnect()


async def test_ack_after_process_clears_pending(bus_service: RedisBusService):
    """ACK removes message from PEL and returns BusAck with valid metadata."""
    stream = f"sos:test:ack:{uuid.uuid4().hex}"
    group = f"g-{uuid.uuid4().hex[:8]}"

    try:
        # Setup: create group and add message
        await bus_service.ensure_group(stream, group)
        await bus_service.client.xadd(stream, {"data": "test"})

        # Claim message via consumer c1
        result = await bus_service.client.xreadgroup(
            groupname=group,
            consumername="c1",
            streams={stream: ">"},
            count=1,
            block=100,
        )
        assert result, "xreadgroup should return a message"
        message_id = result[0][1][0][0]

        # Verify message is in PEL
        pending = await bus_service.client.xpending(stream, group)
        assert pending["pending"] == 1, "Message should be in PEL before ACK"

        # ACK the message
        ack_result = await bus_service.ack(stream, group, message_id, status="ok")

        # Verify BusAck structure
        assert isinstance(ack_result, BusAck)
        assert ack_result.message_id == message_id
        assert ack_result.status == "ok"
        # Parseable ISO-8601 — don't over-specify Z vs +00:00 here.
        parsed = datetime.fromisoformat(ack_result.acked_at)
        assert parsed.tzinfo is not None

        # Verify message cleared from PEL
        pending_after = await bus_service.client.xpending(stream, group)
        assert pending_after["pending"] == 0, "Message should be cleared from PEL after ACK"

    finally:
        await bus_service.client.delete(stream)


async def test_no_ack_leaves_message_in_pending(bus_service: RedisBusService):
    """Message remains in PEL if not ACKed."""
    stream = f"sos:test:nack:{uuid.uuid4().hex}"
    group = f"g-{uuid.uuid4().hex[:8]}"

    try:
        await bus_service.ensure_group(stream, group)
        await bus_service.client.xadd(stream, {"data": "test"})

        # Claim message
        result = await bus_service.client.xreadgroup(
            groupname=group,
            consumername="c1",
            streams={stream: ">"},
            count=1,
            block=100,
        )
        assert result

        # Do NOT call ack()
        pending = await bus_service.client.xpending(stream, group)
        assert pending["pending"] == 1, "Message should remain in PEL without ACK"
        # consumers shape differs across redis-py versions — list of
        # {name, pending} dicts in our version. Just assert c1 owns it.
        consumers = pending["consumers"]
        owner_names = [c["name"] for c in consumers]
        assert "c1" in owner_names, "c1 should own the pending message"

    finally:
        await bus_service.client.delete(stream)


async def test_nack_still_releases_pending_but_records_intent(
    bus_service: RedisBusService,
):
    """NACK XACKs the message but records nack intent for W3 re-enqueue."""
    stream = f"sos:test:dlq:{uuid.uuid4().hex}"
    group = f"g-{uuid.uuid4().hex[:8]}"

    try:
        await bus_service.ensure_group(stream, group)
        await bus_service.client.xadd(stream, {"data": "test"})

        # Claim message
        result = await bus_service.client.xreadgroup(
            groupname=group,
            consumername="c1",
            streams={stream: ">"},
            count=1,
            block=100,
        )
        message_id = result[0][1][0][0]

        # NACK the message
        ack_result = await bus_service.ack(stream, group, message_id, status="nack")

        # Verify BusAck records nack intent
        assert ack_result.status == "nack"
        assert ack_result.message_id == message_id

        # Verify message cleared from PEL (W2 semantics: XACK regardless of status)
        pending = await bus_service.client.xpending(stream, group)
        assert pending["pending"] == 0, "NACK should still XACK and clear PEL"

    finally:
        await bus_service.client.delete(stream)


async def test_ack_rejects_invalid_status(bus_service: RedisBusService):
    """Invalid status raises ValueError before Redis call."""
    with pytest.raises(ValueError, match="status"):
        await bus_service.ack("sos:test:invalid", "g-invalid", "0-0", status="bogus")  # type: ignore[arg-type]
