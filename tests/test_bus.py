"""Kernel bus — scope contract + smoke.

v0.9.1 / W1: ``MessageBus.send()`` requires ``tenant_id`` and
``project`` kwargs. Missing scope raises — no silent default. These
tests pin that behavior. The original Redis smoke path still runs when
a local Redis is available; otherwise it skips cleanly.
"""

from __future__ import annotations

import pytest

from sos.contracts.errors import MessageValidationError
from sos.kernel import Message, MessageType
from sos.kernel.bus import MessageBus, enforce_scope


def _msg() -> Message:
    return Message(
        type=MessageType.CHAT,
        source="hive_mind",
        target="test_agent",
        payload={"instruction": "ping"},
    )


async def test_kernel_send_requires_tenant_id() -> None:
    bus = MessageBus()
    with pytest.raises(ValueError, match="tenant_id is required"):
        await bus.send(_msg(), tenant_id="", project="journeys")


async def test_kernel_send_requires_project() -> None:
    bus = MessageBus()
    with pytest.raises(ValueError, match="project is required"):
        await bus.send(_msg(), tenant_id="mumega", project="")


def test_enforce_scope_rejects_missing_tenant_id() -> None:
    with pytest.raises(MessageValidationError) as exc:
        enforce_scope({"type": "send", "project": "journeys"})
    assert exc.value.code == "SOS-4005"


def test_enforce_scope_rejects_missing_project() -> None:
    with pytest.raises(MessageValidationError) as exc:
        enforce_scope({"type": "send", "tenant_id": "mumega"})
    assert exc.value.code == "SOS-4006"


def test_enforce_scope_passes_valid_envelope() -> None:
    envelope = {"type": "send", "tenant_id": "mumega", "project": "journeys"}
    assert enforce_scope(envelope) is envelope


async def test_nervous_system_smoke() -> None:
    """Hippocampus + send-with-scope smoke over real Redis when available.

    Listener side is intentionally out of scope — pub/sub timing races
    belong in integration tests, not unit smoke. What this locks:
    memory push/recall works, and a scoped send() completes without
    raising against a live Redis.
    """
    bus = MessageBus()
    await bus.connect()
    if not bus._redis:
        pytest.skip("Redis not available")

    try:
        agent_id = "test_agent_001"
        await bus.memory_push(agent_id, "thought one", role="user")
        await bus.memory_push(agent_id, "thought two", role="assistant")
        memories = await bus.memory_recall(agent_id, limit=5)
        assert len(memories) >= 2

        # Scoped send succeeds end-to-end (xadd + publish) without raising.
        await bus.send(_msg(), tenant_id="mumega", project="journeys")
    finally:
        await bus.disconnect()
