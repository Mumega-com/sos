"""Route Squad task events into generic SOS information streams."""
from __future__ import annotations

from typing import Any

import redis

from sos.kernel.information_event import InformationEvent, information_event_for_task, project_event_stream
from sos.observability.logging import get_logger
from sos.services.squad.service import REDIS_HOST, REDIS_PASSWORD, REDIS_PORT, SquadBus

log = get_logger("squad_event_router")


def _redis_client() -> redis.Redis:
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD or None,
        decode_responses=True,
    )


def project_event_to_bus(event: InformationEvent) -> str | None:
    """Write an information event to the project event stream."""
    try:
        return _redis_client().xadd(
            project_event_stream(event.project),
            event.to_redis_fields(),
            maxlen=5000,
            approximate=True,
        )
    except Exception as exc:
        log.warn("information event bus projection failed", event_type=event.event_type, error=str(exc))
        return None


def project_task_event_to_discord(event: InformationEvent, task: Any, actor: str) -> None:
    """Optional host projection hook.

    Public SOS does not require Discord projection. If a deployment provides the
    historical task projection module, use it; otherwise this is a no-op.
    """
    try:
        from sos.services.squad.tasks import project_task_to_discord
    except ImportError:
        return
    project_task_to_discord(event.event_type, task, actor)


def project_task_to_notion(task: Any, actor: str) -> None:
    """Optional host projection hook for Notion."""
    try:
        from sos.services.squad.notion_projection import project_task_to_notion as _project
    except ImportError:
        return
    _project(task, actor)


def route_task_event(
    event_type: str,
    task: Any,
    actor: str,
    *,
    bus: SquadBus | None,
    tenant_id: str,
    squad_payload: dict[str, Any] | None = None,
    discord: bool = True,
    notion: bool = True,
) -> InformationEvent:
    """Route a task event to SquadBus and the generic information stream."""
    event = information_event_for_task(
        event_type,
        task,
        actor,
        tenant_id=tenant_id,
        payload=squad_payload,
    )

    if bus is not None:
        try:
            bus.emit(event_type, getattr(task, "squad_id", ""), actor, squad_payload or event.payload)
        except Exception as exc:
            log.warn("squad event projection failed", event_type=event_type, error=str(exc))

    project_event_to_bus(event)
    if discord:
        project_task_event_to_discord(event, task, actor)
    if notion:
        project_task_to_notion(task, actor)

    return event
