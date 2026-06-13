"""Integration tests for BrainService._on_task_created — scoring + enqueue + emit.

Exercises the Sprint 2 wiring end-to-end against an in-memory fakeredis:

    task.created → score_task → BrainState.enqueue → XADD task.scored

All five bus-consumer invariants stay intact (idempotency, checkpoints,
fail-open, SCAN discovery, replay tolerance).

G64b note: tasks must carry "project" in payload matching the stream suffix and
the active_projects.json registry. These tests use project="mumega" on the
sos:stream:global:squad:mumega stream, which is always in the active set.

Dispatch note: these tests exercise scoring/enqueue/emit only, not dispatch.
The registry client is stubbed empty so no agent is matched and tasks stay on
the queue, matching the Sprint 2 scope these tests were written for.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import fakeredis.aioredis
import pytest

from sos.contracts.messages import TaskScoredMessage, parse_message
from sos.services.brain import service as brain_service
from sos.services.brain.service import BrainService

_BRAIN_EMIT_STREAM = "sos:stream:global:squad:brain"
# G64b gate: stream suffix must match payload "project" and be in active_projects.json.
# "mumega" is always in the active set (real + safe-default). Stream key suffix == project.
_TASKS_STREAM = "sos:stream:global:squad:mumega"


def _patch_registry_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the registry to return no agents — keeps tasks on the queue.

    These tests are Sprint 2 (scoring/enqueue/emit). Dispatch is Sprint 3.
    An empty registry means select_agent returns None → tasks stay enqueued.
    """
    async def _no_agents():
        return []
    monkeypatch.setattr(brain_service._registry_client, "list_agents", _no_agents)


def _make_task_created_fields(
    task_id: str,
    *,
    message_id: str | None = None,
    priority: str | None = "medium",
    title: str = "do the thing",
) -> dict[str, str]:
    """Build redis XADD fields for a minimal v1 task.created envelope."""
    # G64b gate requires "project" in payload matching the stream suffix.
    payload: dict[str, object] = {"task_id": task_id, "title": title, "project": "mumega"}
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
async def test_task_created_enqueues_and_emits_scored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, fake = await _make_service()
    _patch_registry_empty(monkeypatch)
    await fake.xadd(
        _TASKS_STREAM,
        _make_task_created_fields("task-high-1", priority="high"),
    )

    await svc._tick()

    # State: one item on the priority queue (no agent matched → stays enqueued)
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
async def test_missing_priority_defaults_to_medium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, fake = await _make_service()
    _patch_registry_empty(monkeypatch)
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
async def test_scored_event_payload_roundtrips_through_parse_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, fake = await _make_service()
    _patch_registry_empty(monkeypatch)
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
async def test_duplicate_message_id_does_not_double_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, fake = await _make_service()
    _patch_registry_empty(monkeypatch)
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
