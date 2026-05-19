"""Contract tests for the BrainSnapshot schema.

These tests freeze the BrainSnapshot wire contract. Any implementation
(Python, Rust, TypeScript) emitting records that pass these tests is
wire-compatible with BrainService's observable state output.
"""
from __future__ import annotations

import pytest

from sos.contracts.brain_snapshot import BrainSnapshot, RoutingDecision, load_schema


def _valid_decision_kwargs() -> dict:
    return {
        "task_id": "task-123",
        "agent_name": "sos-medic",
        "score": 0.87,
        "routed_at": "2026-04-17T20:00:00Z",
    }


def _valid_snapshot_kwargs() -> dict:
    return {
        "queue_size": 0,
        "in_flight": [],
        "recent_routes": [],
        "events_by_type": {},
        "events_seen": 0,
        "last_update_ts": "2026-04-17T20:00:00Z",
        "service_started_at": "2026-04-17T19:00:00Z",
    }


def test_minimal_valid_snapshot_instantiates():
    """A snapshot with all required fields and empty collections validates."""
    snap = BrainSnapshot(**_valid_snapshot_kwargs())
    assert snap.queue_size == 0
    assert snap.in_flight == []
    assert snap.recent_routes == []
    assert snap.events_by_type == {}
    assert snap.events_seen == 0


def test_full_snapshot_with_routes():
    """A snapshot carrying 3 RoutingDecision entries validates."""
    decisions = [
        RoutingDecision(**{**_valid_decision_kwargs(), "task_id": f"task-{i}"})
        for i in range(3)
    ]
    snap = BrainSnapshot(
        **{
            **_valid_snapshot_kwargs(),
            "queue_size": 5,
            "in_flight": ["task-0", "task-1", "task-2"],
            "recent_routes": decisions,
            "events_by_type": {"task.created": 3, "task.completed": 1},
            "events_seen": 4,
        }
    )
    assert len(snap.recent_routes) == 3
    assert snap.recent_routes[0].task_id == "task-0"


def test_queue_size_non_negative():
    """queue_size must be >= 0."""
    with pytest.raises(ValueError):
        BrainSnapshot(**{**_valid_snapshot_kwargs(), "queue_size": -1})


def test_events_seen_non_negative():
    """events_seen must be >= 0."""
    with pytest.raises(ValueError):
        BrainSnapshot(**{**_valid_snapshot_kwargs(), "events_seen": -1})


def test_in_flight_pattern_validated():
    """in_flight entries must match ^[a-zA-Z0-9_-]+$."""
    with pytest.raises(ValueError):
        BrainSnapshot(**{**_valid_snapshot_kwargs(), "in_flight": ["bad id!"]})


def test_in_flight_duplicates_rejected():
    """in_flight must contain unique task_ids."""
    with pytest.raises(ValueError):
        BrainSnapshot(**{**_valid_snapshot_kwargs(), "in_flight": ["t1", "t1"]})


def test_recent_routes_max_length():
    """recent_routes must not exceed 50 entries."""
    decisions = [
        RoutingDecision(**{**_valid_decision_kwargs(), "task_id": f"task-{i}"})
        for i in range(51)
    ]
    with pytest.raises(ValueError):
        BrainSnapshot(**{**_valid_snapshot_kwargs(), "recent_routes": decisions})


def test_events_by_type_values_non_negative():
    """events_by_type counts must be >= 0."""
    with pytest.raises(ValueError):
        BrainSnapshot(
            **{**_valid_snapshot_kwargs(), "events_by_type": {"task.created": -1}}
        )


def test_last_update_ts_iso():
    """last_update_ts must be an ISO-8601 date-time string."""
    with pytest.raises(ValueError):
        BrainSnapshot(**{**_valid_snapshot_kwargs(), "last_update_ts": "yesterday"})


def test_routing_decision_agent_name_pattern():
    """RoutingDecision.agent_name must match ^[a-z][a-z0-9-]*$ (lowercase only)."""
    with pytest.raises(ValueError):
        RoutingDecision(**{**_valid_decision_kwargs(), "agent_name": "Foo"})


def test_routing_decision_score_non_negative():
    """RoutingDecision.score must be >= 0."""
    with pytest.raises(ValueError):
        RoutingDecision(**{**_valid_decision_kwargs(), "score": -1})


def test_extra_fields_forbidden():
    """Unknown kwargs must be rejected (extra='forbid')."""
    with pytest.raises(ValueError):
        BrainSnapshot(**{**_valid_snapshot_kwargs(), "spurious": 1})


def test_schema_file_parses():
    """The published JSON Schema must meta-validate against Draft 2020-12."""
    from jsonschema import Draft202012Validator

    schema = load_schema()
    Draft202012Validator.check_schema(schema)


def test_load_schema_returns_dict():
    """load_schema() returns a dict with a 'properties' key."""
    schema = load_schema()
    assert isinstance(schema, dict)
    assert "properties" in schema
