"""Contract tests for sos.bus.envelope — the canonical SOS message envelope.

These tests pin the sender/receiver contract so that a fourth drifting sender
can't silently drop message bodies (regression for the 2026-04-19 bug where
raw-string ``payload`` produced empty text on every receiver).
"""

from __future__ import annotations

import json

import pytest

from sos.bus import envelope


def test_build_returns_all_canonical_fields() -> None:
    env = envelope.build(
        msg_type="chat",
        source="agent:loom",
        target="agent:kasra",
        text="hello",
    )
    assert set(env.keys()) >= {"id", "type", "source", "target", "payload", "timestamp", "version"}
    assert env["type"] == "chat"
    assert env["source"] == "agent:loom"
    assert env["target"] == "agent:kasra"
    assert env["version"] == envelope.CANONICAL_VERSION

    payload = json.loads(env["payload"])
    assert payload["text"] == "hello"
    assert payload["source"] == "agent:loom"
    assert isinstance(payload["timestamp"], (int, float))


def test_build_requires_kind_prefix_on_source() -> None:
    with pytest.raises(ValueError, match="source must be prefixed"):
        envelope.build(msg_type="chat", source="loom", target="agent:kasra", text="hi")


def test_build_requires_kind_prefix_on_target() -> None:
    with pytest.raises(ValueError, match="target must be prefixed"):
        envelope.build(msg_type="chat", source="agent:loom", target="kasra", text="hi")


def test_build_rejects_none_text() -> None:
    with pytest.raises(ValueError, match="text must not be None"):
        envelope.build(msg_type="chat", source="agent:x", target="agent:y", text=None)  # type: ignore[arg-type]


def test_build_accepts_empty_text() -> None:
    env = envelope.build(msg_type="chat", source="agent:x", target="agent:y", text="")
    assert json.loads(env["payload"])["text"] == ""


def test_build_includes_project_when_given() -> None:
    env = envelope.build(
        msg_type="chat", source="agent:x", target="agent:y", text="hi", project="mumega"
    )
    assert env["project"] == "mumega"


def test_build_omits_project_when_missing() -> None:
    env = envelope.build(msg_type="chat", source="agent:x", target="agent:y", text="hi")
    assert "project" not in env


def test_build_merges_extras_into_payload() -> None:
    env = envelope.build(
        msg_type="remember",
        source="agent:loom",
        target="agent:loom",
        text="note body",
        extras={"remember": True, "content_type": "text/plain"},
    )
    payload = json.loads(env["payload"])
    assert payload["remember"] is True
    assert payload["content_type"] == "text/plain"
    assert payload["text"] == "note body"


def test_build_accepts_explicit_message_id() -> None:
    env = envelope.build(
        msg_type="chat",
        source="agent:x",
        target="agent:y",
        text="hi",
        message_id="fixed-id-123",
    )
    assert env["id"] == "fixed-id-123"


def test_parse_canonical_roundtrip() -> None:
    env = envelope.build(
        msg_type="chat", source="agent:loom", target="agent:kasra", text="roundtrip"
    )
    parsed = envelope.parse(env)
    assert parsed["type"] == "chat"
    assert parsed["source"] == "agent:loom"
    assert parsed["target"] == "agent:kasra"
    assert parsed["text"] == "roundtrip"
    assert isinstance(parsed["timestamp"], float)


def test_parse_tolerates_raw_string_payload() -> None:
    """The 2026-04-19 bug — raw string in payload must not drop the text."""
    fields = {
        "type": "chat",
        "source": "agent:buggy_sender",
        "target": "agent:loom",
        "payload": "this was a raw string not JSON",
    }
    parsed = envelope.parse(fields)
    assert parsed["text"] == "this was a raw string not JSON"
    assert parsed["source"] == "agent:buggy_sender"


def test_parse_tolerates_missing_payload() -> None:
    parsed = envelope.parse({"type": "heartbeat", "source": "agent:loom", "target": "squad:x"})
    assert parsed["text"] == ""
    assert parsed["source"] == "agent:loom"


def test_parse_tolerates_empty_payload_string() -> None:
    parsed = envelope.parse(
        {"type": "chat", "source": "agent:loom", "target": "agent:x", "payload": ""}
    )
    assert parsed["text"] == ""


def test_parse_tolerates_json_non_dict() -> None:
    """Payload JSON-loadable but not a dict (e.g. a bare string, number, list)."""
    fields = {
        "type": "chat",
        "source": "agent:x",
        "target": "agent:y",
        "payload": json.dumps("just a string"),
    }
    parsed = envelope.parse(fields)
    assert parsed["text"] == "just a string"


def test_parse_falls_back_to_field_source_when_payload_has_none() -> None:
    fields = {
        "type": "chat",
        "source": "agent:toplevel",
        "target": "agent:y",
        "payload": json.dumps({"text": "hi"}),
    }
    parsed = envelope.parse(fields)
    assert parsed["source"] == "agent:toplevel"


def test_parse_prefers_payload_source_over_field_source() -> None:
    """Hermes consumer does this — payload.source wins over top-level."""
    fields = {
        "type": "chat",
        "source": "agent:legacy",
        "target": "agent:y",
        "payload": json.dumps({"text": "hi", "source": "agent:canonical"}),
    }
    parsed = envelope.parse(fields)
    assert parsed["source"] == "agent:canonical"


def test_parse_extracts_extras() -> None:
    fields = {
        "type": "remember",
        "source": "agent:x",
        "target": "agent:x",
        "payload": json.dumps(
            {
                "text": "note",
                "source": "agent:x",
                "timestamp": 1234567890.0,
                "remember": True,
                "content_type": "text/plain",
            }
        ),
    }
    parsed = envelope.parse(fields)
    assert parsed["extras"] == {"remember": True, "content_type": "text/plain"}
    assert parsed["timestamp"] == 1234567890.0


def test_parse_coerces_invalid_timestamp_to_none() -> None:
    fields = {
        "type": "chat",
        "source": "agent:x",
        "target": "agent:y",
        "payload": json.dumps({"text": "hi", "timestamp": "not-a-number"}),
    }
    parsed = envelope.parse(fields)
    assert parsed["timestamp"] is None


def test_parse_returns_id_from_message_id_alias() -> None:
    """sos_mcp_sse remember path uses message_id instead of id."""
    fields = {
        "type": "send",
        "source": "agent:x",
        "target": "agent:x",
        "message_id": "uuid-abc-123",
        "payload": json.dumps({"text": "hi"}),
    }
    parsed = envelope.parse(fields)
    assert parsed["id"] == "uuid-abc-123"
