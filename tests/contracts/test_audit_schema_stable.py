"""Schema stability lock for AuditEvent (v0.5.0 baseline).

New kinds in AuditEventKind are additions and allowed. New fields on
AuditEvent MUST be optional (have defaults). Field renames or type
narrowing require an explicit test edit — a visible signal to reviewers.
"""
from __future__ import annotations

from typing import Any

from sos.contracts.audit import AuditDecision, AuditEvent, AuditEventKind

# Baseline recorded 2026-04-18 at the moment v0.5.0 shipped. Compare
# against the live model to detect drift.
_EXPECTED_FIELDS: dict[str, tuple[str, bool]] = {
    # field_name: (annotation_string, is_required)
    "id": ("str", True),
    "timestamp": ("str", True),
    "agent": ("str", True),
    "tenant": ("str", True),
    "trace_id": ("str | None", False),
    "parent_event_id": ("str | None", False),
    "kind": ("AuditEventKind", True),
    "action": ("str", True),
    "target": ("str", True),
    "decision": ("AuditDecision", False),
    "reason": ("str", False),
    "policy_tier": ("str | None", False),
    "cost_micros": ("int", False),
    "cost_currency": ("str", False),
    "inputs": ("dict[str, Any]", False),
    "outputs": ("dict[str, Any]", False),
    "metadata": ("dict[str, Any]", False),
}

_EXPECTED_KINDS = {"intent", "policy_decision", "action_completed", "action_failed", "arbitration"}

_EXPECTED_DECISIONS = {"allow", "deny", "require_approval", "n/a"}


def test_audit_event_has_no_removed_fields() -> None:
    """All baseline fields must still exist."""
    current = set(AuditEvent.model_fields.keys())
    for field in _EXPECTED_FIELDS:
        assert field in current, f"AuditEvent field '{field}' removed — breaking change"


def test_audit_event_required_fields_unchanged() -> None:
    """Fields that were required at v0.5.0 must remain required."""
    for field, (_, is_required) in _EXPECTED_FIELDS.items():
        model_field = AuditEvent.model_fields[field]
        # In Pydantic v2, required fields have is_required() True
        actually_required = model_field.is_required()
        assert actually_required == is_required, (
            f"AuditEvent.{field} changed required-status: "
            f"baseline={is_required}, now={actually_required}"
        )


def test_audit_event_is_frozen() -> None:
    """The model must remain frozen — immutability is part of the contract."""
    config = AuditEvent.model_config
    assert config.get("frozen") is True, "AuditEvent.model_config must have frozen=True"


def test_audit_event_kind_is_superset() -> None:
    """New kinds may be added. None may be removed or renamed."""
    current = {k.value for k in AuditEventKind}
    missing = _EXPECTED_KINDS - current
    assert not missing, f"AuditEventKind lost values: {missing}"


def test_audit_decision_is_superset() -> None:
    current = {d.value for d in AuditDecision}
    missing = _EXPECTED_DECISIONS - current
    assert not missing, f"AuditDecision lost values: {missing}"
