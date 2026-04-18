"""Contract tests for TaskScoredMessage + TaskScoredPayload (task.scored v1).

Mirrors tests/contracts/test_messages_core.py — envelope + payload helper,
invalid-input rejection, boundary checks, dispatcher roundtrip, and JSON
Schema meta-validation.
"""
from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sos.contracts.messages import (
    TaskScoredMessage,
    TaskScoredPayload,
    parse_message,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mid() -> str:
    return str(uuid.uuid4())


def _valid_kwargs() -> dict:
    return {
        "source": "agent:brain",
        "target": "sos:channel:tasks",
        "timestamp": _now(),
        "message_id": _mid(),
        "payload": {
            "task_id": "task-123",
            "score": 42.0,
            "urgency": "high",
            "ts": _now(),
        },
    }


# ---------------------------------------------------------------------------
# Minimal valid
# ---------------------------------------------------------------------------


def test_minimal_valid_task_scored_parses():
    """Minimal required envelope + payload instantiate cleanly with type='task.scored'."""
    msg = TaskScoredMessage(**_valid_kwargs())
    assert msg.type == "task.scored"


def test_optional_fields_accepted():
    """Optional payload fields (impact, unblock_count, cost) round-trip via model_dump()."""
    kwargs = _valid_kwargs()
    kwargs["payload"] = {
        **kwargs["payload"],
        "impact": 5.0,
        "unblock_count": 3,
        "cost": 1.0,
    }
    msg = TaskScoredMessage(**kwargs)
    dumped = msg.model_dump()
    assert dumped["payload"]["impact"] == 5.0
    assert dumped["payload"]["unblock_count"] == 3
    assert dumped["payload"]["cost"] == 1.0


# ---------------------------------------------------------------------------
# Invalid inputs
# ---------------------------------------------------------------------------


def test_invalid_task_id_rejected():
    """payload.task_id with spaces violates the pattern and is rejected."""
    kwargs = _valid_kwargs()
    kwargs["payload"] = {**kwargs["payload"], "task_id": "bad id with spaces"}
    with pytest.raises(ValueError):
        TaskScoredMessage(**kwargs)


def test_score_must_be_non_negative():
    """payload.score = -1 is rejected (score >= 0 required)."""
    kwargs = _valid_kwargs()
    kwargs["payload"] = {**kwargs["payload"], "score": -1}
    with pytest.raises(ValueError):
        TaskScoredMessage(**kwargs)


def test_score_upper_bound():
    """payload.score >= 10000 is rejected (exclusiveMaximum)."""
    kwargs = _valid_kwargs()
    kwargs["payload"] = {**kwargs["payload"], "score": 10000}
    with pytest.raises(ValueError):
        TaskScoredMessage(**kwargs)


def test_urgency_must_be_in_enum():
    """payload.urgency = 'urgent' is rejected; only critical/high/medium/low allowed."""
    kwargs = _valid_kwargs()
    kwargs["payload"] = {**kwargs["payload"], "urgency": "urgent"}
    with pytest.raises(ValueError):
        TaskScoredMessage(**kwargs)


def test_impact_bounds():
    """payload.impact must satisfy 1 <= impact <= 10; 0 and 11 rejected, 5 accepted."""
    kwargs = _valid_kwargs()

    kwargs_low = {**kwargs, "payload": {**kwargs["payload"], "impact": 0}}
    with pytest.raises(ValueError):
        TaskScoredMessage(**kwargs_low)

    kwargs_high = {**kwargs, "payload": {**kwargs["payload"], "impact": 11}}
    with pytest.raises(ValueError):
        TaskScoredMessage(**kwargs_high)

    kwargs_ok = {**kwargs, "payload": {**kwargs["payload"], "impact": 5}}
    msg = TaskScoredMessage(**kwargs_ok)
    assert msg.payload.impact == 5


def test_cost_bounds():
    """payload.cost must satisfy 0.1 <= cost <= 10; 0.05 and 11 rejected, 1.0 accepted."""
    kwargs = _valid_kwargs()

    kwargs_low = {**kwargs, "payload": {**kwargs["payload"], "cost": 0.05}}
    with pytest.raises(ValueError):
        TaskScoredMessage(**kwargs_low)

    kwargs_high = {**kwargs, "payload": {**kwargs["payload"], "cost": 11}}
    with pytest.raises(ValueError):
        TaskScoredMessage(**kwargs_high)

    kwargs_ok = {**kwargs, "payload": {**kwargs["payload"], "cost": 1.0}}
    msg = TaskScoredMessage(**kwargs_ok)
    assert msg.payload.cost == 1.0


def test_unblock_count_non_negative():
    """payload.unblock_count = -1 is rejected (minimum 0)."""
    kwargs = _valid_kwargs()
    kwargs["payload"] = {**kwargs["payload"], "unblock_count": -1}
    with pytest.raises(ValueError):
        TaskScoredMessage(**kwargs)


def test_ts_must_be_iso_8601():
    """payload.ts = 'not-a-date' is rejected by the ts ISO-8601 validator."""
    kwargs = _valid_kwargs()
    kwargs["payload"] = {**kwargs["payload"], "ts": "not-a-date"}
    with pytest.raises(ValueError):
        TaskScoredMessage(**kwargs)


def test_source_must_match_agent_pattern():
    """source = 'brain' (no 'agent:' prefix) is rejected by the envelope pattern."""
    kwargs = _valid_kwargs()
    kwargs["source"] = "brain"
    with pytest.raises(ValueError):
        TaskScoredMessage(**kwargs)


def test_type_discriminator_locked_to_task_scored():
    """Attempting to pass type='task.created' is rejected — type is locked to 'task.scored'."""
    kwargs = _valid_kwargs()
    kwargs["type"] = "task.created"
    with pytest.raises(ValueError):
        TaskScoredMessage(**kwargs)


# ---------------------------------------------------------------------------
# Dispatcher roundtrip
# ---------------------------------------------------------------------------


def test_parse_message_dispatches_to_task_scored():
    """Full roundtrip: build → model_dump(mode='json') → parse_message yields TaskScoredMessage."""
    msg = TaskScoredMessage(**_valid_kwargs())
    raw = msg.model_dump(mode="json")
    result = parse_message(raw)
    assert isinstance(result, TaskScoredMessage)
    assert result.type == "task.scored"
    assert result.payload.task_id == msg.payload.task_id


# ---------------------------------------------------------------------------
# JSON Schema meta-validation
# ---------------------------------------------------------------------------


def test_schema_file_parses():
    """The task.scored_v1 JSON Schema file parses and passes Draft202012 meta-validation."""
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "sos"
        / "contracts"
        / "schemas"
        / "messages"
        / "task.scored_v1.json"
    )
    schema = json.loads(schema_path.read_text())
    Draft202012Validator.check_schema(schema)
