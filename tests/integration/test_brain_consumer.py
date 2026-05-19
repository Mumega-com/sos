"""Integration tests for the brain bus consumer — XACK-based at-least-once.

Part of v0.9.1 W6: BrainService migrated from XREAD + checkpoint to
XREADGROUP + XACK. The contract is "no silent drops": if ``_handle_event``
raises, the entry must stay in the pending-entries list (PEL), and the
retry worker (W3) must eventually redeliver it so a restarted consumer
can process it.

Why we inject into ``_handle_event`` instead of a domain handler: brain's
``_on_task_completed`` is deliberately lightweight (log + discard). What
W6 cares about is the *tick-loop* invariant: when ``_handle_event`` itself
raises, the tick must NOT XACK. That's the path retry exists to rescue.

Flow:

1. Register the brain consumer group via the bus service (which also
   arms the retry worker with short backoffs for the test);
2. XADD a ``task.completed`` envelope (lightweight — no scoring side-effects);
3. Run ``consumer_a`` with ``_handle_event`` patched to raise — tick must
   NOT XACK; entry stays in PEL;
4. Sleep past the backoff, call ``service._retry_worker.scan_once()`` —
   retry XACKs the original and re-XADDs with ``__retry_count=1``;
5. Run ``consumer_b`` (fresh BrainService instance) — it consumes the
   redelivered entry, handler runs, state counter updated, PEL empty.

Needs real Redis (``MUMEGA_REDIS_URL`` env var). Skips if unavailable.
Must run with ``pytest-asyncio`` in auto mode.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest

from sos.services.bus.redis_bus import RedisBusService
from sos.services.brain.service import BrainService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_completed_fields(*, task_id: str, message_id: str) -> dict[str, str]:
    payload = {
        "task_id": task_id,
        "status": "done",
        "completed_at": _now(),
    }
    return {
        "type": "task.completed",
        "source": "agent:test-worker",
        "target": "sos:channel:tasks",
        "timestamp": _now(),
        "version": "1.0",
        "message_id": message_id,
        "payload": json.dumps(payload),
    }


async def test_crashed_consumer_message_redelivered_on_restart() -> None:
    """_handle_event exception → unacked → retry redelivers → restart processes."""
    service = RedisBusService()
    await service.connect()
    if not service.is_connected:
        pytest.skip("Redis not available")

    stream = f"sos:stream:global:squad:test-brain-{uuid.uuid4().hex[:8]}"
    group = f"brain-test-{uuid.uuid4().hex[:8]}"
    task_id = f"t-{uuid.uuid4().hex[:8]}"
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

        await service.client.xadd(stream, _task_completed_fields(task_id=task_id, message_id=mid))

        # --- consumer_a: _handle_event patched to raise --------------------
        consumer_a = BrainService(
            redis_client=service.client,
            stream_patterns=[stream],
            consumer_name="consumer-a",
            group_name=group,
        )
        # Group already armed by service.register_consumer_group above.
        consumer_a._groups_registered.add(stream)

        crash_calls: list[str] = []

        async def _boom(stream_name: str, entry_id: str, fields: dict[str, str]) -> None:
            crash_calls.append(fields.get("message_id", ""))
            raise RuntimeError("simulated brain handler failure")

        consumer_a._handle_event = _boom  # type: ignore[assignment]
        await consumer_a._tick()

        # Handler raised. Tick must NOT XACK — entry stays in PEL.
        assert crash_calls == [mid], f"expected handler called with {mid!r}, got {crash_calls}"
        pending = await service.client.xpending_range(stream, group, min="-", max="+", count=10)
        assert len(pending) == 1, f"expected 1 unacked entry in PEL, got {pending}"

        # --- retry worker reclaims after backoff ---------------------------
        # Wait past backoff (1s + margin), then drive one scan cycle.
        await asyncio.sleep(1.2)
        await service._retry_worker.scan_once()

        # Retry re-XADDs the payload as a fresh stream entry and XACKs the
        # original. Stream now holds the original + retry copy.
        entries = await service.client.xrange(stream)
        assert len(entries) == 2, f"expected original + retry copy, got {entries}"
        retry_fields = entries[-1][1]
        assert (
            retry_fields.get("__retry_count") == "1"
        ), f"expected __retry_count=1, got {retry_fields.get('__retry_count')}"
        assert (
            retry_fields.get("message_id") == mid
        ), f"expected message_id={mid!r}, got {retry_fields.get('message_id')}"

        # --- consumer_b: restarted process, fresh in-memory seen set -------
        consumer_b = BrainService(
            redis_client=service.client,
            stream_patterns=[stream],
            consumer_name="consumer-b",
            group_name=group,
        )
        consumer_b._groups_registered.add(stream)
        await consumer_b._tick()

        # The redelivered envelope reached the real handler.
        # task.completed → _on_task_completed → state.tasks_in_flight.discard
        # events_seen should be 1 (one successful dispatch).
        assert (
            consumer_b.state.events_seen == 1
        ), f"expected 1 event processed by consumer_b, got {consumer_b.state.events_seen}"
        assert consumer_b.state.events_by_type.get("task.completed", 0) == 1

        # PEL should be empty after consumer_b ACKs the retry entry.
        pending_after = await service.client.xpending_range(
            stream, group, min="-", max="+", count=10
        )
        assert pending_after == [], f"expected empty PEL after ack, got {pending_after}"

    finally:
        await service.client.delete(stream)
        await service.disconnect()
