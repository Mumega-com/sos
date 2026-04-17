"""Contract tests for task-lifecycle + announce + agent_joined messages.

These tests are the freeze point: if they pass, any implementation (Python,
Rust, TypeScript) that emits records passing them is wire-compatible.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from sos.contracts.messages import (
    AgentJoinedMessage,
    AnnounceMessage,
    TaskClaimedMessage,
    TaskCompletedMessage,
    TaskCreatedMessage,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mid() -> str:
    return str(uuid.uuid4())


def _task_kwargs(payload: dict) -> dict:
    return {
        "type": "task_created",
        "source": "agent:squad",
        "timestamp": _now(),
        "version": "1.0",
        "message_id": _mid(),
        "payload": payload,
    }


def _claimed_kwargs(payload: dict) -> dict:
    return {
        "type": "task_claimed",
        "source": "agent:kasra",
        "timestamp": _now(),
        "version": "1.0",
        "message_id": _mid(),
        "payload": payload,
    }


def _completed_kwargs(payload: dict) -> dict:
    return {
        "type": "task_completed",
        "source": "agent:kasra",
        "timestamp": _now(),
        "version": "1.0",
        "message_id": _mid(),
        "payload": payload,
    }


def _announce_kwargs(**extra) -> dict:
    base = {
        "type": "announce",
        "source": "agent:mumega",
        "timestamp": _now(),
        "version": "1.0",
        "message_id": _mid(),
    }
    base.update(extra)
    return base


def _joined_kwargs(payload: dict) -> dict:
    return {
        "type": "agent_joined",
        "source": "agent:kernel",
        "timestamp": _now(),
        "version": "1.0",
        "message_id": _mid(),
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# TaskCreatedMessage
# ---------------------------------------------------------------------------


def test_task_created_minimal():
    msg = TaskCreatedMessage(**_task_kwargs({
        "task_id": "TASK-001",
        "title": "Minimal task",
        "priority": "medium",
    }))
    assert msg.target == "sos:channel:tasks"
    assert msg.payload.task_id == "TASK-001"
    assert msg.payload.description is None
    assert msg.payload.assignee is None
    assert msg.payload.labels is None


def test_task_created_with_all_optionals():
    msg = TaskCreatedMessage(**_task_kwargs({
        "task_id": "TASK-002",
        "title": "Full task",
        "priority": "high",
        "description": "Detailed description with *markdown*.",
        "assignee": "kasra",
        "skill_id": "backend",
        "project": "sos-core",
        "labels": ["infra", "urgent"],
        "token_budget": 50000,
        "bounty_cents": 500,
    }))
    assert msg.payload.assignee == "kasra"
    assert msg.payload.bounty_cents == 500
    assert msg.payload.labels == ["infra", "urgent"]


def test_task_created_invalid_priority_rejected():
    with pytest.raises(ValueError):
        TaskCreatedMessage(**_task_kwargs({
            "task_id": "TASK-003",
            "title": "Bad priority",
            "priority": "urgent",  # not in enum
        }))


def test_task_created_title_too_long_rejected():
    with pytest.raises(ValueError):
        TaskCreatedMessage(**_task_kwargs({
            "task_id": "TASK-004",
            "title": "x" * 300,  # max 280
            "priority": "low",
        }))


def test_task_created_task_id_pattern_rejected():
    with pytest.raises(ValueError):
        TaskCreatedMessage(**_task_kwargs({
            "task_id": "TASK 005",  # space not allowed
            "title": "Bad task id",
            "priority": "low",
        }))


def test_task_created_assignee_pattern_rejected():
    with pytest.raises(ValueError):
        TaskCreatedMessage(**_task_kwargs({
            "task_id": "TASK-006",
            "title": "Bad assignee",
            "priority": "low",
            "assignee": "1agent",  # must start with lowercase letter
        }))


# ---------------------------------------------------------------------------
# TaskClaimedMessage
# ---------------------------------------------------------------------------


def test_task_claimed_minimal():
    msg = TaskClaimedMessage(**_claimed_kwargs({
        "task_id": "TASK-007",
        "claimed_at": _now(),
    }))
    assert msg.source == "agent:kasra"
    assert msg.payload.task_id == "TASK-007"
    assert msg.payload.worker_info is None


def test_task_claimed_with_worker_info():
    msg = TaskClaimedMessage(**_claimed_kwargs({
        "task_id": "TASK-008",
        "claimed_at": _now(),
        "worker_info": {
            "model": "claude-sonnet-4-6",
            "plan": "growth",
            "estimated_duration_s": 120,
        },
    }))
    assert msg.payload.worker_info is not None
    assert msg.payload.worker_info.model == "claude-sonnet-4-6"
    assert msg.payload.worker_info.estimated_duration_s == 120


# ---------------------------------------------------------------------------
# TaskCompletedMessage
# ---------------------------------------------------------------------------


def test_task_completed_status_done():
    msg = TaskCompletedMessage(**_completed_kwargs({
        "task_id": "TASK-009",
        "status": "done",
        "completed_at": _now(),
        "duration_s": 45,
        "tokens_spent": 12000,
        "cost_cents": 18,
    }))
    assert msg.payload.status == "done"
    assert msg.payload.duration_s == 45
    assert msg.payload.cost_cents == 18


def test_task_completed_status_failed_with_error():
    msg = TaskCompletedMessage(**_completed_kwargs({
        "task_id": "TASK-010",
        "status": "failed",
        "completed_at": _now(),
        "error": {
            "code": "SOS-5001",
            "message": "insufficient budget",
        },
    }))
    assert msg.payload.status == "failed"
    assert msg.payload.error is not None
    assert msg.payload.error.code == "SOS-5001"
    assert msg.payload.error.message == "insufficient budget"


def test_task_completed_invalid_status_rejected():
    with pytest.raises(ValueError):
        TaskCompletedMessage(**_completed_kwargs({
            "task_id": "TASK-011",
            "status": "completed",  # wrong word; valid: done|failed|cancelled|timeout
            "completed_at": _now(),
        }))


def test_task_completed_negative_duration_rejected():
    with pytest.raises(ValueError):
        TaskCompletedMessage(**_completed_kwargs({
            "task_id": "TASK-012",
            "status": "done",
            "completed_at": _now(),
            "duration_s": -5,  # minimum 0
        }))


# ---------------------------------------------------------------------------
# AnnounceMessage
# ---------------------------------------------------------------------------


def test_announce_minimal():
    msg = AnnounceMessage(**_announce_kwargs())
    assert msg.type == "announce"
    assert msg.target == "sos:channel:global"
    assert msg.payload is None


def test_announce_with_payload():
    msg = AnnounceMessage(**_announce_kwargs(payload={
        "text": "Agent mumega online",
        "tool": "claude-code",
        "pid": 12345,
        "tty": "/dev/pts/3",
        "cwd": "/mnt/HC_Volume_104325311/SOS",
    }))
    assert msg.payload is not None
    assert msg.payload.pid == 12345
    assert msg.payload.tool == "claude-code"


# ---------------------------------------------------------------------------
# AgentJoinedMessage
# ---------------------------------------------------------------------------


def test_agent_joined_minimal():
    msg = AgentJoinedMessage(**_joined_kwargs({
        "agent_name": "kasra",
        "joined_at": _now(),
        "tenant_id": None,
    }))
    assert msg.source == "agent:kernel"
    assert msg.target == "sos:channel:system:events"
    assert msg.payload.agent_name == "kasra"
    assert msg.payload.tenant_id is None


def test_agent_joined_with_agent_card():
    msg = AgentJoinedMessage(**_joined_kwargs({
        "agent_name": "trop",
        "joined_at": _now(),
        "agent_card": {
            "name": "trop",
            "role": "executor",
            "skills": ["content", "seo"],
            "arbitrary_key": "accepted because additionalProperties: true",
        },
    }))
    assert msg.payload.agent_card is not None
    assert msg.payload.agent_card["role"] == "executor"
    assert "arbitrary_key" in msg.payload.agent_card


def test_agent_joined_agent_name_pattern_rejected():
    with pytest.raises(ValueError):
        AgentJoinedMessage(**_joined_kwargs({
            "agent_name": "Bad Name",  # uppercase + space not allowed
            "joined_at": _now(),
        }))
