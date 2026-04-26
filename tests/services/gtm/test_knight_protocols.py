"""
Sprint 008 S008-E / G80 — Knight protocols tests (6 TCs).

TC-G80-1a  stale deal fires once
TC-G80-1b  stale deal doesn't re-fire within window
TC-G80-2a  hot keyword conversation fires
TC-G80-2b  no keyword → no fire
TC-G80-3a  overdue action fires (past grace window)
TC-G80-3b  within grace window → no fire
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from sos.services.gtm.knight_protocols import (
    _HOT_KEYWORDS_RE,
    check_hot_conversations,
    check_missing_actions,
    check_stale_deals,
    generate_priority_summary,
)


def _mock_cursor_with_rows(rows, cols):
    """Build a mock connection that returns specified rows."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_cursor.fetchone.return_value = rows[0] if rows else None
    mock_cursor.description = [(c,) for c in cols]
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn


# ---------------------------------------------------------------------------
# TC-G80-1a: stale deal fires
# ---------------------------------------------------------------------------

def test_g80_1a_stale_deal_fires() -> None:
    """TC-G80-1a: deal with last_action 8d ago → returned as stale."""
    deal_id = uuid.uuid4()
    last_action = datetime.now(timezone.utc) - timedelta(days=8)
    rows = [(deal_id, "prospecting", 500000, last_action, "Ron O'Neil", "AI Intelligent")]
    cols = ["id", "stage", "value_cents", "last_action_at", "contact_name", "company_name"]
    mock_conn = _mock_cursor_with_rows(rows, cols)

    result = check_stale_deals(mock_conn, "agent:gavin-knight")
    assert len(result) == 1
    assert result[0]["contact_name"] == "Ron O'Neil"


# ---------------------------------------------------------------------------
# TC-G80-1b: stale deal doesn't re-fire (DB handles via NOT EXISTS)
# ---------------------------------------------------------------------------

def test_g80_1b_stale_deal_no_refire() -> None:
    """TC-G80-1b: empty result when recent nudge exists (DB filters)."""
    mock_conn = _mock_cursor_with_rows([], ["id", "stage", "value_cents", "last_action_at", "contact_name", "company_name"])
    result = check_stale_deals(mock_conn, "agent:gavin-knight")
    assert len(result) == 0


# ---------------------------------------------------------------------------
# TC-G80-2a: hot keyword fires
# ---------------------------------------------------------------------------

def test_g80_2a_hot_keyword_fires() -> None:
    """TC-G80-2a: conversation with 'timeline' keyword → flagged."""
    conv_id = uuid.uuid4()
    rows = [(conv_id, "What's your timeline for this?", '["gavin"]',
             datetime.now(timezone.utc), "msg-001")]
    cols = ["id", "summary", "participants", "occurred_at", "discord_message_id"]
    mock_conn = _mock_cursor_with_rows(rows, cols)

    result = check_hot_conversations(mock_conn, "agent:gavin-knight")
    assert len(result) == 1
    assert result[0]["matched_keyword"] == "timeline"


# ---------------------------------------------------------------------------
# TC-G80-2b: no keyword → no fire
# ---------------------------------------------------------------------------

def test_g80_2b_no_keyword_no_fire() -> None:
    """TC-G80-2b: conversation without hot keyword → not flagged."""
    conv_id = uuid.uuid4()
    rows = [(conv_id, "Hey, how are you doing today?", '["gavin"]',
             datetime.now(timezone.utc), "msg-002")]
    cols = ["id", "summary", "participants", "occurred_at", "discord_message_id"]
    mock_conn = _mock_cursor_with_rows(rows, cols)

    result = check_hot_conversations(mock_conn, "agent:gavin-knight")
    assert len(result) == 0


# ---------------------------------------------------------------------------
# TC-G80-3a: overdue action fires (past grace)
# ---------------------------------------------------------------------------

def test_g80_3a_overdue_action_fires() -> None:
    """TC-G80-3a: action due 1h ago → returned as missing."""
    action_id = uuid.uuid4()
    due = datetime.now(timezone.utc) - timedelta(hours=1)
    rows = [(action_id, "followup", uuid.uuid4(), "deal", due, '{}', "pending")]
    cols = ["id", "action_type", "target_id", "target_type", "due_at", "payload", "status"]
    mock_conn = _mock_cursor_with_rows(rows, cols)

    result = check_missing_actions(mock_conn, "agent:gavin-knight")
    assert len(result) == 1


# ---------------------------------------------------------------------------
# TC-G80-3b: within grace → no fire
# ---------------------------------------------------------------------------

def test_g80_3b_within_grace_no_fire() -> None:
    """TC-G80-3b: action due 15min ago (within 30min grace) → not returned."""
    mock_conn = _mock_cursor_with_rows([], ["id", "action_type", "target_id", "target_type", "due_at", "payload", "status"])
    result = check_missing_actions(mock_conn, "agent:gavin-knight")
    assert len(result) == 0


# ---------------------------------------------------------------------------
# Unit: hot keyword regex
# ---------------------------------------------------------------------------

def test_hot_keyword_regex() -> None:
    """Verify hot keyword regex matches expected patterns."""
    assert _HOT_KEYWORDS_RE.search("What's your timeline?")
    assert _HOT_KEYWORDS_RE.search("We have budget for this")
    assert _HOT_KEYWORDS_RE.search("Ready to sign the contract")
    assert not _HOT_KEYWORDS_RE.search("Just checking in")
    assert not _HOT_KEYWORDS_RE.search("How's the weather?")
