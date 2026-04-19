"""Integration tests for the DLQ schema + dashboard read helpers.

Covers the W4 slice: the retry worker writes DLQ entries using the
shared schema in :mod:`sos.services.bus.dlq`, and the read helpers
return them as typed :class:`DLQEntry` instances — verifying writer
and reader don't drift on field names.

Also exercises :func:`list_dlq_streams` so the dashboard index route
has coverage beyond the per-stream detail route.
"""

from __future__ import annotations

import uuid

import pytest

from sos.services.bus.dlq import (
    DLQ_STREAM_PREFIX,
    DLQEntry,
    dlq_stream_for,
    list_dlq_streams,
    read_dlq,
)
from sos.services.bus.redis_bus import RedisBusService


@pytest.fixture
async def bus_service():
    service = RedisBusService()
    try:
        await service.connect()
        if not service.is_connected:
            pytest.skip("Redis not available")
    except Exception as e:
        pytest.skip(f"Redis not available: {e}")
    yield service
    await service.disconnect()


async def test_retry_writer_produces_parseable_dlq_entry(bus_service: RedisBusService):
    """A DLQ entry written by the retry worker parses back into a DLQEntry."""
    stream = f"sos:test:dlqschema:{uuid.uuid4().hex}"
    group = f"g-{uuid.uuid4().hex[:8]}"

    try:
        await bus_service.register_consumer_group(
            stream,
            group,
            retry_scan_interval=60,
            retry_backoffs={1: 1, 2: 1, 3: 1},
        )

        # Seed a message and claim it so retry._to_dlq has something to read.
        await bus_service.client.xadd(stream, {"data": "payload-bytes", "k": "v"})
        result = await bus_service.client.xreadgroup(
            groupname=group,
            consumername="c1",
            streams={stream: ">"},
            count=1,
            block=100,
        )
        assert result is not None
        message_id = result[0][1][0][0]

        synthetic = {
            "message_id": message_id,
            "time_since_delivered": 999999,
            "times_delivered": 5,
            "consumer": "c1",
        }
        await bus_service._retry_worker._route(stream, group, synthetic)

        # Read back through the shared helper.
        entries = await read_dlq(bus_service.client, stream, limit=10)
        assert len(entries) == 1
        entry = entries[0]
        assert isinstance(entry, DLQEntry)
        assert entry.original_stream == stream
        assert entry.original_id == message_id
        assert entry.group == group
        assert entry.retry_count == 5
        assert entry.dlq_stream == dlq_stream_for(stream)
        assert entry.payload.get("data") == "payload-bytes"
        assert entry.payload.get("k") == "v"

    finally:
        await bus_service.client.delete(stream)
        await bus_service.client.delete(dlq_stream_for(stream))


async def test_read_dlq_missing_stream_returns_empty(bus_service: RedisBusService):
    """Reading a DLQ that doesn't exist returns [], not an exception."""
    never_wrote = f"sos:test:never:{uuid.uuid4().hex}"
    entries = await read_dlq(bus_service.client, never_wrote, limit=10)
    assert entries == []


async def test_list_dlq_streams_surfaces_written_streams(
    bus_service: RedisBusService,
):
    """list_dlq_streams returns the ORIGINAL stream names, not DLQ keys."""
    marker = uuid.uuid4().hex[:8]
    stream_a = f"sos:test:list:a:{marker}"
    stream_b = f"sos:test:list:b:{marker}"

    try:
        # Write one entry to each DLQ stream directly.
        await bus_service.client.xadd(
            dlq_stream_for(stream_a),
            {
                "original_stream": stream_a,
                "original_id": "0-0",
                "group": "g",
                "retry_count": "4",
                "payload": "{}",
            },
        )
        await bus_service.client.xadd(
            dlq_stream_for(stream_b),
            {
                "original_stream": stream_b,
                "original_id": "0-0",
                "group": "g",
                "retry_count": "4",
                "payload": "{}",
            },
        )

        streams = await list_dlq_streams(bus_service.client)
        # Filter to just our test run's marker so we don't trip over
        # leftover state from other tests or prior runs.
        our = [s for s in streams if marker in s]
        assert stream_a in our
        assert stream_b in our
        # Result is original stream name, not the DLQ key.
        for s in our:
            assert not s.startswith(DLQ_STREAM_PREFIX)

    finally:
        await bus_service.client.delete(dlq_stream_for(stream_a))
        await bus_service.client.delete(dlq_stream_for(stream_b))
