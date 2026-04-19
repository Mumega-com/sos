"""Integration tests for retry worker and DLQ routing.

Tests cover message redelivery after backoff, exhausted retry routing to DLQ, and
clean worker lifecycle on disconnect. Part of W3 phase 2 bus stability, v0.9.1.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from sos.services.bus.redis_bus import RedisBusService


async def test_unacked_message_redelivered_after_backoff() -> None:
    """Verify that unacked messages are redelivered after backoff delay."""
    service = RedisBusService()
    await service.connect()
    if not service.is_connected:
        pytest.skip("Redis not available")

    stream = f"sos:test:retry:{uuid.uuid4().hex}"
    group = f"g-{uuid.uuid4().hex[:8]}"

    try:
        # Register with fast scan interval for testing and backoffs.
        await service.register_consumer_group(
            stream,
            group,
            retry_scan_interval=60,
            retry_backoffs={1: 1, 2: 1, 3: 1},
        )

        # Add initial message to stream.
        await service.client.xadd(stream, {"data": "first"})

        # Consumer claims message but doesn't ack (simulates crash).
        result = await service.client.xreadgroup(
            groupname=group,
            consumername="c1",
            streams={stream: ">"},
            count=1,
            block=100,
        )
        assert result is not None

        # Wait for backoff threshold to exceed (1s backoff + margin).
        await asyncio.sleep(1.2)

        # Manually trigger one scan cycle.
        await service._retry_worker.scan_once()

        # Verify stream now has 2 entries: original + retry copy.
        entries = await service.client.xrange(stream)
        assert len(entries) == 2

        # Verify retry copy has __retry_count=1 and original data.
        _, retry_fields = entries[1]
        assert retry_fields.get("__retry_count") == "1"
        assert retry_fields.get("data") == "first"

        # Verify original was XACKed (PEL empty).
        pending = await service.client.xpending(stream, group)
        assert pending["pending"] == 0

    finally:
        await service.client.delete(stream)
        await service.client.delete(f"sos:stream:dlq:{stream}")
        await service.disconnect()


async def test_exhausted_retries_route_to_dlq() -> None:
    """Verify that messages exceeding max retries are routed to DLQ."""
    service = RedisBusService()
    await service.connect()
    if not service.is_connected:
        pytest.skip("Redis not available")

    stream = f"sos:test:dlq:{uuid.uuid4().hex}"
    group = f"g-{uuid.uuid4().hex[:8]}"

    try:
        await service.register_consumer_group(
            stream,
            group,
            retry_scan_interval=60,
            retry_backoffs={1: 1, 2: 1, 3: 1},
        )

        # Add message and claim it (puts in PEL).
        await service.client.xadd(stream, {"data": "goodbye"})
        result = await service.client.xreadgroup(
            groupname=group,
            consumername="c1",
            streams={stream: ">"},
            count=1,
            block=100,
        )
        assert result is not None
        message_id = result[0][1][0][0]

        # Synthesize pending entry with times_delivered=4 (> max_retry=3).
        synthetic = {
            "message_id": message_id,
            "time_since_delivered": 999999,
            "times_delivered": 4,
            "consumer": "c1",
        }
        await service._retry_worker._route(stream, group, synthetic)

        # Verify DLQ has one entry with metadata.
        dlq_stream = f"sos:stream:dlq:{stream}"
        dlq_entries = await service.client.xrange(dlq_stream)
        assert len(dlq_entries) == 1

        _, dlq_fields = dlq_entries[0]
        assert dlq_fields["original_stream"] == stream
        assert dlq_fields["original_id"] == message_id
        assert dlq_fields["group"] == group
        assert dlq_fields["retry_count"] == "4"

        # Verify payload contains original data.
        payload = json.loads(dlq_fields["payload"])
        assert payload.get("data") == "goodbye"

        # Verify original was XACKed.
        pending = await service.client.xpending(stream, group)
        assert pending["pending"] == 0

    finally:
        await service.client.delete(stream)
        await service.client.delete(f"sos:stream:dlq:{stream}")
        await service.disconnect()


async def test_retry_worker_stops_cleanly_on_disconnect() -> None:
    """Verify retry worker stops cleanly on service disconnect."""
    service = RedisBusService()
    await service.connect()
    if not service.is_connected:
        pytest.skip("Redis not available")

    stream = f"sos:test:lifecycle:{uuid.uuid4().hex}"
    group = f"g-{uuid.uuid4().hex[:8]}"

    try:
        await service.register_consumer_group(
            stream,
            group,
            retry_scan_interval=60,
            retry_backoffs={1: 1},
        )

        worker = service._retry_worker
        assert worker is not None
        assert worker._running is True
        assert worker._task is not None

        await service.disconnect()

        # Worker fully stopped.
        assert worker._running is False
        assert worker._task is None

    finally:
        # disconnect already cleaned up resources.
        pass
