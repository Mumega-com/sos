"""Canonical information events for project-level circulation.

This module is intentionally generic. It defines a small event envelope and
helpers that public SOS services and host add-ons can share without depending
on private product code.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

_ALLOWED_EXTRA_PAYLOAD_KEYS = frozenset(
    {
        "task_id",
        "project",
        "skill_id",
        "assignee",
        "reason",
        "score",
        "attempt",
        "claimed_at",
        "page_id",
        "decision_status",
        "selected_decision",
    }
)


@dataclass(frozen=True)
class InformationEvent:
    """Context-bearing event suitable for Redis stream projection."""

    event_id: str
    project: str
    tenant: str
    actor: str
    event_type: str
    summary: str
    links: list[str] = field(default_factory=list)
    visibility: str = "internal"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    entity_type: str = ""
    entity_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_redis_fields(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "project": self.project,
            "tenant": self.tenant,
            "actor": self.actor,
            "event_type": self.event_type,
            "summary": self.summary,
            "links": json.dumps(self.links),
            "visibility": self.visibility,
            "created_at": self.created_at,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "payload": json.dumps(self.payload, default=str),
        }


def project_event_stream(project: str | None) -> str:
    return f"sos:stream:project:{project}:events" if project else "sos:stream:global:events"


def safe_summary(value: Any, limit: int = 240) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _task_public_payload(task: Any) -> dict[str, Any]:
    status = getattr(task, "status", "")
    priority = getattr(task, "priority", "")
    return {
        "task_id": getattr(task, "id", ""),
        "squad_id": getattr(task, "squad_id", ""),
        "title": getattr(task, "title", ""),
        "status": getattr(status, "value", status),
        "priority": getattr(priority, "value", priority),
        "project": getattr(task, "project", None),
        "assignee": getattr(task, "assignee", None),
        "decision_required": bool(getattr(task, "decision_required", False)),
    }


def _public_extra_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    public: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in _ALLOWED_EXTRA_PAYLOAD_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            public[key] = safe_summary(value) if isinstance(value, str) else value
    return public


def information_event_for_task(
    event_type: str,
    task: Any,
    actor: str,
    *,
    tenant_id: str,
    payload: dict[str, Any] | None = None,
) -> InformationEvent:
    project = str(getattr(task, "project", "") or getattr(task, "squad_id", "") or "sos")
    task_id = str(getattr(task, "id", ""))
    title = str(getattr(task, "title", task_id))
    status = getattr(getattr(task, "status", ""), "value", getattr(task, "status", ""))
    summary = safe_summary(f"{event_type}: {task_id} {title} [{status}]")
    return InformationEvent(
        event_id=str(uuid.uuid4()),
        project=project,
        tenant=tenant_id,
        actor=actor,
        event_type=event_type,
        summary=summary,
        visibility="internal",
        entity_type="squad_task",
        entity_id=task_id,
        payload={**_task_public_payload(task), **_public_extra_payload(payload)},
    )


def information_event_to_dict(event: InformationEvent) -> dict[str, Any]:
    return asdict(event)
