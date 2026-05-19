"""Default event handlers — the organism's reflexes.

These wire services together through events.
Each handler is a simple async function that reacts to a stimulus.

Handlers are intentionally thin — they translate events into actions
by calling existing service code. No business logic lives here.
"""

from __future__ import annotations

import logging

from sos.kernel.events import (
    AGENT_JOINED,
    ANALYTICS_INGESTED,
    EventBus,
    EventHandler,
    HEALTH_DEGRADED,
    PAYMENT_RECEIVED,
    TASK_COMPLETED,
    TENANT_CREATED,
)

logger = logging.getLogger("sos.event_handlers")


# ---------------------------------------------------------------------------
# Individual handlers
# ---------------------------------------------------------------------------


async def on_tenant_created(event: dict) -> None:
    """Send welcome message when a tenant is provisioned."""
    tenant = event.get("data", {})
    tenant_id = tenant.get("tenant_id", "unknown")
    logger.info("Tenant created: %s — queueing welcome flow", tenant_id)
    # TODO: bus send welcome to tenant agent
    # TODO: mirror store onboarding event


async def on_payment_received(event: dict) -> None:
    """Provision workstation when payment is received."""
    data = event.get("data", {})
    tenant_id = data.get("tenant_id", "unknown")
    amount = data.get("amount")
    logger.info("Payment received for %s (amount=%s) — provisioning", tenant_id, amount)
    # TODO: call provision_tenant from billing service


async def on_task_completed(event: dict) -> None:
    """Score task result in feedback loop."""
    data = event.get("data", {})
    task_id = data.get("task_id", "unknown")
    agent = data.get("agent", "unknown")
    logger.info("Task %s completed by %s — triggering feedback scoring", task_id, agent)
    # TODO: trigger feedback scoring via cortex-events


async def on_analytics_ingested(event: dict) -> None:
    """Wake decision agent when new analytics arrive."""
    data = event.get("data", {})
    source = data.get("source", "unknown")
    logger.info("Analytics ingested from %s — waking decision agent", source)
    # TODO: trigger decision agent run


async def on_health_degraded(event: dict) -> None:
    """Alert and attempt recovery when health degrades."""
    data = event.get("data", {})
    service = data.get("service", "unknown")
    reason = data.get("reason", "unknown")
    logger.info("Health degraded: %s (%s) — escalating", service, reason)
    # TODO: send bus message to athena
    # TODO: attempt service restart via systemd


async def on_agent_joined(event: dict) -> None:
    """Sentinel checks a new agent."""
    data = event.get("data", {})
    agent_name = data.get("name", "unknown")
    logger.info("Agent joined: %s — triggering sentinel challenge", agent_name)
    # TODO: trigger sentinel identity challenge


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

DEFAULT_HANDLERS: dict[str, EventHandler] = {
    TENANT_CREATED: on_tenant_created,
    PAYMENT_RECEIVED: on_payment_received,
    TASK_COMPLETED: on_task_completed,
    ANALYTICS_INGESTED: on_analytics_ingested,
    HEALTH_DEGRADED: on_health_degraded,
    AGENT_JOINED: on_agent_joined,
}


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


async def start_event_system(redis_url: str | None = None) -> EventBus:
    """Start the event bus and register all default handlers.

    Returns the running EventBus instance so callers can emit events.
    """
    bus = EventBus(redis_url)

    for event_type, handler in DEFAULT_HANDLERS.items():
        await bus.subscribe([event_type], handler)
        logger.info("Registered handler for %s", event_type)

    logger.info("Event system started with %d default handlers", len(DEFAULT_HANDLERS))
    return bus
