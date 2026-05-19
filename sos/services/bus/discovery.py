
import os
import json
import asyncio
import logging
from typing import Optional, Dict, Any
from sos.kernel.bus import get_bus

log = logging.getLogger("sos.discovery")

REGISTRY_PREFIX = "sos:registry:service"

async def register_service(name: str, port: int, metadata: Optional[Dict[str, Any]] = None):
    """
    Announce a service on the Redis Bus.
    Writes to sos:registry:service:{name} with TTL.
    """
    bus = get_bus()
    await bus.connect()
    
    if not bus._redis:
        log.warning(f"Discovery: Redis not connected. Cannot register {name}.")
        return

    key = f"{REGISTRY_PREFIX}:{name}"
    data = {
        "name": name,
        "port": port,
        "pid": os.getpid(),
        "status": "online",
        "metadata": metadata or {}
    }
    
    # Store with 60s TTL
    await bus._redis.set(key, json.dumps(data), ex=60)
    log.info(f"📡 Service Discovery: Registered '{name}' on port {port}")
    
    # Start background task to keep-alive
    asyncio.create_task(_keep_alive(key, data))

async def _keep_alive(key: str, data: dict):
    bus = get_bus()
    while True:
        await asyncio.sleep(30)
        if bus._redis:
            try:
                await bus._redis.set(key, json.dumps(data), ex=60)
            except Exception as e:
                log.error(f"Discovery: Keep-alive failed for {key}: {e}")

async def get_service_info(name: str) -> Optional[dict]:
    """Retrieve service registration info."""
    bus = get_bus()
    await bus.connect()
    
    if not bus._redis:
        return None

    key = f"{REGISTRY_PREFIX}:{name}"
    raw = await bus._redis.get(key)
    return json.loads(raw) if raw else None

async def list_services() -> Dict[str, dict]:
    """List all currently registered services."""
    bus = get_bus()
    await bus.connect()
    if not bus._redis: return {}

    services = {}
    cursor = 0
    while True:
        cursor, keys = await bus._redis.scan(cursor, match=f"{REGISTRY_PREFIX}:*")
        for k in keys:
            name = k.split(":")[-1]
            raw = await bus._redis.get(k)
            if raw:
                services[name] = json.loads(raw)
        if cursor == 0:
            break
    return services
