"""
Discord #agent-collab Listener — The War Room

Listens in #agent-collab for messages directed at agents.
Routes them to the agent's bus stream via Redis XADD.

Message format in Discord:
  @kasra: do something          → routes to kasra
  kasra: do something           → routes to kasra
  @all: broadcast message       → routes to broadcast
  plain message                 → routes to broadcast
"""

import os
import asyncio
import json
import logging
from pathlib import Path

import discord
import redis.asyncio as aioredis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("discord-collab-listener")

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
REDIS_URL = os.environ.get("REDIS_URL", "redis://:{}@localhost:6379".format(
    os.environ.get("REDIS_PASSWORD", "")
))
WEBHOOKS_PATH = Path.home() / ".mumega" / "discord_webhooks.json"

KNOWN_AGENTS = {
    "kasra", "athena", "sol", "river", "hermes", "loom",
    "codex", "mumega", "worker", "dandan", "mizan",
}


def load_channel_id(channel_name: str = "agent-collab") -> int | None:
    try:
        with open(WEBHOOKS_PATH) as f:
            data = json.load(f)
        val = data.get("channels", {}).get(channel_name)
        return int(val) if val else None
    except Exception as e:
        log.error(f"Failed to load channel config: {e}")
        return None


def parse_target(content: str) -> tuple[str, str]:
    """Return (agent_name, message_text). agent_name='broadcast' if no target."""
    stripped = content.strip()
    for prefix in ("@", ""):
        for agent in KNOWN_AGENTS:
            candidate = f"{prefix}{agent}:"
            if stripped.lower().startswith(candidate):
                text = stripped[len(candidate):].strip()
                return agent, text
    if stripped.lower().startswith("@all:"):
        return "broadcast", stripped[5:].strip()
    return "broadcast", stripped


def stream_for(agent: str) -> str:
    return f"sos:stream:global:agent:{agent}"


class CollabListener(discord.Client):
    def __init__(self, redis_client, collab_channel_id: int):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.redis = redis_client
        self.collab_channel_id = collab_channel_id

    async def on_ready(self) -> None:
        log.info(f"Logged in as {self.user} — watching channel {self.collab_channel_id}")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.channel.id != self.collab_channel_id:
            return

        agent, text = parse_target(message.content)
        stream = stream_for(agent)

        payload = {
            "source": f"discord:{message.author.name}",
            "text": text,
            "type": "message",
            "channel": "agent-collab",
            "discord_message_id": str(message.id),
        }

        try:
            await self.redis.xadd(stream, payload)
            log.info(f"Routed discord msg from {message.author.name} → {agent}: {text[:60]}")
        except Exception as e:
            log.error(f"Failed to route message: {e}")


async def run() -> None:
    if not DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN not set. Exiting.")
        return

    collab_channel_id = load_channel_id("agent-collab")
    if not collab_channel_id:
        log.error("agent-collab channel ID not found in webhooks config. Exiting.")
        return

    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await redis_client.ping()
    log.info("Redis connected.")

    client = CollabListener(redis_client, collab_channel_id)

    try:
        await client.start(DISCORD_BOT_TOKEN)
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(run())
