"""Integration tests for BrainService._on_task_created — scoring + enqueue + emit.

Exercises the Sprint 2 wiring end-to-end against an in-memory fakeredis:

    task.created → score_task → BrainState.enqueue → XADD task.scored

All five bus-consumer invariants stay intact (idempotency, checkpoints,
fail-open, SCAN discovery, replay tolerance).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import fakeredis.aioredis
import pytest

from sos.contracts.messages import TaskScoredMessage, parse_message
from sos.services.brain.service import BrainService

_BRAIN_EMIT_STREAM = "sos:stream:global:squad:brain"
_TASKS_STREAM = "sos:stream:global:squad:tasks"


def _make_task_created_fields(
    task_id: str,
    *,
    message_id: str | None = None,
    priority: str | None = "medium",
    title: str = "do the thing",
) -> dict[str, str]:
    """Build redis XADD fields for a minimal v1 task.created envelope."""
    payload: dict[str, object] = {"task_id": task_id, "title": title}
    if priority is not None:
        payload["priority"] = priority
    envelope: dict[str, str] = {
        "type": "task.created",
        "source": "agent:squad",
        "target": "sos:channel:tasks",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0",
        "message_id": message_id or str(uuid.uuid4()),
        "payload": json.dumps(payload),
    }
    return envelope


async def _make_service() -> tuple[BrainService, fakeredis.aioredis.FakeRedis]:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    svc = BrainService(
        redis_client=fake,
        stream_patterns=["sos:stream:global:squad:*"],
    )
    return svc, fake


@pytest.mark.asyncio
async def test_task_created_enqueues_and_emits_scored() -> None:
    svc, fake = await _make_service()
    await fake.xadd(
        _TASKS_STREAM,
        _make_task_created_fields("task-high-1", priority="high"),
    )

    await svc._tick()

    # State: one item on the priority queue
    assert svc.state.queue_size() == 1

    # Emission: one entry on the brain stream with type=task.scored
    assert await fake.xlen(_BRAIN_EMIT_STREAM) == 1
    entries = await fake.xrange(_BRAIN_EMIT_STREAM)
    _entry_id, fields = entries[0]
    assert fields["type"] == "task.scored"
    assert fields["source"] == "agent:brain"
    assert fields["target"] == "sos:channel:tasks"

    payload = json.loads(fields["payload"])
    assert payload["task_id"] == "task-high-1"
    assert payload["urgency"] == "high"
    assert payload["score"] > 0


@pytest.mark.asyncio
async def test_missing_priority_defaults_to_medium() -> None:
    svc, fake = await _make_service()
    await fake.xadd(
        _TASKS_STREAM,
        _make_task_created_fields("task-no-priority", priority=None),
    )

    await svc._tick()

    assert await fake.xlen(_BRAIN_EMIT_STREAM) == 1
    entries = await fake.xrange(_BRAIN_EMIT_STREAM)
    _entry_id, fields = entries[0]
    payload = json.loads(fields["payload"])
    assert payload["urgency"] == "medium"


@pytest.mark.asyncio
async def test_scored_event_payload_roundtrips_through_parse_message() -> None:
    svc, fake = await _make_service()
    await fake.xadd(
        _TASKS_STREAM,
        _make_task_created_fields("task-roundtrip", priority="critical"),
    )

    await svc._tick()

    entries = await fake.xrange(_BRAIN_EMIT_STREAM)
    _entry_id, fields = entries[0]

    # Rebuild the dict shape parse_message expects (payload as dict, not JSON str)
    rebuilt: dict[str, object] = dict(fields)
    rebuilt["payload"] = json.loads(fields["payload"])

    parsed = parse_message(rebuilt)
    assert isinstance(parsed, TaskScoredMessage)
    assert parsed.payload.task_id == "task-roundtrip"
    assert parsed.payload.urgency == "critical"
    assert parsed.source == "agent:brain"


@pytest.mark.asyncio
async def test_duplicate_message_id_does_not_double_enqueue() -> None:
    svc, fake = await _make_service()
    shared_id = str(uuid.uuid4())
    fields = _make_task_created_fields(
        "task-dup", message_id=shared_id, priority="medium"
    )

    await fake.xadd(_TASKS_STREAM, fields)
    await svc._tick()

    # Re-seed the same logical message (same message_id) and tick again
    await fake.xadd(_TASKS_STREAM, fields)
    await svc._tick()

    assert svc.state.queue_size() == 1
    # And only one scored event was emitted (idempotency held)
    assert await fake.xlen(_BRAIN_EMIT_STREAM) == 1
