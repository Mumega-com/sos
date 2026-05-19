"""Schema stability lock for PolicyDecision (v0.5.1 baseline).

New fields on PolicyDecision MUST be optional (have defaults). Field renames,
type narrowing, or removals require an explicit test edit — a visible signal
to reviewers that the contract is changing.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sos.contracts.policy import PolicyDecision

# v0.5.1 baseline — required fields have no default; optional fields do.
_REQUIRED_FIELDS = {"allowed", "reason", "tier", "action", "resource"}
_OPTIONAL_FIELDS = {
    "agent",
    "tenant",
    "pillars_passed",
    "pillars_failed",
    "capability_ok",
    "audit_id",
    "metadata",
}
_ALL_BASELINE_FIELDS = _REQUIRED_FIELDS | _OPTIONAL_FIELDS


def test_frozen_config() -> None:
    """The model must remain frozen — immutability is part of the contract."""
    assert PolicyDecision.model_config.get("frozen") is True


def test_required_fields_baseline() -> None:
    """Every field that was required at v0.5.1 must remain required."""
    actually_required = {
        name
        for name, field in PolicyDecision.model_fields.items()
        if field.is_required()
    }
    for field in _REQUIRED_FIELDS:
        assert field in actually_required, (
            f"PolicyDecision.{field} is no longer required — breaking change"
        )


def test_optional_fields_baseline() -> None:
    """Every field that was optional at v0.5.1 must remain optional (have a default)."""
    actually_required = {
        name
        for name, field in PolicyDecision.model_fields.items()
        if field.is_required()
    }
    for field in _OPTIONAL_FIELDS:
        assert field not in actually_required, (
            f"PolicyDecision.{field} became required — breaking change"
        )


def test_no_fields_removed() -> None:
    """The current field set must be a superset of the v0.5.1 baseline."""
    current = set(PolicyDecision.model_fields.keys())
    missing = _ALL_BASELINE_FIELDS - current
    assert not missing, f"PolicyDecision lost fields: {missing}"


def test_instance_is_immutable() -> None:
    """Instances must be immutable — assignments must raise ValidationError."""
    d = PolicyDecision(
        allowed=True,
        reason="ok",
        tier="act_freely",
        action="x",
        resource="y",
    )
    with pytest.raises(ValidationError):
        d.allowed = False  # type: ignore[misc]
