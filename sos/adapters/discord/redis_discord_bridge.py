"""
Redis → Discord Bridge

Subscribes to sos:stream:global:agent:broadcast and per-agent streams,
forwards messages to Discord via webhooks in ~/.mumega/discord_webhooks.json
"""

import os
import asyncio
import json
import time
import logging
from pathlib import Path

import redis.asyncio as aioredis
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("redis-discord-bridge")

REDIS_URL = os.environ.get("REDIS_URL", "redis://:{}@localhost:6379".format(
    os.environ.get("REDIS_PASSWORD", "")
))
WEBHOOKS_PATH = Path.home() / ".mumega" / "discord_webhooks.json"

# Streams to watch: broadcast + per-agent
WATCHED_STREAMS = [
    "sos:stream:global:agent:broadcast",
    "sos:stream:global:agent:kasra",
    "sos:stream:global:agent:athena",
    "sos:stream:global:agent:sol",
    "sos:stream:global:agent:hermes",
    "sos:stream:global:agent:loom",
]

# How to map stream → webhook target
STREAM_TO_WEBHOOK = {
    "sos:stream:global:agent:broadcast": "system.organ-daemon",
    "sos:stream:global:agent:kasra": "agents.kasra",
    "sos:stream:global:agent:athena": "agents.athena",
    "sos:stream:global:agent:sol": "agents.sol",
    "sos:stream:global:agent:hermes": "agents.hermes",
    "sos:stream:global:agent:loom": "agents.kasra",  # loom → kasra channel
}


def load_webhooks() -> dict:
    try:
        with open(WEBHOOKS_PATH) as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Failed to load webhooks: {e}")
        return {}


def get_webhook_url(webhooks: dict, path: str) -> str | None:
    """Resolve dot-path like 'agents.kasra' or 'system.standup'."""
    parts = path.split(".")
    node = webhooks
    for p in parts:
        if isinstance(node, dict):
            node = node.get(p)
        else:
            return None
    return node if isinstance(node, str) else None


def format_message(stream: str, data: dict) -> str:
    source = data.get("source", "unknown")
    text = data.get("text", "")
    msg_type = data.get("type", "message")
    agent = stream.split(":")[-1]

    type_emoji = {
        "security_alert": "🚨",
        "health_alert": "⚠️",
        "heartbeat": "💓",
        "task": "📋",
        "deploy": "🚀",
        "error": "❌",
        "message": "💬",
    }.get(msg_type, "📡")

    if msg_type in ("heartbeat",):
        return None  # suppress heartbeat noise

    return f"{type_emoji} **[{agent}]** `{source}` → {text}"


async def post_to_discord(webhook_url: str, content: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(webhook_url, json={"content": content[:2000]})
            if resp.status_code not in (200, 204):
                log.warning(f"Discord webhook {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            log.error(f"Webhook post failed: {e}")


async def run() -> None:
    webhooks = load_webhooks()
    if not webhooks:
        log.error("No webhooks loaded. Exiting.")
        return

    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    await r.ping()
    log.info(f"Connected to Redis. Watching {len(WATCHED_STREAMS)} streams.")

    # Build initial read positions (read from now, not history)
    last_ids = {stream: "$" for stream in WATCHED_STREAMS}

    # Seed with actual last IDs so $ works on first XREAD
    for stream in WATCHED_STREAMS:
        try:
            entries = await r.xrevrange(stream, "+", "-", count=1)
            if entries:
                last_ids[stream] = entries[0][0]
        except Exception:
            pass

    while True:
        try:
            streams_arg = {s: last_ids[s] for s in WATCHED_STREAMS}
            results = await r.xread(streams_arg, count=10, block=5000)

            for stream, entries in (results or []):
                for entry_id, data in entries:
                    last_ids[stream] = entry_id
                    content = format_message(stream, data)
                    if not content:
                        continue

                    webhook_path = STREAM_TO_WEBHOOK.get(stream, "system.organ-daemon")
                    webhook_url = get_webhook_url(webhooks, webhook_path)
                    if webhook_url:
                        await post_to_discord(webhook_url, content)
                    else:
                        log.warning(f"No webhook for path {webhook_path}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"Bridge error: {e}")
            await asyncio.sleep(5)

    await r.aclose()


if __name__ == "__main__":
    asyncio.run(run())
