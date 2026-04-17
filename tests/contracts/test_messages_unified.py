"""Tests for unified bus event type naming (island #10: dot-separated convention).

These tests prove:
1. Old underscore names (task_created, task_claimed, task_completed) are rejected.
2. New dot-separated names (task.created, task.claimed, task.completed) are accepted.
3. New v1 message types (task.routed, task.failed, skill.executed) validate correctly.
4. Enforcement._V1_TYPES reflects the unified set.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sos.contracts.messages import (
    SkillExecutedMessage,
    TaskCreatedMessage,
    TaskFailedMessage,
    TaskRoutedMessage,
    parse_message,
)
from sos.services.bus.enforcement import _V1_TYPES, enforce
from sos.services.bus.enforcement import MessageValidationError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mid() -> str:
    return str(uuid.uuid4())


def _base(type_: str, **extra) -> dict:
    return {
        "type": type_,
        "source": "agent:brain",
        "timestamp": _now(),
        "version": "1.0",
        "message_id": _mid(),
        **extra,
    }


# ---------------------------------------------------------------------------
# Naming unification: underscore names must be rejected
# ---------------------------------------------------------------------------


def test_task_created_underscore_rejected_by_parse_message():
    """task_created (old name) must raise ValueError — dot-name wins."""
    with pytest.raises(ValueError, match="Unknown message type"):
        parse_message({**_base("task_created"), "payload": {
            "task_id": "T1", "title": "X", "priority": "low"
        }})


def test_task_claimed_underscore_rejected_by_parse_message():
    """task_claimed (old name) must raise ValueError."""
    with pytest.raises(ValueError, match="Unknown message type"):
        parse_message({**_base("task_claimed"), "payload": {
            "task_id": "T1", "claimed_at": _now()
        }})


def test_task_completed_underscore_rejected_by_parse_message():
    """task_completed (old name) must raise ValueError."""
    with pytest.raises(ValueError, match="Unknown message type"):
        parse_message({**_base("task_completed"), "payload": {
            "task_id": "T1", "status": "done", "completed_at": _now()
        }})


def test_task_created_underscore_rejected_by_enforcement():
    """Enforcement must raise SOS-4004 for old underscore name."""
    with pytest.raises(MessageValidationError) as exc_info:
        enforce({**_base("task_created"), "payload": {
            "task_id": "T1", "title": "X", "priority": "low"
        }})
    assert exc_info.value.code == "SOS-4004"


def test_task_completed_underscore_rejected_by_enforcement():
    """Enforcement must raise SOS-4004 for old underscore name."""
    with pytest.raises(MessageValidationError) as exc_info:
        enforce({**_base("task_completed"), "payload": {
            "task_id": "T1", "status": "done", "completed_at": _now()
        }})
    assert exc_info.value.code == "SOS-4004"


# ---------------------------------------------------------------------------
# Naming unification: dot names must be accepted
# ---------------------------------------------------------------------------


def test_task_created_dot_accepted_by_parse_message():
    """task.created (new canonical name) must parse cleanly."""
    msg = parse_message({**_base("task.created"), "payload": {
        "task_id": "T1", "title": "Hello", "priority": "medium"
    }})
    assert msg.type == "task.created"


def test_task_claimed_dot_accepted_by_enforcement():
    """Enforcement must accept task.claimed."""
    result = enforce({**_base("task.claimed"), "payload": {
        "task_id": "T2", "claimed_at": _now()
    }})
    assert result["type"] == "task.claimed"


def test_task_completed_dot_accepted_by_enforcement():
    """Enforcement must accept task.completed."""
    result = enforce({**_base("task.completed"), "payload": {
        "task_id": "T3", "status": "done", "completed_at": _now()
    }})
    assert result["type"] == "task.completed"


# ---------------------------------------------------------------------------
# _V1_TYPES set membership
# ---------------------------------------------------------------------------


def test_v1_types_contains_dot_names():
    """enforcement._V1_TYPES must include all dot-separated task/skill types."""
    assert "task.created" in _V1_TYPES
    assert "task.claimed" in _V1_TYPES
    assert "task.completed" in _V1_TYPES
    assert "task.routed" in _V1_TYPES
    assert "task.failed" in _V1_TYPES
    assert "skill.executed" in _V1_TYPES


def test_v1_types_does_not_contain_underscore_names():
    """enforcement._V1_TYPES must NOT include old underscore names."""
    assert "task_created" not in _V1_TYPES
    assert "task_claimed" not in _V1_TYPES
    assert "task_completed" not in _V1_TYPES


# ---------------------------------------------------------------------------
# TaskRoutedMessage
# ---------------------------------------------------------------------------


def test_task_routed_minimal():
    msg = TaskRoutedMessage(**{**_base("task.routed"), "payload": {
        "task_id": "T4",
        "routed_to": "kasra",
        "routed_at": _now(),
    }})
    assert msg.type == "task.routed"
    assert msg.target == "sos:channel:tasks"
    assert msg.payload.task_id == "T4"
    assert msg.payload.routed_to == "kasra"
    assert msg.payload.fallback is False


def test_task_routed_with_score_and_reason():
    msg = TaskRoutedMessage(**{**_base("task.routed"), "payload": {
        "task_id": "T5",
        "routed_to": "mumega",
        "routed_at": _now(),
        "score": 0.92,
        "reason": "Best skill match for seo+content",
        "skill_matched": "content-writer",
        "fallback": False,
    }})
    assert msg.payload.score == 0.92
    assert msg.payload.skill_matched == "content-writer"


def test_task_routed_fallback():
    msg = TaskRoutedMessage(**{**_base("task.routed"), "payload": {
        "task_id": "T6",
        "routed_to": "backup-agent",
        "routed_at": _now(),
        "fallback": True,
    }})
    assert msg.payload.fallback is True


def test_task_routed_score_out_of_range_rejected():
    with pytest.raises(ValidationError):
        TaskRoutedMessage(**{**_base("task.routed"), "payload": {
            "task_id": "T7",
            "routed_to": "kasra",
            "routed_at": _now(),
            "score": 1.5,  # max 1.0
        }})


def test_task_routed_missing_routed_to_rejected():
    with pytest.raises(ValidationError):
        TaskRoutedMessage(**{**_base("task.routed"), "payload": {
            "task_id": "T8",
            "routed_at": _now(),
            # routed_to missing — required
        }})


def test_task_routed_accepted_by_enforcement():
    result = enforce({**_base("task.routed"), "payload": {
        "task_id": "T9", "routed_to": "kasra", "routed_at": _now()
    }})
    assert result["type"] == "task.routed"


# ---------------------------------------------------------------------------
# TaskFailedMessage
# ---------------------------------------------------------------------------


def test_task_failed_minimal():
    msg = TaskFailedMessage(**{**_base("task.failed"), "payload": {
        "task_id": "T10",
        "failed_at": _now(),
        "error": {"code": "SOS-5001", "message": "watchdog timeout"},
    }})
    assert msg.type == "task.failed"
    assert msg.payload.retryable is False
    assert msg.payload.error.code == "SOS-5001"


def test_task_failed_retryable():
    msg = TaskFailedMessage(**{**_base("task.failed"), "payload": {
        "task_id": "T11",
        "failed_at": _now(),
        "error": {"code": "SOS-5002", "message": "transient network error"},
        "retryable": True,
        "retry_count": 2,
        "assigned_to": "kasra",
    }})
    assert msg.payload.retryable is True
    assert msg.payload.retry_count == 2
    assert msg.payload.assigned_to == "kasra"


def test_task_failed_missing_error_rejected():
    with pytest.raises(ValidationError):
        TaskFailedMessage(**{**_base("task.failed"), "payload": {
            "task_id": "T12",
            "failed_at": _now(),
            # error missing — required
        }})


def test_task_failed_bad_error_code_rejected():
    with pytest.raises(ValidationError):
        TaskFailedMessage(**{**_base("task.failed"), "payload": {
            "task_id": "T13",
            "failed_at": _now(),
            "error": {"code": "ERR-001", "message": "bad code format"},
        }})


def test_task_failed_accepted_by_enforcement():
    result = enforce({**_base("task.failed"), "payload": {
        "task_id": "T14",
        "failed_at": _now(),
        "error": {"code": "SOS-5001", "message": "timeout"},
    }})
    assert result["type"] == "task.failed"


# ---------------------------------------------------------------------------
# SkillExecutedMessage
# ---------------------------------------------------------------------------


def test_skill_executed_minimal():
    msg = SkillExecutedMessage(**{**_base("skill.executed"), "payload": {
        "skill_id": "content-writer",
        "invocation_id": _mid(),
        "agent": "mumega",
        "started_at": _now(),
        "completed_at": _now(),
        "success": True,
    }})
    assert msg.type == "skill.executed"
    assert msg.target == "sos:channel:skills"
    assert msg.payload.success is True


def test_skill_executed_with_task_and_cost():
    msg = SkillExecutedMessage(**{**_base("skill.executed"), "payload": {
        "skill_id": "seo-optimizer",
        "invocation_id": _mid(),
        "agent": "trop",
        "started_at": _now(),
        "completed_at": _now(),
        "success": True,
        "task_id": "TASK-099",
        "tokens_spent": 8000,
        "cost_cents": 12,
    }})
    assert msg.payload.task_id == "TASK-099"
    assert msg.payload.tokens_spent == 8000
    assert msg.payload.cost_cents == 12


def test_skill_executed_failure_with_error():
    msg = SkillExecutedMessage(**{**_base("skill.executed"), "payload": {
        "skill_id": "pdf-parser",
        "invocation_id": _mid(),
        "agent": "codex",
        "started_at": _now(),
        "completed_at": _now(),
        "success": False,
        "error": {"code": "SOS-6001", "message": "parse error", "traceback": "..."},
    }})
    assert msg.payload.success is False
    assert msg.payload.error is not None
    assert msg.payload.error.code == "SOS-6001"


def test_skill_executed_invalid_invocation_id_rejected():
    with pytest.raises(ValidationError):
        SkillExecutedMessage(**{**_base("skill.executed"), "payload": {
            "skill_id": "test",
            "invocation_id": "not-a-uuid",
            "agent": "codex",
            "started_at": _now(),
            "completed_at": _now(),
            "success": True,
        }})


def test_skill_executed_accepted_by_enforcement():
    result = enforce({**_base("skill.executed"), "payload": {
        "skill_id": "test-skill",
        "invocation_id": _mid(),
        "agent": "codex",
        "started_at": _now(),
        "completed_at": _now(),
        "success": True,
    }})
    assert result["type"] == "skill.executed"
