"""Contract tests for UsageEvent v1 JSON Schema and Pydantic dataclass binding.

These tests are the freeze point: if they pass, any implementation that
emits records passing them is wire-compatible with the UsageEvent v1 schema.

JSON Schema source of truth: sos/contracts/schemas/usage_event_v1.json
Pydantic/dataclass binding:  sos/services/economy/usage_log.py
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from sos.services.economy.usage_log import UsageEvent

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SCHEMA_PATH = (
    Path(__file__).parents[2]
    / "sos"
    / "contracts"
    / "schemas"
    / "usage_event_v1.json"
)

_VALID_UUID = "123e4567-e89b-12d3-a456-426614174000"


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _minimal_data() -> dict[str, Any]:
    """Minimum fields required by the JSON Schema."""
    return {
        "schema_version": "1",
        "id": _VALID_UUID,
        "tenant": "acme",
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "occurred_at": "2026-04-17T12:00:00Z",
    }


def _full_data() -> dict[str, Any]:
    """All optional fields populated."""
    return {
        **_minimal_data(),
        "endpoint": "/api/archetype-report",
        "input_tokens": 1024,
        "output_tokens": 512,
        "image_count": 2,
        "cost_micros": 50000,
        "cost_currency": "USD",
        "metadata": {"request_id": "req-abc", "session_id": "sess-xyz"},
        "received_at": "2026-04-17T12:00:01Z",
    }


@pytest.fixture
def schema() -> dict[str, Any]:
    return load_schema()


@pytest.fixture
def minimal() -> dict[str, Any]:
    return _minimal_data()


@pytest.fixture
def full() -> dict[str, Any]:
    return _full_data()


# ---------------------------------------------------------------------------
# 1. Schema structure tests
# ---------------------------------------------------------------------------


class TestSchemaStructure:
    def test_load_schema_returns_dict_with_correct_title(self, schema):
        """load_schema() returns a dict with title == 'UsageEvent v1'."""
        assert isinstance(schema, dict)
        assert schema["title"] == "UsageEvent v1"

    def test_schema_is_draft_2020_12(self, schema):
        """Schema declares JSON Schema Draft 2020-12."""
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def test_required_fields_match(self, schema):
        """Required fields list matches the Pydantic dataclass required fields."""
        expected = {"schema_version", "id", "tenant", "provider", "model", "occurred_at"}
        assert set(schema["required"]) == expected

    def test_additional_properties_false_at_top(self, schema):
        """`additionalProperties: false` is set at the top level."""
        assert schema.get("additionalProperties") is False

    def test_minimal_valid_example_passes(self, schema):
        """A minimal-valid example passes jsonschema.validate."""
        jsonschema.validate(instance=_minimal_data(), schema=schema)

    def test_fully_populated_example_passes(self, schema):
        """A fully-populated example (all optional fields set) passes."""
        jsonschema.validate(instance=_full_data(), schema=schema)

    def test_schema_id_contains_usage_event(self, schema):
        """$id is set and references usage_event."""
        assert "$id" in schema
        assert "usage_event" in schema["$id"]

    def test_schema_version_enum_locked_to_1(self, schema):
        """schema_version property uses enum ['1']."""
        assert schema["properties"]["schema_version"]["enum"] == ["1"]


# ---------------------------------------------------------------------------
# 2. Validation — rejection cases
# ---------------------------------------------------------------------------


class TestValidationRejection:
    def test_invalid_tenant_empty_rejected(self, schema):
        """Invalid tenant (empty string) is rejected."""
        data = {**_minimal_data(), "tenant": ""}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_invalid_uuid_id_rejected(self, schema):
        """Invalid UUID id is rejected."""
        data = {**_minimal_data(), "id": "not-a-uuid"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_negative_cost_micros_rejected(self, schema):
        """Negative cost_micros is rejected."""
        data = {**_minimal_data(), "cost_micros": -1}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_lowercase_cost_currency_rejected(self, schema):
        """Invalid cost_currency (lowercase 'usd') is rejected."""
        data = {**_minimal_data(), "cost_currency": "usd"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_invalid_occurred_at_not_iso8601_rejected(self, schema):
        """Invalid occurred_at (not ISO 8601) is rejected."""
        data = {**_minimal_data(), "occurred_at": "April 17, 2026"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_missing_schema_version_rejected(self, schema):
        """Missing schema_version is rejected."""
        data = {k: v for k, v in _minimal_data().items() if k != "schema_version"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_negative_input_tokens_rejected(self, schema):
        """Negative input_tokens is rejected."""
        data = {**_minimal_data(), "input_tokens": -5}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_negative_image_count_rejected(self, schema):
        """Negative image_count is rejected."""
        data = {**_minimal_data(), "image_count": -1}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_schema_version_wrong_value_rejected(self, schema):
        """schema_version value other than '1' is rejected."""
        data = {**_minimal_data(), "schema_version": "2"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_additional_property_rejected(self, schema):
        """Unknown additional properties are rejected at top level."""
        data = {**_minimal_data(), "unknown_field": "surprise"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_cost_currency_with_special_chars_rejected(self, schema):
        """cost_currency with special characters (e.g. 'US$') is rejected."""
        data = {**_minimal_data(), "cost_currency": "US$"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_missing_required_provider_rejected(self, schema):
        """Missing required field 'provider' is rejected."""
        data = {k: v for k, v in _minimal_data().items() if k != "provider"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)


# ---------------------------------------------------------------------------
# 3. Pydantic dataclass roundtrip
# ---------------------------------------------------------------------------


class TestPydanticRoundtrip:
    def test_pydantic_roundtrip_minimal_passes_schema(self, schema):
        """Construct UsageEvent, to_dict(), validate against schema — passes."""
        event = UsageEvent(
            id=_VALID_UUID,
            tenant="acme",
            provider="anthropic",
            model="claude-sonnet-4-6",
        )
        data = event.to_dict()
        # Add schema_version for wire format
        data["schema_version"] = "1"
        jsonschema.validate(instance=data, schema=schema)

    def test_pydantic_roundtrip_full_passes_schema(self, schema):
        """Fully-populated UsageEvent roundtrip passes schema."""
        event = UsageEvent(
            id=_VALID_UUID,
            tenant="mumega",
            provider="google",
            model="gemini-flash-lite-latest",
            endpoint="/api/report",
            input_tokens=2048,
            output_tokens=1024,
            image_count=3,
            cost_micros=75000,
            cost_currency="USD",
            metadata={"request_id": "req-001"},
            occurred_at="2026-04-17T12:00:00+00:00",
            received_at="2026-04-17T12:00:01+00:00",
        )
        data = event.to_dict()
        data["schema_version"] = "1"
        jsonschema.validate(instance=data, schema=schema)

    def test_pydantic_to_dict_id_field_present(self):
        """UsageEvent.to_dict() includes the id field."""
        event = UsageEvent(tenant="t1", provider="openai", model="gpt-4o")
        d = event.to_dict()
        assert "id" in d
        assert len(d["id"]) == 36  # UUID string length

    def test_pydantic_defaults_cost_currency_usd(self):
        """UsageEvent defaults cost_currency to 'USD'."""
        event = UsageEvent(tenant="t1", provider="openai", model="gpt-4o")
        assert event.cost_currency == "USD"

    def test_pydantic_roundtrip_mind_currency_passes_schema(self, schema):
        """MIND currency code passes schema validation."""
        event = UsageEvent(
            id=_VALID_UUID,
            tenant="mumega",
            provider="mumega-marketplace",
            model="sos-agent-v1",
            cost_micros=1000,
            cost_currency="MIND",
        )
        data = event.to_dict()
        data["schema_version"] = "1"
        jsonschema.validate(instance=data, schema=schema)
