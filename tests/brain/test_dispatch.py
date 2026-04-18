"""Integration tests for BrainService dispatch — skill-matrix routing.

Exercises Sprint 3 wiring: after scoring, the Brain attempts to match the
highest-score queued task against the registered agents. On a skill match
it emits ``task.routed`` on the brain stream and moves the task into
in-flight state. With no match it leaves the task on the queue unchanged.

All five bus-consumer invariants stay intact.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import fakeredis.aioredis
import pytest

from sos.contracts.messages import TaskRoutedMessage, parse_message
from sos.kernel.identity import AgentIdentity
from sos.services.brain import service as brain_service
from sos.services.brain.service import BrainService

_BRAIN_EMIT_STREAM = "sos:stream:global:squad:brain"
_TASKS_STREAM = "sos:stream:global:squad:tasks"
_AGENTS_STREAM = "sos:stream:global:agent:events"


def _agent(name: str, caps: list[str]) -> AgentIdentity:
    """Build an AgentIdentity with the given capabilities."""
    a = AgentIdentity(name=name)
    a.capabilities.extend(caps)
    return a


def _make_task_created_fields(
    task_id: str,
    *,
    message_id: str | None = None,
    priority: str = "high",
    labels: list[str] | None = None,
    skill_id: str | None = None,
    title: str = "do the thing",
) -> dict[str, str]:
    """Build redis XADD fields for a minimal v1 task.created envelope."""
    payload: dict[str, object] = {
        "task_id": task_id,
        "title": title,
        "priority": priority,
    }
    if labels is not None:
        payload["labels"] = labels
    if skill_id is not None:
        payload["skill_id"] = skill_id
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


def _make_agent_joined_fields(
    agent_name: str,
    *,
    message_id: str | None = None,
) -> dict[str, str]:
    """Build redis XADD fields for a v1 agent_joined envelope."""
    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, object] = {
        "agent_name": agent_name,
        "joined_at": now,
    }
    envelope: dict[str, str] = {
        "type": "agent_joined",
        "source": "agent:kernel",
        "target": "sos:channel:system:events",
        "timestamp": now,
        "version": "1.0",
        "message_id": message_id or str(uuid.uuid4()),
        "payload": json.dumps(payload),
    }
    return envelope


async def _make_service() -> tuple[BrainService, fakeredis.aioredis.FakeRedis]:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    svc = BrainService(
        redis_client=fake,
        stream_patterns=[
            "sos:stream:global:squad:*",
            "sos:stream:global:agent:*",
        ],
    )
    return svc, fake


async def _emitted_routed(fake: fakeredis.aioredis.FakeRedis) -> list[dict[str, str]]:
    """Return all task.routed entries emitted on the brain stream."""
    entries = await fake.xrange(_BRAIN_EMIT_STREAM)
    return [fields for _eid, fields in entries if fields.get("type") == "task.routed"]


@pytest.mark.asyncio
async def test_task_created_dispatches_when_matching_agent_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, fake = await _make_service()
    monkeypatch.setattr(
        brain_service,
        "registry_read_all",
        lambda: [_agent("hermes", ["wordpress"])],
    )

    await fake.xadd(
        _TASKS_STREAM,
        _make_task_created_fields(
            "task-wp-1", priority="high", labels=["wordpress"]
        ),
    )
    await svc._tick()

    routed = await _emitted_routed(fake)
    assert len(routed) == 1, "expected exactly one task.routed emitted"
    assert svc.state.queue_size() == 0, "queue should drain after dispatch"


@pytest.mark.asyncio
async def test_task_created_no_match_keeps_in_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, fake = await _make_service()
    monkeypatch.setattr(
        brain_service,
        "registry_read_all",
        lambda: [_agent("rusty", ["rust"])],
    )

    await fake.xadd(
        _TASKS_STREAM,
        _make_task_created_fields(
            "task-nomatch", priority="high", labels=["kubernetes"]
        ),
    )
    await svc._tick()

    assert svc.state.queue_size() == 1, "task should remain on the queue"
    routed = await _emitted_routed(fake)
    assert routed == [], "no task.routed event should be emitted"


@pytest.mark.asyncio
async def test_agent_joined_triggers_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, fake = await _make_service()

    # Phase 1 — registry empty; both tasks queue up with no routing.
    monkeypatch.setattr(brain_service, "registry_read_all", lambda: [])
    await fake.xadd(
        _TASKS_STREAM,
        _make_task_created_fields(
            "task-wp-a", priority="high", labels=["wordpress"]
        ),
    )
    await fake.xadd(
        _TASKS_STREAM,
        _make_task_created_fields(
            "task-wp-b", priority="medium", labels=["wordpress"]
        ),
    )
    await svc._tick()
    assert svc.state.queue_size() == 2
    assert await _emitted_routed(fake) == []

    # Phase 2 — hermes registers; agent_joined triggers drain.
    monkeypatch.setattr(
        brain_service,
        "registry_read_all",
        lambda: [_agent("hermes", ["wordpress"])],
    )
    await fake.xadd(_AGENTS_STREAM, _make_agent_joined_fields("hermes"))
    await svc._tick()

    routed = await _emitted_routed(fake)
    assert len(routed) >= 1, "agent_joined should dispatch at least one task"
    assert svc.state.queue_size() <= 1, "queue should be drained to 1 or 0"


@pytest.mark.asyncio
async def test_routed_payload_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, fake = await _make_service()
    monkeypatch.setattr(
        brain_service,
        "registry_read_all",
        lambda: [_agent("hermes", ["wordpress"])],
    )

    await fake.xadd(
        _TASKS_STREAM,
        _make_task_created_fields(
            "task-valid", priority="high", labels=["wordpress"]
        ),
    )
    await svc._tick()

    routed = await _emitted_routed(fake)
    assert len(routed) == 1

    # Reconstruct the shape parse_message expects (payload as dict).
    fields = routed[0]
    rebuilt: dict[str, object] = dict(fields)
    rebuilt["payload"] = json.loads(fields["payload"])

    parsed = parse_message(rebuilt)
    assert isinstance(parsed, TaskRoutedMessage)
    assert parsed.payload.routed_to == "hermes"


@pytest.mark.asyncio
async def test_routing_decision_recorded_in_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, fake = await _make_service()
    monkeypatch.setattr(
        brain_service,
        "registry_read_all",
        lambda: [_agent("hermes", ["wordpress"])],
    )

    await fake.xadd(
        _TASKS_STREAM,
        _make_task_created_fields(
            "task-recorded", priority="high", labels=["wordpress"]
        ),
    )
    await svc._tick()

    assert len(svc.state.recent_routing_decisions) >= 1
    decision = svc.state.recent_routing_decisions[-1]
    assert decision.agent_name == "hermes"
    assert decision.task_id == "task-recorded"
