"""
Sprint 008 S008-D / G79 — GTM relationship graph ORM tests (4 TCs).

TC-G79-a  Migration applies + all tables exist (manual verification)
TC-G79-b  upsert_person twice with same email returns same id
TC-G79-c  add_edge duplicate raises EdgeAlreadyExistsError
TC-G79-d  upsert_deal with invalid person_id raises GraphPersistError
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from sos.services.gtm.graph import (
    EdgeAlreadyExistsError,
    GraphPersistError,
    add_edge,
    create_action,
    mark_action_done,
    record_conversation,
    upsert_company,
    upsert_deal,
    upsert_person,
)


# ---------------------------------------------------------------------------
# Mock connection helper
# ---------------------------------------------------------------------------


def _make_mock_conn(*, fetchone_return=None, rowcount=1, side_effect=None):
    """Build a mock psycopg2 connection."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = fetchone_return
    mock_cursor.rowcount = rowcount
    mock_cursor.description = [
        ("id",), ("name",), ("email",), ("phone",), ("source",),
        ("created_at",), ("last_seen_at",),
    ]
    if side_effect:
        mock_cursor.execute.side_effect = side_effect

    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


# ---------------------------------------------------------------------------
# TC-G79-b: upsert_person idempotent on email
# ---------------------------------------------------------------------------


def test_g79_b_upsert_person_idempotent() -> None:
    """TC-G79-b: upsert_person twice with same email returns same row."""
    person_id = str(uuid.uuid4())
    row = (person_id, "Test Person", "test@example.com", None, "manual",
           datetime.now(timezone.utc), datetime.now(timezone.utc))

    mock_conn, mock_cursor = _make_mock_conn(fetchone_return=row)

    result1 = upsert_person(mock_conn, "Test Person", email="test@example.com", source="manual")
    result2 = upsert_person(mock_conn, "Test Person", email="test@example.com", source="manual")

    assert result1["id"] == person_id
    assert result2["id"] == person_id
    assert result1["email"] == "test@example.com"


# ---------------------------------------------------------------------------
# TC-G79-c: add_edge duplicate raises EdgeAlreadyExistsError
# ---------------------------------------------------------------------------


def test_g79_c_duplicate_edge_raises() -> None:
    """TC-G79-c: add_edge with duplicate from/to/type raises EdgeAlreadyExistsError."""
    from psycopg2 import IntegrityError

    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.execute.side_effect = IntegrityError("duplicate key value violates unique constraint")

    with pytest.raises(EdgeAlreadyExistsError):
        add_edge(mock_conn, str(uuid.uuid4()), "person", str(uuid.uuid4()), "company", "knows")


# ---------------------------------------------------------------------------
# TC-G79-d: deal with invalid person_id raises GraphPersistError
# ---------------------------------------------------------------------------


def test_g79_d_invalid_fk_raises() -> None:
    """TC-G79-d: upsert_deal with invalid person_id raises GraphPersistError."""
    from psycopg2 import IntegrityError

    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.execute.side_effect = IntegrityError("violates foreign key constraint")

    with pytest.raises(GraphPersistError):
        upsert_deal(
            mock_conn,
            person_id=str(uuid.uuid4()),
            company_id=None,
            product="gaf",
            stage="prospecting",
        )


# ---------------------------------------------------------------------------
# Unit: create_action + mark_action_done
# ---------------------------------------------------------------------------


def test_create_and_complete_action() -> None:
    """create_action + mark_action_done round-trip."""
    action_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    mock_conn, mock_cursor = _make_mock_conn(
        fetchone_return=(action_id, "agent:gavin-knight", "follow_up", None, None, "pending", now, now),
    )
    mock_cursor.description = [
        ("id",), ("knight_id",), ("action_type",), ("target_id",),
        ("target_type",), ("status",), ("due_at",), ("created_at",),
    ]

    result = create_action(mock_conn, "agent:gavin-knight", "follow_up", due_at=now)
    assert result["id"] == action_id
    assert result["status"] == "pending"

    mock_cursor.rowcount = 1
    done = mark_action_done(mock_conn, action_id)
    assert done is True
