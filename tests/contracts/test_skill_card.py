"""Contract tests for SkillCard v1 JSON Schema and Pydantic binding.

These tests are the freeze point: if they pass, any implementation that
emits records passing them is wire-compatible with the SkillCard v1 schema.

JSON Schema source of truth: sos/contracts/schemas/skill_card_v1.json
Pydantic binding:             sos/contracts/skill_card.py
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import ValidationError

from sos.contracts.skill_card import (
    CommerceInfo,
    EarningsInfo,
    LineageEntry,
    RevenueSplit,
    RuntimeInfo,
    SkillCard,
    VerificationInfo,
    load_schema,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SCHEMA_PATH = Path(__file__).parents[2] / "sos" / "contracts" / "schemas" / "skill_card_v1.json"


def _minimal_kwargs() -> dict[str, Any]:
    """Minimum required fields for a valid SkillCard."""
    return {
        "schema_version": "1",
        "id": "skill-abc-123",
        "skill_descriptor_id": "skill-abc-123",
        "name": "Test Skill",
        "version": "1.0.0",
        "author_agent": "agent:codex",
        "created_at": "2026-04-17T00:00:00Z",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }


def _full_kwargs() -> dict[str, Any]:
    """All optional fields populated."""
    return {
        **_minimal_kwargs(),
        "description": "A full-featured skill for testing.",
        "tags": ["ai", "test", "sos"],
        "authored_by_ai": True,
        "updated_at": "2026-04-17T01:00:00Z",
        "lineage": [
            {"parent_skill_id": "skill-parent-001", "relation": "forked"},
        ],
        "earnings": {
            "total_invocations": 42,
            "total_earned_micros": 1000000,
            "currency": "USD",
            "last_invocation_at": "2026-04-16T12:00:00Z",
            "invocations_by_tenant": {"tenant-a": 30, "tenant-b": 12},
        },
        "verification": {
            "status": "human_verified",
            "sample_output_refs": ["engram:abc123"],
            "verified_by": ["agent:sentinel", "human:hadi"],
            "verified_at": "2026-04-16T12:00:00Z",
            "dispute_reason": None,
        },
        "required_tools": ["search_code", "read_file"],
        "required_models": ["claude-sonnet-4-6"],
        "commerce": {
            "price_per_call_micros": 25000,
            "currency": "USD",
            "revenue_split": {"author": 0.7, "operator": 0.2, "network": 0.1},
            "marketplace_listed": True,
        },
        "runtime": {
            "entry_point": "sos/skills/test/main.py",
            "backend": "claude-code",
            "timeout_seconds": 120,
            "memory_mb": 256,
        },
        "metadata": {"owner_slack": "#dev", "tier": "premium"},
    }


@pytest.fixture
def schema() -> dict[str, Any]:
    return load_schema()


@pytest.fixture
def minimal() -> dict[str, Any]:
    return _minimal_kwargs()


@pytest.fixture
def full() -> dict[str, Any]:
    return _full_kwargs()


# ---------------------------------------------------------------------------
# 1. Schema tests
# ---------------------------------------------------------------------------


class TestSchemaStructure:
    def test_load_schema_returns_dict(self, schema):
        assert isinstance(schema, dict)

    def test_schema_title(self, schema):
        assert schema["title"] == "SkillCard v1"

    def test_schema_draft_2020_12(self, schema):
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def test_additional_properties_false_at_top_level(self, schema):
        assert schema.get("additionalProperties") is False

    def test_required_fields_match(self, schema):
        expected = {
            "schema_version", "id", "skill_descriptor_id", "name", "version",
            "author_agent", "created_at",
        }
        assert set(schema["required"]) == expected

    def test_minimal_example_validates_against_schema(self, schema):
        """jsonschema.validate must not raise for a minimal valid document."""
        jsonschema.validate(instance=_minimal_kwargs(), schema=schema)

    def test_full_example_validates_against_schema(self, schema):
        """A fully-populated SkillCard must also pass JSON Schema validation."""
        # strip None values that aren't in the JSON schema
        data = {k: v for k, v in _full_kwargs().items() if v is not None}
        # drop nested None (verification.dispute_reason)
        if "verification" in data:
            data["verification"] = {k: v for k, v in data["verification"].items() if v is not None}
        jsonschema.validate(instance=data, schema=schema)

    def test_schema_id_is_present(self, schema):
        assert "$id" in schema
        assert "skill_card" in schema["$id"]


# ---------------------------------------------------------------------------
# 2. Pydantic construction — happy path
# ---------------------------------------------------------------------------


class TestSkillCardHappyPath:
    def test_construct_minimal(self):
        card = SkillCard(**_minimal_kwargs())
        assert card.id == "skill-abc-123"
        assert card.name == "Test Skill"
        assert card.version == "1.0.0"
        assert card.author_agent == "agent:codex"

    def test_construct_full(self):
        card = SkillCard(**_full_kwargs())
        assert card.authored_by_ai is True
        assert card.tags == ["ai", "test", "sos"]
        assert card.earnings is not None
        assert card.verification is not None
        assert card.commerce is not None
        assert card.runtime is not None
        assert card.metadata is not None

    def test_now_iso_returns_iso_string(self):
        ts = SkillCard.now_iso()
        assert isinstance(ts, str)
        # Must be parseable as ISO 8601
        from datetime import datetime
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.year >= 2026

    def test_now_iso_method_on_skill_card(self):
        """SkillCard exposes now_iso() as a static/class helper."""
        ts = SkillCard.now_iso()
        assert isinstance(ts, str)
        assert "T" in ts

    def test_optional_fields_default_to_none(self):
        card = SkillCard(**_minimal_kwargs())
        assert card.description is None
        assert card.tags is None
        assert card.lineage is None
        assert card.earnings is None
        assert card.verification is None
        assert card.commerce is None
        assert card.runtime is None
        assert card.metadata is None

    def test_authored_by_ai_defaults_false(self):
        card = SkillCard(**_minimal_kwargs())
        assert card.authored_by_ai is False


# ---------------------------------------------------------------------------
# 3. Pydantic validation — rejection cases
# ---------------------------------------------------------------------------


class TestSkillCardRejection:
    def test_invalid_semver_version(self):
        with pytest.raises((ValidationError, Exception)):
            SkillCard(**{**_minimal_kwargs(), "version": "not-semver"})

    def test_invalid_semver_missing_patch(self):
        with pytest.raises((ValidationError, Exception)):
            SkillCard(**{**_minimal_kwargs(), "version": "1.0"})

    def test_author_agent_wrong_prefix(self):
        with pytest.raises((ValidationError, Exception)):
            SkillCard(**{**_minimal_kwargs(), "author_agent": "user:codex"})

    def test_author_agent_uppercase_rejected(self):
        with pytest.raises((ValidationError, Exception)):
            SkillCard(**{**_minimal_kwargs(), "author_agent": "agent:Codex"})

    def test_author_agent_spaces_rejected(self):
        with pytest.raises((ValidationError, Exception)):
            SkillCard(**{**_minimal_kwargs(), "author_agent": "agent:code x"})

    def test_name_too_long(self):
        with pytest.raises((ValidationError, Exception)):
            SkillCard(**{**_minimal_kwargs(), "name": "x" * 201})

    def test_tags_too_many(self):
        with pytest.raises((ValidationError, Exception)):
            SkillCard(**{**_minimal_kwargs(), "tags": [f"tag{i}" for i in range(21)]})

    def test_lineage_invalid_relation_enum(self):
        with pytest.raises((ValidationError, Exception)):
            SkillCard(
                **{
                    **_minimal_kwargs(),
                    "lineage": [{"parent_skill_id": "skill-x", "relation": "stolen"}],
                }
            )

    def test_verification_status_invalid_enum(self):
        with pytest.raises((ValidationError, Exception)):
            SkillCard(
                **{
                    **_minimal_kwargs(),
                    "verification": {"status": "maybe"},
                }
            )

    def test_verified_by_invalid_pattern(self):
        with pytest.raises((ValidationError, Exception)):
            SkillCard(
                **{
                    **_minimal_kwargs(),
                    "verification": {
                        "status": "human_verified",
                        "verified_by": ["robot:r2d2"],
                    },
                }
            )

    def test_commerce_price_per_call_micros_negative(self):
        with pytest.raises((ValidationError, Exception)):
            SkillCard(
                **{
                    **_minimal_kwargs(),
                    "commerce": {"price_per_call_micros": -1},
                }
            )

    def test_runtime_backend_invalid_enum(self):
        with pytest.raises((ValidationError, Exception)):
            SkillCard(
                **{
                    **_minimal_kwargs(),
                    "runtime": {"backend": "tensorflow"},
                }
            )

    def test_runtime_timeout_seconds_exceeds_max(self):
        with pytest.raises((ValidationError, Exception)):
            SkillCard(
                **{
                    **_minimal_kwargs(),
                    "runtime": {"timeout_seconds": 3601},
                }
            )

    def test_missing_required_id(self):
        kwargs = {k: v for k, v in _minimal_kwargs().items() if k != "id"}
        with pytest.raises((ValidationError, Exception)):
            SkillCard(**kwargs)

    def test_missing_required_skill_descriptor_id(self):
        kwargs = {k: v for k, v in _minimal_kwargs().items() if k != "skill_descriptor_id"}
        with pytest.raises((ValidationError, Exception)):
            SkillCard(**kwargs)


# ---------------------------------------------------------------------------
# 4. Submodel tests
# ---------------------------------------------------------------------------


class TestSubmodels:
    def test_lineage_entry_forked(self):
        entry = LineageEntry(parent_skill_id="skill-001", relation="forked")
        assert entry.relation == "forked"

    def test_lineage_entry_refined(self):
        entry = LineageEntry(parent_skill_id="skill-002", relation="refined")
        assert entry.relation == "refined"

    def test_lineage_entry_composed(self):
        entry = LineageEntry(parent_skill_id="skill-003", relation="composed")
        assert entry.relation == "composed"

    def test_lineage_entry_inspired_by(self):
        entry = LineageEntry(parent_skill_id="skill-004", relation="inspired_by")
        assert entry.relation == "inspired_by"

    def test_earnings_info_defaults(self):
        earnings = EarningsInfo()
        assert earnings.total_invocations == 0
        assert earnings.currency == "USD"
        assert earnings.last_invocation_at is None

    def test_verification_info_defaults_unverified(self):
        v = VerificationInfo()
        assert v.status == "unverified"

    def test_verification_info_all_statuses(self):
        for status in ("unverified", "auto_verified", "human_verified", "disputed"):
            v = VerificationInfo(status=status)
            assert v.status == status

    def test_commerce_info_revenue_split(self):
        split = RevenueSplit(author=0.7, operator=0.2, network=0.1)
        c = CommerceInfo(price_per_call_micros=50000, revenue_split=split)
        assert c.revenue_split is not None
        assert c.revenue_split.author == 0.7
        assert c.price_per_call_micros == 50000

    def test_runtime_info_valid_backends(self):
        valid_backends = [
            "claude-code", "cma", "openai-agents-sdk",
            "langgraph", "crewai", "local-python", "custom",
        ]
        for backend in valid_backends:
            r = RuntimeInfo(backend=backend)
            assert r.backend == backend


# ---------------------------------------------------------------------------
# 5. Round-trip tests
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_model_dump_reconstructs_equivalent_model(self):
        original = SkillCard(**_minimal_kwargs())
        dump = original.model_dump()
        reconstructed = SkillCard(**dump)
        assert reconstructed.id == original.id
        assert reconstructed.name == original.name
        assert reconstructed.version == original.version
        assert reconstructed.author_agent == original.author_agent

    def test_full_model_dump_reconstructs_equivalent_model(self):
        original = SkillCard(**_full_kwargs())
        dump = original.model_dump()
        reconstructed = SkillCard(**dump)
        assert reconstructed.id == original.id
        assert reconstructed.tags == original.tags
        assert reconstructed.authored_by_ai == original.authored_by_ai

    def test_model_dump_passes_json_schema_validation(self):
        schema = load_schema()
        card = SkillCard(**_minimal_kwargs())
        dump = card.model_dump(exclude_none=True)
        # JSON Schema validate — must not raise
        jsonschema.validate(instance=dump, schema=schema)

    def test_full_model_dump_passes_json_schema_validation(self):
        schema = load_schema()
        card = SkillCard(**_full_kwargs())
        dump = card.model_dump(exclude_none=True)
        jsonschema.validate(instance=dump, schema=schema)


# ---------------------------------------------------------------------------
# 6. Athena gate blockers (2026-04-17 review)
# ---------------------------------------------------------------------------


class TestAthenaGateInvariants:
    """Covers the 4 blockers + key nits raised by Athena's architectural review."""

    # Blocker 1: verified_by pattern must match author_agent shape (start with letter)
    def test_verified_by_rejects_identifier_starting_with_digit(self):
        kw = _minimal_kwargs()
        kw["verification"] = {"verified_by": ["agent:9bad"]}
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    def test_verified_by_rejects_identifier_starting_with_hyphen(self):
        kw = _minimal_kwargs()
        kw["verification"] = {"verified_by": ["human:-bad"]}
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    def test_verified_by_accepts_letter_starting_identifier(self):
        kw = _minimal_kwargs()
        kw["verification"] = {"verified_by": ["agent:sentinel", "human:hadi"]}
        SkillCard(**kw)  # must not raise

    # Blocker 2: revenue_split must sum to 1.0 ±0.001
    def test_revenue_split_author_only_underpays_network_raises(self):
        kw = _minimal_kwargs()
        kw["commerce"] = {"price_per_call_micros": 100, "revenue_split": {"author": 0.1}}
        with pytest.raises(ValidationError) as exc:
            SkillCard(**kw)
        assert "sum to 1.0" in str(exc.value)

    def test_revenue_split_above_one_raises(self):
        kw = _minimal_kwargs()
        kw["commerce"] = {"revenue_split": {"author": 0.5, "operator": 0.4, "network": 0.3}}
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    def test_revenue_split_sum_to_one_accepted(self):
        kw = _minimal_kwargs()
        kw["commerce"] = {"revenue_split": {"author": 0.7, "operator": 0.2, "network": 0.1}}
        SkillCard(**kw)  # must not raise

    def test_revenue_split_empty_accepted(self):
        # all None is valid — split is simply unspecified
        kw = _minimal_kwargs()
        kw["commerce"] = {"revenue_split": {}}
        SkillCard(**kw)

    # Blocker 3: currency must match uppercase alphanumeric pattern
    def test_lowercase_currency_rejected(self):
        kw = _minimal_kwargs()
        kw["earnings"] = {"currency": "usd"}
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    def test_dollar_sign_currency_rejected(self):
        kw = _minimal_kwargs()
        kw["commerce"] = {"currency": "US$"}
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    def test_mind_currency_accepted(self):
        kw = _minimal_kwargs()
        kw["earnings"] = {"currency": "MIND"}
        SkillCard(**kw)

    def test_operator_defined_currency_accepted(self):
        kw = _minimal_kwargs()
        kw["earnings"] = {"currency": "CREDITS"}
        SkillCard(**kw)

    # Blocker 4: schema_version required + locked to "1"
    def test_schema_version_defaults_to_one(self):
        kw = _minimal_kwargs()
        del kw["schema_version"]
        card = SkillCard(**kw)
        assert card.schema_version == "1"

    def test_schema_version_rejects_other_values(self):
        kw = _minimal_kwargs()
        kw["schema_version"] = "2"
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    def test_schema_version_on_json_schema_is_required(self):
        schema = load_schema()
        assert "schema_version" in schema["required"]

    # Nit 9: id pattern blocks malicious / whitespace inputs
    def test_id_with_spaces_rejected(self):
        kw = _minimal_kwargs()
        kw["id"] = "  "
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    def test_id_with_path_traversal_rejected(self):
        kw = _minimal_kwargs()
        kw["id"] = "../../etc/passwd"
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    def test_id_uuid_v4_accepted(self):
        kw = _minimal_kwargs()
        kw["id"] = "123e4567-e89b-12d3-a456-426614174000"
        SkillCard(**kw)

    # Nit 10: marketplace_listed requires runtime.entry_point
    def test_marketplace_listed_without_entry_point_rejected(self):
        kw = _minimal_kwargs()
        kw["commerce"] = {"marketplace_listed": True}
        # no runtime at all
        with pytest.raises(ValidationError) as exc:
            SkillCard(**kw)
        assert "entry_point" in str(exc.value)

    def test_marketplace_listed_with_runtime_no_entry_point_rejected(self):
        kw = _minimal_kwargs()
        kw["commerce"] = {"marketplace_listed": True}
        kw["runtime"] = {"backend": "claude-code"}
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    def test_marketplace_listed_with_entry_point_accepted(self):
        kw = _minimal_kwargs()
        kw["commerce"] = {"marketplace_listed": True}
        kw["runtime"] = {"entry_point": "sos/skills/x.py", "backend": "claude-code"}
        SkillCard(**kw)

    # Nit 11: input_schema / output_schema must carry '$schema' or 'type'
    def test_input_schema_without_schema_or_type_rejected(self):
        kw = _minimal_kwargs()
        kw["input_schema"] = {"foo": "bar"}
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    def test_input_schema_with_schema_field_accepted(self):
        kw = _minimal_kwargs()
        kw["input_schema"] = {"$schema": "https://json-schema.org/draft/2020-12/schema"}
        SkillCard(**kw)

    def test_output_schema_without_schema_or_type_rejected(self):
        kw = _minimal_kwargs()
        kw["output_schema"] = {"random": "dict"}
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    # Nit 5+6: per-item constraints + lineage max
    def test_empty_string_tag_rejected(self):
        kw = _minimal_kwargs()
        kw["tags"] = ["valid", "  ", "also-valid"]
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    def test_lineage_over_20_entries_rejected(self):
        kw = _minimal_kwargs()
        kw["lineage"] = [
            {"parent_skill_id": f"skill-{i}", "relation": "forked"} for i in range(21)
        ]
        with pytest.raises(ValidationError):
            SkillCard(**kw)


