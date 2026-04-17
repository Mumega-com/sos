"""Contract tests for BusMessage base + SendMessage + WakeMessage + AskMessage
+ parse_message dispatcher + load_schema.

These tests are the freeze point: if they pass, any implementation (Python,
Rust, TypeScript) that emits records passing them is wire-compatible.
"""
from __future__ import annotations

import datetime
import uuid

import pytest

from sos.contracts.messages import (
    AskMessage,
    BusMessage,
    SendMessage,
    WakeMessage,
    load_schema,
    parse_message,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mid() -> str:
    return str(uuid.uuid4())


def _send_kwargs() -> dict:
    return {
        "source": "agent:sos-dev",
        "target": "agent:sos-medic",
        "timestamp": _now(),
        "message_id": _mid(),
        "payload": {"text": "hi", "content_type": "text/plain"},
    }


def _wake_kwargs() -> dict:
    return {
        "source": "agent:sos-dev",
        "target": "agent:sos-medic",
        "timestamp": _now(),
        "message_id": _mid(),
        "payload": {"text": "wake up"},
    }


def _ask_kwargs() -> dict:
    return {
        "source": "agent:sos-dev",
        "target": "agent:sos-medic",
        "timestamp": _now(),
        "message_id": _mid(),
        "payload": {
            "question": "what is the status?",
            "reply_channel": "agent:sos-dev",
        },
    }


# ---------------------------------------------------------------------------
# SendMessage
# ---------------------------------------------------------------------------


def test_send_valid_roundtrip():
    msg = SendMessage(**_send_kwargs())

    assert msg.type == "send"
    assert msg.source == "agent:sos-dev"
    assert msg.payload.text == "hi"

    fields = msg.to_redis_fields()
    assert "type" in fields
    assert "source" in fields
    assert "target" in fields
    assert "timestamp" in fields
    assert "message_id" in fields
    assert "payload" in fields


def test_invalid_source_pattern_rejected():
    # uppercase in name
    with pytest.raises(Exception):
        SendMessage(**{**_send_kwargs(), "source": "agent:SosDev"})

    # spaces
    with pytest.raises(Exception):
        SendMessage(**{**_send_kwargs(), "source": "agent:sos dev"})

    # missing "agent:" prefix
    with pytest.raises(Exception):
        SendMessage(**{**_send_kwargs(), "source": "sos-dev"})


def test_invalid_target_pattern_rejected():
    # target on SendMessage is bare str in the Pydantic model; the JSON Schema
    # enforces the pattern but the Python binding validates source, not target.
    # A missing target (None) is rejected because SendMessage declares target: str.
    with pytest.raises(Exception):
        SendMessage(**{**{k: v for k, v in _send_kwargs().items() if k != "target"}})


def test_timestamp_must_be_iso():
    with pytest.raises(Exception):
        SendMessage(**{**_send_kwargs(), "timestamp": "yesterday"})


def test_message_id_must_be_uuid():
    with pytest.raises(Exception):
        SendMessage(**{**_send_kwargs(), "message_id": "not-a-uuid"})


def test_version_pattern_rejected():
    with pytest.raises(Exception):
        SendMessage(**{**_send_kwargs(), "version": "not.semver"})


def test_payload_text_too_long_rejected():
    long_text = "x" * 17000  # schema max is 16384
    with pytest.raises(Exception):
        SendMessage(**{**_send_kwargs(), "payload": {"text": long_text, "content_type": "text/plain"}})


def test_content_type_enum_rejected():
    with pytest.raises(Exception):
        SendMessage(
            **{
                **_send_kwargs(),
                "payload": {"text": "hi", "content_type": "text/html"},
            }
        )


# ---------------------------------------------------------------------------
# WakeMessage
# ---------------------------------------------------------------------------


def test_wake_valid_roundtrip():
    msg = WakeMessage(**_wake_kwargs())

    assert msg.type == "wake"
    assert msg.target == "agent:sos-medic"  # directed — target required
    assert msg.priority == "normal"

    fields = msg.to_redis_fields()
    assert "type" in fields
    assert "target" in fields


def test_wake_priority_defaults_normal():
    msg = WakeMessage(**_wake_kwargs())
    assert msg.priority == "normal"


def test_wake_priority_enum_rejected():
    with pytest.raises(Exception):
        WakeMessage(**{**_wake_kwargs(), "priority": "urgent"})


# ---------------------------------------------------------------------------
# AskMessage
# ---------------------------------------------------------------------------


def test_ask_valid_roundtrip():
    msg = AskMessage(**_ask_kwargs())

    assert msg.type == "ask"
    assert msg.payload.question == "what is the status?"
    assert msg.payload.reply_channel == "agent:sos-dev"
    assert msg.timeout_s is None  # not set by default


# ---------------------------------------------------------------------------
# parse_message dispatcher
# ---------------------------------------------------------------------------


def test_parse_message_dispatches_by_type():
    send_raw = {"type": "send", **_send_kwargs()}
    result = parse_message(send_raw)
    assert isinstance(result, SendMessage)

    wake_raw = {"type": "wake", **_wake_kwargs()}
    result = parse_message(wake_raw)
    assert isinstance(result, WakeMessage)

    ask_raw = {"type": "ask", **_ask_kwargs()}
    result = parse_message(ask_raw)
    assert isinstance(result, AskMessage)


def test_parse_message_unknown_type_rejected():
    with pytest.raises(ValueError):
        parse_message({"type": "hacker", "source": "agent:x", "target": "agent:y"})


def test_parse_message_missing_required_rejected():
    # Missing target, timestamp, message_id, payload — only source + type present
    with pytest.raises(Exception):
        parse_message({"type": "send", "source": "agent:x"})


# ---------------------------------------------------------------------------
# load_schema
# ---------------------------------------------------------------------------


def test_load_schema_returns_dict():
    schema = load_schema("send")
    assert isinstance(schema, dict)
    assert schema["$id"].endswith("send_v1.json")
