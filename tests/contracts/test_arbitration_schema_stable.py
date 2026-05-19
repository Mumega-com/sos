"""Schema stability lock for ArbitrationDecision and LoserRecord (v0.5.2 baseline).

New fields on ArbitrationDecision or LoserRecord MUST be optional (have defaults).
Field renames, type narrowing, or removals require an explicit test edit — a
visible signal to reviewers that the contract is changing.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sos.contracts.arbitration import ArbitrationDecision, LoserRecord

# v0.5.2 baseline — required fields have no default; optional fields do.
_ARBITRATION_REQUIRED = {"resource", "tenant", "strategy", "window_ms"}
_ARBITRATION_OPTIONAL = {
    "winner_agent",
    "winner_proposal_id",
    "winner_reason",
    "losers",
    "proposal_count",
    "audit_id",
    "metadata",
}
_ARBITRATION_ALL_BASELINE = _ARBITRATION_REQUIRED | _ARBITRATION_OPTIONAL

_LOSER_REQUIRED = {"agent", "proposal_id", "reason"}


def test_frozen_config_arbitration_decision() -> None:
    """ArbitrationDecision must remain frozen — immutability is part of the contract."""
    assert ArbitrationDecision.model_config.get("frozen") is True


def test_frozen_config_loser_record() -> None:
    """LoserRecord must remain frozen — immutability is part of the contract."""
    assert LoserRecord.model_config.get("frozen") is True


def test_arbitration_required_fields_baseline() -> None:
    """Every field that was required at v0.5.2 must remain required."""
    actually_required = {
        name
        for name, field in ArbitrationDecision.model_fields.items()
        if field.is_required()
    }
    assert actually_required == _ARBITRATION_REQUIRED, (
        f"ArbitrationDecision required fields changed — breaking change. "
        f"Expected {_ARBITRATION_REQUIRED}, got {actually_required}"
    )


def test_arbitration_optional_fields_baseline() -> None:
    """Every field that was optional at v0.5.2 must remain optional (have a default)."""
    actually_required = {
        name
        for name, field in ArbitrationDecision.model_fields.items()
        if field.is_required()
    }
    for field in _ARBITRATION_OPTIONAL:
        assert field not in actually_required, (
            f"ArbitrationDecision.{field} became required — breaking change"
        )


def test_arbitration_instance_is_immutable() -> None:
    """Instances must be immutable — assignments must raise ValidationError."""
    d = ArbitrationDecision(
        resource="queue:default",
        tenant="acme",
        strategy="priority+coherence+recency",
        window_ms=500,
    )
    with pytest.raises(ValidationError):
        d.resource = "queue:other"  # type: ignore[misc]


def test_loser_record_required_fields_baseline() -> None:
    """Every field that was required on LoserRecord at v0.5.2 must remain required."""
    actually_required = {
        name
        for name, field in LoserRecord.model_fields.items()
        if field.is_required()
    }
    assert actually_required == _LOSER_REQUIRED, (
        f"LoserRecord required fields changed — breaking change. "
        f"Expected {_LOSER_REQUIRED}, got {actually_required}"
    )


def test_no_fields_removed_arbitration() -> None:
    """The current ArbitrationDecision field set must be a superset of the v0.5.2 baseline."""
    current = set(ArbitrationDecision.model_fields.keys())
    missing = _ARBITRATION_ALL_BASELINE - current
    assert not missing, f"ArbitrationDecision lost fields: {missing}"
