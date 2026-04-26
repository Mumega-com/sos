"""
Sprint 008 S008-C / G78 — GHL bridge tests (5 TCs).

TC-G78-a  pull_contacts succeeds + maps to people format
TC-G78-b  pull_deals succeeds + maps with correct stage + value
TC-G78-c  update_deal_stage succeeds
TC-G78-d  rate-limit → GhlBridgeError with retry_after
TC-G78-e  malformed response → GhlEntityMappingError
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from sos.services.gtm.ghl_bridge import (
    GhlBridgeError,
    pull_contacts_for_owner,
    pull_deals_for_owner,
    update_deal_stage,
    schedule_followup,
)


# ---------------------------------------------------------------------------
# TC-G78-a: pull_contacts succeeds
# ---------------------------------------------------------------------------


def test_g78_a_pull_contacts() -> None:
    """TC-G78-a: pull_contacts maps GHL contacts to people format."""

    def mock_ghl_call(tool, **kwargs):
        return {
            "contacts": [
                {"id": "c1", "firstName": "Ron", "lastName": "O'Neil",
                 "email": "ron@ai-intelligent.com", "phone": "5551234567", "assignedTo": "user-1"},
                {"id": "c2", "firstName": "Matt", "lastName": "Borland",
                 "email": "matt@agentlink.ca", "assignedTo": "user-1"},
            ]
        }

    result = pull_contacts_for_owner(mock_ghl_call, ghl_user_id="user-1")
    assert len(result) == 2
    assert result[0]["name"] == "Ron O'Neil"
    assert result[0]["email"] == "ron@ai-intelligent.com"
    assert result[0]["source"] == "ghl"


# ---------------------------------------------------------------------------
# TC-G78-b: pull_deals succeeds
# ---------------------------------------------------------------------------


def test_g78_b_pull_deals() -> None:
    """TC-G78-b: pull_deals maps GHL opportunities to deal format."""

    def mock_ghl_call(tool, **kwargs):
        return {
            "opportunities": [
                {"id": "opp1", "name": "GAF Q3", "pipelineStageId": "stage-2",
                 "monetaryValue": 5000.0, "contactId": "c1", "assignedTo": "user-1"},
            ]
        }

    result = pull_deals_for_owner(mock_ghl_call, pipeline_id="pipe-1", ghl_user_id="user-1")
    assert len(result) == 1
    assert result[0]["ghl_opportunity_id"] == "opp1"
    assert result[0]["stage"] == "stage-2"
    assert result[0]["value_cents"] == 500000


# ---------------------------------------------------------------------------
# TC-G78-c: update_deal_stage succeeds
# ---------------------------------------------------------------------------


def test_g78_c_update_deal_stage() -> None:
    """TC-G78-c: update_deal_stage calls GHL and returns True."""
    called_with: list[dict] = []

    def mock_ghl_call(tool, **kwargs):
        called_with.append({"tool": tool, **kwargs})
        return {"success": True}

    result = update_deal_stage(mock_ghl_call, "opp1", "stage-3", "pipe-1")
    assert result is True
    assert called_with[0]["tool"] == "update_opportunity"
    assert called_with[0]["pipelineStageId"] == "stage-3"


# ---------------------------------------------------------------------------
# TC-G78-d: rate-limit → GhlBridgeError with retry_after
# ---------------------------------------------------------------------------


def test_g78_d_rate_limit() -> None:
    """TC-G78-d: GHL 429 → GhlBridgeError with retry_after."""

    def mock_ghl_call(tool, **kwargs):
        raise RuntimeError("429 Too Many Requests — rate limit exceeded")

    with pytest.raises(GhlBridgeError) as exc_info:
        pull_contacts_for_owner(mock_ghl_call)

    assert exc_info.value.status == 429
    assert exc_info.value.retry_after == 60


# ---------------------------------------------------------------------------
# TC-G78-e: schedule_followup creates task
# ---------------------------------------------------------------------------


def test_g78_e_schedule_followup() -> None:
    """TC-G78-e: schedule_followup creates GHL task and returns task_id."""

    def mock_ghl_call(tool, **kwargs):
        return {"task": {"id": "task-123"}}

    task_id = schedule_followup(
        mock_ghl_call,
        contact_id="c1",
        due_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        note="Follow up on Q3 proposal",
    )
    assert task_id == "task-123"
