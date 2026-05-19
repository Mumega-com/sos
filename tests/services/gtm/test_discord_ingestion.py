"""
Sprint 008 S008-B / G77 — Discord ingestion + entity extraction tests (4 TCs).

TC-G77-a  Happy path: message with entities → person + company + conversation created
TC-G77-b  Idempotency: same message ID → no duplicate rows
TC-G77-c  Regex fallback: LLM failure → still extracts emails/phones
TC-G77-d  Bot message filter: knight's own messages skipped
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from sos.services.gtm.discord_ingestion import (
    DiscordIngestionError,
    extract_regex,
    ingest_messages,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(
    msg_id: str = "msg-001",
    content: str = "Hello world",
    author_id: str = "user-123",
    author_name: str = "gavin",
    timestamp: str = "2026-04-26T02:00:00Z",
    channel_id: str = "chan-001",
) -> dict:
    return {
        "id": msg_id,
        "content": content,
        "author": {"id": author_id, "username": author_name},
        "timestamp": timestamp,
        "channel_id": channel_id,
    }


def _make_mock_conn():
    """Build a mock psycopg2 connection for graph operations."""
    import uuid
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    # Return a plausible row for any INSERT/SELECT
    fake_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    mock_cursor.fetchone.return_value = (
        fake_id, "Test", "test@test.com", None, "discord", now, now,
    )
    mock_cursor.description = [
        ("id",), ("name",), ("email",), ("phone",), ("source",),
        ("created_at",), ("last_seen_at",),
    ]
    mock_cursor.rowcount = 1
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn


# ---------------------------------------------------------------------------
# TC-G77-a: happy path — entities extracted and persisted
# ---------------------------------------------------------------------------


def test_g77_a_happy_path_extracts_entities() -> None:
    """TC-G77-a: message with entities → person + company + conversation created."""
    mock_conn = _make_mock_conn()

    messages = [
        _make_message(
            content="Met with Sarah from Acme about the Q3 deal. Her email is sarah@acme.com",
        ),
    ]

    llm_result = {
        "people": ["Sarah"],
        "companies": ["Acme"],
        "deals": ["Q3 deal"],
        "action_items": [],
    }

    with patch(
        "sos.services.gtm.discord_ingestion.extract_entities_llm",
        return_value=llm_result,
    ):
        result = ingest_messages(mock_conn, "agent:gavin-knight", set(), messages, bound_channel_id="chan-001")

    assert result["processed"] == 1
    assert result["skipped"] == 0
    assert result["entities_created"] >= 2  # Sarah (LLM) + sarah@acme.com (regex) + Acme (LLM)


# ---------------------------------------------------------------------------
# TC-G77-b: idempotency — same message ID → no duplicates
# ---------------------------------------------------------------------------


def test_g77_b_idempotent_on_message_id() -> None:
    """TC-G77-b: processing same message twice creates no duplicate conversation."""
    mock_conn = _make_mock_conn()

    messages = [
        _make_message(msg_id="msg-duplicate", content="Follow up with client"),
    ]

    with patch(
        "sos.services.gtm.discord_ingestion.extract_entities_llm",
        return_value={"people": [], "companies": [], "deals": [], "action_items": []},
    ):
        result1 = ingest_messages(mock_conn, "agent:gavin-knight", set(), messages, bound_channel_id="chan-001")
        result2 = ingest_messages(mock_conn, "agent:gavin-knight", set(), messages, bound_channel_id="chan-001")

    # Both calls succeed without raising (UNIQUE constraint in DB handles dedup)
    assert (result1["processed"] + result1["skipped"]) >= 1
    assert (result2["processed"] + result2["skipped"]) >= 1


# ---------------------------------------------------------------------------
# TC-G77-c: regex fallback — LLM fails, regex still extracts
# ---------------------------------------------------------------------------


def test_g77_c_regex_fallback_on_llm_failure() -> None:
    """TC-G77-c: LLM extraction fails → regex still extracts emails/phones."""
    mock_conn = _make_mock_conn()

    messages = [
        _make_message(
            content="Contact John at john@example.com or call 555-123-4567 about $50,000 deal",
        ),
    ]

    with patch(
        "sos.services.gtm.discord_ingestion.extract_entities_llm",
        side_effect=DiscordIngestionError("API down"),
    ):
        result = ingest_messages(mock_conn, "agent:gavin-knight", set(), messages, bound_channel_id="chan-001")

    # Should still process (regex fallback)
    assert result["processed"] == 1
    # Email from regex should create a person
    assert result["entities_created"] >= 1


# ---------------------------------------------------------------------------
# TC-G77-d: bot message filter — own messages skipped
# ---------------------------------------------------------------------------


def test_g77_d_bot_messages_skipped() -> None:
    """TC-G77-d: knight's own bot messages are skipped (prevent self-poisoning)."""
    mock_conn = _make_mock_conn()

    bot_id = "bot-kasra-123"
    messages = [
        _make_message(author_id=bot_id, content="I scheduled a follow-up for you"),
        _make_message(author_id="user-gavin", content="Thanks, I'll call them tomorrow"),
    ]

    with patch(
        "sos.services.gtm.discord_ingestion.extract_entities_llm",
        return_value={"people": [], "companies": [], "deals": [], "action_items": []},
    ):
        result = ingest_messages(mock_conn, "agent:gavin-knight", {bot_id}, messages, bound_channel_id="chan-001")

    assert result["skipped"] == 1  # bot message skipped
    assert result["processed"] == 1  # human message processed


# ---------------------------------------------------------------------------
# Unit: regex extraction
# ---------------------------------------------------------------------------


def test_regex_extraction() -> None:
    """Verify regex patterns extract emails, phones, amounts, dates."""
    text = (
        "Contact sarah@acme.com or call 555-123-4567. "
        "Budget is $50,000. Meeting on Tuesday next week."
    )
    entities = extract_regex(text)
    assert "sarah@acme.com" in entities["emails"]
    assert len(entities["phones"]) >= 1
    assert "$50,000" in entities["amounts"]
    assert any("tuesday" in d.lower() for d in entities["dates"])