# ---------------------------------------------------------------------------
# 7. SkillDescriptor overlay invariants (Island #2 — 2026-04-18)
# ---------------------------------------------------------------------------


class TestSkillDescriptorOverlay:
    """SkillCard is a provenance/commerce overlay; skill_descriptor_id is required."""

    def test_skill_descriptor_id_required(self):
        """Missing skill_descriptor_id must raise ValidationError."""
        kwargs = {k: v for k, v in _minimal_kwargs().items() if k != "skill_descriptor_id"}
        with pytest.raises((ValidationError, Exception)):
            SkillCard(**kwargs)

    def test_skill_descriptor_id_format_slug(self):
        """skill_descriptor_id must match the id slug pattern."""
        kw = _minimal_kwargs()
        kw["skill_descriptor_id"] = "valid-descriptor-id-v1"
        card = SkillCard(**kw)
        assert card.skill_descriptor_id == "valid-descriptor-id-v1"

    def test_skill_descriptor_id_format_invalid_rejected(self):
        """skill_descriptor_id with invalid characters must raise ValidationError."""
        kw = _minimal_kwargs()
        kw["skill_descriptor_id"] = "../../etc/passwd"
        with pytest.raises((ValidationError, Exception)):
            SkillCard(**kw)

    def test_input_output_schemas_no_longer_required(self):
        """SkillCard without input_schema / output_schema is valid (overlay only)."""
        kw = {k: v for k, v in _minimal_kwargs().items()
              if k not in ("input_schema", "output_schema")}
        card = SkillCard(**kw)
        assert card.input_schema is None
        assert card.output_schema is None

    def test_resolve_descriptor_stub_returns_none(self):
        """resolve_descriptor() is a stub that returns None until wired to squad-service."""
        card = SkillCard(**_minimal_kwargs())
        assert card.resolve_descriptor() is None

    def test_skill_descriptor_id_in_json_schema_required(self):
        """JSON Schema must list skill_descriptor_id as required."""
        schema = load_schema()
        assert "skill_descriptor_id" in schema["required"]

    def test_input_schema_not_in_json_schema_required(self):
        """JSON Schema must NOT list input_schema as required (it's now optional echo)."""
        schema = load_schema()
        assert "input_schema" not in schema["required"]

    def test_output_schema_not_in_json_schema_required(self):
        """JSON Schema must NOT list output_schema as required (it's now optional echo)."""
        schema = load_schema()
        assert "output_schema" not in schema["required"]


