"""Tests for QNFT Marketplace Bridge — S063 Track D."""
from __future__ import annotations

import json
import pytest

from sos.services.qnft_marketplace_bridge import _parse_completed_event, _redis_idempotency_key


def _make_fields(
    event_type: str = "task.completed",
    source: str = "agent:squad",
    bounty_id: str | None = "bounty-001",
    capability_grant: str | None = "marketplace:list",
    project: str = "acme",
    agent: str = "river",
) -> dict:
    result: dict = {"agent_addr": agent, "project": project}
    if bounty_id:
        result["bounty_id"] = bounty_id
    if capability_grant:
        result["capability_grant"] = capability_grant
    return {
        "type": event_type,
        "source": source,
        "payload": json.dumps({"task_id": "task-1", "result": result}),
    }


def test_valid_bounty_completed_parses():
    event = _parse_completed_event(_make_fields())
    assert event is not None
    assert event["capability_grant"] == "marketplace:list"
    assert event["bounty_id"] == "bounty-001"
    assert event["tenant"] == "acme"
    assert event["agent"] == "river"


def test_missing_bounty_id_rejected():
    assert _parse_completed_event(_make_fields(bounty_id=None)) is None


def test_missing_capability_grant_rejected():
    assert _parse_completed_event(_make_fields(capability_grant=None)) is None


def test_wrong_event_type_rejected():
    assert _parse_completed_event(_make_fields(event_type="task.created")) is None


def test_untrusted_source_rejected():
    assert _parse_completed_event(_make_fields(source="agent:attacker")) is None


def test_trusted_sources_accepted():
    for source in ("agent:squad", "agent:sovereign", "agent:loom", "system"):
        event = _parse_completed_event(_make_fields(source=source))
        assert event is not None, f"source {source!r} should be trusted"


def test_idempotency_key_stable():
    k1 = _redis_idempotency_key("bounty-001", "marketplace:list")
    k2 = _redis_idempotency_key("bounty-001", "marketplace:list")
    assert k1 == k2
    k3 = _redis_idempotency_key("bounty-001", "marketplace:post")
    assert k1 != k3
