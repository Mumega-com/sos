"""Contract tests for the Objective schema — v0.8.0 living-objective-tree primitive.

These tests are the freeze point: if they pass, any implementation (Python,
Rust, TypeScript) that emits records passing them is wire-compatible.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sos.contracts.objective import Objective


# ---------------------------------------------------------------------------
# Fixture / helpers
# ---------------------------------------------------------------------------

_VALID_ID = "01J1ZXBC3DEF4GHKMNP5TV6WXY"   # 26-char Crockford Base32 ULID
_VALID_ID2 = "01J1ZXBC3DEF4GHKMNP5TV6WXZ"  # second ULID for parent_id tests


def _min_kwargs() -> dict:
    """Minimum required fields to construct a valid Objective."""
    ts = Objective.now_iso()
    return {
        "id": _VALID_ID,
        "title": "Build the objectives tree",
        "created_by": "agent:codex",
        "created_at": ts,
        "updated_at": ts,
    }


# ---------------------------------------------------------------------------
# 1. Happy-path construction with minimum required fields
# ---------------------------------------------------------------------------

def test_happy_path_minimum_fields():
    obj = Objective(**_min_kwargs())
    assert obj.title == "Build the objectives tree"
    assert obj.created_by == "agent:codex"
    assert obj.state == "open"
    assert obj.tenant_id == "default"


# ---------------------------------------------------------------------------
# 2. Default values applied correctly
# ---------------------------------------------------------------------------

def test_default_values():
    obj = Objective(**_min_kwargs())
    assert obj.parent_id is None
    assert obj.description == ""
    assert obj.bounty_mind == 0
    assert obj.state == "open"
    assert obj.holder_agent is None
    assert obj.holder_heartbeat_at is None
    assert obj.subscribers == []
    assert obj.tags == []
    assert obj.capabilities_required == []
    assert obj.completion_artifact_url is None
    assert obj.completion_notes == ""
    assert obj.acks == []
    assert obj.tenant_id == "default"
    assert obj.project is None


# ---------------------------------------------------------------------------
# 3. bounty_mind rejects negative values
# ---------------------------------------------------------------------------

def test_bounty_mind_rejects_negative():
    with pytest.raises(ValueError):
        Objective(**{**_min_kwargs(), "bounty_mind": -1})


def test_bounty_mind_zero_allowed():
    obj = Objective(**{**_min_kwargs(), "bounty_mind": 0})
    assert obj.bounty_mind == 0


def test_bounty_mind_positive_allowed():
    obj = Objective(**{**_min_kwargs(), "bounty_mind": 500})
    assert obj.bounty_mind == 500


# ---------------------------------------------------------------------------
# 4. state rejects invalid literal
# ---------------------------------------------------------------------------

def test_state_rejects_invalid():
    with pytest.raises(ValueError):
        Objective(**{**_min_kwargs(), "state": "pending"})

    with pytest.raises(ValueError):
        Objective(**{**_min_kwargs(), "state": "done"})


def test_all_valid_states_accepted():
    for state in ("open", "claimed", "shipped", "paid", "blocked"):
        obj = Objective(**{**_min_kwargs(), "state": state})
        assert obj.state == state


# ---------------------------------------------------------------------------
# 5. title rejects empty string
# ---------------------------------------------------------------------------

def test_title_rejects_empty():
    with pytest.raises(ValueError):
        Objective(**{**_min_kwargs(), "title": ""})


# ---------------------------------------------------------------------------
# 6. to_redis_hash → from_redis_hash round-trip
# ---------------------------------------------------------------------------

def test_round_trip_minimum():
    obj = Objective(**_min_kwargs())
    h = obj.to_redis_hash()
    assert all(isinstance(v, str) for v in h.values()), "all hash values must be str"
    restored = Objective.from_redis_hash(h)
    assert restored == obj


def test_round_trip_with_none_fields():
    obj = Objective(**_min_kwargs())
    assert obj.parent_id is None
    assert obj.completion_artifact_url is None
    h = obj.to_redis_hash()
    assert h["parent_id"] == "null"
    assert h["completion_artifact_url"] == "null"
    restored = Objective.from_redis_hash(h)
    assert restored.parent_id is None
    assert restored.completion_artifact_url is None


def test_round_trip_full_fields():
    ts = Objective.now_iso()
    obj = Objective(
        id=_VALID_ID,
        parent_id=_VALID_ID2,
        title="Ship v0.8.0",
        description="Complete the objectives tree primitive",
        bounty_mind=200,
        state="claimed",
        holder_agent="agent:kasra",
        holder_heartbeat_at=ts,
        subscribers=["agent:mumega", "agent:codex"],
        tags=["core", "v0.8"],
        capabilities_required=["python", "redis"],
        completion_artifact_url="https://example.com/artifact",
        completion_notes="shipped with tests",
        acks=["agent:mumega"],
        created_by="agent:hadi",
        created_at=ts,
        updated_at=ts,
        tenant_id="mumega",
        project="sos",
    )
    h = obj.to_redis_hash()
    restored = Objective.from_redis_hash(h)
    assert restored == obj
    assert restored.bounty_mind == 200
    assert restored.subscribers == ["agent:mumega", "agent:codex"]
    assert restored.tags == ["core", "v0.8"]
    assert restored.acks == ["agent:mumega"]
    assert restored.tenant_id == "mumega"
    assert restored.project == "sos"


# ---------------------------------------------------------------------------
# 7. now_iso() produces parseable ISO-8601 with Z suffix
# ---------------------------------------------------------------------------

def test_now_iso_parseable():
    ts = Objective.now_iso()
    assert ts.endswith("Z"), f"expected Z suffix, got {ts!r}"
    # Must parse without error
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_now_iso_utc():
    ts = Objective.now_iso()
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# 8. subscribers / tags / acks round-trip (JSON-encoded lists)
# ---------------------------------------------------------------------------

def test_list_fields_json_encoded_in_hash():
    obj = Objective(**{
        **_min_kwargs(),
        "subscribers": ["agent:a", "agent:b"],
        "tags": ["alpha", "beta"],
        "acks": ["agent:c"],
    })
    h = obj.to_redis_hash()
    import json
    assert json.loads(h["subscribers"]) == ["agent:a", "agent:b"]
    assert json.loads(h["tags"]) == ["alpha", "beta"]
    assert json.loads(h["acks"]) == ["agent:c"]

    restored = Objective.from_redis_hash(h)
    assert restored.subscribers == ["agent:a", "agent:b"]
    assert restored.tags == ["alpha", "beta"]
    assert restored.acks == ["agent:c"]


def test_empty_lists_round_trip():
    obj = Objective(**_min_kwargs())
    h = obj.to_redis_hash()
    restored = Objective.from_redis_hash(h)
    assert restored.subscribers == []
    assert restored.tags == []
    assert restored.capabilities_required == []
    assert restored.acks == []


# ---------------------------------------------------------------------------
# 9. no extra fields allowed (model_config extra=forbid)
# ---------------------------------------------------------------------------

def test_extra_fields_rejected():
    with pytest.raises(ValueError):
        Objective(**{**_min_kwargs(), "unknown_field": "oops"})


# ---------------------------------------------------------------------------
# 10. Field count guard — all 18 declared fields present
# ---------------------------------------------------------------------------

def test_all_18_fields_present():
    expected = {
        "id", "parent_id", "title", "description", "bounty_mind",
        "state", "holder_agent", "holder_heartbeat_at",
        "subscribers", "tags", "capabilities_required",
        "completion_artifact_url", "completion_notes", "acks",
        "done_when",
        "created_by", "created_at", "updated_at",
        "tenant_id", "project",
        "outcome_score",
    }
    actual = set(Objective.model_fields.keys())
    assert actual == expected, f"field mismatch: {actual.symmetric_difference(expected)}"


# ---------------------------------------------------------------------------
# 11. outcome_score (v0.8.1) — defaults to None, round-trips, range-checked
# ---------------------------------------------------------------------------


def test_outcome_score_defaults_to_none():
    obj = Objective(**_min_kwargs())
    assert obj.outcome_score is None


def test_outcome_score_round_trips_via_redis_hash():
    obj = Objective(**{**_min_kwargs(), "outcome_score": 0.73})
    h = obj.to_redis_hash()
    assert "outcome_score" in h, "outcome_score must be stored when set"
    restored = Objective.from_redis_hash(h)
    assert restored.outcome_score == 0.73

    # Also confirm that when outcome_score is None, the key is NOT stored
    obj_none = Objective(**_min_kwargs())
    h_none = obj_none.to_redis_hash()
    assert "outcome_score" not in h_none
    restored_none = Objective.from_redis_hash(h_none)
    assert restored_none.outcome_score is None


def test_outcome_score_rejects_out_of_range():
    with pytest.raises(ValueError):
        Objective(**{**_min_kwargs(), "outcome_score": 1.5})

    with pytest.raises(ValueError):
        Objective(**{**_min_kwargs(), "outcome_score": -0.1})


# ---------------------------------------------------------------------------
# 12. done_when (closure-v1 T1.3) — structured completion gate on Objective
# ---------------------------------------------------------------------------


def test_done_when_defaults_to_empty_list():
    obj = Objective(**_min_kwargs())
    assert obj.done_when == []


def test_done_when_round_trips_via_redis_hash():
    from sos.contracts.done_check import DoneCheck

    checks = [
        DoneCheck(id="c1", text="tests pass"),
        DoneCheck(id="c2", text="docs updated", done=True,
                  acked_by="agent:kasra", acked_at=Objective.now_iso()),
    ]
    obj = Objective(**{**_min_kwargs(), "done_when": checks})
    h = obj.to_redis_hash()

    import json
    parsed = json.loads(h["done_when"])
    assert len(parsed) == 2
    assert parsed[0]["id"] == "c1" and parsed[0]["done"] is False
    assert parsed[1]["id"] == "c2" and parsed[1]["done"] is True

    restored = Objective.from_redis_hash(h)
    assert len(restored.done_when) == 2
    assert all(isinstance(c, DoneCheck) for c in restored.done_when)
    assert restored.done_when[0].text == "tests pass"
    assert restored.done_when[1].acked_by == "agent:kasra"
    assert restored == obj


def test_done_when_accepts_dict_input_for_pydantic_coercion():
    obj = Objective(**{
        **_min_kwargs(),
        "done_when": [{"id": "x", "text": "ship it"}],
    })
    assert len(obj.done_when) == 1
    assert obj.done_when[0].id == "x"
    assert obj.done_when[0].done is False


def test_done_when_rejects_empty_id():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Objective(**{**_min_kwargs(),
                     "done_when": [{"id": "", "text": "nope"}]})
