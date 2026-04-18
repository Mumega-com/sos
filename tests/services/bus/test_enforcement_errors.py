"""Tests for bus.enforcement backward-compat with SOS-4001..4004 error codes.

Moved from tests/contracts/test_errors.py in v0.4.4 structural cleanup —
these tests exercise bus.enforcement (a service), not pure contract types.
The remaining contract-level error tests stay in tests/contracts/test_errors.py.
"""
from __future__ import annotations

import pytest

from sos.contracts.errors import (
    EnvelopeError,
    MessageValidationError,
    UnknownTypeError,
)


# ---------------------------------------------------------------------------
# Enforcement backward-compat — SOS-4001/2/3/4 still work
# ---------------------------------------------------------------------------


def test_enforcement_no_type_raises_sos_4002():
    """enforce() on a dict with no 'type' produces code SOS-4002."""
    from sos.services.bus.enforcement import enforce

    with pytest.raises(MessageValidationError) as exc_info:
        enforce({"source": "agent:test", "payload": {}})
    assert exc_info.value.code == "SOS-4002"


def test_enforcement_unknown_type_raises_sos_4004():
    from sos.services.bus.enforcement import enforce

    with pytest.raises(MessageValidationError) as exc_info:
        enforce({"type": "legacy_chat", "source": "agent:test"})
    assert exc_info.value.code == "SOS-4004"


def test_enforcement_unknown_type_cause_is_sos_error():
    """The __cause__ of MessageValidationError is now an UnknownTypeError."""
    from sos.services.bus.enforcement import enforce

    with pytest.raises(MessageValidationError) as exc_info:
        enforce({"type": "legacy_chat", "source": "agent:test"})
    assert isinstance(exc_info.value.__cause__, UnknownTypeError)


def test_enforcement_envelope_error_cause():
    """Envelope errors carry EnvelopeError as __cause__."""
    from sos.services.bus.enforcement import enforce

    with pytest.raises(MessageValidationError) as exc_info:
        enforce({"source": "agent:test"})
    assert isinstance(exc_info.value.__cause__, EnvelopeError)
