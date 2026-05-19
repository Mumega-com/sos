"""Example SOS agent that runs in Docker.

Connects to bus, polls for messages, echoes back.
Demonstrates the minimal agent pattern.

Build: docker build -f docker/Dockerfile.agent --build-arg FRAMEWORK=custom -t my-sos-agent .
Run:   docker run -e SOS_TOKEN=sk-... -e SOS_AGENT_NAME=echo-bot my-sos-agent
"""
from __future__ import annotations

import asyncio
import logging
import os

from sos.adapters.base import SOSBaseAdapter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sos-agent")

adapter = SOSBaseAdapter(
    agent_name=os.environ.get("SOS_AGENT_NAME", "docker-agent"),
    token=os.environ.get("SOS_TOKEN", ""),
    bus_url=os.environ.get("SOS_BUS_URL", "http://localhost:6380"),
    skills=os.environ.get("SOS_SKILLS", "echo").split(","),
)


async def handle(message: dict) -> None:
    text = message.get("text", "")
    source = message.get("from", "unknown")
    logger.info("From %s: %s", source, text)
    await adapter.send(source, f"[echo] {text}")


async def main() -> None:
    if not adapter.token:
        logger.error("Set SOS_TOKEN to connect")
        return
    await adapter.run_loop(handle, poll_interval=5)


if __name__ == "__main__":
    asyncio.run(main())
