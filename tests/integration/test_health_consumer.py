"""Integration tests for the health bus consumer — XACK-based at-least-once.

Part of v0.9.1 W6: the health consumer migrated off XREAD + per-stream Redis
checkpoints + in-memory LRU to an XREADGROUP + XACK consumer group. The
contract that migration has to honour is "no silent drops": if
``_handle_event`` raises, the entry must stay in the pending-entries list, and
the retry worker (W3) must eventually redeliver it so a restarted consumer
can process it.

Why we inject into ``_handle_event`` instead of conductance_update: the
consumer deliberately lets conductance-write exceptions propagate up through
``_on_task_completed`` to ``_handle_event`` — a disk-write failure should
leave the entry unacked so it can be retried. What W6 cares about is the
*tick-loop* invariant: when ``_handle_event`` itself raises, the tick must
NOT XACK. That's the path retry exists to rescue.

Flow:

1. register the health consumer group via the bus service (which also
   arms the retry worker);
2. XADD a task.completed envelope with agent_addr + labels + reward_mind;
3. run ``consumer_a`` with ``_handle_event`` patched to raise — the tick
   catches it but leaves the entry unacked (still in PEL);
4. wait past the backoff, drive ``retry_worker.scan_once()`` — retry
   XACKs the original and re-XADDs the payload (visible as a new stream
   entry with ``__retry_count=1``);
5. run ``consumer_b`` with an isolated conductance file — it consumes the
   redelivered entry and calls ``conductance_update`` for the agent + label.
   Assert conductance was updated. Assert PEL is empty.

If this breaks, it likely means the consumer is XACK'ing on the exception
path (silent drop) — the exact regression W6 exists to prevent.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sos.services.bus.redis_bus import RedisBusService
from sos.services.health.bus_consumer import HealthBusConsumer


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_completed_fields(
    *, agent_addr: str, labels: list[str], reward_mind: float, message_id: str
) -> dict[str, str]:
    payload = {
        "task_id": f"t-{uuid.uuid4().hex[:8]}",
        "status": "done",
        "completed_at": _now(),
        "result": {
            "agent_addr": agent_addr,
            "labels": labels,
            "reward_mind": reward_mind,
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


async def test_crashed_consumer_message_redelivered_on_restart(
    tmp_path: Path,
) -> None:
    """_handle_event exception → unacked → retry redelivers → restart processes conductance."""
    service = RedisBusService()
    await service.connect()
    if not service.is_connected:
        pytest.skip("Redis not available")

    stream = f"sos:stream:global:squad:test-health-{uuid.uuid4().hex[:8]}"
    group = f"health-test-{uuid.uuid4().hex[:8]}"
    mid = str(uuid.uuid4())

    # Redirect conductance file to a tmp path so the test doesn't collide.
    from sos.services.health import calcifer
    from sos.kernel import conductance as conductance_mod

    conductance_file = tmp_path / "conductance.json"
    original_calcifer_file = calcifer.CONDUCTANCE_FILE
    original_conductance_file = conductance_mod.CONDUCTANCE_FILE
    calcifer.CONDUCTANCE_FILE = conductance_file
    conductance_mod.CONDUCTANCE_FILE = conductance_file

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
            stream,
            _task_completed_fields(
                agent_addr="worker-xyz",
                labels=["wordpress"],
                reward_mind=1.0,
                message_id=mid,
            ),
        )

        # --- consumer_a: _handle_event patched to raise -------------------
        consumer_a = HealthBusConsumer(
            redis_client=service.client,
            stream_patterns=[stream],
            consumer_name="consumer-a",
            group_name=group,
        )
        # Group already armed by service.register_consumer_group above;
        # skip consumer_a's own ensure_group.
        consumer_a._groups_registered.add(stream)

        crash_calls: list[str] = []

        async def _boom(stream: str, entry_id: str, fields: dict) -> None:
            crash_calls.append(fields.get("message_id", ""))
            raise RuntimeError("simulated handler failure")

        consumer_a._handle_event = _boom  # type: ignore[assignment]
        await consumer_a._tick()

        # Handler raised. Tick must NOT XACK — entry stays in PEL.
        assert crash_calls == [mid]
        pending = await service.client.xpending_range(stream, group, min="-", max="+", count=10)
        assert len(pending) == 1, f"expected 1 unacked entry, got {pending}"

        # --- retry worker reclaims after backoff --------------------------
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

        # --- consumer_b: restarted process, fresh in-memory seen set ------
        consumer_b = HealthBusConsumer(
            redis_client=service.client,
            stream_patterns=[stream],
            consumer_name="consumer-b",
            group_name=group,
        )
        consumer_b._groups_registered.add(stream)
        await consumer_b._tick()

        # The redelivered envelope must have updated conductance.
        G = conductance_mod._load_conductance()
        assert "worker-xyz" in G, f"expected worker-xyz in conductance, got {G}"
        assert (
            G["worker-xyz"].get("wordpress", 0) > 0
        ), f"expected wordpress conductance > 0, got {G['worker-xyz']}"

        # PEL must be empty — consumer_b XACKed the retry entry.
        pending_after = await service.client.xpending_range(
            stream, group, min="-", max="+", count=10
        )
        assert pending_after == [], f"expected empty PEL after ack, got {pending_after}"

    finally:
        calcifer.CONDUCTANCE_FILE = original_calcifer_file
        conductance_mod.CONDUCTANCE_FILE = original_conductance_file
        await service.client.delete(stream)
        await service.disconnect()
