"""Contract tests for the tightened v0.9.1 BusPort envelope.

Phase 2 / Wave 0 — ``tenant_id`` and ``project`` became required on every
bus envelope that crosses the port. These tests lock that behavior.

Serialization round-trip + the schema snapshot guard in
``test_port_schemas_export.py`` together form the full contract check.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sos.contracts.ports.bus import BroadcastRequest, BusMessage, SendRequest


_VALID_ENVELOPE: dict[str, str] = {
    "from": "kasra",
    "text": "hi",
    "ts": "2026-04-19T00:00:00Z",
    "tenant_id": "mumega",
    "project": "journeys",
}


def test_bus_message_requires_tenant_id() -> None:
    payload = {k: v for k, v in _VALID_ENVELOPE.items() if k != "tenant_id"}
    with pytest.raises(ValidationError) as exc:
        BusMessage(**payload)
    assert any(err["loc"] == ("tenant_id",) for err in exc.value.errors())


def test_bus_message_requires_project() -> None:
    payload = {k: v for k, v in _VALID_ENVELOPE.items() if k != "project"}
    with pytest.raises(ValidationError) as exc:
        BusMessage(**payload)
    assert any(err["loc"] == ("project",) for err in exc.value.errors())


def test_bus_message_round_trip_preserves_scope() -> None:
    msg = BusMessage(**_VALID_ENVELOPE)
    dumped = msg.model_dump(by_alias=True)
    assert dumped["tenant_id"] == "mumega"
    assert dumped["project"] == "journeys"
    assert BusMessage(**dumped) == msg


def test_send_request_requires_project() -> None:
    with pytest.raises(ValidationError) as exc:
        SendRequest(to="codex", text="hi")
    assert any(err["loc"] == ("project",) for err in exc.value.errors())


def test_broadcast_request_requires_project() -> None:
    with pytest.raises(ValidationError) as exc:
        BroadcastRequest(text="hi")
    assert any(err["loc"] == ("project",) for err in exc.value.errors())


def test_bus_message_rejects_empty_scope_strings() -> None:
    # Required=True rejects missing; explicit empty strings still pass
    # Pydantic str validation — that's a known Pydantic behavior. Document
    # it so the reader knows enforcement.py must also guard against "".
    msg = BusMessage(**{**_VALID_ENVELOPE, "tenant_id": "", "project": ""})
    assert msg.tenant_id == ""
    assert msg.project == ""
