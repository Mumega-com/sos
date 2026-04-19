"""Service registration client.

Every SOS service calls this on startup:

    from sos.kernel.client import register_service

    cancel = await register_service(
        name="mirror",
        tools=[...],
        health_endpoint="http://localhost:8844/health",
    )

    # ... service runs ...

    await cancel()  # on shutdown
"""

from __future__ import annotations

import asyncio
import logging

from sos.kernel.registry import ServiceRegistry

logger = logging.getLogger("sos.kernel.client")


async def register_service(
    name: str,
    tools: list[dict],
    health_endpoint: str,
    redis_url: str | None = None,
    heartbeat_interval: int = 30,
    ttl: int = 60,
) -> asyncio.Task:  # type: ignore[type-arg]
    """Register a service and start a background heartbeat loop.

    Returns an asyncio.Task. Call task.cancel() to stop the heartbeat
    and deregister the service on shutdown.
    """
    registry = ServiceRegistry(redis_url=redis_url)
    await registry.register(
        name=name,
        tools=tools,
        health_endpoint=health_endpoint,
        ttl=ttl,
    )

    async def _heartbeat_loop() -> None:
        try:
            while True:
                await asyncio.sleep(heartbeat_interval)
                try:
                    await registry.heartbeat(name, ttl=ttl)
                except Exception as exc:
                    logger.error("Heartbeat failed for %s: %s", name, exc)
        except asyncio.CancelledError:
            logger.info("Heartbeat cancelled for %s, deregistering", name)
            try:
                await registry.deregister(name)
            except Exception as exc:
                logger.error("Deregister failed for %s: %s", name, exc)
            finally:
                await registry.close()

    task = asyncio.create_task(_heartbeat_loop(), name=f"heartbeat-{name}")
    return task


async def deregister_service(name: str, redis_url: str | None = None) -> None:
    """Manually deregister a service (e.g., during graceful shutdown)."""
    registry = ServiceRegistry(redis_url=redis_url)
    try:
        await registry.deregister(name)
    finally:
        await registry.close()
