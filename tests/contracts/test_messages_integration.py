"""Integration-style contract tests for SOS bus message types.

Representative shapes from real SOS bus traffic, Redis field round-trips,
and JSON Schema meta-validation. These are the freeze points: if they pass,
any implementation (Python, Rust, TypeScript) that emits records passing them
is wire-compatible.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from sos.contracts.messages import (
    BusMessage,
    SendMessage,
    SendPayload,
    TaskCompletedMessage,
    TaskCompletedPayload,
    TaskCreatedMessage,
    TaskCreatedPayload,
    TaskError,
    WakeMessage,
    WakePayload,
    parse_message,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MESSAGES_DIR = Path(__file__).parent.parent.parent / "sos" / "contracts" / "schemas" / "messages"
_SCHEMA_FILES = sorted(MESSAGES_DIR.glob("*_v1.json"))

# 5 structural envelope fields that every message must declare in `required`
_STRUCTURAL_FIELDS = {"type", "source", "timestamp", "version", "message_id"}


def _send_kwargs(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid SendMessage kwargs dict."""
    base: dict[str, Any] = {
        "source": "agent:hadi",
        "target": "agent:sos-medic",
        "timestamp": BusMessage.now_iso(),
        "message_id": str(uuid.uuid4()),
        "payload": {"text": "ping — are you online?", "content_type": "text/plain"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Representative traffic shapes
# ---------------------------------------------------------------------------


def test_representative_hadi_to_sos_medic_ping() -> None:
    """A real-world ping from hadi to sos-medic mirrors the most common bus pattern."""
    msg = SendMessage(
        source="agent:hadi",
        target="agent:sos-medic",
        timestamp=BusMessage.now_iso(),
        message_id=str(uuid.uuid4()),
        payload=SendPayload(
            text=(
                "sos-medic — ping. are you online? "
                "kernel shows you registered 4 minutes ago. "
                "reply with your current load and last-seen task id."
            ),
            content_type="text/plain",
        ),
    )
    assert msg.type == "send"
    assert msg.source == "agent:hadi"
    assert msg.target == "agent:sos-medic"
    assert "ping" in msg.payload.text


def test_representative_kasra_deprecation_message() -> None:
    """A long body (~500 chars) from kasra to sos-dev exercises realistic content length."""
    long_body = (
        "sos-dev — kasra here. heads up: the /v1/agents/register endpoint "
        "currently accepts both flat JSON and nested payload formats. "
        "this dual-tolerance was a stopgap while the Pydantic contracts were finalised. "
        "now that messages.py is locked at v1.0, we need to deprecate the flat path. "
        "i will open a PR stripping the compat shim after the sprint-1 gate passes. "
        "expected timeline: today. no action needed from you right now — "
        "just letting you know so the SaaS builder tests don't silently rely on the old shape."
    )
    assert len(long_body) >= 400  # confirm the fixture is actually long
    msg = SendMessage(
        source="agent:kasra",
        target="agent:sos-dev",
        timestamp=BusMessage.now_iso(),
        message_id=str(uuid.uuid4()),
        payload=SendPayload(text=long_body, content_type="text/plain"),
    )
    assert msg.type == "send"
    assert msg.source == "agent:kasra"
    assert msg.target == "agent:sos-dev"
    assert len(msg.payload.text) >= 400


def test_representative_trop_task_created() -> None:
    """A TROP v1.1 content task matches the realistic squad task_create input."""
    msg = TaskCreatedMessage(
        source="agent:trop",
        timestamp=BusMessage.now_iso(),
        message_id=str(uuid.uuid4()),
        payload=TaskCreatedPayload(
            task_id="trop-2026-04-16-glass-commerce-post",
            title="Write TROP Glass Commerce featured article — v1.1",
            priority="high",
            description=(
                "Produce the featured article for the Glass Commerce topic cluster "
                "per the TROP v1.1 content brief. Target 1 200 words, SEO keyword: "
                "'glass commerce platform'. Include 3 internal links, 1 CTA block, "
                "and a meta description under 160 chars. Publish to trop.mumega.com/blog."
            ),
            assignee="trop",
            skill_id="content",
            project="trop",
            labels=["content", "seo", "glass-commerce", "trop-v1.1"],
            token_budget=80000,
            bounty_cents=2500,
        ),
    )
    assert msg.type == "task_created"
    assert msg.payload.project == "trop"
    assert msg.payload.priority == "high"
    assert msg.payload.bounty_cents == 2500
    assert "glass-commerce" in (msg.payload.labels or [])


def test_representative_wake_from_kernel() -> None:
    """A kernel-originated wake message matches the bus preview format."""
    preview_text = (
        "[bus:sos-medic] send from agent:hadi — "
        "ping — are you online? (msg_id: abc123)"
    )
    msg = WakeMessage(
        source="agent:kernel",
        target="agent:sos-medic",
        timestamp=BusMessage.now_iso(),
        message_id=str(uuid.uuid4()),
        payload=WakePayload(text=preview_text),
        priority="normal",
    )
    assert msg.type == "wake"
    assert msg.source == "agent:kernel"
    assert msg.target == "agent:sos-medic"
    assert msg.payload.text.startswith("[bus:sos-medic]")
    assert msg.priority == "normal"


# ---------------------------------------------------------------------------
# Redis field serialisation
# ---------------------------------------------------------------------------


def test_to_redis_fields_all_strings() -> None:
    """Every message type must produce only str values for redis.xadd compatibility."""
    from sos.contracts.messages import (
        AgentJoinedMessage,
        AgentJoinedPayload,
        AnnounceMessage,
        AskMessage,
        AskPayload,
        TaskClaimedMessage,
        TaskClaimedPayload,
    )

    now = BusMessage.now_iso()
    mid = str(uuid.uuid4())

    messages: list[BusMessage] = [
        SendMessage(
            source="agent:hadi",
            target="agent:sos-medic",
            timestamp=now,
            message_id=mid,
            payload=SendPayload(text="hello", content_type="text/plain"),
        ),
        WakeMessage(
            source="agent:kernel",
            target="agent:sos-medic",
            timestamp=now,
            message_id=mid,
            payload=WakePayload(text="[bus:sos-medic] wake"),
        ),
        AnnounceMessage(
            source="agent:kasra",
            timestamp=now,
            message_id=mid,
        ),
        AskMessage(
            source="agent:hadi",
            target="agent:kasra",
            timestamp=now,
            message_id=mid,
            payload=AskPayload(
                question="What is the current task count?",
                reply_channel="agent:hadi",
            ),
        ),
        TaskCreatedMessage(
            source="agent:trop",
            timestamp=now,
            message_id=mid,
            payload=TaskCreatedPayload(
                task_id="t-001",
                title="Sample task",
                priority="medium",
            ),
        ),
        TaskClaimedMessage(
            source="agent:trop",
            timestamp=now,
            message_id=mid,
            payload=TaskClaimedPayload(
                task_id="t-001",
                claimed_at=now,
            ),
        ),
        TaskCompletedMessage(
            source="agent:trop",
            timestamp=now,
            message_id=mid,
            payload=TaskCompletedPayload(
                task_id="t-001",
                status="done",
                completed_at=now,
            ),
        ),
        AgentJoinedMessage(
            timestamp=now,
            message_id=mid,
            payload=AgentJoinedPayload(
                agent_name="trop",
                joined_at=now,
            ),
        ),
    ]

    for msg in messages:
        fields = msg.to_redis_fields()
        bad = {k: type(v).__name__ for k, v in fields.items() if not isinstance(v, str)}
        assert not bad, (
            f"{msg.type}.to_redis_fields() returned non-str values: {bad}"
        )


def test_to_redis_fields_roundtrip_preserves_semantics() -> None:
    """Serialize a SendMessage to redis fields and parse back; compare key fields."""
    original_text = "Roundtrip test — semantic check for bus envelope integrity."
    original = SendMessage(
        source="agent:hadi",
        target="agent:sos-medic",
        timestamp=BusMessage.now_iso(),
        message_id=str(uuid.uuid4()),
        payload=SendPayload(text=original_text, content_type="text/plain"),
    )

    fields = original.to_redis_fields()

    # Reconstruct a raw dict that parse_message can work with.
    # payload is stored as JSON string in redis fields.
    raw: dict[str, Any] = dict(fields)
    raw["payload"] = json.loads(raw["payload"])

    restored = parse_message(raw)

    assert restored.type == original.type
    assert restored.source == original.source
    assert restored.target == original.target
    assert isinstance(restored, SendMessage)
    assert restored.payload.text == original_text


# ---------------------------------------------------------------------------
# Schema file validation
# ---------------------------------------------------------------------------


def test_all_8_schema_files_parse() -> None:
    """All 8 message schema files must be valid JSON and Draft 2020-12."""
    assert len(_SCHEMA_FILES) == 8, (
        f"Expected 8 schema files, found {len(_SCHEMA_FILES)}: {_SCHEMA_FILES}"
    )
    for path in _SCHEMA_FILES:
        schema = json.loads(path.read_text())
        assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", (
            f"{path.name}: missing or wrong $schema value"
        )


def test_all_8_schemas_have_5_structural_required() -> None:
    """Every message schema must declare all 5 structural fields in 'required'."""
    assert len(_SCHEMA_FILES) == 8
    for path in _SCHEMA_FILES:
        schema = json.loads(path.read_text())
        required = set(schema.get("required", []))
        missing = _STRUCTURAL_FIELDS - required
        assert not missing, (
            f"{path.name}: missing structural required fields: {missing}"
        )


def test_all_8_schemas_have_type_const() -> None:
    """Every message schema must declare properties.type.const equal to its type name."""
    assert len(_SCHEMA_FILES) == 8
    for path in _SCHEMA_FILES:
        # Derive expected type name from filename: e.g. "send_v1.json" → "send"
        expected_type = path.stem.rsplit("_v", 1)[0]
        schema = json.loads(path.read_text())
        actual_const = schema.get("properties", {}).get("type", {}).get("const")
        assert actual_const == expected_type, (
            f"{path.name}: properties.type.const={actual_const!r}, expected {expected_type!r}"
        )


def test_all_8_schemas_disallow_additional_properties() -> None:
    """Every message schema must set additionalProperties: false at top level."""
    assert len(_SCHEMA_FILES) == 8
    for path in _SCHEMA_FILES:
        schema = json.loads(path.read_text())
        assert schema.get("additionalProperties") is False, (
            f"{path.name}: additionalProperties is not false — unknown field smuggling is possible"
        )


# ---------------------------------------------------------------------------
# Source pattern enforcement
# ---------------------------------------------------------------------------


def test_source_pattern_rejects_spoofed_agents() -> None:
    """The source field pattern must reject uppercase, whitespace, and wrong prefix."""
    base = _send_kwargs()

    # Uppercase in agent name — must fail
    with pytest.raises(Exception):
        SendMessage(**{**base, "source": "agent:FAKE_AGENT"})

    # Whitespace embedded in agent name — must fail
    with pytest.raises(Exception):
        SendMessage(**{**base, "source": "agent:admin  evil"})

    # Wrong prefix (user: instead of agent:) — must fail
    with pytest.raises(Exception):
        SendMessage(**{**base, "source": "user:hadi"})


def test_source_must_be_registered_is_not_validated_by_schema_but_is_valid_shape() -> None:
    """The schema validates SHAPE of source (agent:<name>), not registry membership.

    Bus-level enforcement (delivery.py, bus worker) is responsible for checking
    that the agent exists in the registry. The schema contract only guarantees
    the URI prefix and name charset — it cannot be stricter without coupling
    the schema to live state.
    """
    # These are valid shape but completely fictional agents — should all succeed.
    valid_shapes = [
        "agent:fictional-agent",
        "agent:x1",
        "agent:a",
        "agent:new-agent-99",
    ]
    base = _send_kwargs()
    for source in valid_shapes:
        msg = SendMessage(**{**base, "source": source})
        assert msg.source == source, f"Valid shape {source!r} was unexpectedly rejected"


# ---------------------------------------------------------------------------
# TaskCompleted error field
# ---------------------------------------------------------------------------


def test_error_object_in_task_completed_when_failed() -> None:
    """A failed task with error object is accepted; failed without error is also valid."""
    now = BusMessage.now_iso()
    mid = str(uuid.uuid4())

    # Failed task WITH structured error
    msg_with_error = TaskCompletedMessage(
        source="agent:trop",
        timestamp=now,
        message_id=mid,
        payload=TaskCompletedPayload(
            task_id="trop-content-001",
            status="failed",
            completed_at=now,
            error=TaskError(
                code="SOS-5001",
                message="no provider available for content skill",
            ),
        ),
    )
    assert msg_with_error.payload.status == "failed"
    assert msg_with_error.payload.error is not None
    assert msg_with_error.payload.error.code == "SOS-5001"
    assert "no provider" in msg_with_error.payload.error.message

    # Failed task WITHOUT error — also valid (error is optional)
    msg_without_error = TaskCompletedMessage(
        source="agent:trop",
        timestamp=now,
        message_id=str(uuid.uuid4()),
        payload=TaskCompletedPayload(
            task_id="trop-content-002",
            status="failed",
            completed_at=now,
        ),
    )
    assert msg_without_error.payload.status == "failed"
    assert msg_without_error.payload.error is None
