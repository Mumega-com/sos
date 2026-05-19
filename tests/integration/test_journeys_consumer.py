"""Integration tests for the journeys bus consumer — XACK-based at-least-once.

Part of v0.9.1 W5: the journeys consumer migrated off in-memory LRU +
Redis checkpoint to an XREADGROUP + XACK consumer group. The contract
that migration has to honour is "no silent drops": if ``_handle_event``
raises, the entry must stay in the pending-entries list, and the
retry worker (W3) must eventually redeliver it so a restarted consumer
can process it.

Why we inject into ``_handle_event`` instead of the tracker: the
consumer deliberately swallows tracker-level exceptions inside
``_on_task_completed`` — a bad milestone record shouldn't block the
stream forever. What W5 cares about is the *tick-loop* invariant:
when ``_handle_event`` itself raises (e.g., Redis dropped mid-handler,
tracker-construction exploded), the tick must NOT XACK. That's the
path retry exists to rescue.

Flow:

1. register the journeys consumer group via the bus service (which
   also arms the retry worker);
2. XADD a task.completed envelope;
3. run ``consumer_A`` with ``_handle_event`` patched to raise — the
   tick catches it but leaves the entry unacked (still in PEL);
4. wait past the backoff, drive ``retry_worker.scan_once()`` — retry
   XACK's the original and re-XADDs the payload (visible as a new
   stream entry with ``__retry_count=1``);
5. run ``consumer_B`` with a real tracker — it consumes the
   redelivered entry and calls ``auto_evaluate`` with the original
   agent name. This is the restart-after-crash case.

If this breaks, it likely means the consumer is XACK'ing on the
exception path (silent drop) — the exact regression W5 exists to
prevent.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from sos.services.bus.redis_bus import RedisBusService
from sos.services.journeys.bus_consumer import JourneysBusConsumer


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_completed_fields(*, agent_addr: str, message_id: str) -> dict[str, str]:
    payload = {
        "task_id": f"t-{uuid.uuid4().hex[:8]}",
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
        "message_id": message_id,
        "payload": json.dumps(payload),
    }


class _RecordingTracker:
    """Tracker that records calls — stands in for the real JourneyTracker."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def auto_evaluate(self, agent: str) -> list[dict[str, Any]]:
        self.calls.append(agent)
        return []


async def test_crashed_consumer_message_redelivered_on_restart() -> None:
    """_handle_event exception → unacked → retry redelivers → restart processes."""
    service = RedisBusService()
    await service.connect()
    if not service.is_connected:
        pytest.skip("Redis not available")

    stream = f"sos:stream:global:squad:test-journeys-{uuid.uuid4().hex[:8]}"
    group = f"journeys-test-{uuid.uuid4().hex[:8]}"
    mid = str(uuid.uuid4())

    try:
        # Arm retry worker with 1s backoff so the test doesn't hang on
        # production cadence (30s/120s/600s).
        await service.register_consumer_group(
            stream,
            group,
            retry_scan_interval=60,
            retry_backoffs={1: 1, 2: 1, 3: 1},
        )

        await service.client.xadd(
            stream, _task_completed_fields(agent_addr="worker-xyz", message_id=mid)
        )

        # --- consumer_A: _handle_event patched to raise --------------------
        consumer_a = JourneysBusConsumer(
            redis_client=service.client,
            stream_patterns=[stream],
            consumer_name="consumer-a",
            group_name=group,
        )
        # Group already armed by service.register_consumer_group above;
        # skip consumer_a's own ensure_group.
        consumer_a._groups_registered.add(stream)

        crash_calls: list[str] = []

        async def _boom(stream: str, entry_id: str, fields: dict[str, str]) -> None:
            crash_calls.append(fields.get("message_id", ""))
            raise RuntimeError("simulated handler failure")

        consumer_a._handle_event = _boom  # type: ignore[assignment]
        await consumer_a._tick()

        # Handler raised. Tick must NOT XACK — entry stays in PEL.
        assert crash_calls == [mid]
        pending = await service.client.xpending_range(stream, group, min="-", max="+", count=10)
        assert len(pending) == 1, f"expected 1 unacked entry, got {pending}"

        # --- retry worker reclaims after backoff ---------------------------
        # Wait past backoff (1s + margin), then drive one scan cycle.
        await asyncio.sleep(1.2)
        await service._retry_worker.scan_once()

        # Retry re-XADDs the payload as a fresh stream entry and XACKs the
        # original. Stream now holds the original + retry copy.
        entries = await service.client.xrange(stream)
        assert len(entries) == 2, f"expected original + retry copy, got {entries}"
        retry_fields = entries[-1][1]
        assert retry_fields.get("__retry_count") == "1"
        assert retry_fields.get("message_id") == mid

        # --- consumer_B: restarted process, fresh in-memory seen set -------
        good_tracker = _RecordingTracker()
        consumer_b = JourneysBusConsumer(
            redis_client=service.client,
            stream_patterns=[stream],
            consumer_name="consumer-b",
            group_name=group,
            tracker=good_tracker,  # type: ignore[arg-type]
        )
        consumer_b._groups_registered.add(stream)
        await consumer_b._tick()

        # The redelivered envelope reached the real handler and was XACK'd.
        assert good_tracker.calls == ["worker-xyz"]
        pending_after = await service.client.xpending_range(
            stream, group, min="-", max="+", count=10
        )
        assert pending_after == [], f"expected empty PEL after ack, got {pending_after}"

    finally:
        await service.client.delete(stream)
        await service.disconnect()
