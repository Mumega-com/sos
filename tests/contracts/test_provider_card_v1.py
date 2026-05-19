"""Contract tests for ProviderCard v1 JSON Schema and Pydantic binding roundtrip.

These tests are the freeze point: if they pass, any implementation that
emits records passing them is wire-compatible with the ProviderCard v1 schema.

JSON Schema source of truth: sos/contracts/schemas/provider_card_v1.json
Pydantic binding:            sos/providers/matrix.py  (ProviderCard)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from sos.providers.matrix import ProviderCard, load_schema

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SCHEMA_PATH = (
    Path(__file__).parents[2]
    / "sos"
    / "contracts"
    / "schemas"
    / "provider_card_v1.json"
)

PROVIDERS_YAML_PATH = (
    Path(__file__).parents[2] / "sos" / "providers" / "providers.yaml"
)


def _load_yaml_providers() -> list[dict[str, Any]]:
    raw = yaml.safe_load(PROVIDERS_YAML_PATH.read_text())
    providers_raw = raw.get("providers", raw) if isinstance(raw, dict) else raw
    return providers_raw


def _minimal_data() -> dict[str, Any]:
    """Minimum fields required by the JSON Schema."""
    return {
        "schema_version": "1",
        "id": "my-provider",
        "name": "My Provider",
        "backend": "claude-adapter",
        "tier": "primary",
        "model": "claude-sonnet-4-6",
    }


@pytest.fixture
def schema() -> dict[str, Any]:
    return load_schema()


@pytest.fixture
def yaml_providers() -> list[dict[str, Any]]:
    return _load_yaml_providers()


# ---------------------------------------------------------------------------
# 1. Schema structure tests
# ---------------------------------------------------------------------------


class TestSchemaStructure:
    def test_load_schema_returns_dict_with_correct_title(self, schema):
        """load_schema() returns a dict with title == 'ProviderCard v1'."""
        assert isinstance(schema, dict)
        assert schema["title"] == "ProviderCard v1"

    def test_schema_is_draft_2020_12(self, schema):
        """Schema declares JSON Schema Draft 2020-12."""
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def test_required_fields_match(self, schema):
        """Required fields list matches the expected set."""
        expected = {"schema_version", "id", "name", "backend", "tier", "model"}
        assert set(schema["required"]) == expected

    def test_additional_properties_false_at_top(self, schema):
        """`additionalProperties: false` is set at the top level."""
        assert schema.get("additionalProperties") is False

    def test_schema_version_enum_locked_to_1(self, schema):
        """schema_version property uses enum ['1']."""
        assert schema["properties"]["schema_version"]["enum"] == ["1"]

    def test_schema_id_contains_provider_card(self, schema):
        """$id is set and references provider_card."""
        assert "$id" in schema
        assert "provider_card" in schema["$id"]


# ---------------------------------------------------------------------------
# 2. YAML provider entries — all 6 validate
# ---------------------------------------------------------------------------


class TestYamlProviderEntries:
    def test_all_yaml_entries_validate(self, schema, yaml_providers):
        """Each of the providers.yaml entries validates against the schema when schema_version is added."""
        assert len(yaml_providers) >= 6, "Expected at least 6 providers in providers.yaml"
        for entry in yaml_providers:
            data = {**entry, "schema_version": "1"}
            jsonschema.validate(instance=data, schema=schema)

    def test_claude_opus_entry_validates(self, schema, yaml_providers):
        """claude-opus-47 entry validates."""
        entry = next(e for e in yaml_providers if e["id"] == "claude-opus-47")
        data = {**entry, "schema_version": "1"}
        jsonschema.validate(instance=data, schema=schema)

    def test_gemini_flash_entry_validates(self, schema, yaml_providers):
        """gemini-25-flash entry validates."""
        entry = next(e for e in yaml_providers if e["id"] == "gemini-25-flash")
        data = {**entry, "schema_version": "1"}
        jsonschema.validate(instance=data, schema=schema)

    def test_claude_managed_fallback_validates(self, schema, yaml_providers):
        """claude-managed-primary (fallback tier) validates."""
        entry = next(e for e in yaml_providers if e["id"] == "claude-managed-primary")
        data = {**entry, "schema_version": "1"}
        jsonschema.validate(instance=data, schema=schema)


# ---------------------------------------------------------------------------
# 3. Validation — rejection cases
# ---------------------------------------------------------------------------


class TestValidationRejection:
    def test_invalid_backend_enum_rejected(self, schema):
        """Invalid backend value is rejected."""
        data = {**_minimal_data(), "backend": "unknown-backend"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_invalid_tier_enum_rejected(self, schema):
        """Invalid tier value is rejected."""
        data = {**_minimal_data(), "tier": "ultra"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_bad_id_pattern_rejected(self, schema):
        """id not matching ^[a-z][a-z0-9-]*$ is rejected."""
        data = {**_minimal_data(), "id": "My-Provider"}  # uppercase — invalid
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_id_starting_with_digit_rejected(self, schema):
        """id starting with a digit is rejected."""
        data = {**_minimal_data(), "id": "1provider"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_negative_cost_per_call_estimate_micros_rejected(self, schema):
        """Negative cost_per_call_estimate_micros is rejected."""
        data = {**_minimal_data(), "cost_per_call_estimate_micros": -1}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_timeout_seconds_above_3600_rejected(self, schema):
        """timeout_seconds > 3600 is rejected."""
        data = {**_minimal_data(), "timeout_seconds": 3601}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_missing_schema_version_rejected(self, schema):
        """Missing schema_version is rejected."""
        data = {k: v for k, v in _minimal_data().items() if k != "schema_version"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)

    def test_additional_property_rejected(self, schema):
        """Unknown additional properties are rejected at top level."""
        data = {**_minimal_data(), "unknown_field": "surprise"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)


# ---------------------------------------------------------------------------
# 4. Circuit breaker sub-schema
# ---------------------------------------------------------------------------


class TestCircuitBreakerSubSchema:
    def test_circuit_breaker_absent_is_valid(self, schema):
        """Omitting circuit_breaker entirely is valid (optional field)."""
        data = _minimal_data()
        jsonschema.validate(instance=data, schema=schema)

    def test_circuit_breaker_present_and_valid(self, schema):
        """A well-formed circuit_breaker sub-object validates."""
        data = {
            **_minimal_data(),
            "circuit_breaker": {
                "failure_threshold": 5,
                "recovery_window_seconds": 60,
                "half_open_max_requests": 1,
            },
        }
        jsonschema.validate(instance=data, schema=schema)

    def test_circuit_breaker_negative_failure_threshold_rejected(self, schema):
        """Negative failure_threshold in circuit_breaker is rejected."""
        data = {
            **_minimal_data(),
            "circuit_breaker": {"failure_threshold": -1},
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=data, schema=schema)


# ---------------------------------------------------------------------------
# 5. Pydantic roundtrip tests
# ---------------------------------------------------------------------------


class TestPydanticRoundtrip:
    def test_pydantic_roundtrip_minimal_passes_schema(self, schema):
        """Construct minimal ProviderCard, model_dump(), validate against schema — passes."""
        card = ProviderCard(
            id="test-provider",
            name="Test Provider",
            backend="claude-adapter",
            tier="primary",
            model="claude-sonnet-4-6",
        )
        data = card.model_dump(exclude_none=True)
        data["schema_version"] = "1"
        jsonschema.validate(instance=data, schema=schema)

    def test_pydantic_roundtrip_all_yaml_entries_pass_schema(self, schema, yaml_providers):
        """ProviderCard.model_validate(entry).model_dump() validates for each providers.yaml entry."""
        for entry in yaml_providers:
            card = ProviderCard.model_validate(entry)
            data = card.model_dump(exclude_none=True)
            data["schema_version"] = "1"
            jsonschema.validate(instance=data, schema=schema)

    def test_pydantic_roundtrip_with_circuit_breaker(self, schema):
        """ProviderCard with explicit circuit_breaker roundtrips cleanly."""
        card = ProviderCard(
            id="cb-provider",
            name="CB Provider",
            backend="openai-adapter",
            tier="fallback",
            model="gpt-5-mini",
            timeout_seconds=30,
            cost_per_call_estimate_micros=1200,
        )
        data = card.model_dump(exclude_none=True)
        # circuit_breaker is always present (default_factory)
        data["schema_version"] = "1"
        jsonschema.validate(instance=data, schema=schema)

    def test_pydantic_roundtrip_local_tier(self, schema):
        """Local tier ProviderCard roundtrips cleanly."""
        card = ProviderCard(
            id="local-llm",
            name="Local LLM",
            backend="local",
            tier="local",
            model="llama-3-8b",
        )
        data = card.model_dump(exclude_none=True)
        data["schema_version"] = "1"
        jsonschema.validate(instance=data, schema=schema)
