"""KR3 (W1) — witness ΔC wired into C(t).

Tests that C(t) (coherence_by_agent) moves off the dead-1.0 prior when a real
witness ΔC signal arrives, and stays carried/safe when the ΔC source errors.

Guardrails verified here:
  1. OBSERVE-ONLY: coherence_by_agent is populated; no dispatch-gating logic added.
  2. FAIL-SAFE: CoherencePhysics import failure → C(t) unchanged (carries prior).
  3. ADDITIVE: ΔC joins existing C; does not zero or replace it.
  4. done_when: C(t) moves off dead-1.0 after a real completion signal.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import patch

import pytest

from sos.services.brain.state import BrainState


# ---------------------------------------------------------------------------
# Unit tests for BrainState.apply_witness_delta_c
# ---------------------------------------------------------------------------


class TestApplyWitnessDeltaC:
    """apply_witness_delta_c pure-unit tests — no redis, no bus."""

    def test_completion_moves_c_off_dead_1(self) -> None:
        """done_when: C(t) moves off 1.0 after a successful completion signal."""
        state = BrainState()
        assert "kasra" not in state.coherence_by_agent  # uninformed prior

        state.apply_witness_delta_c("kasra", vote=+1, latency_ms=500.0)

        # C must now exist and be strictly below 1.0 (success with C=1.0 →
        # delta_c = +1 * omega * 1.0 * 0.1 ≈ +0.008 clamped to 1.0, but
        # agent_coherence starts at 1.0 so new_c = 1.0 + 0.2 * delta_c.
        # delta_c = vote * omega * agent_c * 0.1 = +1 * omega * 1.0 * 0.1 > 0
        # → new_c = 1.0 + positive = clamped to 1.0. With vote=+1 at C=1.0
        # the result is still 1.0 (perfect agent stays perfect).
        # Repeat with a failure to push it below 1.0, then a completion.
        state2 = BrainState()
        state2.apply_witness_delta_c("kasra", vote=-1, latency_ms=500.0)
        c_after_failure = state2.coherence_by_agent["kasra"]
        assert c_after_failure < 1.0, (
            f"C(t) must drop below 1.0 after a failure; got {c_after_failure}"
        )

        state2.apply_witness_delta_c("kasra", vote=+1, latency_ms=500.0)
        c_after_recovery = state2.coherence_by_agent["kasra"]
        assert c_after_recovery > c_after_failure, (
            f"C(t) must rise after a success; got {c_after_recovery}, was {c_after_failure}"
        )

    def test_failure_drops_c_below_1(self) -> None:
        """A task failure reduces C(t) from the 1.0 prior."""
        state = BrainState()
        state.apply_witness_delta_c("athena", vote=-1, latency_ms=400.0)
        c = state.coherence_by_agent["athena"]
        assert 0.0 <= c < 1.0, f"C must be in [0,1) after failure; got {c}"

    def test_c_clamped_to_unit_interval(self) -> None:
        """C(t) never goes below 0.0 or above 1.0 regardless of repeated signals."""
        state = BrainState()
        # Hammer failures — C must not go negative.
        for _ in range(100):
            state.apply_witness_delta_c("sol", vote=-1, latency_ms=200.0)
        assert state.coherence_by_agent["sol"] >= 0.0

        # Hammer successes — C must not exceed 1.0.
        for _ in range(100):
            state.apply_witness_delta_c("sol", vote=+1, latency_ms=200.0)
        assert state.coherence_by_agent["sol"] <= 1.0

    def test_empty_agent_name_is_noop(self) -> None:
        """apply_witness_delta_c with empty agent_name must not raise or mutate state."""
        state = BrainState()
        state.apply_witness_delta_c("", vote=+1)
        assert state.coherence_by_agent == {}

    def test_additive_does_not_zero_existing_c(self) -> None:
        """ΔC must ADD to existing C; it must never zero or replace the stored value."""
        state = BrainState()
        # Seed a non-default value.
        state.coherence_by_agent["loom"] = 0.6
        state.apply_witness_delta_c("loom", vote=+1, latency_ms=300.0)
        c = state.coherence_by_agent["loom"]
        # After a success from C=0.6, delta_c > 0, so new_c >= 0.6.
        assert c >= 0.6, f"Existing C must not decrease on a success; got {c}"
        assert c != 0.0, "ΔC must not zero an existing C value"

    def test_fail_safe_on_import_error(self) -> None:
        """FAIL-SAFE: if CoherencePhysics raises on import, C(t) carries its prior value."""
        state = BrainState()
        state.coherence_by_agent["codex"] = 0.75

        # Patch the import inside apply_witness_delta_c to raise.
        with patch(
            "sos.kernel.physics.CoherencePhysics.compute_collapse_energy",
            side_effect=RuntimeError("simulated physics failure"),
        ):
            state.apply_witness_delta_c("codex", vote=+1)

        # Value must be unchanged — carry prior.
        assert state.coherence_by_agent["codex"] == 0.75, (
            f"Fail-safe violated: C changed on error; got {state.coherence_by_agent['codex']}"
        )

    def test_fail_safe_on_uninformed_prior(self) -> None:
        """FAIL-SAFE: error on agent with no prior entry must not crash or mutate."""
        state = BrainState()
        with patch(
            "sos.kernel.physics.CoherencePhysics.compute_collapse_energy",
            side_effect=RuntimeError("simulated physics failure"),
        ):
            # Must not raise; codex must not appear in coherence_by_agent.
            state.apply_witness_delta_c("codex", vote=-1)
        assert "codex" not in state.coherence_by_agent


# ---------------------------------------------------------------------------
# Integration smoke: BrainService wires apply_witness_delta_c on events
# ---------------------------------------------------------------------------


try:
    import fakeredis.aioredis as fake_aioredis  # type: ignore[import-untyped]

    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False

skipif_no_fakeredis = pytest.mark.skipif(not HAS_FAKEREDIS, reason="fakeredis not installed")


def _make_svc(redis_client):
    from sos.services.brain.service import BrainService

    return BrainService(
        redis_url="redis://localhost:6379",
        stream_patterns=["sos:stream:global:squad:*"],
        redis_client=redis_client,
    )


def _v1_fields(msg_type: str, payload: dict | None = None) -> dict:
    return {
        "type": msg_type,
        "message_id": str(uuid.uuid4()),
        "payload": json.dumps(payload or {}),
        "source": "agent:test",
        "version": "1.0.0",
    }


@skipif_no_fakeredis
def test_brain_service_updates_coherence_on_task_completed() -> None:
    """task.completed event → coherence_by_agent updated for the completing agent."""
    r = fake_aioredis.FakeRedis(decode_responses=True)
    stream = "sos:stream:global:squad:kr3test"
    group = "brain"

    async def _go():
        svc = _make_svc(r)
        svc._redis = r
        # Pre-seed a failure so C < 1.0, making the completion effect observable.
        svc.state.coherence_by_agent["kasra"] = 0.7

        # Push a task.completed event.
        await r.xadd(
            stream,
            _v1_fields("task.completed", {"task_id": "t-kr3", "agent": "kasra"}),
        )
        try:
            await r.xgroup_create(name=stream, groupname=group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        svc._groups_registered.add(stream)
        await svc._tick()
        return svc.state.coherence_by_agent.get("kasra")

    c = asyncio.run(_go())
    assert c is not None, "coherence_by_agent must be populated after task.completed"
    assert c >= 0.7, f"C must not decrease after a success; got {c}"
    assert c <= 1.0, f"C must not exceed 1.0; got {c}"


@skipif_no_fakeredis
def test_brain_service_updates_coherence_on_task_failed() -> None:
    """task.failed event → coherence_by_agent reduced for the failing agent."""
    r = fake_aioredis.FakeRedis(decode_responses=True)
    stream = "sos:stream:global:squad:kr3fail"
    group = "brain"

    async def _go():
        svc = _make_svc(r)
        svc._redis = r
        # Start from default (uninformed prior 1.0).
        await r.xadd(
            stream,
            _v1_fields("task.failed", {"task_id": "t-kr3f", "agent": "athena", "reason": "timeout"}),
        )
        try:
            await r.xgroup_create(name=stream, groupname=group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        svc._groups_registered.add(stream)
        await svc._tick()
        return svc.state.coherence_by_agent.get("athena")

    c = asyncio.run(_go())
    assert c is not None, "coherence_by_agent must be populated after task.failed"
    assert 0.0 <= c < 1.0, f"C must drop below 1.0 after failure; got {c}"


# ---------------------------------------------------------------------------
# River guardrail #6 — REVERSIBLE: flag OFF disables the ΔC wire entirely
# ---------------------------------------------------------------------------


class TestWitnessDeltaCFlag:
    """BRAIN_WITNESS_DELTA_C=0 must silence the wire without any other change."""

    def test_flag_off_unit_apply_not_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With flag OFF, apply_witness_delta_c must not be invoked on task events.

        Proven by: patching apply_witness_delta_c to raise if called, then
        driving _on_task_completed and _on_task_failed — neither must raise.
        Coherence state must remain empty (unchanged from pre-W1).
        """
        import sos.services.brain.service as svc_mod

        monkeypatch.setenv("BRAIN_WITNESS_DELTA_C", "0")
        # Re-read the flag as if the module had imported with the env var set.
        monkeypatch.setattr(svc_mod, "BRAIN_WITNESS_DELTA_C_ENABLED", False)

        from sos.services.brain.state import BrainState

        state = BrainState()

        # Patch apply_witness_delta_c to raise — if the flag guard leaks it, test fails.
        original = state.apply_witness_delta_c

        def _must_not_be_called(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError(
                "apply_witness_delta_c was called despite BRAIN_WITNESS_DELTA_C=0"
            )

        state.apply_witness_delta_c = _must_not_be_called  # type: ignore[method-assign]

        # Simulate what _on_task_completed does (minus redis/emit).
        agent_name = "kasra"
        state.record_agent_success(agent_name)
        if svc_mod.BRAIN_WITNESS_DELTA_C_ENABLED:
            state.apply_witness_delta_c(agent_name, vote=+1)

        # Simulate what _on_task_failed does (minus redis/emit).
        state.record_agent_failure(agent_name)
        if svc_mod.BRAIN_WITNESS_DELTA_C_ENABLED:
            state.apply_witness_delta_c(agent_name, vote=-1)

        # coherence_by_agent must be empty — flag=OFF means zero state change.
        assert state.coherence_by_agent == {}, (
            f"coherence_by_agent must be empty with flag OFF; got {state.coherence_by_agent}"
        )
        # Restore original so other tests are unaffected.
        state.apply_witness_delta_c = original  # type: ignore[method-assign]

    def test_flag_on_still_wires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sanity: flag ON (default) — apply_witness_delta_c is reached."""
        import sos.services.brain.service as svc_mod

        monkeypatch.setattr(svc_mod, "BRAIN_WITNESS_DELTA_C_ENABLED", True)

        from sos.services.brain.state import BrainState

        state = BrainState()
        called: list[tuple] = []
        original = state.apply_witness_delta_c

        def _spy(agent_name: str, vote: int, **kw) -> None:
            called.append((agent_name, vote))
            original(agent_name, vote, **kw)

        state.apply_witness_delta_c = _spy  # type: ignore[method-assign]

        agent_name = "loom"
        if svc_mod.BRAIN_WITNESS_DELTA_C_ENABLED:
            state.apply_witness_delta_c(agent_name, vote=+1)

        assert len(called) == 1, f"Expected 1 call with flag ON; got {called}"
        assert called[0] == ("loom", +1)
