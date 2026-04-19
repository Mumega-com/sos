"""Tests for sos.services.journeys.bus_consumer — P0-05 decoupling.

Proves that squad → journeys now crosses the bus instead of an in-process
import. Seeds a v1 task.completed envelope onto a fake ``sos:stream:global:squad:*``
stream, runs the consumer's tick, and asserts that
``JourneyTracker.auto_evaluate`` was called with the expected agent name.
Idempotency: the same ``message_id`` delivered twice must trigger exactly
one call.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest

try:
    import fakeredis.aioredis as fake_aioredis  # type: ignore[import-untyped]

    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False

skipif_no_fakeredis = pytest.mark.skipif(not HAS_FAKEREDIS, reason="fakeredis not installed")


_STREAM = "sos:stream:global:squad:test-squad"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_task_completed_fields(
    *,
    task_id: str,
    agent_addr: str,
    message_id: str | None = None,
) -> dict[str, str]:
    payload = {
        "task_id": task_id,
        "status": "done",
        "completed_at": _now(),
        "result": {
            "agent_addr": agent_addr,
            "labels": ["wordpress"],
            "reward_mind": 1.0,
            "squad_id": "test-squad",
        },
    }
    return {
        "type": "task.completed",
        "source": f"agent:{agent_addr}",
        "target": "sos:channel:tasks",
        "timestamp": _now(),
        "version": "1.0",
        "message_id": message_id or str(uuid.uuid4()),
        "payload": json.dumps(payload),
    }


class _FakeTracker:
    """Minimal stand-in for JourneyTracker — records auto_evaluate calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def auto_evaluate(self, agent: str) -> list[dict]:
        self.calls.append(agent)
        return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@skipif_no_fakeredis
def test_task_completed_triggers_auto_evaluate() -> None:
    """Consumer calls JourneyTracker.auto_evaluate with the source agent name."""
    from sos.services.journeys.bus_consumer import JourneysBusConsumer

    async def _go() -> list[str]:
        r = fake_aioredis.FakeRedis(decode_responses=True)
        tracker = _FakeTracker()
        consumer = JourneysBusConsumer(
            redis_client=r,
            stream_patterns=[_STREAM],
            tracker=tracker,  # type: ignore[arg-type]
        )

        await r.xadd(
            _STREAM,
            _make_task_completed_fields(task_id="task-001", agent_addr="worker-a"),
        )
        await consumer._tick()
        return tracker.calls

    calls = asyncio.run(_go())
    assert calls == ["worker-a"], f"expected one call for worker-a, got {calls}"


@skipif_no_fakeredis
def test_duplicate_message_evaluates_once() -> None:
    """Same message_id delivered twice → auto_evaluate called once."""
    from sos.services.journeys.bus_consumer import JourneysBusConsumer

    async def _go() -> list[str]:
        r = fake_aioredis.FakeRedis(decode_responses=True)
        tracker = _FakeTracker()
        consumer = JourneysBusConsumer(
            redis_client=r,
            stream_patterns=[_STREAM],
            tracker=tracker,  # type: ignore[arg-type]
        )

        mid = str(uuid.uuid4())
        fields = _make_task_completed_fields(
            task_id="task-dupe",
            agent_addr="worker-b",
            message_id=mid,
        )
        await r.xadd(_STREAM, fields)
        await r.xadd(_STREAM, fields)

        await consumer._tick()
        await consumer._tick()
        return tracker.calls

    calls = asyncio.run(_go())
    assert calls == ["worker-b"], f"duplicate delivery must not re-run auto_evaluate; got {calls}"


@skipif_no_fakeredis
def test_system_source_is_skipped() -> None:
    """source=agent:system (or empty agent) never triggers auto_evaluate."""
    from sos.services.journeys.bus_consumer import JourneysBusConsumer

    async def _go() -> list[str]:
        r = fake_aioredis.FakeRedis(decode_responses=True)
        tracker = _FakeTracker()
        consumer = JourneysBusConsumer(
            redis_client=r,
            stream_patterns=[_STREAM],
            tracker=tracker,  # type: ignore[arg-type]
        )

        # Envelope with source=agent:system — payload result has no agent_addr.
        fields = {
            "type": "task.completed",
            "source": "agent:system",
            "target": "sos:channel:tasks",
            "timestamp": _now(),
            "version": "1.0",
            "message_id": str(uuid.uuid4()),
            "payload": json.dumps(
                {
                    "task_id": "t-sys",
                    "status": "done",
                    "completed_at": _now(),
                    "result": {
                        "labels": ["ops"],
                        "reward_mind": 0.0,
                        "squad_id": "test-squad",
                    },
                }
            ),
        }
        await r.xadd(_STREAM, fields)
        await consumer._tick()
        return tracker.calls

    calls = asyncio.run(_go())
    assert calls == [], f"system source must not trigger auto_evaluate; got {calls}"


@skipif_no_fakeredis
def test_falls_back_to_agent_addr_when_source_is_squad() -> None:
    """If source normalizes to agent:squad, consumer uses result.agent_addr."""
    from sos.services.journeys.bus_consumer import JourneysBusConsumer

    async def _go() -> list[str]:
        r = fake_aioredis.FakeRedis(decode_responses=True)
        tracker = _FakeTracker()
        consumer = JourneysBusConsumer(
            redis_client=r,
            stream_patterns=[_STREAM],
            tracker=tracker,  # type: ignore[arg-type]
        )

        fields = {
            "type": "task.completed",
            "source": "agent:squad",
            "target": "sos:channel:tasks",
            "timestamp": _now(),
            "version": "1.0",
            "message_id": str(uuid.uuid4()),
            "payload": json.dumps(
                {
                    "task_id": "t-fallback",
                    "status": "done",
                    "completed_at": _now(),
                    "result": {
                        "agent_addr": "Worker_Z",
                        "labels": ["video"],
                        "reward_mind": 2.0,
                        "squad_id": "test-squad",
                    },
                }
            ),
        }
        await r.xadd(_STREAM, fields)
        await consumer._tick()
        return tracker.calls

    calls = asyncio.run(_go())
    assert calls == ["Worker_Z"], f"expected fallback to agent_addr, got {calls}"
