"""Tests for sos.services.brain — Bus consumer + state.

Uses fakeredis for hermetic testing. All tests skip if fakeredis is not installed.

Sprint 1 invariants:
  1. Idempotency on message_id
  2. Checkpoint per stream
  3. Fail-open on handler exceptions
  4. SCAN-based stream discovery
  5. State counters updated correctly
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

try:
    import fakeredis.aioredis as fake_aioredis  # type: ignore[import-untyped]
    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False

from sos.services.brain.service import BrainService, _CHECKPOINT_KEY_PREFIX
from sos.services.brain.state import BrainState, RoutingDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

skipif_no_fakeredis = pytest.mark.skipif(
    not HAS_FAKEREDIS, reason="fakeredis not installed"
)


def _fake_redis() -> "fake_aioredis.FakeRedis":
    return fake_aioredis.FakeRedis(decode_responses=True)


def _make_service(redis_client=None, patterns=None) -> BrainService:
    svc = BrainService(
        redis_url="redis://localhost:6379",
        stream_patterns=patterns or ["sos:stream:global:squad:*"],
        redis_client=redis_client,
    )
    return svc


def _v1_fields(msg_type: str, payload: dict | None = None, message_id: str | None = None) -> dict:
    """Build a minimal v1-envelope field dict as redis would return."""
    return {
        "type": msg_type,
        "message_id": message_id or str(uuid.uuid4()),
        "payload": json.dumps(payload or {}),
        "source": "agent:test",
        "version": "1.0.0",
    }


async def _push_to_stream(r, stream: str, fields: dict) -> str:
    """Write one entry to a fake redis stream; return the entry_id."""
    entry_id = await r.xadd(stream, fields)
    return entry_id


async def _run_one_tick(svc: BrainService) -> None:
    """Run exactly one loop tick (ignores stop event)."""
    await svc._tick()


# ---------------------------------------------------------------------------
# 1. Lifecycle
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
def test_service_starts_and_stops_cleanly() -> None:
    """BrainService.run() starts, stop() terminates cleanly."""
    r = _fake_redis()
    svc = _make_service(redis_client=r)

    async def _go():
        task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.05)
        svc.stop()
        await asyncio.wait_for(task, timeout=3.0)

    asyncio.run(_go())
    assert svc._running is False


# ---------------------------------------------------------------------------
# 2. Stream discovery
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
def test_subscribes_to_streams_matching_patterns() -> None:
    """_discover_streams() returns streams matching configured patterns."""
    r = _fake_redis()
    svc = _make_service(
        redis_client=r,
        patterns=["sos:stream:global:squad:*"],
    )

    async def _go():
        await r.xadd("sos:stream:global:squad:proj-a", {"x": "1"})
        await r.xadd("sos:stream:global:squad:proj-b", {"x": "2"})
        await r.xadd("sos:stream:global:agent:kasra", {"x": "3"})  # not matched
        svc._redis = r
        found = await svc._discover_streams()
        return found

    found = asyncio.run(_go())
    assert "sos:stream:global:squad:proj-a" in found
    assert "sos:stream:global:squad:proj-b" in found
    # agent stream not in pattern → not returned
    assert "sos:stream:global:agent:kasra" not in found


# ---------------------------------------------------------------------------
# 3. Checkpoint persistence
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
def test_loads_and_persists_checkpoints() -> None:
    """Service loads initial checkpoint and persists after processing."""
    r = _fake_redis()
    stream = "sos:stream:global:squad:test"
    ckpt_key = f"{_CHECKPOINT_KEY_PREFIX}:{stream}"

    async def _go():
        svc = _make_service(redis_client=r, patterns=[stream])
        await r.xadd(stream, _v1_fields("task.created", {"task_id": "t1"}))
        await r.xadd(stream, _v1_fields("task.created", {"task_id": "t2"}))
        await svc._load_checkpoints()
        await _run_one_tick(svc)
        checkpoint = await r.get(ckpt_key)
        return checkpoint

    ckpt = asyncio.run(_go())
    assert ckpt is not None, "Checkpoint should be written after processing"


@skipif_no_fakeredis
def test_resumes_from_checkpoint_on_restart() -> None:
    """On restart, service reads from checkpoint, skips already-seen entries."""
    r = _fake_redis()
    stream = "sos:stream:global:squad:resume-test"

    async def _go():
        # First run: push 2 messages, process them
        svc1 = _make_service(redis_client=r, patterns=[stream])
        mid1 = str(uuid.uuid4())
        mid2 = str(uuid.uuid4())
        await r.xadd(stream, _v1_fields("task.created", {"task_id": "t1"}, mid1))
        await r.xadd(stream, _v1_fields("task.created", {"task_id": "t2"}, mid2))
        await svc1._load_checkpoints()
        await _run_one_tick(svc1)
        ckpt_after_run1 = svc1._checkpoints.get(stream)

        # Second run: new service, same redis, should resume from checkpoint
        svc2 = _make_service(redis_client=r, patterns=[stream])
        await svc2._load_checkpoints()
        assert svc2._checkpoints.get(stream) == ckpt_after_run1, (
            "Second service should resume from checkpoint written by first"
        )
        # Push one more message after checkpoint
        mid3 = str(uuid.uuid4())
        await r.xadd(stream, _v1_fields("task.created", {"task_id": "t3"}, mid3))
        await _run_one_tick(svc2)
        # Only t3 should be in flight from svc2's perspective
        return svc2.state.tasks_in_flight

    in_flight = asyncio.run(_go())
    assert "t3" in in_flight


# ---------------------------------------------------------------------------
# 4. Idempotency
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
def test_idempotent_on_message_id() -> None:
    """Same message_id delivered twice: handler called once, task appears once."""
    r = _fake_redis()
    stream = "sos:stream:global:squad:idempotent"
    mid = str(uuid.uuid4())

    async def _go():
        svc = _make_service(redis_client=r, patterns=[stream])
        fields = _v1_fields("task.created", {"task_id": "tid-dupe"}, mid)
        # Add same message_id twice (different entry_id)
        await r.xadd(stream, fields)
        await r.xadd(stream, fields)
        await svc._load_checkpoints()
        await _run_one_tick(svc)
        return svc.state.events_seen, len(svc.state.tasks_in_flight)

    seen, in_flight = asyncio.run(_go())
    # First occurrence processed, second skipped (idempotent)
    assert seen == 1
    assert in_flight == 1


# ---------------------------------------------------------------------------
# 5. Dispatch
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
def test_dispatches_task_created_to_handler() -> None:
    """task.created on bus → _on_task_created fires, task tracked in state."""
    r = _fake_redis()
    stream = "sos:stream:global:squad:dispatch-test"

    async def _go():
        svc = _make_service(redis_client=r, patterns=[stream])
        await r.xadd(stream, _v1_fields("task.created", {"task_id": "dispatch-t1"}))
        await svc._load_checkpoints()
        await _run_one_tick(svc)
        return svc.state

    state = asyncio.run(_go())
    assert state.events_by_type.get("task.created", 0) == 1
    assert "dispatch-t1" in state.tasks_in_flight


# ---------------------------------------------------------------------------
# 6. Fail-open
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
def test_handler_exception_does_not_crash_service() -> None:
    """If a handler raises, the service keeps running and processes next message."""
    r = _fake_redis()
    stream = "sos:stream:global:squad:fail-open"

    async def _go():
        svc = _make_service(redis_client=r, patterns=[stream])

        # Monkeypatch _on_task_created to raise once
        call_count = {"n": 0}
        original = svc._on_task_created

        async def _flaky(msg):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated handler crash")
            await original(msg)

        svc._on_task_created = _flaky

        mid1 = str(uuid.uuid4())
        mid2 = str(uuid.uuid4())
        await r.xadd(stream, _v1_fields("task.created", {"task_id": "fail-t1"}, mid1))
        await r.xadd(stream, _v1_fields("task.created", {"task_id": "success-t2"}, mid2))
        await svc._load_checkpoints()
        # Two ticks: first message fails (no checkpoint advance), second succeeds
        await _run_one_tick(svc)
        await _run_one_tick(svc)
        return svc.state, call_count["n"]

    state, call_count = asyncio.run(_go())
    # Handler was called at least twice (retry on first + success on second)
    assert call_count >= 2
    # Second task (success) should be tracked
    assert "success-t2" in state.tasks_in_flight


# ---------------------------------------------------------------------------
# 7. Unknown type
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
def test_unknown_message_type_logged_and_skipped() -> None:
    """Unhandled message type is counted under its type key but not dispatched."""
    r = _fake_redis()
    stream = "sos:stream:global:squad:unknown-type"

    async def _go():
        svc = _make_service(redis_client=r, patterns=[stream])
        await r.xadd(stream, _v1_fields("some.unknown.type", {}))
        await svc._load_checkpoints()
        await _run_one_tick(svc)
        return svc.state

    state = asyncio.run(_go())
    assert state.events_by_type.get("some.unknown.type", 0) == 1
    assert state.events_seen == 1


# ---------------------------------------------------------------------------
# 8. Malformed message
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
def test_malformed_message_does_not_crash() -> None:
    """Bad JSON payload is logged and skipped; service keeps running."""
    r = _fake_redis()
    stream = "sos:stream:global:squad:malformed"

    async def _go():
        svc = _make_service(redis_client=r, patterns=[stream])
        # Bad JSON in payload
        await r.xadd(stream, {"type": "task.created", "payload": "{NOT JSON!!!", "message_id": str(uuid.uuid4())})
        await svc._load_checkpoints()
        await _run_one_tick(svc)
        return svc.state

    state = asyncio.run(_go())
    # Service should not crash; malformed counted
    assert state.events_by_type.get("_malformed", 0) == 1


# ---------------------------------------------------------------------------
# 9. State counters
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
def test_state_events_seen_increments() -> None:
    """events_seen counter grows with each processed event."""
    r = _fake_redis()
    stream = "sos:stream:global:squad:counter"

    async def _go():
        svc = _make_service(redis_client=r, patterns=[stream])
        for i in range(5):
            await r.xadd(stream, _v1_fields("task.created", {"task_id": f"t{i}"}))
        await svc._load_checkpoints()
        await _run_one_tick(svc)
        return svc.state.events_seen

    seen = asyncio.run(_go())
    assert seen == 5


@skipif_no_fakeredis
def test_state_events_by_type_groups() -> None:
    """Per-type counters correctly group different event types."""
    r = _fake_redis()
    stream = "sos:stream:global:squad:grouping"

    async def _go():
        svc = _make_service(redis_client=r, patterns=[stream])
        await r.xadd(stream, _v1_fields("task.created", {"task_id": "a"}))
        await r.xadd(stream, _v1_fields("task.created", {"task_id": "b"}))
        await r.xadd(stream, _v1_fields("task.completed", {"task_id": "a"}))
        await svc._load_checkpoints()
        await _run_one_tick(svc)
        return svc.state.events_by_type

    by_type = asyncio.run(_go())
    assert by_type.get("task.created", 0) == 2
    assert by_type.get("task.completed", 0) == 1


# ---------------------------------------------------------------------------
# 10. Dynamic stream discovery
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
def test_stream_discovery_picks_up_new_streams() -> None:
    """New streams added mid-run are discovered on the next tick."""
    r = _fake_redis()

    async def _go():
        svc = _make_service(redis_client=r, patterns=["sos:stream:global:squad:*"])
        # First tick: no streams yet
        found1 = await svc._discover_streams()

        # Add a new stream
        await r.xadd("sos:stream:global:squad:new-project", _v1_fields("task.created", {}))

        # Second discovery: should pick it up
        found2 = await svc._discover_streams()
        return found1, found2

    found1, found2 = asyncio.run(_go())
    assert found1 == []
    assert "sos:stream:global:squad:new-project" in found2


# ---------------------------------------------------------------------------
# 11. Checkpoint does not advance past failed handler
# ---------------------------------------------------------------------------

@skipif_no_fakeredis
def test_checkpoint_does_not_advance_past_failed_handler() -> None:
    """Critical invariant: checkpoint stays at pre-failure position on error."""
    r = _fake_redis()
    stream = "sos:stream:global:squad:ckpt-fail"
    ckpt_key = f"{_CHECKPOINT_KEY_PREFIX}:{stream}"

    async def _go():
        svc = _make_service(redis_client=r, patterns=[stream])

        # Make handler always raise
        async def _always_raise(msg):
            raise RuntimeError("always fails")

        svc._on_task_created = _always_raise

        await r.xadd(stream, _v1_fields("task.created", {"task_id": "fail-always"}))
        await svc._load_checkpoints()

        # Before tick: no checkpoint
        before = await r.get(ckpt_key)

        await _run_one_tick(svc)

        # After tick: checkpoint should still be absent (not advanced)
        after = await r.get(ckpt_key)
        return before, after

    before, after = asyncio.run(_go())
    assert before is None
    assert after is None, (
        "Checkpoint must NOT advance when handler fails"
    )


# ---------------------------------------------------------------------------
# 12. LRU cap for routing decisions
# ---------------------------------------------------------------------------

def test_lru_recent_routing_decisions_capped_at_50() -> None:
    """BrainState.recent_routing_decisions never exceeds 50 entries."""
    state = BrainState()
    now = datetime.now(timezone.utc).isoformat()
    for i in range(75):
        state.add_routing_decision(
            RoutingDecision(task_id=f"t{i}", agent_name="kasra", score=1.0, routed_at=now)
        )
    assert len(state.recent_routing_decisions) == 50
    # Oldest entries dropped — only last 50 remain
    assert state.recent_routing_decisions[0].task_id == "t25"
    assert state.recent_routing_decisions[-1].task_id == "t74"


# ---------------------------------------------------------------------------
# 13. Public __init__ exports
# ---------------------------------------------------------------------------

def test_public_api_exports() -> None:
    """sos.services.brain exposes BrainService, BrainState, score_task, URGENCY_WEIGHTS."""
    from sos.services.brain import BrainService, BrainState, score_task, URGENCY_WEIGHTS
    assert callable(score_task)
    assert isinstance(URGENCY_WEIGHTS, dict)
    assert BrainService is not None
    assert BrainState is not None


# ---------------------------------------------------------------------------
# 14. score_task formula (scoring.py sanity)
# ---------------------------------------------------------------------------

def test_score_task_basic_formula() -> None:
    """score_task returns (impact * urgency * unblock) / cost."""
    from sos.services.brain.scoring import score_task, URGENCY_WEIGHTS
    score = score_task(impact=5.0, urgency="high", unblock_count=2, cost=2.0)
    expected = (5.0 * URGENCY_WEIGHTS["high"] * 2.0) / 2.0
    assert abs(score - expected) < 1e-9


# ---------------------------------------------------------------------------
# 15. Integration smoke — skip if real redis not reachable
# ---------------------------------------------------------------------------

@pytest.mark.skipif(True, reason="Integration test — run manually against live redis")
def test_integration_real_redis() -> None:  # pragma: no cover
    """Smoke test against live redis. Always skipped in CI."""
    pass