# ---------------------------------------------------------------------------
# 8. Artifact CID / sample_output_refs (Island #6 — 2026-04-18)
# ---------------------------------------------------------------------------


class TestArtifactCIDRefs:
    """Validate the SkillCard.verification.sample_output_refs pattern upgrade."""

    _VALID_CID = "a" * 64  # 64-char hex

    def _kw_with_refs(self, refs: list[str]) -> dict[str, Any]:
        kw = _minimal_kwargs()
        kw["verification"] = {"sample_output_refs": refs}
        return kw

    # ---- acceptance ----

    def test_valid_artifact_cid_ref_accepted(self):
        """artifact:<64-hex> must be accepted."""
        SkillCard(**self._kw_with_refs([f"artifact:{self._VALID_CID}"]))

    def test_valid_engram_legacy_ref_accepted(self):
        """engram:<slug> must remain accepted (backward-compat)."""
        SkillCard(**self._kw_with_refs(["engram:gaf-metrobit-estimate-2025"]))

    def test_multiple_mixed_refs_accepted(self):
        """A mix of artifact: and engram: refs must all pass."""
        refs = [
            f"artifact:{self._VALID_CID}",
            "engram:dnu-test-123",
        ]
        SkillCard(**self._kw_with_refs(refs))

    # ---- rejection ----

    def test_invalid_format_rejected(self):
        """Bare strings without a recognized prefix must be rejected."""
        with pytest.raises(ValidationError):
            SkillCard(**self._kw_with_refs(["random-string"]))

    def test_artifact_with_short_cid_rejected(self):
        """artifact: CID shorter than 64 hex chars must be rejected."""
        with pytest.raises(ValidationError):
            SkillCard(**self._kw_with_refs(["artifact:abc123"]))

    def test_engram_with_uppercase_rejected(self):
        """engram slugs must be lowercase."""
        with pytest.raises(ValidationError):
            SkillCard(**self._kw_with_refs(["engram:BAD-SLUG"]))

    def test_engram_starting_with_hyphen_rejected(self):
        """engram slugs must start with a letter or digit."""
        with pytest.raises(ValidationError):
            SkillCard(**self._kw_with_refs(["engram:-starts-wrong"]))

    # ---- primary_artifact_cid ----

    def test_primary_artifact_cid_pattern_validated(self):
        """primary_artifact_cid must be exactly 64 lowercase hex chars."""
        kw = _minimal_kwargs()
        kw["verification"] = {"primary_artifact_cid": self._VALID_CID}
        card = SkillCard(**kw)
        assert card.verification is not None
        assert card.verification.primary_artifact_cid == self._VALID_CID

    def test_primary_artifact_cid_uppercase_rejected(self):
        kw = _minimal_kwargs()
        kw["verification"] = {"primary_artifact_cid": "A" * 64}
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    def test_primary_artifact_cid_short_rejected(self):
        kw = _minimal_kwargs()
        kw["verification"] = {"primary_artifact_cid": "abc123"}
        with pytest.raises(ValidationError):
            SkillCard(**kw)

    def test_primary_artifact_cid_optional_none_accepted(self):
        kw = _minimal_kwargs()
        kw["verification"] = {}
        card = SkillCard(**kw)
        assert card.verification is not None
        assert card.verification.primary_artifact_cid is None

    # ---- resolve_artifacts helper ----

    def test_resolve_artifacts_filters_non_artifact_refs(self):
        """resolve_artifacts() must only call registry for artifact: refs."""
        from unittest.mock import MagicMock, patch

        v = VerificationInfo(
            sample_output_refs=[
                f"artifact:{self._VALID_CID}",
                "engram:legacy-ref",
            ]
        )

        fake_manifest = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get.return_value = fake_manifest

        with patch("sos.artifacts.registry.ArtifactRegistry", return_value=mock_registry):
            results = v.resolve_artifacts()

        mock_registry.get.assert_called_once_with(self._VALID_CID)
        assert results == [fake_manifest]

    def test_resolve_artifacts_empty_when_only_engram_refs(self):
        """resolve_artifacts() returns [] when all refs are engram: legacy."""
        from unittest.mock import MagicMock, patch

        v = VerificationInfo(sample_output_refs=["engram:some-slug"])

        mock_registry = MagicMock()
        with patch("sos.artifacts.registry.ArtifactRegistry", return_value=mock_registry):
            results = v.resolve_artifacts()

        mock_registry.get.assert_not_called()
        assert results == []
