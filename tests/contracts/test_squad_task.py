"""Contract tests for SquadTask v1 JSON Schema and Pydantic binding.

These tests are the freeze point: if they pass, any implementation that
emits records passing them is wire-compatible with the SquadTask v1 schema.

JSON Schema source of truth: sos/contracts/schemas/squad_task_v1.json
Pydantic binding:             sos/contracts/squad_task.py
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError as JSValidationError
from pydantic import ValidationError as PydanticValidationError

from sos.contracts.squad_task import (
    SquadTaskV1,
    load_schema,
    parse_squad_task,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SCHEMA_PATH = (
    Path(__file__).parents[2] / "sos" / "contracts" / "schemas" / "squad_task_v1.json"
)


def _minimal() -> dict[str, Any]:
    """Minimum required fields for a valid SquadTask."""
    return {
        "schema_version": "1",
        "id": "task-abc-001",
        "squad_id": "squad-marketing",
        "title": "Write Q2 blog post",
        "status": "backlog",
        "created_at": "2026-04-17T10:00:00Z",
    }


def _full() -> dict[str, Any]:
    """All optional fields populated for a valid SquadTask."""
    return {
        **_minimal(),
        "description": "A detailed blog post about SOS automation.",
        "priority": "high",
        "assignee": "kasra",
        "skill_id": "mkt-blog-post-drafter",
        "project": "mumega",
        "labels": ["content", "marketing"],
        "blocked_by": ["task-prereq-001"],
        "blocks": ["task-downstream-002"],
        "inputs": {
            "fuel_grade": "premium",
            "impact": 3,
            "urgency": 2,
            "estimated_cost_cents": 30,
        },
        "result": {"summary": "Published to mumega.com/blog/q2"},
        "token_budget": 8000,
        "bounty": {"reward": 100, "bounty_id": "bty-001"},
        "bounty_micros": 100_000_000,
        "external_ref": "clickup:task-xyz",
        "updated_at": "2026-04-17T11:00:00Z",
        "completed_at": "2026-04-17T12:00:00Z",
        "claimed_at": "2026-04-17T10:30:00Z",
        "attempt": 2,
    }


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return load_schema()


@pytest.fixture(scope="module")
def validator(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(schema)


def js_validate(data: dict[str, Any], validator: Draft202012Validator) -> None:
    """Raise JSValidationError if invalid, else pass."""
    validator.validate(data)


# ---------------------------------------------------------------------------
# 1. Schema integrity
# ---------------------------------------------------------------------------


def test_schema_loads_from_file() -> None:
    raw = SCHEMA_PATH.read_text()
    parsed = json.loads(raw)
    assert parsed["$id"] == "https://sos.mumega.com/contracts/squad_task/v1"
    assert parsed["title"] == "SquadTask v1"


def test_load_schema_returns_dict() -> None:
    s = load_schema()
    assert isinstance(s, dict)
    assert s["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_schema_is_valid_draft_2020_12(schema: dict[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)


def test_schema_required_fields(schema: dict[str, Any]) -> None:
    required = set(schema["required"])
    assert required == {"schema_version", "id", "squad_id", "title", "status", "created_at"}


def test_schema_no_additional_properties(schema: dict[str, Any]) -> None:
    assert schema.get("additionalProperties") is False


# ---------------------------------------------------------------------------
# 2. Valid instances — JSON Schema
# ---------------------------------------------------------------------------


def test_minimal_passes_json_schema(validator: Draft202012Validator) -> None:
    js_validate(_minimal(), validator)


def test_full_passes_json_schema(validator: Draft202012Validator) -> None:
    js_validate(_full(), validator)


# ---------------------------------------------------------------------------
# 3. Status enum — JSON Schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [
    "backlog", "queued", "claimed", "in_progress",
    "review", "done", "blocked", "canceled", "failed",
])
def test_each_status_accepted_by_schema(
    status: str, validator: Draft202012Validator
) -> None:
    data = {**_minimal(), "status": status}
    js_validate(data, validator)


def test_invalid_status_rejected_by_schema(validator: Draft202012Validator) -> None:
    data = {**_minimal(), "status": "open"}  # "open" is NOT in the enum
    with pytest.raises(JSValidationError):
        js_validate(data, validator)


def test_invalid_status_uppercase_rejected(validator: Draft202012Validator) -> None:
    data = {**_minimal(), "status": "DONE"}
    with pytest.raises(JSValidationError):
        js_validate(data, validator)


# ---------------------------------------------------------------------------
# 4. Priority enum — JSON Schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("priority", ["critical", "high", "medium", "low"])
def test_each_priority_accepted_by_schema(
    priority: str, validator: Draft202012Validator
) -> None:
    data = {**_minimal(), "priority": priority}
    js_validate(data, validator)


def test_invalid_priority_rejected(validator: Draft202012Validator) -> None:
    data = {**_minimal(), "priority": "p0"}
    with pytest.raises(JSValidationError):
        js_validate(data, validator)


# ---------------------------------------------------------------------------
# 5. ID validation — JSON Schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("valid_id", [
    "task-abc-001",
    "abc",
    "task_001",
    "a1b2c3d",
    "123e4567-e89b-12d3-a456-426614174000",
])
def test_valid_ids_accepted(valid_id: str, validator: Draft202012Validator) -> None:
    data = {**_minimal(), "id": valid_id}
    js_validate(data, validator)


@pytest.mark.parametrize("bad_id", [
    "ab",          # too short (slug needs 3+ chars after first)
    "Task-001",    # uppercase not allowed in slug
    "task with spaces",
    "../etc/passwd",
    "",
])
def test_bad_ids_rejected(bad_id: str, validator: Draft202012Validator) -> None:
    data = {**_minimal(), "id": bad_id}
    with pytest.raises(JSValidationError):
        js_validate(data, validator)


# ---------------------------------------------------------------------------
# 6. bounty_micros — JSON Schema
# ---------------------------------------------------------------------------


def test_zero_bounty_micros_accepted(validator: Draft202012Validator) -> None:
    data = {**_minimal(), "bounty_micros": 0}
    js_validate(data, validator)


def test_positive_bounty_micros_accepted(validator: Draft202012Validator) -> None:
    data = {**_minimal(), "bounty_micros": 100_000_000}
    js_validate(data, validator)


def test_negative_bounty_micros_rejected(validator: Draft202012Validator) -> None:
    data = {**_minimal(), "bounty_micros": -1}
    with pytest.raises(JSValidationError):
        js_validate(data, validator)


# ---------------------------------------------------------------------------
# 7. Missing schema_version rejected
# ---------------------------------------------------------------------------


def test_missing_schema_version_rejected(validator: Draft202012Validator) -> None:
    data = {k: v for k, v in _minimal().items() if k != "schema_version"}
    with pytest.raises(JSValidationError):
        js_validate(data, validator)


def test_wrong_schema_version_rejected(validator: Draft202012Validator) -> None:
    data = {**_minimal(), "schema_version": "2"}
    with pytest.raises(JSValidationError):
        js_validate(data, validator)


# ---------------------------------------------------------------------------
# 8. Required fields — each missing one should fail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_field", [
    "id", "squad_id", "title", "status", "created_at",
])
def test_missing_required_field_rejected(
    missing_field: str, validator: Draft202012Validator
) -> None:
    data = {k: v for k, v in _minimal().items() if k != missing_field}
    with pytest.raises(JSValidationError):
        js_validate(data, validator)


# ---------------------------------------------------------------------------
# 9. Additional properties rejected
# ---------------------------------------------------------------------------


def test_additional_property_rejected(validator: Draft202012Validator) -> None:
    data = {**_minimal(), "unknown_field_xyz": "value"}
    with pytest.raises(JSValidationError):
        js_validate(data, validator)


# ---------------------------------------------------------------------------
# 10. Pydantic binding — valid cases
# ---------------------------------------------------------------------------


def test_pydantic_minimal_valid() -> None:
    task = parse_squad_task(_minimal())
    assert task.id == "task-abc-001"
    assert task.schema_version == "1"
    assert task.status == "backlog"
    assert task.priority == "medium"  # default


def test_pydantic_full_valid() -> None:
    task = parse_squad_task(_full())
    assert task.bounty_micros == 100_000_000
    assert task.labels == ["content", "marketing"]
    assert task.blocked_by == ["task-prereq-001"]
    assert task.blocks == ["task-downstream-002"]
    assert task.attempt == 2


# ---------------------------------------------------------------------------
# 11. Pydantic binding — invalid cases
# ---------------------------------------------------------------------------


def test_pydantic_bad_status_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        parse_squad_task({**_minimal(), "status": "open"})


def test_pydantic_bad_priority_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        parse_squad_task({**_minimal(), "priority": "urgent"})


def test_pydantic_bad_id_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        parse_squad_task({**_minimal(), "id": "AB"})


def test_pydantic_negative_bounty_micros_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        parse_squad_task({**_minimal(), "bounty_micros": -500})


def test_pydantic_negative_token_budget_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        parse_squad_task({**_minimal(), "token_budget": -1})


def test_pydantic_negative_attempt_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        parse_squad_task({**_minimal(), "attempt": -1})


# ---------------------------------------------------------------------------
# 12. Roundtrip: Pydantic → dict → jsonschema.validate
# ---------------------------------------------------------------------------


def test_roundtrip_minimal(validator: Draft202012Validator) -> None:
    task = parse_squad_task(_minimal())
    dumped = task.model_dump(exclude_none=True)
    js_validate(dumped, validator)


def test_roundtrip_full(validator: Draft202012Validator) -> None:
    task = parse_squad_task(_full())
    dumped = task.model_dump(exclude_none=True)
    js_validate(dumped, validator)


# ---------------------------------------------------------------------------
# 13. now_iso helper
# ---------------------------------------------------------------------------


def test_now_iso_returns_valid_timestamp() -> None:
    ts = SquadTaskV1.now_iso()
    assert "T" in ts
    # Should be parseable as ISO
    from datetime import datetime
    datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# 14. Real-world shape from squad service (reverse-engineered from row_to_task)
# ---------------------------------------------------------------------------


def test_realistic_squad_service_task_validates(validator: Draft202012Validator) -> None:
    """Mimics the dict shape that row_to_task() produces via asdict()."""
    realistic = {
        "schema_version": "1",
        "id": "a1b2c3d",
        "squad_id": "squad-marketing",
        "title": "Draft weekly blog post",
        "description": "600-word SOS automation explainer for mumega.com",
        "status": "claimed",
        "priority": "high",
        "assignee": "kasra",
        "skill_id": "mkt-blog-post-drafter",
        "project": "mumega",
        "labels": ["content", "blog"],
        "blocked_by": [],
        "blocks": [],
        "inputs": {
            "fuel_grade": "premium",
            "impact": 4,
            "urgency": 3,
            "cost": 2,
            "estimated_cost_cents": 30,
            "model": "sonnet",
        },
        "result": {},
        "token_budget": 8000,
        "bounty": {"reward": 50, "bounty_id": "bty-mkt-001"},
        "bounty_micros": 50_000_000,
        "external_ref": None,
        "created_at": "2026-04-17T08:00:00+00:00",
        "updated_at": "2026-04-17T08:05:00+00:00",
        "completed_at": None,
        "claimed_at": "2026-04-17T08:05:00+00:00",
        "attempt": 1,
    }
    # Remove null optional fields for schema validation (pattern field cannot match None)
    data_for_schema = {k: v for k, v in realistic.items() if v is not None}
    js_validate(data_for_schema, validator)
    # Full roundtrip via Pydantic
    task = parse_squad_task(realistic)
    assert task.status == "claimed"
    assert task.assignee == "kasra"


# ---------------------------------------------------------------------------
# 15. Dependency self-reference check (contract-level invariant)
# ---------------------------------------------------------------------------


def test_task_not_in_own_blocked_by(validator: Draft202012Validator) -> None:
    """A task blocking itself is semantically invalid.

    The JSON Schema itself can't express cross-field uniqueness, but
    we verify the shape passes schema and the Pydantic layer accepts
    the data — enforcement is the service's responsibility.
    This test documents the known gap.
    """
    task_id = "task-abc-001"
    data = {**_minimal(), "id": task_id, "blocked_by": [task_id]}
    # Schema allows it (no cross-field constraint at schema level)
    js_validate(data, validator)
    # Pydantic also allows it — service layer must enforce
    task = parse_squad_task(data)
    assert task_id in task.blocked_by
