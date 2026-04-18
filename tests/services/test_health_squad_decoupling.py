"""Tests for sos.services.health.bus_consumer — P0-04 decoupling.

Proves that squad → health now crosses the bus instead of an in-process
import. Seeds a v1 task.completed envelope onto a fake ``sos:stream:global:squad:*``
stream, runs the consumer's tick, and asserts that ``_load_conductance()``
reflects the update. Also proves idempotency: consuming the same message
(same ``message_id``) twice does not double-increment the conductance.

Mirrors the fakeredis + tick pattern used by ``tests/brain/test_e2e_brain.py``.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

try:
    import fakeredis.aioredis as fake_aioredis  # type: ignore[import-untyped]
    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False

skipif_no_fakeredis = pytest.mark.skipif(
    not HAS_FAKEREDIS, reason="fakeredis not installed"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STREAM = "sos:stream:global:squad:test-squad"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_task_completed_fields(
    *,
    task_id: str,
    agent_addr: str,
    labels: list[str],
    reward_mind: float,
    message_id: str | None = None,
) -> dict[str, str]:
    """Build a v1 task.completed envelope as redis would return it."""
    payload = {
        "task_id": task_id,
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
        "message_id": message_id or str(uuid.uuid4()),
        "payload": json.dumps(payload),
    }


@pytest.fixture
def isolated_conductance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the conductance file to a tmp path so tests don't collide."""
    from sos.services.health import calcifer

    conductance_file = tmp_path / "conductance.json"
    monkeypatch.setattr(calcifer, "CONDUCTANCE_FILE", conductance_file)
    return conductance_file


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@skipif_no_fakeredis
def test_task_completed_updates_conductance(
    isolated_conductance: Path,
) -> None:
    """task.completed on the bus → conductance_update runs per label."""
    # Imported here so the monkeypatched module is picked up
    from sos.services.health import calcifer
    from sos.services.health.bus_consumer import HealthBusConsumer

    async def _go() -> dict[str, dict[str, float]]:
        r = fake_aioredis.FakeRedis(decode_responses=True)
        consumer = HealthBusConsumer(
            redis_client=r,
            stream_patterns=[_STREAM],
        )

        fields = _make_task_completed_fields(
            task_id="task-001",
            agent_addr="worker-a",
            labels=["wordpress", "seo"],
            reward_mind=10.0,
        )
        await r.xadd(_STREAM, fields)

        await consumer._load_checkpoints()
        await consumer._tick()
        return calcifer._load_conductance()

    G = asyncio.run(_go())
    assert "worker-a" in G
    assert G["worker-a"].get("wordpress", 0) > 0
    assert G["worker-a"].get("seo", 0) > 0


@skipif_no_fakeredis
def test_duplicate_message_does_not_double_increment(
    isolated_conductance: Path,
) -> None:
    """Consuming the same message_id twice must not double-update conductance."""
    from sos.services.health import calcifer
    from sos.services.health.bus_consumer import HealthBusConsumer

    async def _go() -> tuple[float, float]:
        r = fake_aioredis.FakeRedis(decode_responses=True)
        consumer = HealthBusConsumer(
            redis_client=r,
            stream_patterns=[_STREAM],
        )

        msg_id = str(uuid.uuid4())
        fields = _make_task_completed_fields(
            task_id="task-dupe",
            agent_addr="worker-b",
            labels=["seo"],
            reward_mind=7.0,
            message_id=msg_id,
        )
        # Same message_id published twice (different entry_id).
        await r.xadd(_STREAM, fields)
        await r.xadd(_STREAM, fields)

        await consumer._load_checkpoints()
        await consumer._tick()
        G_after_first = calcifer._load_conductance()
        seo_after_first = G_after_first.get("worker-b", {}).get("seo", 0.0)

        # Second tick picks up the duplicate — must not re-apply.
        await consumer._tick()
        G_after_second = calcifer._load_conductance()
        seo_after_second = G_after_second.get("worker-b", {}).get("seo", 0.0)

        return seo_after_first, seo_after_second

    first, second = asyncio.run(_go())
    assert first > 0, "First delivery should have updated conductance"
    assert first == second, (
        f"Duplicate delivery must not double-update conductance; "
        f"first={first} second={second}"
    )


@skipif_no_fakeredis
def test_zero_reward_skips_conductance_update(
    isolated_conductance: Path,
) -> None:
    """A task.completed with reward_mind == 0 is a no-op."""
    from sos.services.health import calcifer
    from sos.services.health.bus_consumer import HealthBusConsumer

    async def _go() -> dict[str, dict[str, float]]:
        r = fake_aioredis.FakeRedis(decode_responses=True)
        consumer = HealthBusConsumer(redis_client=r, stream_patterns=[_STREAM])
        fields = _make_task_completed_fields(
            task_id="task-zero",
            agent_addr="worker-c",
            labels=["outreach"],
            reward_mind=0.0,
        )
        await r.xadd(_STREAM, fields)
        await consumer._load_checkpoints()
        await consumer._tick()
        return calcifer._load_conductance()

    G = asyncio.run(_go())
    assert "worker-c" not in G or not G["worker-c"], (
        "Zero-reward completion must not create a conductance entry"
    )


@skipif_no_fakeredis
def test_malformed_payload_does_not_crash(
    isolated_conductance: Path,
) -> None:
    """Malformed JSON payload logs + skips, next message still processes."""
    from sos.services.health import calcifer
    from sos.services.health.bus_consumer import HealthBusConsumer

    async def _go() -> dict[str, dict[str, float]]:
        r = fake_aioredis.FakeRedis(decode_responses=True)
        consumer = HealthBusConsumer(redis_client=r, stream_patterns=[_STREAM])

        # Broken payload first
        await r.xadd(
            _STREAM,
            {
                "type": "task.completed",
                "source": "agent:worker-d",
                "timestamp": _now(),
                "version": "1.0",
                "message_id": str(uuid.uuid4()),
                "payload": "{not json",
            },
        )
        # Good payload after
        await r.xadd(
            _STREAM,
            _make_task_completed_fields(
                task_id="task-good",
                agent_addr="worker-d",
                labels=["video"],
                reward_mind=5.0,
            ),
        )
        await consumer._load_checkpoints()
        await consumer._tick()
        return calcifer._load_conductance()

    G = asyncio.run(_go())
    assert G.get("worker-d", {}).get("video", 0) > 0, (
        "Consumer must fail-open and still process the good message"
    )
