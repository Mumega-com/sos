"""JSON Schema snapshot test for the Objective contract (v0.8.0 baseline).

The committed file at sos/contracts/schemas/objective.schema.json is the
frozen contract that external consumers (Inkwell, CF D1 layer, etc.) depend on.

If this test fails, the Objective model changed in a way that alters its JSON
Schema. To regenerate the snapshot run:

    python3 -c '
from sos.contracts.objective import Objective
import json
print(json.dumps(Objective.model_json_schema(), indent=2, sort_keys=True))
' > sos/contracts/schemas/objective.schema.json

Then commit the updated file with a CHANGELOG entry explaining the contract
change and notifying downstream consumers.
"""
from __future__ import annotations

import json
from pathlib import Path

from sos.contracts.objective import Objective

_SCHEMA_PATH = (
    Path(__file__).parent.parent.parent
    / "sos"
    / "contracts"
    / "schemas"
    / "objective.schema.json"
)

_EXPECTED_PROPERTIES = {
    "id",
    "parent_id",
    "title",
    "description",
    "bounty_mind",
    "state",
    "holder_agent",
    "holder_heartbeat_at",
    "subscribers",
    "tags",
    "capabilities_required",
    "completion_artifact_url",
    "completion_notes",
    "acks",
    "done_when",
    "created_by",
    "created_at",
    "updated_at",
    "tenant_id",
    "project",
    "outcome_score",
}


def test_schema_file_is_valid_json() -> None:
    """The committed snapshot must parse as valid JSON."""
    assert _SCHEMA_PATH.exists(), f"Schema file missing: {_SCHEMA_PATH}"
    schema = json.loads(_SCHEMA_PATH.read_text())
    assert schema["type"] == "object"
    assert "properties" in schema


def test_schema_snapshot_matches_model() -> None:
    """Live model_json_schema() must match the committed snapshot exactly.

    If this fails, the Objective contract changed. Either revert the model
    change or regenerate the snapshot (see module docstring) and commit it
    with a CHANGELOG entry.
    """
    committed = json.loads(_SCHEMA_PATH.read_text())
    live = json.loads(
        json.dumps(Objective.model_json_schema(), indent=2, sort_keys=True)
    )
    assert live == committed, (
        "Objective JSON Schema has drifted from the committed snapshot.\n"
        "To regenerate:\n"
        "  python3 -c '"
        "from sos.contracts.objective import Objective; import json; "
        "print(json.dumps(Objective.model_json_schema(), indent=2, sort_keys=True))' "
        "> sos/contracts/schemas/objective.schema.json\n"
        "Then commit with a CHANGELOG entry."
    )


def test_schema_contains_all_21_fields() -> None:
    """The committed snapshot must expose all 21 Objective fields."""
    schema = json.loads(_SCHEMA_PATH.read_text())
    actual = set(schema["properties"].keys())
    missing = _EXPECTED_PROPERTIES - actual
    assert not missing, f"Schema is missing fields: {missing}"
    assert len(actual) == 21, f"Expected 21 properties, got {len(actual)}: {actual}"


def test_required_fields_present() -> None:
    """Required fields (id, title, created_by, created_at, updated_at) must be declared."""
    schema = json.loads(_SCHEMA_PATH.read_text())
    required = set(schema.get("required", []))
    assert {"id", "title", "created_by", "created_at", "updated_at"} <= required
