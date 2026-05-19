"""QNFT Marketplace Bridge — S063 Track D.

Subscribes to squad task.completed events. When a completed task carries a
``capability_grant`` in its bounty metadata and was verified by the system
(bounty_id present), writes the capability to the tenant's QNFT record.

Auth contract (strict):
  - Event must arrive on a squad stream: sos:stream:global:squad:<squad_id>
  - source field must be agent:squad or a system source (not caller-supplied)
  - bounty_id must be present — system-verified bounties only
  - capability_grant is idempotent: same bounty_id + capability = no-op

Storage:
  - Redis set  qnft:capabilities:{tenant}:{agent}  (fast lookup)
  - Redis key  qnft:grant:idempotency:{bounty_id}:{cap}  (dedup)
  - JSON file  ~/.sos/qnft/{tenant}/{agent}/capabilities.json  (durable)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import redis.asyncio as aioredis

logger = logging.getLogger("sos.qnft_marketplace_bridge")

_STREAM_PATTERNS = ["sos:stream:global:squad:*"]
_BLOCK_MS = 2000
_CONSUMER_GROUP = "qnft-bridge"
_CONSUMER_NAME = "qnft-bridge-0"

# Trusted sources — only these may trigger capability grants
_TRUSTED_SOURCES = frozenset({"agent:squad", "agent:sovereign", "agent:loom", "system"})

_QNFT_DIR = Path(os.environ.get("QNFT_STATE_DIR", Path.home() / ".sos" / "qnft"))


def _build_redis_url() -> str:
    from sos.kernel.settings import get_settings
    return get_settings().redis.build_url()


def _redis_cap_set(tenant: str, agent: str) -> str:
    return f"qnft:capabilities:{tenant}:{agent}"


def _redis_idempotency_key(bounty_id: str, capability: str) -> str:
    return f"qnft:grant:idempotency:{bounty_id}:{capability}"


def _cap_file(tenant: str, agent: str) -> Path:
    path = _QNFT_DIR / tenant / agent / "capabilities.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


async def _write_capability(
    r: aioredis.Redis,
    tenant: str,
    agent: str,
    capability: str,
    bounty_id: str,
    granted_at: str,
) -> bool:
    """Write capability grant. Returns True if newly granted, False if already existed."""
    idem_key = _redis_idempotency_key(bounty_id, capability)

    # Atomic idempotency check — SETNX returns 1 only on first write
    granted = await r.set(idem_key, granted_at, nx=True, ex=86400 * 365)
    if not granted:
        logger.info(
            "[qnft-bridge] capability already granted bounty_id=%s cap=%s — skipping",
            bounty_id, capability,
        )
        return False

    # Write to Redis capability set
    await r.sadd(_redis_cap_set(tenant, agent), capability)  # type: ignore[misc]

    # Persist to durable JSON
    cap_file = _cap_file(tenant, agent)
    try:
        existing: dict = json.loads(cap_file.read_text()) if cap_file.exists() else {}
        existing.setdefault("capabilities", [])
        if capability not in existing["capabilities"]:
            existing["capabilities"].append(capability)
        existing.setdefault("grants", [])
        existing["grants"].append({
            "capability": capability,
            "bounty_id": bounty_id,
            "granted_at": granted_at,
        })
        cap_file.write_text(json.dumps(existing, indent=2))
        logger.info(
            "[qnft-bridge] capability granted tenant=%s agent=%s cap=%s bounty=%s",
            tenant, agent, capability, bounty_id,
        )
    except Exception:
        logger.exception("[qnft-bridge] capability file write failed tenant=%s agent=%s", tenant, agent)

    return True


def _parse_completed_event(fields: dict[str, str]) -> dict | None:
    """Return parsed event dict if this is a system-verified bounty.completed, else None."""
    event_type = fields.get("type", "")
    if event_type != "task.completed":
        return None

    source = fields.get("source", "")
    if source not in _TRUSTED_SOURCES:
        logger.warning("[qnft-bridge] untrusted source=%r in task.completed — rejected", source)
        return None

    try:
        payload = json.loads(fields.get("payload", "{}"))
    except json.JSONDecodeError:
        return None

    result = payload.get("result", {})
    bounty_id = result.get("bounty_id") or payload.get("bounty_id")
    if not bounty_id:
        return None  # not a system-verified bounty

    capability_grant = result.get("capability_grant") or payload.get("capability_grant")
    if not capability_grant:
        return None  # no capability to grant

    return {
        "task_id": payload.get("task_id", ""),
        "bounty_id": bounty_id,
        "capability_grant": str(capability_grant),
        "tenant": result.get("project") or payload.get("project") or "",
        "agent": result.get("agent_addr") or payload.get("assignee") or "",
        "source": source,
    }


class QNFTMarketplaceBridge:
    """Async service that bridges bounty completions to QNFT capability grants."""

    def __init__(self, redis_client: aioredis.Redis | None = None) -> None:
        self._redis: aioredis.Redis | None = redis_client
        self._running = False

    async def run(self) -> None:
        self._running = True
        if self._redis is None:
            self._redis = await aioredis.from_url(_build_redis_url(), decode_responses=True)

        logger.info("[qnft-bridge] starting — watching squad streams for bounty completions")

        # Discover and ensure consumer groups on all squad streams
        streams = await self._discover_streams()
        for stream in streams:
            await self._ensure_group(stream)

        while self._running:
            try:
                streams = await self._discover_streams()
                for stream in streams:
                    await self._ensure_group(stream)

                if not streams:
                    await asyncio.sleep(5)
                    continue

                stream_map = {s: ">" for s in streams}
                results = await self._redis.xreadgroup(  # type: ignore[union-attr]
                    groupname=_CONSUMER_GROUP,
                    consumername=_CONSUMER_NAME,
                    streams=stream_map,
                    count=20,
                    block=_BLOCK_MS,
                )
                for stream_name, entries in (results or []):
                    for entry_id, fields in entries:
                        await self._handle(stream_name, entry_id, fields)
                        await self._redis.xack(stream_name, _CONSUMER_GROUP, entry_id)  # type: ignore[union-attr]
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[qnft-bridge] tick error — sleeping 5s")
                await asyncio.sleep(5)

        logger.info("[qnft-bridge] stopped")

    def stop(self) -> None:
        self._running = False

    async def _discover_streams(self) -> list[str]:
        assert self._redis is not None
        streams: list[str] = []
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(cursor, match="sos:stream:global:squad:*", count=100)
            streams.extend(str(k) for k in keys if ":brain" not in str(k))
            if cursor == 0:
                break
        return streams

    async def _ensure_group(self, stream: str) -> None:
        assert self._redis is not None
        try:
            await self._redis.xgroup_create(stream, _CONSUMER_GROUP, id="$", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                logger.debug("[qnft-bridge] group exists on %s", stream)

    async def _handle(self, stream: str, entry_id: str, fields: dict[str, str]) -> None:
        event = _parse_completed_event(fields)
        if event is None:
            return

        tenant = event["tenant"]
        agent = event["agent"]
        capability = event["capability_grant"]
        bounty_id = event["bounty_id"]

        if not tenant or not agent:
            logger.warning(
                "[qnft-bridge] capability_grant on task=%s has no tenant/agent — skipped",
                event["task_id"],
            )
            return

        granted_at = datetime.now(timezone.utc).isoformat()
        newly_granted = await _write_capability(
            self._redis,  # type: ignore[arg-type]
            tenant, agent, capability, bounty_id, granted_at,
        )

        if newly_granted:
            # Emit a bounty.completed event for downstream consumers (Dreamer, etc.)
            await self._redis.xadd(  # type: ignore[union-attr]
                "sos:stream:global:brain:outcomes",
                {
                    "type": "bounty.completed",
                    "source": "agent:qnft-bridge",
                    "tenant": tenant,
                    "agent": agent,
                    "bounty_id": bounty_id,
                    "capability_grant": capability,
                    "task_id": event["task_id"],
                    "granted_at": granted_at,
                },
            )
            logger.info(
                "[qnft-bridge] bounty.completed emitted task=%s cap=%s",
                event["task_id"], capability,
            )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bridge = QNFTMarketplaceBridge()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(__import__("signal").SIGTERM, bridge.stop)
    await bridge.run()


if __name__ == "__main__":
    asyncio.run(main())
