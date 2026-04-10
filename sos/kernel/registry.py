"""SOS Microkernel Service Registry.

Services register their MCP tools on startup.
Kernel discovers tools dynamically.
Service dies -> tools auto-removed (TTL expiry).

This is the foundation of the microkernel architecture:
- Kernel = bus + auth + registry (tiny)
- Everything else = service that registers tools
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import redis.asyncio as aioredis

logger = logging.getLogger("sos.kernel.registry")

REDIS_URL = os.environ.get("SOS_REDIS_URL", "redis://localhost:6379/0")
KEY_PREFIX = "sos:kernel:services:"


class ServiceRegistry:
    """Dynamic service registry backed by Redis with TTL-based liveness."""

    def __init__(self, redis_url: str | None = None) -> None:
        url = redis_url or REDIS_URL
        self._redis: aioredis.Redis = aioredis.from_url(url, decode_responses=True)

    async def register(
        self,
        name: str,
        tools: list[dict],
        health_endpoint: str,
        tenant_scope: str = "global",
        ttl: int = 60,
    ) -> None:
        """Register a service and its tools. Must heartbeat within ttl seconds."""
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "name": name,
            "tools": tools,
            "health_endpoint": health_endpoint,
            "tenant_scope": tenant_scope,
            "registered_at": now,
            "last_heartbeat": now,
        }
        key = f"{KEY_PREFIX}{name}"
        await self._redis.set(key, json.dumps(payload), ex=ttl)
        logger.info("Service %s registered %d tools", name, len(tools))

    async def deregister(self, name: str) -> None:
        """Remove a service from the registry."""
        key = f"{KEY_PREFIX}{name}"
        await self._redis.delete(key)
        logger.info("Service %s deregistered", name)

    async def heartbeat(self, name: str, ttl: int = 60) -> None:
        """Refresh TTL and update last_heartbeat timestamp."""
        key = f"{KEY_PREFIX}{name}"
        raw = await self._redis.get(key)
        if raw is None:
            logger.warning("Service %s heartbeat but not registered", name)
            return
        payload = json.loads(raw)
        payload["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
        await self._redis.set(key, json.dumps(payload), ex=ttl)

    async def list_services(self) -> list[dict]:
        """Return all currently registered services."""
        services: list[dict] = []
        cursor: int | str = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor=int(cursor), match=f"{KEY_PREFIX}*", count=100
            )
            for key in keys:
                raw = await self._redis.get(key)
                if raw is not None:
                    services.append(json.loads(raw))
            if cursor == 0:
                break
        return services

    async def list_tools(self, tenant: str | None = None) -> list[dict]:
        """Flatten all registered tools into an MCP-compatible list.

        If tenant is specified, only include tools from services whose
        tenant_scope is 'global' or matches the tenant.
        """
        services = await self.list_services()
        tools: list[dict] = []
        for svc in services:
            scope = svc.get("tenant_scope", "global")
            if tenant and scope not in ("global", tenant):
                continue
            for tool in svc.get("tools", []):
                tools.append(
                    {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "inputSchema": tool.get("inputSchema", {}),
                        "_service": svc["name"],
                    }
                )
        return tools

    async def get_service_for_tool(self, tool_name: str) -> dict | None:
        """Find which service provides a given tool."""
        services = await self.list_services()
        for svc in services:
            for tool in svc.get("tools", []):
                if tool["name"] == tool_name:
                    return {
                        "name": svc["name"],
                        "health_endpoint": svc["health_endpoint"],
                    }
        return None

    async def health_check(self) -> dict[str, str]:
        """Check health of every registered service by calling its endpoint."""
        import httpx

        services = await self.list_services()
        results: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            for svc in services:
                name = svc["name"]
                endpoint = svc.get("health_endpoint", "")
                try:
                    resp = await client.get(endpoint)
                    results[name] = "healthy" if resp.status_code == 200 else "unhealthy"
                except Exception:
                    results[name] = "unhealthy"
        return results

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.aclose()
